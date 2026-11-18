"""#skin-feed: every new bonkverse.io skin, posted in a Discord channel.

Owns: polling bonkverse.io for new skins, posting them (up to 10 per
message), remembering how far it got, and the ⭐ Collect buttons under each
post, which keep working after a restart (a DynamicItem).
Does not own: the skins (bonkverse.io) or collecting (collect.py).

Set SKIN_FEED_CHANNEL_ID to the channel's id to turn it on. A brand-new feed
starts from the newest skin at that moment; it never posts the backlog.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re

import discord

from db.pool import get_pool
from services import bonkverse_client as site
from utils.ui import INFO, card, send

from . import collect

logger = logging.getLogger(__name__)

POLL_SECONDS = 120
PER_TICK = 10  # Discord allows 10 embeds per message
STATE_KEY = "skin_feed_after"

SCHEMA = "CREATE TABLE IF NOT EXISTS bot_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)"

SOURCES = {
    "website": "Uploaded on bonkverse.io",
    "editor": "Made in the Bonkverse skin editor",
    "discord": "Added from Discord",
}


def channel_id() -> int | None:
    raw = os.getenv("SKIN_FEED_CHANNEL_ID", "").strip()
    return int(raw) if raw.isdigit() else None


def feed_card(skin: dict) -> discord.Embed:
    embed = card(skin["name"], f"by **{skin.get('creator') or 'Unknown'}**", INFO)
    embed.url = skin["url"]
    if skin.get("image_url"):
        embed.set_image(url=skin["image_url"])
    label = SOURCES.get(skin.get("source") or "")
    embed.set_footer(text=f"Skin #{skin['id']}" + (f" · {label}" if label else ""))
    return embed


class FeedCollect(discord.ui.DynamicItem[discord.ui.Button], template=r"feed:collect:(?P<id>\d+)"):
    """⭐ under a feed post: adds that skin to the presser's collection.
    Rebuilt from its custom_id, so old posts keep working after a restart."""

    def __init__(self, skin_id: int, label: str = "⭐ Collect"):
        super().__init__(
            discord.ui.Button(
                label=label[:80], style=discord.ButtonStyle.secondary,
                custom_id=f"feed:collect:{skin_id}",
            )
        )
        self.skin_id = skin_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        _, embed = await collect.add(interaction.user.id, collect.SkinRef(skin_id=self.skin_id))
        await send(interaction, embed, private=True)


def feed_view(skins: list[dict]) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    one = len(skins) == 1
    for skin in skins[:25]:
        view.add_item(FeedCollect(skin["id"], "⭐ Collect" if one else f"⭐ {skin['name']}"))
    return view


# --- state -------------------------------------------------------------------------


async def ensure_schema() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA)


async def load_after() -> int | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        value = await conn.fetchval("SELECT value FROM bot_state WHERE key = $1", STATE_KEY)
    return int(value) if value is not None else None


async def save_after(skin_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO bot_state (key, value) VALUES ($1, $2) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            STATE_KEY, str(skin_id),
        )


# --- polling -----------------------------------------------------------------------


async def tick(channel) -> int:
    """Post whatever is new; returns how many skins were posted."""
    after = await load_after()
    if after is None:
        reply = await site.latest_skins()
        if reply.ok:
            await save_after(reply.data["newest_id"])  # start from now
        return 0
    reply = await site.latest_skins(after, PER_TICK)
    if not reply.ok or not reply.data["skins"]:
        return 0
    skins = reply.data["skins"]
    await channel.send(
        embeds=[feed_card(s) for s in skins],
        view=feed_view(skins),
        allowed_mentions=discord.AllowedMentions.none(),
    )
    await save_after(max(s["id"] for s in skins))
    return len(skins)


_running = False


async def start_skin_feed(bot: discord.Client) -> None:
    """Runs for the life of the bot. on_ready fires again after a reconnect,
    so a second call returns at once instead of starting a second feed."""
    global _running
    if _running:
        return
    _running = True
    target = channel_id()
    if target is None:
        logger.info("skin feed off: SKIN_FEED_CHANNEL_ID isn't set")
        return
    await bot.wait_until_ready()
    await ensure_schema()
    while not bot.is_closed():
        try:
            channel = bot.get_channel(target) or await bot.fetch_channel(target)
            await tick(channel)
        except Exception:
            logger.exception("skin feed tick failed")
        await asyncio.sleep(POLL_SECONDS)
