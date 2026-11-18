"""Linked bonk.io accounts, stored in BonxBot's database.

Owns: the `discord_bonk_accounts` table (copied from LevelBot on 2026-11-17;
LevelBot may still write to it until it's retired) and `link_claims`, and every
rule about them: one Discord user per bonk.io account (names compared ignoring
case), one main account per Discord user, at most MAX_ACCOUNTS each, and how
often someone may try to link.
Does not own: talking to bonk.io (services/bonk_api.py), deciding which friend
request proves a claim (matching.py), or Discord replies (commands.py, verify.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from db.pool import get_pool
from features.accounts.matching import CLAIM_MINUTES, Claim, Match

MAX_ACCOUNTS = 10  # main + alts per Discord user
CLAIMS_PER_HOUR = 3  # /link tries per Discord user per hour

SOURCE_FRIEND_REQUEST = "friend_request"
SOURCE_LEVELBOT = "levelbot"  # rows LevelBot wrote (its room check)


async def ensure_accounts_schema():
    """Create or extend the tables this feature needs. Safe on every start.

    LevelBot's copy of `discord_bonk_accounts` may keep Discord ids as text;
    they're converted to BIGINT once (LevelBot's driver still writes to it fine).
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS discord_bonk_accounts (
                discord_user_id BIGINT NOT NULL,
                bonk_username TEXT NOT NULL,
                is_main BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE (discord_user_id, bonk_username)
            )
            """
        )
        id_type = await conn.fetchval(
            "SELECT data_type FROM information_schema.columns"
            " WHERE table_name = 'discord_bonk_accounts' AND column_name = 'discord_user_id'"
        )
        if id_type != "bigint":
            await conn.execute(
                "ALTER TABLE discord_bonk_accounts"
                " ALTER COLUMN discord_user_id TYPE BIGINT USING discord_user_id::bigint"
            )
        await conn.execute(
            f"""
            ALTER TABLE discord_bonk_accounts
                ADD COLUMN IF NOT EXISTS bonk_id BIGINT,
                ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT '{SOURCE_LEVELBOT}',
                ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ;
            CREATE UNIQUE INDEX IF NOT EXISTS discord_bonk_accounts_one_owner
                ON discord_bonk_accounts (lower(bonk_username));
            CREATE TABLE IF NOT EXISTS link_claims (
                id SERIAL PRIMARY KEY,
                discord_user_id BIGINT NOT NULL,
                bonk_username TEXT NOT NULL,
                started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMPTZ NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                finished_at TIMESTAMPTZ
            );
            CREATE INDEX IF NOT EXISTS link_claims_pending
                ON link_claims (expires_at) WHERE status = 'pending';
            """
        )


# --- reading -------------------------------------------------------------------


async def accounts_of(discord_user_id: int):
    """A Discord user's linked accounts, main first, then oldest first."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT bonk_username, bonk_id, is_main, source, verified_at, created_at"
            " FROM discord_bonk_accounts WHERE discord_user_id = $1"
            " ORDER BY is_main DESC, created_at, bonk_username",
            discord_user_id,
        )


async def owner_of(bonk_username: str):
    """The row linking this bonk.io name (any case), or None."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT discord_user_id, bonk_username, is_main FROM discord_bonk_accounts"
            " WHERE lower(bonk_username) = lower($1)",
            bonk_username,
        )


async def main_account(discord_user_id: int) -> str | None:
    """The bonk.io name a Discord user submits as, or None if they have none."""
    rows = await accounts_of(discord_user_id)
    return rows[0]["bonk_username"] if rows else None


# --- claims ----------------------------------------------------------------------


@dataclass(frozen=True)
class ClaimStart:
    claim_id: int | None = None  # set when a claim is (still) pending
    minutes_left: int = 0
    problem: str | None = None  # why not, in words for the user


async def start_claim(discord_user_id: int, bonk_username: str) -> ClaimStart:
    """Start (or report the pending) claim that this Discord user owns `bonk_username`."""
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        # One claim at a time per name, even under concurrent /link calls.
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext(lower($1)))", bonk_username)

        owner = await conn.fetchrow(
            "SELECT discord_user_id, bonk_username FROM discord_bonk_accounts"
            " WHERE lower(bonk_username) = lower($1)",
            bonk_username,
        )
        if owner and owner["discord_user_id"] == discord_user_id:
            return ClaimStart(problem=f"**{owner['bonk_username']}** is already linked to you.")
        if owner:
            return ClaimStart(
                problem=(
                    f"**{owner['bonk_username']}** is linked to another Discord account. "
                    "If that's wrong, ask a staff member."
                )
            )

        pending = await conn.fetchrow(
            "SELECT id, discord_user_id, expires_at - NOW() AS time_left FROM link_claims"
            " WHERE status = 'pending' AND expires_at > NOW()"
            " AND lower(bonk_username) = lower($1)",
            bonk_username,
        )
        if pending and pending["discord_user_id"] == discord_user_id:
            return ClaimStart(claim_id=pending["id"], minutes_left=_minutes(pending["time_left"]))
        if pending:
            return ClaimStart(
                problem="Someone else is verifying that account right now. Try again in a few minutes."
            )

        linked = await conn.fetchval(
            "SELECT count(*) FROM discord_bonk_accounts WHERE discord_user_id = $1",
            discord_user_id,
        )
        if linked >= MAX_ACCOUNTS:
            return ClaimStart(problem=f"You can link up to {MAX_ACCOUNTS} accounts.")

        recent = await conn.fetchval(
            "SELECT count(*) FROM link_claims"
            " WHERE discord_user_id = $1 AND started_at > NOW() - INTERVAL '1 hour'",
            discord_user_id,
        )
        if recent >= CLAIMS_PER_HOUR:
            return ClaimStart(problem="That's a lot of tries. Wait a bit, then try again.")

        claim_id = await conn.fetchval(
            "INSERT INTO link_claims (discord_user_id, bonk_username, expires_at)"
            " VALUES ($1, $2, NOW() + $3::interval) RETURNING id",
            discord_user_id,
            bonk_username,
            timedelta(minutes=CLAIM_MINUTES),
        )
        return ClaimStart(claim_id=claim_id, minutes_left=CLAIM_MINUTES)


async def pending_claims() -> list[Claim]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, discord_user_id, bonk_username, started_at FROM link_claims"
            " WHERE status = 'pending' AND expires_at > NOW() ORDER BY started_at"
        )
    return [Claim(r["id"], r["discord_user_id"], r["bonk_username"], r["started_at"]) for r in rows]


async def expire_claims():
    """Mark claims past their time as expired; returns them (to tell their users)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            "UPDATE link_claims SET status = 'expired', finished_at = NOW()"
            " WHERE status = 'pending' AND expires_at <= NOW()"
            " RETURNING discord_user_id, bonk_username"
        )


