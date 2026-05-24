from db.pool import get_pool
from datetime import datetime, timedelta

DAILY_AMOUNT = 1000


async def get_or_create_user(discord_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT * FROM users WHERE discord_id=$1",
            discord_id
        )
        if not user:
            await conn.execute(
                "INSERT INTO users (discord_id) VALUES ($1)",
                discord_id
            )
            user = await conn.fetchrow(
                "SELECT * FROM users WHERE discord_id=$1",
                discord_id
            )
        return user


async def claim_daily(discord_id: int):
    # Ensure user exists first — otherwise fetchrow returns None
    # and we crash trying to subscript it.
    await get_or_create_user(discord_id)

    pool = await get_pool()

    async with pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT last_daily FROM users WHERE discord_id=$1",
            discord_id
        )

        now = datetime.utcnow()

        if user["last_daily"]:
            next_claim = user["last_daily"] + timedelta(hours=24)
            if now < next_claim:
                return False, next_claim

        await conn.execute(
            "UPDATE users SET last_daily=$1, bonx=bonx+$2 WHERE discord_id=$3",
            now, DAILY_AMOUNT, discord_id
        )

        # Log the daily claim in the ledger for consistency with other rewards
        await conn.execute(
            "INSERT INTO bonx_transactions (discord_id, amount, reason) VALUES ($1, $2, $3)",
            discord_id, DAILY_AMOUNT, "daily"
        )

        return True, now


async def adjust_bonx(discord_id: int, amount: int, reason: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET bonx = bonx + $1 WHERE discord_id = $2",
            amount, discord_id
        )
        await conn.execute(
            "INSERT INTO bonx_transactions (discord_id, amount, reason) VALUES ($1, $2, $3)",
            discord_id, amount, reason
        )