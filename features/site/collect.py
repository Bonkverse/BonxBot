"""Adding skins to your bonkverse.io collection from Discord.

Owns: every way in, and what they reply:
  - `/collect <link>`: a BonkLeagues link, a bonkverse.io skin link, or #number
  - Apps → "Add to my collection" on any message with skin links
  - @BonxBot in a message with links, or in a reply to one
  - ⭐ Collect under /skin cards (features/site/commands.py)
Skins bonkverse.io doesn't have yet are uploaded first (the site does that).
The collection belongs to your main linked bonk.io account.
Does not own: the upload or the collection itself (bonkverse.io).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from features.accounts import service as accounts
from services import bonkverse_client as site
from utils.ui import MUTED, SUCCESS, card, problem, refuse, send

MAX_PER_MESSAGE = 5

BONKLEAGUES_LINK = re.compile(r"https?://(?:www\.)?bonkleagues\.io/[^\s<>()\[\]]+", re.IGNORECASE)
BONKVERSE_SKIN = re.compile(r"https?://(?:www\.)?bonkverse\.io/skins/detail/(\d+)", re.IGNORECASE)
SKIN_NUMBER = re.compile(r"^#?(\d{1,9})$")

COLLECT_ERRORS = {
    "invalid_link": "That isn't a BonkLeagues skin link.",
    "link_too_long": "That link is too long to be a skin link.",
    "fetch_failed": "BonkLeagues didn't send that skin. Check the link opens, then try again.",
    "render_failed": "bonkverse.io couldn't draw that skin.",
    "invalid_skin_code": "That skin's code couldn't be read.",
    "unsafe_svg": "That skin's image has content bonkverse.io doesn't allow.",
    "svg_too_large": "That skin's image is too large for bonkverse.io.",
    "skin_not_found": "There's no skin with that number on bonkverse.io.",
    "missing_skin": "Give a BonkLeagues link, a bonkverse.io skin link, or a skin #number.",
    "unauthorized": "BonxBot isn't connected to bonkverse.io right now. Let a staff member know.",
}
SITE_DOWN = "bonkverse.io isn't answering right now. Try again in a minute."
NOT_LINKED = (
    "Your collection lives on your bonk.io account, so link it first: run `/link` with your "
    "bonk.io username. It takes about a minute."
)


@dataclass(frozen=True)
class SkinRef:
    link: str | None = None
    skin_id: int | None = None

    @property
    def key(self):
        return ("id", self.skin_id) if self.skin_id is not None else ("link", self.link.rstrip("/").lower())


def find_skins(*texts: str | None) -> list[SkinRef]:
    """Skin links in some text, in order, without repeats, at most MAX_PER_MESSAGE."""
    found: list[SkinRef] = []
    seen = set()
    for text in texts:
        if not text:
            continue
        hits = [(m.start(), SkinRef(skin_id=int(m.group(1)))) for m in BONKVERSE_SKIN.finditer(text)]
        hits += [(m.start(), SkinRef(link=m.group(0).rstrip(".,!?)>"))) for m in BONKLEAGUES_LINK.finditer(text)]
        for _, ref in sorted(hits, key=lambda hit: hit[0]):
            if ref.key not in seen:
                seen.add(ref.key)
                found.append(ref)
    return found[:MAX_PER_MESSAGE]


def parse_one(text: str) -> SkinRef | None:
    """What /collect was given: a link of either kind, or a #number."""
    number = SKIN_NUMBER.match(text.strip())
    if number:
        return SkinRef(skin_id=int(number.group(1)))
    refs = find_skins(text)
    return refs[0] if refs else None


def message_texts(message: discord.Message) -> list[str]:
    """The message's text and its link previews, where skin links can hide."""
    texts = [message.content]
    for embed in message.embeds:
        texts += [embed.url or "", embed.description or ""]
    return texts


# --- adding ----------------------------------------------------------------------


def collected_card(data: dict, bonk_name: str) -> discord.Embed:
    skin = data["skin"]
    if data.get("already_collected"):
        embed = card("Already in your collection", f"**{skin['name']}** by **{skin.get('creator') or 'Unknown'}**", MUTED)
    else:
        text = f"**{skin['name']}** by **{skin.get('creator') or 'Unknown'}**"
        if data.get("uploaded"):
            text += "\nIt wasn't on bonkverse.io yet, so it's been uploaded too."
        embed = card("⭐ Added to your collection", text, SUCCESS)
    embed.url = skin["url"]
    embed.set_thumbnail(url=skin["image_url"])
    links = f"[Open on bonkverse.io]({skin['url']})"
    if data.get("collection_url"):
        links += f" · [Your collection]({data['collection_url']})"
    embed.add_field(name="Links", value=links, inline=False)
    size = data.get("collection_size")
    footer = f"bonk.io: {bonk_name}"
    if size:
        footer = f"{size:,} skin{'s' if size != 1 else ''} in your collection · " + footer
    embed.set_footer(text=footer)
    return embed