@dataclass(frozen=True)
class LinkOutcome:
    linked: bool
    is_main: bool = False
    problem: str | None = None


async def complete_claim(match: Match) -> LinkOutcome:
    """Link the account a friend request proved, unless someone got there first."""
    claim = match.claim
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext(lower($1)))", match.bonk_username)
        still_pending = await conn.fetchval(
            "UPDATE link_claims SET status = 'linked', finished_at = NOW()"
            " WHERE id = $1 AND status = 'pending' RETURNING id",
            claim.id,
        )
        if not still_pending:
            return LinkOutcome(linked=False)

        owner = await conn.fetchval(
            "SELECT discord_user_id FROM discord_bonk_accounts WHERE lower(bonk_username) = lower($1)",
            match.bonk_username,
        )
        if owner is not None and owner != claim.discord_user_id:
            await conn.execute("UPDATE link_claims SET status = 'taken' WHERE id = $1", claim.id)
            return LinkOutcome(linked=False, problem="That account was linked to someone else first.")
        if owner == claim.discord_user_id:
            await conn.execute(
                "UPDATE discord_bonk_accounts SET bonk_id = $2, verified_at = NOW()"
                " WHERE lower(bonk_username) = lower($1)",
                match.bonk_username,
                match.bonk_id,
            )
            return LinkOutcome(linked=True)

        has_main = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM discord_bonk_accounts"
            " WHERE discord_user_id = $1 AND is_main)",
            claim.discord_user_id,
        )
        await conn.execute(
            "INSERT INTO discord_bonk_accounts"
            " (discord_user_id, bonk_username, is_main, bonk_id, source, verified_at)"
            " VALUES ($1, $2, $3, $4, $5, NOW())",
            claim.discord_user_id,
            match.bonk_username,  # bonk.io's own spelling
            not has_main,
            match.bonk_id,
            SOURCE_FRIEND_REQUEST,
        )
        return LinkOutcome(linked=True, is_main=not has_main)


# --- changing links ---------------------------------------------------------------


async def set_main(discord_user_id: int, bonk_username: str) -> str | None:
    """Make one of the user's accounts their main; returns its spelling, or None if not theirs."""
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        name = await conn.fetchval(
            "SELECT bonk_username FROM discord_bonk_accounts"
            " WHERE discord_user_id = $1 AND lower(bonk_username) = lower($2)",
            discord_user_id,
            bonk_username,
        )
        if name is None:
            return None
        await conn.execute(
            "UPDATE discord_bonk_accounts SET is_main = (lower(bonk_username) = lower($2))"
            " WHERE discord_user_id = $1",
            discord_user_id,
            name,
        )
        return name


async def unlink(discord_user_id: int, bonk_username: str) -> str | None:
    """Remove one of the user's accounts; returns its spelling, or None if not theirs.

    Removing the main makes the oldest remaining account the main.
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        removed = await conn.fetchrow(
            "DELETE FROM discord_bonk_accounts"
            " WHERE discord_user_id = $1 AND lower(bonk_username) = lower($2)"
            " RETURNING bonk_username, is_main",
            discord_user_id,
            bonk_username,
        )
        if removed is None:
            return None
        if removed["is_main"]:
            await conn.execute(
                "UPDATE discord_bonk_accounts SET is_main = TRUE WHERE ctid = ("
                " SELECT ctid FROM discord_bonk_accounts WHERE discord_user_id = $1"
                " ORDER BY created_at, bonk_username LIMIT 1)",
                discord_user_id,
            )
        return removed["bonk_username"]


def _minutes(interval) -> int:
    return max(1, round(interval.total_seconds() / 60))
