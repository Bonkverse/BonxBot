"""/replay: a bonk.io replay as a video in Discord.

Owns: the command, reading what it's given (a replay id, "cycle:id", or a
replays.bonkverse.io watch link), and the post: an embed with the map,
players and a link to watch it on the replay site, with the MP4 attached so
Discord plays it inline. The video is rendered on request by the replay video
service (services/replay_renderer.py) and stored nowhere but in the message.
"""

from __future__ import annotations

import io
import re

import discord
from discord import app_commands
from discord.ext import commands

from services import replay_renderer as renderer
from utils.ui import INFO, card, refuse

WATCH_LINK = re.compile(r"/watch/(\d+)/(\d+)")
CYCLE_ID = re.compile(r"^(\d+)\s*[:/]\s*(\d+)$")
BARE_ID = re.compile(r"^#?(\d{1,18})$")

TEAM_DOTS = {1: "⚪", 2: "🔴", 3: "🔵", 4: "🟢", 5: "🟡"}
TEAM_NAMES = {2: "Red", 3: "Blue", 4: "Green", 5: "Yellow"}

ERRORS = {
    "replay_not_found": "There's no replay with that number in the Bonkverse archive.",
    "replay_too_long": "That replay is too long to post as a video.",
    "video_too_large": "That replay's video came out too big for Discord to post.",
    "render_timeout": "That replay took too long to render. Try again in a minute.",
    "render_failed": "The replay couldn't be turned into a video.",
    "db_unavailable": "The replay archive isn't answering right now. Try again in a minute.",
    "not_configured": "Replay videos aren't set up on this bot yet. Let a staff member know.",
    "unauthorized": "Replay videos aren't set up on this bot yet. Let a staff member know.",
}
DOWN = "The replay video service isn't answering right now. Try again in a minute."
HOW = "Give a replay number like `1234567`, `6:1234567` (cycle:number), or a replays.bonkverse.io watch link."


def parse_replay(text: str) -> tuple[str, int | None] | None:
    """(replay id, cycle or None) from what someone typed."""
    text = text.strip()
    for pattern in (WATCH_LINK, CYCLE_ID):
        found = pattern.search(text)
        if found:
            return found.group(2), int(found.group(1))
    found = BARE_ID.match(text)
    return (found.group(1), None) if found else None


def player_lines(players: list[dict]) -> str:
    """Grouped by team in team games, otherwise one line per player."""
    def who(p):
        name = f"{p['name']} (guest)" if p.get("guest") else f"**{p['name']}**"
        return f"{name} · Lv {p['level']}" if p.get("level") and not p.get("guest") else name

    teams = {p.get("team") for p in players}
    if len(teams - {None, 1}) > 1:
        lines = []
        for team in sorted(t for t in teams if t):
            members = ", ".join(who(p) for p in players if p.get("team") == team)
            lines.append(f"{TEAM_DOTS.get(team, '•')} {TEAM_NAMES.get(team, 'Team')}: {members}")
        return "\n".join(lines)
    return "\n".join(f"{TEAM_DOTS.get(p.get('team'), '•')} {who(p)}" for p in players)


def replay_card(replay: dict, url: str) -> discord.Embed:
    title = replay.get("map") or "Unknown map"
    if replay.get("author"):
        title += f" by {replay['author']}"
    embed = card(title[:256], player_lines(replay.get("players") or [])[:4000] or None, INFO)
    embed.url = url
    if replay.get("mode"):
        embed.add_field(name="Mode", value=replay["mode"])
    if replay.get("seconds") is not None:
        embed.add_field(name="Length", value=f"0:{replay['seconds']:02d}" if replay["seconds"] < 60 else f"{replay['seconds'] // 60}:{replay['seconds'] % 60:02d}")
    if replay.get("day"):
        embed.add_field(name="Played", value=f"~{replay['day']}")
    embed.add_field(name="Watch", value=f"[On replays.bonkverse.io]({url})", inline=False)
    embed.set_footer(text=f"Replay #{replay.get('id')} · cycle {replay.get('cycle')}")
    return embed


class Replays(commands.Cog):
    @app_commands.command(name="replay", description="Post a bonk.io replay as a video")
    @app_commands.describe(replay="A replay number, cycle:number, or a replays.bonkverse.io link")
    async def replay(self, interaction: discord.Interaction, replay: app_commands.Range[str, 1, 300]):
        parsed = parse_replay(replay)
        if parsed is None:
            await refuse(interaction, HOW, "Couldn't read that replay")
            return
        replay_id, cycle = parsed
        await interaction.response.defer(thinking=True)
        reply = await renderer.render(replay_id, cycle)
        if not reply.ok:
            await refuse(interaction, ERRORS.get(reply.error or "", DOWN), "Couldn't post that replay")
            return
        info = reply.replay
        url = renderer.watch_url(info.get("id") or replay_id, info.get("cycle"))
        file = discord.File(io.BytesIO(reply.video), filename=f"replay-{info.get('id') or replay_id}.mp4")
        await interaction.followup.send(
            embed=replay_card(info, url), file=file, allowed_mentions=discord.AllowedMentions.none()
        )


async def setup(bot):
    await bot.add_cog(Replays())
