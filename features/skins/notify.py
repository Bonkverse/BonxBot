"""DMs to the person who submitted a skin: when it's live on bonkverse.io,
when it couldn't be added, and when its tier vote closes.

Owns: who gets a DM and what it says, and the opt-out (/skin_dms).
Does not own: when those things happen (bonkverse_sync.py, poller.py).

Only recent submissions are announced, so retrying an old backlog never
floods anyone's DMs. A closed DM inbox is skipped quietly.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from db.pool import get_pool
from services import bonkverse_client as site
from utils.ui import MUTED, SUCCESS, card, send

from . import cards

logger = logging.getLogger(__name__)

RECENT = timedelta(days=3)  # older submissions are never announced

SCHEMA = "ALTER TABLE users ADD COLUMN IF NOT EXISTS skin_dms BOOLEAN NOT NULL DEFAULT TRUE"

# Why a skin couldn't go on bonkverse.io, from Bonkverse's error code
# ("422: invalid_skin_code" -> invalid_skin_code).
FAILURE_REASONS = {
    "invalid_link": "the link isn't a BonkLeagues skin link",
    "link_too_long": "the link is too long",
    "invalid_skin_code": "its skin code couldn't be read",
    "unsafe_svg": "its image had content Bonkverse doesn't allow",
    "svg_too_large": "its image is too large",
    "render_failed": "Bonkverse couldn't draw it",
    "fetch_failed": "BonkLeagues didn't send the skin",
}

_bot: discord.Client | None = None


def attach(bot: discord.Client) -> None:
    """Called once on startup so background jobs can send DMs."""
    global _bot
    _bot = bot


async def ensure_schema() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA)


def is_recent(ends_at: datetime | None, now: datetime | None = None) -> bool:
    """Voting lasts 24h from submission, so ends_at tells us when it was sent."""
    if ends_at is None:
        return False
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    return ends_at - timedelta(hours=24) >= now - RECENT


def failure_reason(error: str | None) -> str:
    code = (error or "").split(":", 1)[-1].strip()
    return FAILURE_REASONS.get(code, "Bonkverse turned it down")


# --- messages ------------------------------------------------------------------------


def live_card(name: str, url: str | None, image_url: str | None) -> discord.Embed:
    embed = card("✅ Your skin is on bonkverse.io", f"**{name}** is live: {url}" if url else f"**{name}** is live.", SUCCESS)
    if image_url:
        embed.set_image(url=image_url)
    embed.set_footer(text="Turn these DMs off with /skin_dms")
    return embed


def failed_card(name: str, error: str | None) -> discord.Embed:
    embed = card(
        "Couldn't add your skin to bonkverse.io",
        f"**{name}** wasn't added: {failure_reason(error)}. It still counts in the tier vote. "
        "If the skin looks fine on BonkLeagues, let a staff member know.",
        MUTED,
    )
    embed.set_footer(text="Turn these DMs off with /skin_dms")
    return embed


def result_card(name: str, tier: str, average: float, votes: int, jump_url: str | None) -> discord.Embed:
    text = cards.result_text(tier, average, votes)
    embed = card(f"🏆 Voting closed on {name}", text + (f"\n{jump_url}" if jump_url else ""),
                 cards.TIER_COLORS.get(tier))
    embed.set_footer(text="All your skins: /submissions · Turn these DMs off with /skin_dms")
    return embed


# --- sending -------------------------------------------------------------------------


async def _recipient(skin_id: int):
    """(submitter id, skin row) if this skin's submitter should hear about it."""
    if _bot is None:  # not running as the bot (tests, scripts)
        return None
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT s.submitter_id, s.skin_name, s.ends_at, COALESCE(u.skin_dms, TRUE) AS wants
            FROM skins s LEFT JOIN users u ON u.discord_id = s.submitter_id
            WHERE s.id = $1
            """,
            skin_id,
        )
    if row is None or not row["submitter_id"] or not row["wants"] or not is_recent(row["ends_at"]):
        return None
    return row


async def _dm(user_id: int, embed: discord.Embed) -> bool:
    if _bot is None:
        return False
    try:
        user = _bot.get_user(user_id) or await _bot.fetch_user(user_id)
        await user.send(embed=embed)
        return True
    except discord.HTTPException:  # DMs closed, user gone
        return False


async def skin_live(skin_id: int, bonkverse_id: int) -> None:
    row = await _recipient(skin_id)
    if row is None:
        return
    reply = await site.get_skin(bonkverse_id)
    skin = reply.data["skin"] if reply.ok else {}
    url = skin.get("url") or f"{site.BONKVERSE_API_URL}/skins/detail/{bonkverse_id}"
    await _dm(row["submitter_id"], live_card(row["skin_name"], url, skin.get("image_url")))


async def skin_failed(skin_id: int, error: str | None) -> None:
    row = await _recipient(skin_id)
    if row is not None:
        await _dm(row["submitter_id"], failed_card(row["skin_name"], error))


async def vote_closed(skin_id: int, result: dict, jump_url: str | None) -> None:
    row = await _recipient(skin_id)
    if row is not None:
        await _dm(
            row["submitter_id"],
            result_card(row["skin_name"], result["tier"], result["avg"], result["total"], jump_url),
        )


async def safely(coro) -> None:
    """Run a notification without letting a failure stop the caller."""
    try:
        await coro
    except Exception:
        logger.exception("skin DM failed")


# --- opt-out -------------------------------------------------------------------------


class SkinDms(commands.Cog):
    @app_commands.command(name="skin_dms", description="Turn DMs about your skin submissions on or off")
    @app_commands.describe(setting="On: DMs when your skin is live on bonkverse.io and when its vote closes")
    @app_commands.choices(setting=[
        app_commands.Choice(name="on", value="on"),
        app_commands.Choice(name="off", value="off"),
    ])
    async def skin_dms(self, interaction: discord.Interaction, setting: app_commands.Choice[str]):
        on = setting.value == "on"
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users (discord_id, skin_dms) VALUES ($1, $2)
                ON CONFLICT (discord_id) DO UPDATE SET skin_dms = EXCLUDED.skin_dms
                """,
                interaction.user.id, on,
            )
        text = (
            "You'll get a DM when your skin is live on bonkverse.io and when its tier vote closes."
            if on else "No more DMs about your skin submissions. Turn them back on with `/skin_dms on`."
        )
        await send(interaction, card("Skin DMs " + ("on" if on else "off"), text, SUCCESS), private=True)


async def setup(bot):
    await bot.add_cog(SkinDms())
