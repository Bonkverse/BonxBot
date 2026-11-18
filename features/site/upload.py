"""Putting a skin on bonkverse.io from Discord, with no tier vote and without
adding it to anyone's collection.

Owns: `/upload <link>`, Apps → "Upload to Bonkverse", and "@BonxBot upload"
(the word "upload" in a mention; collect.handle_mention routes it here).
Credit follows the same rule as /submit_skin and the website upload page
(features/skins/creator.py): BonkLeagues lists no author or one of your
linked accounts → you; it lists someone else → you're asked "Who made this
skin?" first, and "Made by <author>" credits them with you recorded as the
uploader. With alts you pick which account gets the credit.
Does not own: the upload itself (bonkverse.io, POST /api/bot/skins/), tier
votes (/submit_skin) or collections (collect.py).
"""

from __future__ import annotations

from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from features.accounts import service as accounts
from features.skins import cards as skin_cards
from features.skins.creator import BONKLEAGUES, SUBMITTER, creator_for
from services import bonkverse_client as site
from services.skin_fetcher import fetch_skin_data
from utils.ui import MUTED, PROBLEM, SUCCESS, OwnedView, card, problem, refuse, send

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
NOT_LINKED_TITLE = "Link your bonk.io account first"


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def error_code(result: site.SubmitResult) -> str:
    """"422: invalid_link" -> "invalid_link"."""
    return (result.error or "").split(":", 1)[-1].strip()


@dataclass
class Pending:
    """A skin fetched from BonkLeagues, waiting to be uploaded."""

    discord_user_id: int
    link: str
    data: dict  # what BonkLeagues returned: name, author, skinCode
    my_accounts: list[str]  # linked bonk.io names, main first

    @property
    def name(self) -> str:
        return self.data.get("name") or "Unnamed"

    @property
    def author(self) -> str | None:
        return self.data.get("author")

    def decide(self, chosen: str | None = None, made_it: bool | None = None):
        """(creator, creator_source), or None while the uploader must be asked."""
        return creator_for(self.my_accounts, chosen or self.my_accounts[0], self.author, made_it)


# --- the steps ---------------------------------------------------------------------


async def prepare(discord_user_id: int, ref: SkinRef) -> Pending | discord.Embed:
    """Everything needed to upload, or the problem embed explaining why not."""
    if ref.link is None:
        return problem(NEEDS_BL_LINK, "Not a BonkLeagues link")
    rows = await accounts.accounts_of(discord_user_id)
    if not rows:
        return problem(NOT_LINKED.replace("Your collection lives", "Uploads are recorded"), NOT_LINKED_TITLE)
    data = await fetch_skin_data(ref.link)
    if not data or not data.get("skinCode"):
        return problem(UPLOAD_ERRORS["fetch_failed"], "Couldn't upload that skin")
    return Pending(discord_user_id, ref.link, data, [r["bonk_username"] for r in rows])


def uploaded_card(skin: dict, *, created: bool, credited_to_you: bool) -> discord.Embed:
    creator = skin.get("creator") or "Unknown"
    if created:
        who = " (you)" if credited_to_you else ""
        embed = card("⬆️ Uploaded to bonkverse.io", f"**{skin['name']}** by **{creator}**{who}", SUCCESS)
    else:
        embed = card("Already on bonkverse.io", f"**{skin['name']}** by **{creator}**", MUTED)
    embed.url = skin["url"]
    if skin.get("image_url"):
        embed.set_thumbnail(url=skin["image_url"])
    embed.add_field(
        name="Links",
        value=f"[Open on bonkverse.io]({skin['url']}) · keep it with `/collect #{skin['id']}`",
        inline=False,
    )
    embed.set_footer(text="Uploaded only: no tier vote. Want one? Use /submit_skin.")
    return embed


async def finish(pending: Pending, creator: str, creator_source: str) -> tuple[bool, discord.Embed]:
    """Send the skin to bonkverse.io, credited as decided."""
    result = await site.submit_skin(
        link=pending.link,
        name=pending.data.get("name"),
        creator=creator,
        skin_code=pending.data.get("skinCode"),
        discord_user_id=pending.discord_user_id,
        bonx_skin_id=None,
        creator_source=creator_source,
    )
    if not result.ok:
        return False, problem(UPLOAD_ERRORS.get(error_code(result), SITE_DOWN), "Couldn't upload that skin")
    reply = await site.get_skin(result.bonkverse_skin_id)
    skin = reply.data["skin"] if reply.ok else {
        "id": result.bonkverse_skin_id, "name": pending.name, "creator": creator,
        "url": f"{site.BONKVERSE_API_URL}/skins/detail/{result.bonkverse_skin_id}", "image_url": None,
    }
    return True, uploaded_card(skin, created=result.created, credited_to_you=creator_source == SUBMITTER)


# --- "Who made this skin?" ------------------------------------------------------------


