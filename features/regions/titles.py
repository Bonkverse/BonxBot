from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from db.pool import get_pool
from .cup import week_start


@dataclass(frozen=True)
class Title:
    name: str
    rule: str
    founding_leader: int | None
    appointed: bool = False


TITLES = {
    "Skintopia": Title("Master of the Guild", "Most skins submitted", 1193371251968852000),
    "Bonktown": Title("First Among Equals", "Highest Bonx balance among residents who claimed a daily this week", 1105490825267396680),
    "Creativia": Title("Hand of the Black Queen", "Appointed by the regional leader from Tier 5 residents", 1205837634073792572, True),
    "Spherehold": Title("Marshal of the Marches", "Longest current daily streak", 1137397479776735313),
    "Versetralis": Title("Employee of the Month", "Appointed by the regional leader from Tier 5 residents", 1410744402976051291, True),
    "Veri": Title("Chancellor of Veri", "Appointed by the regional leader from Tier 5 residents", 1116043991037775972, True),
    "Arena City": Title("Arena Champion", "Most coinflip wins this week", None),
}

TITLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS region_leaders (
    region TEXT PRIMARY KEY REFERENCES regions(name),
    leader_id BIGINT NOT NULL,
    since TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS region_titles (
    region TEXT PRIMARY KEY REFERENCES regions(name),
    holder_id BIGINT,
    since TIMESTAMP,
    week_start TIMESTAMP
);
"""

# Each expression is selected only from this fixed set. The date filters use the
# same UTC week as the Cup. Lifetime submissions and weekly wins are distinct.
TITLE_METRICS = {
    "Skintopia": "SELECT COUNT(*) FROM skins WHERE submitter_id=u.discord_id",
    "Bonktown": """
        SELECT u.bonx WHERE EXISTS (
            SELECT 1 FROM bonx_transactions
            WHERE discord_id=u.discord_id AND reason='daily'
              AND created_at >= $2 AND created_at < $3
        )
    """,
    "Spherehold": """
        SELECT u.daily_streak
        WHERE u.last_daily > $3::timestamp - INTERVAL '48 hours' AND u.last_daily <= $3
    """,
    "Arena City": """
        SELECT COUNT(*) FROM bonx_transactions
        WHERE discord_id=u.discord_id AND reason='coinflip_win'
          AND created_at >= $2 AND created_at < $3
    """,
}


async def ensure_title_schema(conn):
    await conn.execute(TITLE_SCHEMA)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for region, title in TITLES.items():
        # Seed once. Future succession can update the registry or title row.
        if title.appointed:
            await conn.execute(
                """
                INSERT INTO region_leaders (region, leader_id, since)
                SELECT name, $2, $3 FROM regions WHERE name=$1
                ON CONFLICT (region) DO NOTHING
                """,
                region, title.founding_leader, now,
            )
        initial_holder = None if title.appointed else title.founding_leader
        await conn.execute(
            """
            INSERT INTO region_titles (region, holder_id, since)
            SELECT name, $2, $3 FROM regions WHERE name=$1
            ON CONFLICT (region) DO NOTHING
            """,
            region, initial_holder, now if initial_holder else None,
        )


async def get_titleholder(region):
    title = TITLES.get(region)
    if title is None:
        return None

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            state = await conn.fetchrow(
                "SELECT * FROM region_titles WHERE region=$1 FOR UPDATE", region
            )
            if state is None:
                return None

            now = datetime.now(timezone.utc).replace(tzinfo=None)
            week = week_start(now)
            holder = None
            if state["holder_id"] is not None:
                holder = await conn.fetchval(
                    """
                    SELECT discord_id FROM users
                    WHERE discord_id=$1 AND region=$2 AND region_joined_at <= $3
                    """,
                    state["holder_id"], region, state["since"],
                )

            if title.appointed:
                if holder is None and state["holder_id"] is not None:
                    await save_titleholder(conn, region, None, now)
                return holder

            if holder is not None and region != "Arena City":
                if now < state["since"] + timedelta(hours=24):
                    return holder

            candidate = await conn.fetchval(
                f"""
                SELECT u.discord_id FROM users u
                CROSS JOIN LATERAL ({TITLE_METRICS[region]}) AS eligible(metric)
                WHERE u.region=$1 AND (eligible.metric > 0 OR ($1='Bonktown' AND eligible.metric >= 0))
                  AND $2::timestamp <= $3::timestamp
                ORDER BY eligible.metric DESC, (u.discord_id=$4) DESC NULLS LAST, u.discord_id
                LIMIT 1
                """,
                region, week, now, holder if state["week_start"] == week or region != "Arena City" else None,
            )
            arena_week = week if region == "Arena City" else None
            if candidate != holder or holder != state["holder_id"] or arena_week != state["week_start"]:
                await save_titleholder(conn, region, candidate, now, arena_week)
            return candidate


async def save_titleholder(conn, region, holder, now, week=None):
    await conn.execute(
        "UPDATE region_titles SET holder_id=$2, since=$3, week_start=$4 WHERE region=$1",
        region, holder, now if holder is not None else None, week,
    )


async def get_region_leader(region):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT leader_id FROM region_leaders WHERE region=$1", region)


async def appoint(leader_id: int, target_id: int) -> tuple[bool, str]:
    """(appointed, what to tell them)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Match against the target's current region so a leader who
            # oversees multiple regions can only appoint in the right one.
            region = await conn.fetchval("SELECT region FROM users WHERE discord_id=$1", target_id)
            if region not in TITLES or not TITLES[region].appointed:
                return False, (
                    "Only Creativia, Versetralis and Veri appoint their Tier 6 title, and "
                    f"<@{target_id}> doesn't live in one of them."
                )

            leader = await conn.fetchval(
                "SELECT leader_id FROM region_leaders WHERE region=$1 FOR UPDATE", region
            )
            if leader != leader_id:
                return False, (
                    f"Only {region}’s leader can appoint its {TITLES[region].name}. "
                    f"See who leads it with `/regioninfo {region}`."
                )

            await conn.fetchval("SELECT region FROM region_titles WHERE region=$1 FOR UPDATE", region)
            residence = await conn.fetchval(
                "SELECT region FROM users WHERE discord_id=$1 FOR UPDATE", target_id
            )
            tier = await conn.fetchval(
                "SELECT tier FROM region_rank_progress WHERE discord_id=$1 AND region=$2",
                target_id, region,
            )
            if residence != region or tier != 5:
                return False, (
                    f"<@{target_id}> has to live in {region} and have reached Tier 5 there. "
                    "They can check with `/rank`."
                )

            now = datetime.now(timezone.utc).replace(tzinfo=None)
            await save_titleholder(conn, region, target_id, now)
    return True, f"<@{target_id}> is now **{TITLES[region].name}** of {region}."
