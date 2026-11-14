from datetime import datetime, timedelta, timezone
from fractions import Fraction

from db.pool import get_pool

CUP_WEIGHTS = (40, 25, 20, 15)
SKIN_SUBMISSION_POINTS = 10


CUP_SCHEMA = """
CREATE TABLE IF NOT EXISTS region_cup_season (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    opened_at TIMESTAMP NOT NULL DEFAULT (clock_timestamp() AT TIME ZONE 'UTC')
);
CREATE TABLE IF NOT EXISTS region_membership_history (
    id BIGSERIAL PRIMARY KEY,
    discord_id BIGINT NOT NULL,
    region TEXT NOT NULL,
    joined_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS region_membership_history_user_idx
    ON region_membership_history (discord_id, joined_at DESC, id DESC);
CREATE TABLE IF NOT EXISTS region_rank_history (
    discord_id BIGINT NOT NULL,
    region TEXT NOT NULL,
    tier SMALLINT NOT NULL,
    earned_at TIMESTAMP NOT NULL DEFAULT (clock_timestamp() AT TIME ZONE 'UTC'),
    PRIMARY KEY (discord_id, region, tier)
);
CREATE TABLE IF NOT EXISTS region_cup_results (
    week_start TIMESTAMP PRIMARY KEY,
    champion TEXT
);
"""


async def ensure_cup_schema(conn):
    await conn.execute(CUP_SCHEMA)
    opened_at = await conn.fetchval(
        "INSERT INTO region_cup_season DEFAULT VALUES ON CONFLICT DO NOTHING RETURNING opened_at"
    )
    if opened_at is None:
        return

    # Preserve existing membership tenure and use existing earned ranks as
    # the baseline when scoring the inaugural week.
    await conn.execute(
        """
        INSERT INTO region_membership_history (discord_id, region, joined_at)
        SELECT discord_id, region, region_joined_at FROM users
        WHERE region IS NOT NULL AND region_joined_at IS NOT NULL
        """
    )
    await conn.execute(
        """
        INSERT INTO region_rank_history (discord_id, region, tier, earned_at)
        SELECT discord_id, region, tier, $1 FROM region_rank_progress
        """,
        opened_at,
    )
    await conn.execute("ALTER TABLE skins ADD COLUMN IF NOT EXISTS cup_submitted_at TIMESTAMP")
    await conn.execute(
        """
        ALTER TABLE skins ALTER COLUMN cup_submitted_at
        SET DEFAULT (clock_timestamp() AT TIME ZONE 'UTC')
        """
    )


def week_start(when):
    return (when - timedelta(days=when.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


async def score_week(conn, start, end, opened_at):
    rows = await conn.fetch(
        """
        WITH residences AS (
            SELECT DISTINCT ON (discord_id) discord_id, region, joined_at
            FROM region_membership_history
            WHERE joined_at < $2
            ORDER BY discord_id, joined_at DESC, id DESC
        ), ledger AS (
            SELECT discord_id,
                BOOL_OR(reason='daily') AS claimed_daily,
                COALESCE(SUM(amount) FILTER (WHERE reason IN ('daily','skin_vote')), 0) AS economy,
                COALESCE(SUM(amount) FILTER (WHERE reason IN ('coinflip_stake','coinflip_win')), 0) AS industry,
                COUNT(*) FILTER (WHERE reason='skin_vote') AS votes
            FROM bonx_transactions b
            JOIN residences h USING (discord_id)
            WHERE b.created_at >= GREATEST($1, h.joined_at) AND b.created_at < $2
            GROUP BY discord_id
        ), submissions AS (
            SELECT submitter_id, COUNT(*) AS skins
            FROM skins s
            JOIN residences h ON h.discord_id=s.submitter_id
            WHERE cup_submitted_at >= GREATEST($1, h.joined_at) AND cup_submitted_at < $2
            GROUP BY submitter_id
        ), ranks AS (
            SELECT discord_id, region, MAX(tier) AS tier
            FROM region_rank_history WHERE earned_at < $2
            GROUP BY discord_id, region
        )
        SELECT r.name, COUNT(l.discord_id) AS members,
            COALESCE(SUM(l.economy), 0) AS economy,
            GREATEST(COALESCE(SUM(l.industry), 0), 0) AS industry,
            COALESCE(SUM(l.votes + $3::INTEGER * COALESCE(s.skins, 0)), 0) AS culture,
            COALESCE(SUM(COALESCE(p.tier, 1)) FILTER (WHERE l.discord_id IS NOT NULL), 0) AS prestige
        FROM regions r
        LEFT JOIN residences h ON h.region=r.name AND h.joined_at <= $2 - INTERVAL '3 days'
        LEFT JOIN ledger l ON l.discord_id=h.discord_id AND l.claimed_daily
        LEFT JOIN submissions s ON s.submitter_id=l.discord_id
        LEFT JOIN ranks p ON p.discord_id=l.discord_id AND p.region=r.name
        WHERE r.name <> 'Ramidia'
        GROUP BY r.name
        """,
        max(start, opened_at), end, SKIN_SUBMISSION_POINTS,
    )
    metrics = ("economy", "industry", "culture", "prestige")
    per_capita = {
        row["name"]: [Fraction(int(row[key]), max(1, row["members"])) for key in metrics]
        for row in rows
    }
    best = [max((values[i] for values in per_capita.values()), default=0) for i in range(4)]
    standings = []
    for row in rows:
        score = sum(
            weight * value / maximum
            for weight, value, maximum in zip(CUP_WEIGHTS, per_capita[row["name"]], best)
            if maximum
        )
        standings.append((row["name"], row["members"], int(row["economy"]), score))
    return sorted(standings, key=lambda row: (-row[3], -row[2], row[1], row[0]))


async def get_cup_standings():
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction(isolation="repeatable_read"):
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            start = week_start(now)
            opened_at = await conn.fetchval("SELECT opened_at FROM region_cup_season")
            current = await score_week(conn, start, now, opened_at)
            previous = start - timedelta(days=7)
            result = await conn.fetchrow(
                "SELECT champion FROM region_cup_results WHERE week_start=$1", previous
            )
            if result is not None:
                champion = result["champion"]
            elif opened_at < start:
                finished = await score_week(conn, previous, start, opened_at)
                champion = finished[0][0] if finished and finished[0][1] else None
            else:
                champion = None

        # Avoid snapshot write conflicts when two members first open the Cup
        # together. A saved result takes precedence over any later calculation.
        if result is None and opened_at < start:
            await conn.execute(
                """
                INSERT INTO region_cup_results (week_start, champion) VALUES ($1, $2)
                ON CONFLICT (week_start) DO NOTHING
                """,
                previous, champion,
            )
            champion = await conn.fetchval(
                "SELECT champion FROM region_cup_results WHERE week_start=$1", previous
            )

    return max(start, opened_at), start + timedelta(days=7), current, champion
