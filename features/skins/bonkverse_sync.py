"""Sending Discord skin submissions to bonkverse.io.

Every row in `skins` starts unsynced (bonkverse_skin_id IS NULL). A new
submission is pushed right away; anything that didn't make it (Bonkverse down,
or skins submitted before this existed) is picked up by `sync_pending`, which
the poller calls every minute. That makes the one-time migration of old
submissions the same code path as live syncing: it just drains the backlog.

A skin that fails temporarily waits longer before each retry (1, 2, 4 ... 128
minutes, about 4 hours in all), so one bad link never holds up the rest. It
stops being retried when Bonkverse says it can never work (bad link, unsafe
SVG) or after MAX_ATTEMPTS. To try a row again, set bonkverse_sync_failed =
FALSE and bonkverse_sync_attempts = 0.
"""

import os

from db.pool import get_pool
from services import bonkverse_client

MAX_ATTEMPTS = 8
# Bonkverse allows 10 skin imports a minute per IP; stay under it with room for new submissions.
BATCH_SIZE = int(os.getenv("BONKVERSE_SYNC_BATCH", "5"))

SCHEMA = """
ALTER TABLE skins
    ADD COLUMN IF NOT EXISTS bonkverse_skin_id INTEGER,
    ADD COLUMN IF NOT EXISTS bonkverse_sync_attempts INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS bonkverse_sync_error TEXT,
    ADD COLUMN IF NOT EXISTS bonkverse_sync_failed BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS bonkverse_next_attempt_at TIMESTAMP,
    ADD COLUMN IF NOT EXISTS bonkverse_synced_at TIMESTAMP;
"""

_warned_unconfigured = False


async def ensure_schema():
    """Add the sync columns if they aren't there yet. Safe to run on every start."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA)


async def sync_skin(skin_id: int) -> bonkverse_client.SubmitResult | None:
    """Push one skin to Bonkverse and record the outcome. Returns None if sync is off."""
    if not _configured():
        return None

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, skin_name, skin_url, creator, submitter_id, bonkverse_skin_id"
            " FROM skins WHERE id=$1",
            skin_id,
        )
    if not row or row["bonkverse_skin_id"] is not None:
        return None

    result = await bonkverse_client.submit_skin(
        link=row["skin_url"],
        name=row["skin_name"],
        creator=row["creator"],
        discord_user_id=row["submitter_id"],
        bonx_skin_id=row["id"],
    )
    await _record(skin_id, result)
    return result


async def sync_pending(batch_size: int = BATCH_SIZE):
    """Push up to `batch_size` unsynced skins that are due, oldest first."""
    if not _configured():
        return

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id FROM skins
            WHERE bonkverse_skin_id IS NULL
              AND NOT bonkverse_sync_failed
              AND (bonkverse_next_attempt_at IS NULL OR bonkverse_next_attempt_at <= NOW())
            ORDER BY id
            LIMIT $1
            """,
            batch_size,
        )

    for row in rows:
        result = await sync_skin(row["id"])
        if result and result.unauthorized:
            print("❌ [BonkverseSync] Bonkverse rejected our token; check BONXBOT_API_TOKEN")
            return
        if result and result.service_down:
            print("⏸️ [BonkverseSync] Bonkverse unavailable; pausing until next tick")
            return

    if rows:
        print(f"🔁 [BonkverseSync] processed {len(rows)} skin(s)")


async def _record(skin_id: int, result: bonkverse_client.SubmitResult):
    pool = await get_pool()
    async with pool.acquire() as conn:
        if result.ok:
            await conn.execute(
                """
                UPDATE skins
                SET bonkverse_skin_id = $1,
                    bonkverse_synced_at = NOW(),
                    bonkverse_sync_error = NULL,
                    bonkverse_next_attempt_at = NULL
                WHERE id = $2
                """,
                result.bonkverse_skin_id,
                skin_id,
            )
            print(f"✅ [BonkverseSync] skin {skin_id} -> Bonkverse #{result.bonkverse_skin_id}")
        elif result.unauthorized:
            # A config problem, not this skin's fault: don't use up its attempts.
            await conn.execute(
                "UPDATE skins SET bonkverse_sync_error = $1 WHERE id = $2", result.error, skin_id
            )
        else:
            await conn.execute(
                """
                UPDATE skins
                SET bonkverse_sync_attempts = bonkverse_sync_attempts + 1,
                    bonkverse_sync_error = $1,
                    bonkverse_sync_failed = (NOT $2) OR bonkverse_sync_attempts + 1 >= $3,
                    bonkverse_next_attempt_at =
                        NOW() + make_interval(mins => power(2, bonkverse_sync_attempts)::int)
                WHERE id = $4
                """,
                result.error,
                result.retryable,
                MAX_ATTEMPTS,
                skin_id,
            )
            print(f"⚠️ [BonkverseSync] skin {skin_id} not synced: {result.error}")


def _configured() -> bool:
    global _warned_unconfigured
    if bonkverse_client.is_configured():
        return True
    if not _warned_unconfigured:
        print("ℹ️ [BonkverseSync] BONXBOT_API_TOKEN not set; Bonkverse sync is off")
        _warned_unconfigured = True
    return False
