"""/replays: search bonk.io replays from Discord, like the replay site's search.

Owns: the command and its browser. Whose replays: yours (your linked bonk.io
accounts) by default, a Discord member's, or any bonk.io player's; narrowed
by map, mode, or an opponent. Results come ten to a page with ◀️ ▶️, and
picking one posts it as a video (the same post as /replay).
Does not own: searching (the replay site's /api/search, services/replay_site.py)
or rendering (services/replay_renderer.py).
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from features.accounts import service as accounts
from services import replay_renderer as renderer
from services import replay_site as site
from utils.ui import INFO, OwnedView, card, refuse, send

from .commands import TEAM_DOTS, video_post

PER_PAGE = 10

MODE_CHOICES = [app_commands.Choice(name=label, value=code) for code, label in site.MODES.items()]


def length(frames) -> str | None:
    if not frames:
        return None
    seconds = round(frames / 30)
    return f"{seconds // 60}:{seconds % 60:02d}"


def result_line(row: dict, highlight: set[str]) -> str:
    """`6:123` **Map** · Mode · 0:15 · players · ~day"""
    players = []
    for p in (row.get("players") or [])[:6]:
        name = p.get("username") or "?"
        shown = f"**{name}**" if name.casefold() in highlight else name
        players.append(f"{TEAM_DOTS.get(p.get('team'), '')}{shown}" if p.get("team") not in (None, 1) else shown)
    extra = len(row.get("players") or []) - len(players)
    who = ", ".join(players) + (f" +{extra}" if extra > 0 else "")
    url = renderer.watch_url(row["id"], row.get("cycle"))
    bits = [f"[`{row.get('cycle')}:{row['id']}`]({url}) **{row.get('mapname') or 'Unknown map'}**"]
    if row.get("mode"):
        bits.append(site.MODES.get(row["mode"], row["mode"]))
    if length(row.get("frames")):
        bits.append(length(row.get("frames")))
    if row.get("estimated_day"):
        bits.append(f"~{row['estimated_day']}")
    return " · ".join(bits) + (f"\n{who}" if who else "")


def results_card(title: str, rows: list[dict], page: int, reply: site.SearchReply, highlight: set[str]) -> discord.Embed:
    lines = [result_line(r, highlight) for r in rows]
    embed = card(f"🎬 {title}"[:256], "\n".join(lines)[:4096] or "No replays on this page.", INFO)
    total = f"{reply.total:,}{'+' if reply.capped else ''}"
    pages = max(1, -(-min(reply.total, 10_000) // PER_PAGE))
    embed.set_footer(text=f"{total} replays · Page {page + 1}/{pages} · Pick one to watch it as a video")
    return embed


class ReplayResults(OwnedView):
    def __init__(self, owner_id: int, *, title: str, query: dict, highlight: set[str]):
        super().__init__(owner_id, timeout=600)
        self.title, self.query, self.highlight = title, query, highlight
        self.page = 0
        self.reply: site.SearchReply | None = None
        self.rows: list[dict] = []

    @property
    def pages(self) -> int:
        total = min(self.reply.total if self.reply else 0, 10_000)
        return max(1, -(-total // PER_PAGE))

    async def load(self) -> site.SearchReply:
        """Fetch the site page holding this Discord page (the site pages by 20)."""
        first = self.page * PER_PAGE
        site_page = first // site.PAGE_SIZE + 1
        self.reply = await site.search(site.search_params(**self.query, page=site_page))
        start = first % site.PAGE_SIZE
        self.rows = self.reply.results[start:start + PER_PAGE]
        self._controls()
        return self.reply

    def embed(self) -> discord.Embed:
        return results_card(self.title, self.rows, self.page, self.reply, self.highlight)

    def _controls(self):
        self.clear_items()
        for label, step, disabled in (("◀️", -1, self.page == 0), ("▶️", 1, self.page >= self.pages - 1)):
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary, disabled=disabled)
            button.callback = self._turn(step)
            self.add_item(button)
        if self.rows:
            menu = discord.ui.Select(
                placeholder="Watch one as a video",
                options=[
                    discord.SelectOption(
                        label=f"{r.get('mapname') or 'Unknown map'}"[:100],
                        description=f"{r.get('cycle')}:{r['id']} · {', '.join(p.get('username') or '?' for p in (r.get('players') or [])[:4])}"[:100],
                        value=f"{r.get('cycle')}:{r['id']}",
                    )
                    for r in self.rows
                ],
                row=1,
            )
            menu.callback = self._watch
            self.add_item(menu)

    def _turn(self, step: int):
        async def turn(interaction: discord.Interaction):
            self.page = max(0, min(self.pages - 1, self.page + step))
            await interaction.response.defer()
            await self.load()
            await interaction.edit_original_response(embed=self.embed(), view=self)
        return turn

    async def _watch(self, interaction: discord.Interaction):
        cycle, replay_id = interaction.data["values"][0].split(":")
        await interaction.response.defer(thinking=True)
        post = await video_post(replay_id, int(cycle))
        if isinstance(post, str):
            await refuse(interaction, post, "Couldn't post that replay")
            return
        embed, file = post
        await interaction.followup.send(embed=embed, file=file, allowed_mentions=discord.AllowedMentions.none())


async def whose(interaction: discord.Interaction, member, player: str | None) -> tuple[list[str], str] | str:
    """(bonk.io names, heading) for the search, or the reason there are none."""
    if player and player.strip():
        return [player.strip()], f"Replays with {player.strip()}"
    target = member or interaction.user
    rows = await accounts.accounts_of(target.id)
    names = [r["bonk_username"] for r in rows]
    if names:
        title = "Your replays" if target.id == interaction.user.id else f"{target.display_name}'s replays"
        return names, title
    if target.id == interaction.user.id:
        return "Link your bonk.io account with `/link` to find your replays, or search for a `player`."
    return f"{target.mention} hasn't linked a bonk.io account. Try searching their bonk.io name as `player`."


class ReplaySearch(commands.Cog):
    @app_commands.command(name="replays", description="Find bonk.io replays: yours, a member's, or any player's")
    @app_commands.describe(
        player="A bonk.io name (leave empty for your own replays)",
        member="Or a Discord member who linked their bonk.io account",
        map_="Only on this map (name or map id)",
        mode="Only this mode",
        against="Only games that also had this bonk.io player",
        oldest="Show the oldest first",
    )
    @app_commands.choices(mode=MODE_CHOICES)
    @app_commands.rename(map_="map")
    async def replays(
        self,
        interaction: discord.Interaction,
        player: app_commands.Range[str, 1, 32] | None = None,
        member: discord.User | None = None,
        map_: app_commands.Range[str, 1, 100] | None = None,
        mode: app_commands.Choice[str] | None = None,
        against: app_commands.Range[str, 1, 32] | None = None,
        oldest: bool = False,
    ):
        if player and member:
            await refuse(interaction, "Pick a bonk.io `player` or a Discord `member`, not both.")
            return
        found = await whose(interaction, member, player)
        if isinstance(found, str):
            await refuse(interaction, found, "Whose replays?")
            return
        names, title = found
        if against:
            # The site matches all or any names: "my accounts AND them" isn't
            # one search, so use the main account.
            names, match = [names[0], against.strip()], "all"
            title += f" vs {against.strip()}"
        else:
            match = "any"
        if map_:
            title += f" on {map_.strip()}"
        if mode:
            title += f" · {mode.name}"

        view = ReplayResults(
            interaction.user.id,
            title=title,
            query={"players": names, "match": match, "map_": map_, "mode": mode.value if mode else None, "oldest": oldest},
            highlight={n.casefold() for n in names},
        )
        await interaction.response.defer(thinking=True)
        reply = await view.load()
        if not reply.ok:
            await refuse(interaction, "The replay archive isn't answering right now. Try again in a minute.")
            return
        if not reply.total:
            await refuse(interaction, f"No replays found for **{title}**.", "No replays")
            return
        await send(interaction, view.embed(), view=view)
        await view.attach(interaction)


async def setup(bot):
    await bot.add_cog(ReplaySearch())