class CreatorCheck(OwnedView):
    """Asked when BonkLeagues credits someone other than the uploader."""

    not_yours = "This is someone else's upload. Start your own with `/upload`."

    def __init__(self, pending: Pending):
        super().__init__(pending.discord_user_id, timeout=300)
        self.pending = pending
        self.chosen = pending.my_accounts[0]
        self.done = False
        self._button("✅ I made this", discord.ButtonStyle.success, self._mine)
        self._button(_short(f"🎨 Made by {pending.author}", 80), discord.ButtonStyle.secondary, self._theirs)
        self._button("✖️ Cancel", discord.ButtonStyle.danger, self._cancel)
        if len(pending.my_accounts) > 1:
            picker = discord.ui.Select(
                placeholder="If you made it: credit which of your accounts?",
                options=[
                    discord.SelectOption(label=_short(n, 100), value=n, default=n == self.chosen)
                    for n in pending.my_accounts[:25]
                ],
                row=1,
            )
            picker.callback = self._pick
            self.picker = picker
            self.add_item(picker)

    def embed(self) -> discord.Embed:
        embed = skin_cards.who_made_it_card(self.pending.name, self.pending.author, self.pending.link)
        embed.set_footer(text="Uploading only: no tier vote.")
        return embed

    def _button(self, label, style, callback):
        button = discord.ui.Button(label=label, style=style)
        button.callback = callback
        self.add_item(button)

    async def _pick(self, interaction: discord.Interaction):
        self.chosen = self.picker.values[0]
        for option in self.picker.options:
            option.default = option.value == self.chosen
        await interaction.response.edit_message(view=self)

    async def _answer(self, interaction: discord.Interaction, made_it: bool):
        self.done = True
        self.stop()
        self.disable_all()
        await interaction.response.edit_message(view=self)  # no double uploads
        creator, source = self.pending.decide(self.chosen, made_it)
        _, embed = await finish(self.pending, creator, source)
        await interaction.edit_original_response(embed=embed, view=None)

    async def _mine(self, interaction):
        await self._answer(interaction, True)

    async def _theirs(self, interaction):
        await self._answer(interaction, False)

    async def _cancel(self, interaction):
        self.done = True
        self.stop()
        await interaction.response.edit_message(
            embed=card("Upload cancelled", f"**{self.pending.name}** wasn't uploaded.", MUTED), view=None
        )

    async def on_timeout(self):
        if self.done or self.message is None:
            return
        try:
            await self.message.edit(
                embed=card("Upload timed out", f"**{self.pending.name}** wasn't uploaded.", MUTED), view=None
            )
        except discord.HTTPException:
            pass


async def start(discord_user_id: int, ref: SkinRef) -> tuple[discord.Embed, CreatorCheck | None]:
    """Upload right away, or return the question to ask first (embed + view)."""
    pending = await prepare(discord_user_id, ref)
    if isinstance(pending, discord.Embed):
        return pending, None
    decision = pending.decide()
    if decision is None:
        view = CreatorCheck(pending)
        return view.embed(), view
    _, embed = await finish(pending, *decision)
    return embed, None


async def upload(discord_user_id: int, ref: SkinRef) -> tuple[bool, discord.Embed]:
    """Upload one skin without asking anything (for callers that can't ask).
    A skin BonkLeagues credits to someone else is credited to them."""
    pending = await prepare(discord_user_id, ref)
    if isinstance(pending, discord.Embed):
        return False, pending
    return await finish(pending, *(pending.decide() or (pending.author, BONKLEAGUES)))


# --- the ways in ---------------------------------------------------------------------


async def upload_from_mention(message: discord.Message, refs: list[SkinRef]) -> None:
    """Reply to "@BonxBot upload …": uploads that need no question are
    answered together; each one that does gets its own question reply."""
    done = []
    for ref in refs:
        embed, view = await start(message.author.id, ref)
        if view is None:
            done.append(embed)
            if embed.title == NOT_LINKED_TITLE:
                break
            continue
        view.message = await message.reply(embed=embed, view=view, mention_author=False)
    if done:
        await message.reply(embeds=done[:10], mention_author=False)


@app_commands.context_menu(name="Upload to Bonkverse")
async def upload_from_message(interaction: discord.Interaction, message: discord.Message):
    refs = [r for r in find_skins(*message_texts(message)) if r.link]
    if not refs:
        await refuse(interaction, "That message has no BonkLeagues skin links.", "No skin links")
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    for ref in refs:
        embed, view = await start(interaction.user.id, ref)
        if view is None:
            await interaction.followup.send(embed=embed, ephemeral=True)
            if embed.title == NOT_LINKED_TITLE:
                return
        else:
            view.message = await interaction.followup.send(embed=embed, view=view, ephemeral=True, wait=True)


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
        embed, view = await start(interaction.user.id, refs[0])
        if view is not None:  # the question is public, like /submit_skin's preview
            await send(interaction, embed, view=view)
            await view.attach(interaction)
            return
        await send(interaction, embed, private=embed.color == PROBLEM)


async def setup(bot):
    await bot.add_cog(Upload(bot))
