from datetime import datetime, timedelta, timezone

from db.pool import get_pool
from .cup import ensure_cup_schema

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


async def join_region(discord_id: int, region_name: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        region = await conn.fetchval(
            "SELECT name FROM regions WHERE LOWER(name)=LOWER($1)", region_name.strip()
        )
        if region is None:
            return "❌ Region not found. Use `/regions` to see where you can join."

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
                return f"🌍 You already live in **{region}**."

            now = datetime.now(timezone.utc).replace(tzinfo=None)
            joined = member["region_joined_at"]
            if joined and now < joined + SWITCH_COOLDOWN:
                available_at = (joined + SWITCH_COOLDOWN).replace(tzinfo=timezone.utc)
                return f"⏳ You can switch regions <t:{int(available_at.timestamp())}:R>."

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
        return "🏛️ Welcome to **Veri**. You earned the **Founding Resident** tag."
    if region == "Veri":
        return "🏛️ Welcome to **Veri**. The capital watches. Arthur Maia says behave."
    return f"🌍 You joined **{region}**."
