import difflib
from datetime import datetime, timedelta, timezone

from db.pool import get_pool
from .cup import ensure_cup_schema
from .titles import ensure_title_schema

SWITCH_COOLDOWN = timedelta(days=7)
FOUNDING_WINDOW = timedelta(days=7)


async def ensure_region_schema():
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                ALTER TABLE users
                  ADD COLUMN IF NOT EXISTS region_joined_at TIMESTAMP,
                  ADD COLUMN IF NOT EXISTS veri_founding_resident BOOLEAN NOT NULL DEFAULT FALSE
                """
            )
            await conn.execute(
                """
                UPDATE users SET best_daily_streak = daily_streak
                WHERE daily_streak > best_daily_streak;

                CREATE TABLE IF NOT EXISTS region_rank_progress (
                    discord_id BIGINT NOT NULL REFERENCES users(discord_id),
                    region TEXT NOT NULL REFERENCES regions(name),
                    tier SMALLINT NOT NULL CHECK (tier BETWEEN 2 AND 5),
                    PRIMARY KEY (discord_id, region)
                );
                CREATE INDEX IF NOT EXISTS bonx_transactions_user_id_idx
                    ON bonx_transactions (discord_id);
                """
            )
            # Existing memberships predate the tracked join date. Give them enough
            # tenure to switch immediately and qualify for the upcoming Cup.
            await conn.execute(
                """
                UPDATE users SET region_joined_at = (now() AT TIME ZONE 'UTC') - INTERVAL '7 days'
                WHERE region IS NOT NULL AND region_joined_at IS NULL
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS region_openings (
                    region TEXT PRIMARY KEY REFERENCES regions(name),
                    opened_at TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'UTC')
                )
                """
            )
            await conn.execute(
                """
                INSERT INTO region_openings (region) SELECT name FROM regions WHERE name = 'Veri'
                ON CONFLICT (region) DO NOTHING
                """
            )
            await ensure_cup_schema(conn)
            await ensure_title_schema(conn)


def did_you_mean(typed: str, names) -> str:
    """'No region called "skintopa". Did you mean Skintopia?' (or a pointer to /regions)."""
    by_key = {name.casefold(): name for name in names}
    close = difflib.get_close_matches(typed.strip().casefold(), list(by_key), n=1, cutoff=0.6)
    text = f"There's no region called **{typed.strip()}**."
    if close:
        return f"{text} Did you mean **{by_key[close[0]]}**?"
    return f"{text} `/regions` lists them all."


async def region_names(conn) -> list[str]:
    return [row["name"] for row in await conn.fetch("SELECT name FROM regions ORDER BY name")]


async def join_region(discord_id: int, region_name: str) -> tuple[bool, str]:
    """(joined, what to tell them). Only a join is posted in the channel."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        region = await conn.fetchval(
            "SELECT name FROM regions WHERE LOWER(name)=LOWER($1)", region_name.strip()
        )
        if region is None:
            return False, did_you_mean(region_name, await region_names(conn))

        async with conn.transaction():
            await conn.execute(
                "INSERT INTO users (discord_id) VALUES ($1) ON CONFLICT (discord_id) DO NOTHING",
                discord_id,
            )
            member = await conn.fetchrow(
                "SELECT region, region_joined_at FROM users WHERE discord_id=$1 FOR UPDATE",
                discord_id,
            )
            if member["region"] == region:
                return False, f"You already live in **{region}**."

            now = datetime.now(timezone.utc).replace(tzinfo=None)
            joined = member["region_joined_at"]
            if joined and now < joined + SWITCH_COOLDOWN:
                available_at = (joined + SWITCH_COOLDOWN).replace(tzinfo=timezone.utc)
                stamp = int(available_at.timestamp())
                return False, (
                    f"Region switches have a 7-day cooldown. You can move again <t:{stamp}:R> "
                    f"(<t:{stamp}:f>)."
                )

            founding_resident = False
            if region == "Veri":
                opened_at = await conn.fetchval(
                    "SELECT opened_at FROM region_openings WHERE region = 'Veri'"
                )
                founding_resident = opened_at is not None and opened_at <= now < opened_at + FOUNDING_WINDOW

            await conn.execute(
                """
                UPDATE users
                SET region=$1, region_joined_at=$2,
                    veri_founding_resident=veri_founding_resident OR $3
                WHERE discord_id=$4
                """,
                region, now, founding_resident, discord_id,
            )
            await conn.execute(
                """
                INSERT INTO region_membership_history (discord_id, region, joined_at)
                VALUES ($1, $2, $3)
                """,
                discord_id, region, now,
            )


    if founding_resident:
        return True, "🏛️ Welcome to **Veri**. You earned the **Founding Resident** tag."
    if region == "Veri":
        return True, "🏛️ Welcome to **Veri**. The capital watches. Arthur Maia says behave."
    return True, f"🌍 You joined **{region}**."
