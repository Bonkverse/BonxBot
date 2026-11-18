"""/submissions: browse skin submissions made through BonxBot.

Owns: the command and its browser. A list page shows stats plus ten skins at
a time; picking one opens its detail page (result, vote breakdown, links,
image), and ◀️ ▶️ step through skins one by one. Scope is your own
submissions, another member's, or every skin credited to a bonk.io name.
Does not own: the queries (submissions.py) or the layouts (cards.py).
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from db.pool import get_pool
from services import bonkverse_client as site
from utils.ui import OwnedView, refuse, send

from . import cards
from . import submissions as subs

VOTING_CHANNEL_ID = 1339872418772160512


def site_url(bonkverse_id: int | None) -> str | None:
    return f"{site.BONKVERSE_API_URL}/skins/detail/{bonkverse_id}" if bonkverse_id else None


class SubmissionsBrowser(OwnedView):
    def __init__(self, owner_id: int, *, scope, who: str, ids: list[int], summary,
                 guild_id: int | None, show_submitter: bool):
        super().__init__(owner_id, timeout=600)
        self.scope, self.who, self.ids, self.summary = scope, who, ids, summary
        self.guild_id = guild_id
        self.show_submitter = show_submitter
        self.mode = "list"
        self.page = 0
        self.index = 0
        self.images: dict[int, str | None] = {}

    @property
    def pages(self) -> int:
        return max(1, -(-len(self.ids) // subs.PAGE_SIZE))

    def page_ids(self) -> list[int]:
        start = self.page * subs.PAGE_SIZE
        return self.ids[start:start + subs.PAGE_SIZE]

    # --- drawing ---------------------------------------------------------------

    async def render(self) -> discord.Embed:
        if self.mode == "list":
            rows = await subs.rows_for(self.page_ids())
            embed = cards.submissions_card(self.who, self.summary, rows, self.page, self.pages)
            self._controls(rows)
            return embed
        skin = await subs.detail(self.ids[self.index])
        embed = cards.submission_card(
            skin,
            position=self.index,
            total=len(self.ids),
            site_url=site_url(skin["bonkverse_skin_id"]),
            image_url=await self._image(skin["bonkverse_skin_id"]),
            vote_url=self._vote_url(skin),
            show_submitter=self.show_submitter,
        )
        self._controls(await subs.rows_for(self.page_ids()))
        return embed

    def _vote_url(self, skin) -> str | None:
        if not (self.guild_id and skin.get("vote_message_id")):
            return None
        return f"https://discord.com/channels/{self.guild_id}/{VOTING_CHANNEL_ID}/{skin['vote_message_id']}"

    async def _image(self, bonkverse_id: int | None) -> str | None:
        """The skin's PNG from bonkverse.io (asked once per skin per browser)."""
        if not bonkverse_id:
            return None
        if bonkverse_id not in self.images:
            reply = await site.get_skin(bonkverse_id)
            self.images[bonkverse_id] = reply.data["skin"]["image_url"] if reply.ok else None
        return self.images[bonkverse_id]

    def _controls(self, rows: list[dict]):
        self.clear_items()
        if self.mode == "list":
            self._button("◀️", self._prev_page, disabled=self.page == 0)
            self._button("▶️", self._next_page, disabled=self.page >= self.pages - 1)
        else:
            self._button("◀️", self._prev_skin, disabled=self.index == 0)
            self._button("▶️", self._next_skin, disabled=self.index >= len(self.ids) - 1)
            self._button("📋 List", self._to_list)
        if rows:
            current = self.ids[self.index] if self.mode == "detail" else None
            menu = discord.ui.Select(
                placeholder="Open a skin",
                options=[
                    discord.SelectOption(
                        label=(r["skin_name"] or "Unnamed")[:100],
                        description=f"#{r['id']} · by {r['creator'] or 'Unknown'}"[:100],
                        value=str(r["id"]),
                        default=r["id"] == current,
                    )
                    for r in rows
                ],
                row=1,
            )
            menu.callback = self._open
            self.add_item(menu)

    def _button(self, label, callback, *, disabled=False):
        button = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary, disabled=disabled)
        button.callback = callback
        self.add_item(button)

    async def _show(self, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=await self.render(), view=self)

    # --- moving around -------------------------------------------------------------

    async def _prev_page(self, interaction):
        self.page = max(0, self.page - 1)
        await self._show(interaction)

    async def _next_page(self, interaction):
        self.page = min(self.pages - 1, self.page + 1)
        await self._show(interaction)

    async def _prev_skin(self, interaction):
        self.open_at(self.index - 1)
        await self._show(interaction)

    async def _next_skin(self, interaction):
        self.open_at(self.index + 1)
        await self._show(interaction)

    async def _to_list(self, interaction):
        self.mode = "list"
        await self._show(interaction)

    async def _open(self, interaction):
        chosen = int(interaction.data["values"][0])
        self.open_at(self.ids.index(chosen))
        await self._show(interaction)

    def open_at(self, index: int):
        """Detail page for the skin at `index`; the list follows to its page."""
        self.mode = "detail"
        self.index = max(0, min(index, len(self.ids) - 1))
        self.page = self.index // subs.PAGE_SIZE