async def add(discord_user_id: int, ref: SkinRef) -> tuple[bool, discord.Embed]:
    """(worked, reply) for adding one skin to this member's collection."""
    bonk_name = await accounts.main_account(discord_user_id)
    if not bonk_name:
        return False, problem(NOT_LINKED, "Link your bonk.io account first")
    reply = await site.collect(bonk_name, discord_user_id, link=ref.link, skin_id=ref.skin_id)
    if not reply.ok:
        return False, problem(COLLECT_ERRORS.get(reply.error or "", SITE_DOWN), "Couldn't add that skin")
    return True, collected_card(reply.data, bonk_name)


async def add_many(discord_user_id: int, refs: list[SkinRef]) -> list[discord.Embed]:
    replies = []
    for ref in refs:
        worked, embed = await add(discord_user_id, ref)
        replies.append(embed)
        if not worked and embed.title == "Link your bonk.io account first":
            break  # the same for every link: say it once
    return replies


# --- @BonxBot ----------------------------------------------------------------------

CANT_READ = (
    "I can't read that message's links. Paste the link with your mention, or use "
    "**Apps → Add to my collection** on the message."
)


UPLOAD_WORD = re.compile(r"\bupload\b", re.IGNORECASE)


def wants_upload(message: discord.Message) -> bool:
    """"@BonxBot upload …" uploads only; any other mention with links collects."""
    return bool(UPLOAD_WORD.search(message.content or ""))


async def handle_mention(message: discord.Message, bot_user: discord.abc.User) -> bool:
    """Collect (or, with the word "upload", just upload) the skins in a message
    that mentions the bot, or in the message it replies to. Returns False when
    there's nothing skin-related to do."""
    refs = find_skins(*message_texts(message))
    if not refs and message.reference is not None:
        replied = message.reference.resolved
        if not hasattr(replied, "content"):  # not cached, or a DeletedReferencedMessage
            try:
                replied = await message.channel.fetch_message(message.reference.message_id)
            except discord.HTTPException:
                replied = None
        if replied is not None:
            refs = find_skins(*message_texts(replied))
            if not refs and not replied.content and replied.author != bot_user:
                await message.reply(embed=problem(CANT_READ, "Can't see that message"), mention_author=False)
                return True
    if not refs:
        return False
    async with message.channel.typing():
        if wants_upload(message):
            from .upload import upload_many  # upload.py builds on this module

            replies = await upload_many(message.author.id, refs)
        else:
            replies = await add_many(message.author.id, refs)
    await message.reply(embeds=replies[:10], mention_author=False)
    return True


# --- commands ------------------------------------------------------------------------


@app_commands.context_menu(name="Add to my collection")
async def collect_from_message(interaction: discord.Interaction, message: discord.Message):
    refs = find_skins(*message_texts(message))
    if not refs:
        await refuse(interaction, "That message has no BonkLeagues or bonkverse.io skin links.", "No skin links")
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    replies = await add_many(interaction.user.id, refs)
    await interaction.followup.send(embeds=replies[:10], ephemeral=True)


class Collect(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.tree.add_command(collect_from_message)

    async def cog_unload(self):
        self.bot.tree.remove_command(collect_from_message.name, type=collect_from_message.type)

    @app_commands.command(name="collect", description="Add a skin to your bonkverse.io collection (uploads it if needed)")
    @app_commands.describe(skin="A BonkLeagues link, a bonkverse.io skin link, or a skin #number")
    async def collect(self, interaction: discord.Interaction, skin: app_commands.Range[str, 1, 500]):
        ref = parse_one(skin)
        if ref is None:
            await refuse(interaction, COLLECT_ERRORS["missing_skin"], "Couldn't read that")
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        _, embed = await add(interaction.user.id, ref)
        await send(interaction, embed, private=True)


def message_content_enabled() -> bool:
    """Reading replied-to messages needs Discord's Message Content intent: turn
    it on in the Developer Portal (Bot → Privileged Gateway Intents) first,
    then set BONXBOT_MESSAGE_CONTENT=1. Mentions and Apps work without it."""
    return os.getenv("BONXBOT_MESSAGE_CONTENT", "").strip() in {"1", "true", "yes"}


async def setup(bot):
    await bot.add_cog(Collect(bot))
