"""Putting a skin on bonkverse.io from Discord, with no tier vote and without
adding it to anyone's collection.

Owns: `/upload <link>`, Apps → "Upload to Bonkverse", and "@BonxBot upload"
(the word "upload" in a mention; collect.handle_mention routes it here), and
how a skin uploaded this way is credited:
  - BonkLeagues lists one of your linked accounts as the author → you
  - BonkLeagues lists someone else → them (you're recorded as the uploader)
  - BonkLeagues lists no author → left unknown, for its maker to claim later
Does not own: the upload itself (bonkverse.io, POST /api/bot/skins/), tier
votes (/submit_skin) or collections (collect.py).
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from features.accounts import service as accounts
from features.skins.creator import BONKLEAGUES, SUBMITTER, author_is_mine
from services import bonkverse_client as site
from services.skin_fetcher import fetch_skin_data
from utils.ui import MUTED, SUCCESS, card, problem, refuse, send

from .collect import NOT_LINKED, SITE_DOWN, SkinRef, find_skins, message_texts

UPLOAD_ERRORS = {
    "invalid_link": "That isn't a BonkLeagues skin link.",
    "link_too_long": "That link is too long to be a skin link.",
    "fetch_failed": "BonkLeagues didn't send that skin. Check the link opens, then try again.",
    "render_failed": "bonkverse.io couldn't draw that skin.",
    "invalid_skin_code": "That skin's code couldn't be read.",
    "unsafe_svg": "That skin's image has content bonkverse.io doesn't allow.",
    "svg_too_large": "That skin's image is too large for bonkverse.io.",
    "unauthorized": "BonxBot isn't connected to bonkverse.io right now. Let a staff member know.",
}
NEEDS_BL_LINK = (
    "Uploading needs a **BonkLeagues** skin link (`https://bonkleagues.io/s/…`). "
    "bonkverse.io links are already on the site; to keep one, use `/collect`."
)


def credit(my_accounts: list[str], author: str | None) -> tuple[str | None, str | None]:
    """(creator, creator_source) for an upload, following the rules above."""
    if author and author_is_mine(my_accounts, author):
        mine = next(a for a in my_accounts if a.casefold() == author.casefold())
        return mine, SUBMITTER
    if author and author.strip():
        return author, BONKLEAGUES
    return None, None


def error_code(result: site.SubmitResult) -> str:
    """"422: invalid_link" -> "invalid_link"."""
    return (result.error or "").split(":", 1)[-1].strip()


def uploaded_card(skin: dict, *, created: bool, credited_to_you: bool) -> discord.Embed:
    creator = skin.get("creator") or "Unknown"
    if created:
        who = " (you)" if credited_to_you else ""
        embed = card("⬆️ Uploaded to bonkverse.io", f"**{skin['name']}** by **{creator}**{who}", SUCCESS)
        if not skin.get("creator") or creator == "UnknownSkinCreator":
            embed.description += "\nBonkLeagues doesn't say who made it, so it's listed without a creator for now."
    else:
        embed = card("Already on bonkverse.io", f"**{skin['name']}** by **{creator}**", MUTED)
    embed.url = skin["url"]
    embed.set_thumbnail(url=skin["image_url"])
    embed.add_field(
        name="Links",
        value=f"[Open on bonkverse.io]({skin['url']}) · keep it with `/collect #{skin['id']}`",
        inline=False,
    )
    embed.set_footer(text="Uploaded only: no tier vote. Want one? Use /submit_skin.")
    return embed


async def upload(discord_user_id: int, ref: SkinRef) -> tuple[bool, discord.Embed]:
    """(worked, reply) for uploading one skin."""
    if ref.link is None:
        return False, problem(NEEDS_BL_LINK, "Not a BonkLeagues link")
    rows = await accounts.accounts_of(discord_user_id)
    if not rows:
        return False, problem(NOT_LINKED.replace("Your collection lives", "Uploads are recorded"),
                              "Link your bonk.io account first")
    my_accounts = [r["bonk_username"] for r in rows]  # main first

    data = await fetch_skin_data(ref.link)
    if not data or not data.get("skinCode"):
        return False, problem(UPLOAD_ERRORS["fetch_failed"], "Couldn't upload that skin")
    creator, creator_source = credit(my_accounts, data.get("author"))

    result = await site.submit_skin(
        link=ref.link,
        name=data.get("name"),
        creator=creator,
        skin_code=data.get("skinCode"),
        discord_user_id=discord_user_id,
        bonx_skin_id=None,
        creator_source=creator_source,
    )
    if not result.ok:
        message = UPLOAD_ERRORS.get(error_code(result), SITE_DOWN)
        return False, problem(message, "Couldn't upload that skin")

    reply = await site.get_skin(result.bonkverse_skin_id)
    skin = reply.data["skin"] if reply.ok else {
        "id": result.bonkverse_skin_id, "name": data.get("name") or "Unnamed", "creator": creator,
        "url": f"{site.BONKVERSE_API_URL}/skins/detail/{result.bonkverse_skin_id}", "image_url": None,
    }
    embed = uploaded_card(skin, created=result.created, credited_to_you=creator_source == SUBMITTER)
    return True, embed


async def upload_many(discord_user_id: int, refs: list[SkinRef]) -> list[discord.Embed]:
    replies = []
    for ref in refs:
        worked, embed = await upload(discord_user_id, ref)
        replies.append(embed)
        if not worked and embed.title == "Link your bonk.io account first":
            break
    return replies


@app_commands.context_menu(name="Upload to Bonkverse")
async def upload_from_message(interaction: discord.Interaction, message: discord.Message):
    refs = [r for r in find_skins(*message_texts(message)) if r.link]
    if not refs:
        await refuse(interaction, "That message has no BonkLeagues skin links.", "No skin links")
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    replies = await upload_many(interaction.user.id, refs)
    await interaction.followup.send(embeds=replies[:10], ephemeral=True)


class Upload(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.tree.add_command(upload_from_message)

    async def cog_unload(self):
        self.bot.tree.remove_command(upload_from_message.name, type=upload_from_message.type)

    @app_commands.command(name="upload", description="Put a skin on bonkverse.io without a tier vote")
    @app_commands.describe(link="The skin's BonkLeagues link")
    async def upload(self, interaction: discord.Interaction, link: app_commands.Range[str, 1, 500]):
        refs = find_skins(link)
        if not refs:
            await refuse(interaction, UPLOAD_ERRORS["invalid_link"] + " Paste its `https://bonkleagues.io/s/…` link.")
            return
        await interaction.response.defer(thinking=True)
        worked, embed = await upload(interaction.user.id, refs[0])
        await send(interaction, embed, private=not worked)


async def setup(bot):
    await bot.add_cog(Upload(bot))