async def creator_choices(typed: str) -> list[app_commands.Choice[str]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT MIN(creator) AS creator FROM skins
            WHERE creator ILIKE '%' || $1 || '%'
            GROUP BY LOWER(creator) ORDER BY COUNT(*) DESC LIMIT 25
            """,
            typed.strip().replace("%", r"\%").replace("_", r"\_"),
        )
    return [app_commands.Choice(name=r["creator"][:100], value=r["creator"][:100]) for r in rows if r["creator"]]


def scope_for(interaction: discord.Interaction, member, player: str | None):
    """(scope, heading, whether to show who submitted each skin)."""
    if player and player.strip():
        return subs.Scope(creator=player.strip()), f"Skins by {player.strip()}", True
    if member is not None and member.id != interaction.user.id:
        return subs.Scope(submitter_id=member.id), f"{member.display_name}'s submissions", False
    return subs.Scope(submitter_id=interaction.user.id), "Your submissions", False


def nothing_yet(interaction, member, player) -> str:
    if player and player.strip():
        return (
            f"No skins credited to **{player.strip()}** have come through BonxBot. "
            f"Try `/skin search {player.strip()}` to search all of bonkverse.io."
        )
    if member is not None and member.id != interaction.user.id:
        return f"{member.mention} hasn't submitted any skins through BonxBot yet."
    return "You haven't submitted any skins yet. Submit one with `/submit_skin` and a BonkLeagues link."


class Browse(commands.Cog):
    @app_commands.command(name="submissions", description="Browse skins submitted through BonxBot, with stats")
    @app_commands.describe(
        member="Whose submissions (you, if left empty)",
        player="Or: every skin credited to this bonk.io name",
        skin="Jump straight to one skin, by name or #number",
    )
    async def submissions(
        self,
        interaction: discord.Interaction,
        member: discord.User | None = None,
        player: app_commands.Range[str, 1, 32] | None = None,
        skin: str | None = None,
    ):
        if member is not None and player:
            await refuse(interaction, "Pick a `member` or a bonk.io `player`, not both.")
            return
        scope, who, show_submitter = scope_for(interaction, member, player)
        ids = await subs.skin_ids(scope)
        if not ids:
            await refuse(interaction, nothing_yet(interaction, member, player), "Nothing yet")
            return

        browser = SubmissionsBrowser(
            interaction.user.id, scope=scope, who=who, ids=ids,
            summary=await subs.summary(scope), guild_id=interaction.guild_id,
            show_submitter=show_submitter,
        )
        if skin:
            found = await subs.find(scope, skin)
            if found is None:
                await refuse(interaction, f"No skin matching **{skin.strip()}** in {who.lower()}.", "Not found")
                return
            browser.open_at(ids.index(found))

        await interaction.response.defer(thinking=True)
        await send(interaction, await browser.render(), view=browser)
        await browser.attach(interaction)

    @submissions.autocomplete("skin")
    async def _skin_choices(self, interaction: discord.Interaction, current: str):
        ns = interaction.namespace
        scope, _, _ = scope_for(interaction, getattr(ns, "member", None), getattr(ns, "player", None))
        return [app_commands.Choice(name=label, value=f"#{skin_id}")
                for label, skin_id in await subs.name_choices(scope, current)]

    @submissions.autocomplete("player")
    async def _player_choices(self, interaction: discord.Interaction, current: str):
        return await creator_choices(current)


async def setup(bot):
    await bot.add_cog(Browse())
