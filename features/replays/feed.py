"""The replay feed: new bonk.io replays of linked members, posted as videos.

Owns: polling the replay video service for new replays with any linked
bonk.io account in them (POST /replays/with), posting each as a video with
who played, remembering where it got to, and a cap so a busy evening doesn't
flood the channel. Linked accounts are read fresh every poll, so a new /link
is picked up on its own.
Does not own: linking (features/accounts), rendering or the archive
(services/replay_renderer.py).

Set REPLAY_FEED_CHANNEL_ID to turn it on. A new feed starts from now. Optional:
REPLAY_FEED_MAX_PER_HOUR (default 10). Replays over the cap are skipped, not
saved up: the feed is for what's happening now.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time

import discord

from db.pool import get_pool
from services import replay_renderer as renderer

from .commands import video_post

logger = logging.getLogger(__name__)

POLL_SECONDS = 180
PER_POLL = 3
STATE_KEY = "replay_feed_after"
SCHEMA = "CREATE TABLE IF NOT EXISTS bot_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)"


def channel_id() -> int | None:
    raw = os.getenv("REPLAY_FEED_CHANNEL_ID", "").strip()
    return int(raw) if raw.isdigit() else None


def max_per_hour() -> int:
    raw = os.getenv("REPLAY_FEED_MAX_PER_HOUR", "").strip()
    return int(raw) if raw.isdigit() else 10


class HourlyCap:
    """At most `limit` posts in any rolling hour."""

    def __init__(self, limit: int, clock=time.monotonic):
        self.limit, self.clock, self.sent = limit, clock, []

    def allow(self) -> bool:
        now = self.clock()
        self.sent = [t for t in self.sent if now - t < 3600]
        if len(self.sent) >= self.limit:
            return False
        self.sent.append(now)
        return True


# --- linked accounts --------------------------------------------------------------


async def linked_accounts() -> dict[str, int]:
    """Every linked bonk.io name (lowercase) -> its Discord member."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT bonk_username, discord_user_id FROM discord_bonk_accounts")
    return {r["bonk_username"].casefold(): int(r["discord_user_id"]) for r in rows}


def who_played(replay: dict, linked: dict[str, int]) -> str:
    """"Played by @member (Name), …" for the linked players in a replay."""
    seen, parts = set(), []
    for p in replay.get("players") or []:
        name = p.get("username") or ""
        member = linked.get(name.casefold())
        if member and not p.get("guest") and name.casefold() not in seen:
            seen.add(name.casefold())
            parts.append(f"<@{member}> ({name})")
    return "🎮 Played by " + ", ".join(parts) if parts else ""


# --- state -------------------------------------------------------------------------


async def ensure_schema() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA)


async def load_after() -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        raw = await conn.fetchval("SELECT value FROM bot_state WHERE key = $1", STATE_KEY)
    return json.loads(raw) if raw else None


async def save_after(cursor: dict) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO bot_state (key, value) VALUES ($1, $2) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            STATE_KEY, json.dumps({"cycle": cursor["cycle"], "id": str(cursor["id"])}),
        )


# --- polling -------------------------------------------------------------------------


async def tick(channel, cap: HourlyCap) -> int:
    """Post what's new; returns how many replays were posted."""
    linked = await linked_accounts()
    after = await load_after()
    reply = await renderer.replays_with(sorted(linked), after, PER_POLL)
    if not reply.ok:
        logger.warning("replay feed: lookup failed: %s", reply.error)
        return 0
    if after is None:  # a new feed: start from now
        await save_after(reply.scanned_to)
        return 0

    posted = 0
    for replay in reply.replays:
        if not cap.allow():
            logger.info("replay feed: hourly cap reached; skipping %s:%s", replay.get("cycle"), replay.get("id"))
            continue
        post = await video_post(str(replay["id"]), replay.get("cycle"))
        if isinstance(post, str):
            logger.warning("replay feed: %s:%s not posted: %s", replay.get("cycle"), replay.get("id"), post)
            continue
        embed, file = post
        await channel.send(
            content=who_played(replay, linked) or None,
            embed=embed,
            file=file,
            allowed_mentions=discord.AllowedMentions.none(),  # names people, never pings
        )
        posted += 1
    if reply.scanned_to:
        await save_after(reply.scanned_to)
    return posted


_running = False


async def start_replay_feed(bot: discord.Client) -> None:
    """Runs for the life of the bot; a second call (on_ready after a
    reconnect) returns at once."""
    global _running
    if _running:
        return
    _running = True
    target = channel_id()
    if target is None or not renderer.is_configured():
        logger.info("replay feed off: REPLAY_FEED_CHANNEL_ID or the video service isn't set")
        return
    await bot.wait_until_ready()
    await ensure_schema()
    cap = HourlyCap(max_per_hour())
    while not bot.is_closed():
        try:
            channel = bot.get_channel(target) or await bot.fetch_channel(target)
            await tick(channel, cap)
        except Exception:
            logger.exception("replay feed tick failed")
        await asyncio.sleep(POLL_SECONDS)
