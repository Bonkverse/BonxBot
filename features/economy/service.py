from db.pool import get_pool

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