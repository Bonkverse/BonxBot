from db.pool import get_pool
from datetime import datetime, timedelta
from secrets import choice

from config.settings import (
    DAILY_AMOUNT,
    DAILY_STREAK_STEP,
    DAILY_STREAK_CAP,
)

# How long after the last claim a new claim still counts as a streak continuation.
# Claims are gated to once per 24h, so "consecutive day" == "claimed within 48h".
STREAK_WINDOW_HOURS = 48


async def ensure_economy_schema():
    """Add columns this feature needs if they aren't there yet.

    Safe to run on every start — mirrors the pattern used by
    features/skins/bonkverse_sync.py (whichever runs first creates them).
    Fresh installs already get the column from schema.sql.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS daily_streak INTEGER NOT NULL DEFAULT 0
            """
        )


async def get_or_create_user(discord_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT * FROM users WHERE discord_id=$1",
            discord_id
        )
        if not user:
            await conn.execute(
                "INSERT INTO users (discord_id) VALUES ($1) ON CONFLICT (discord_id) DO NOTHING",
                discord_id
            )
            user = await conn.fetchrow(
                "SELECT * FROM users WHERE discord_id=$1",
                discord_id
            )
        return user


async def claim_daily(discord_id: int):
    """Claim the daily reward with a consecutive-day streak bonus.

    Returns:
      (False, next_claim_datetime)  — already claimed, come back later
      (True, {"amount": int, "streak": int})
    """
    # Ensure user exists first — otherwise fetchrow returns None
    # and we crash trying to subscript it.
    await get_or_create_user(discord_id)

    pool = await get_pool()

    async with pool.acquire() as conn:
        # Row lock + transaction: two rapid /daily calls can't both pay.
        # The second one waits on the lock, then sees the updated last_daily.
        async with conn.transaction():
            user = await conn.fetchrow(
                "SELECT last_daily, daily_streak FROM users WHERE discord_id=$1 FOR UPDATE",
                discord_id
            )

            now = datetime.utcnow()

            if user["last_daily"]:
                next_claim = user["last_daily"] + timedelta(hours=24)
                if now < next_claim:
                    return False, next_claim

                if now < user["last_daily"] + timedelta(hours=STREAK_WINDOW_HOURS):
                    streak = user["daily_streak"] + 1
                else:
                    streak = 1  # missed more than a day — streak resets
            else:
                streak = 1

            bonus_steps = min(streak - 1, DAILY_STREAK_CAP)
            amount = DAILY_AMOUNT + bonus_steps * DAILY_STREAK_STEP

            await conn.execute(
                "UPDATE users SET last_daily=$1, daily_streak=$2, bonx=bonx+$3 WHERE discord_id=$4",
                now, streak, amount, discord_id
            )

            # Log the daily claim in the ledger for consistency with other rewards
            await conn.execute(
                "INSERT INTO bonx_transactions (discord_id, amount, reason, created_at) VALUES ($1, $2, $3, $4)",
                discord_id, amount, "daily", now
            )

    return True, {"amount": amount, "streak": streak}


async def record_balance_change(conn, discord_id, amount, reason, *, minimum=0):
    """Update the balance and ledger using the caller's transaction."""
    balance = await conn.fetchval(
        """
        UPDATE users SET bonx = bonx + $1
        WHERE discord_id = $2 AND ($3::integer IS NULL OR bonx >= $3)
        RETURNING bonx
        """,
        amount, discord_id, max(minimum, -amount) if minimum or amount < 0 else None,
    )
    if balance is not None:
        await conn.execute(
            "INSERT INTO bonx_transactions (discord_id, amount, reason) VALUES ($1, $2, $3)",
            discord_id, amount, reason,
        )
    return balance


async def adjust_bonx(discord_id: int, amount: int, reason: str, *, minimum: int = 0):
    """Return the committed balance, or None if funds are insufficient."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            return await record_balance_change(conn, discord_id, amount, reason, minimum=minimum)


async def lock_balances(conn, *discord_ids):
    rows = await conn.fetch(
        """
        SELECT discord_id, bonx FROM users
        WHERE discord_id = ANY($1::bigint[])
        ORDER BY discord_id FOR UPDATE
        """,
        discord_ids,
    )
    return {row["discord_id"]: row["bonx"] for row in rows}


async def transfer_bonx(sender_id: int, receiver_id: int, amount: int) -> bool:
    """Atomically move Bonx sender -> receiver (both ledger rows included).

    Returns False if the sender's balance no longer covers the amount.
    Caller must make sure both users exist (get_or_create_user).
    """
    if sender_id == receiver_id or amount <= 0:
        return False

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            balances = await lock_balances(conn, sender_id, receiver_id)
            if len(balances) != 2 or balances[sender_id] < amount:
                return False

            await record_balance_change(conn, sender_id, -amount, "give")
            await record_balance_change(conn, receiver_id, amount, "receive")
    return True


async def resolve_coinflip(challenger_id: int, target_id: int, amount: int):
    """Escrow both stakes and pay the winner in ONE transaction.

    The coin decides the winner: Heads = challenger, Tails = target.
    Both rows are locked in a stable (sorted) order so two overlapping
    coinflips can never deadlock each other.

    Returns None if either player can no longer cover the stake,
    otherwise a result dict for the announcement.
    """
    if challenger_id == target_id or amount <= 0:
        return None

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            balances = await lock_balances(conn, challenger_id, target_id)
            if len(balances) != 2 or min(balances.values()) < amount:
                return None

            side = choice(["Heads", "Tails"])
            winner_id = challenger_id if side == "Heads" else target_id
            loser_id = target_id if side == "Heads" else challenger_id

            loser_balance = await record_balance_change(conn, loser_id, -amount, "coinflip_stake")
            await record_balance_change(conn, winner_id, -amount, "coinflip_stake")
            winner_balance = await record_balance_change(conn, winner_id, amount * 2, "coinflip_win")

    return {
        "side": side,
        "winner_id": winner_id,
        "loser_id": loser_id,
        "amount": amount,
        "winner_balance": winner_balance,
        "loser_balance": loser_balance,
    }
