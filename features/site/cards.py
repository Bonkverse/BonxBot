"""What /skin, /player and /mapdecode look like. Pure functions over the
data Bonkverse's bot API returns (services/bonkverse_client.py)."""

from __future__ import annotations

from datetime import datetime

import discord

from utils.ui import INFO, card

# bonk.io's mode codes, as stored in a map's "mo".
MODES = {
    "b": "Classic",
    "ar": "Arrows",
    "ard": "Death Arrows",
    "sp": "Grapple",
    "v": "VTOL",
    "f": "Football",
}

# Bonkverse error code -> what to tell the player (private).
SITE_ERRORS = {
    "skin_not_found": "No skin with that id on bonkverse.io.",
    "search_timeout": "That search took too long. Try a more specific name.",
    "player_not_found": "Bonkverse hasn't seen **{name}** in replays and has no skins by them.",
    "invalid_username": "That isn't a valid bonk.io username.",
    "missing_code": "Paste a bonk.io map code, or attach it as a .txt file.",
    "map_rejected": "That doesn't decode as a bonk.io map.",
    "unauthorized": "BonxBot isn't connected to bonkverse.io right now. Let a staff member know.",
}
SITE_DOWN = "bonkverse.io isn't answering right now. Try again in a minute."


def site_problem(error: str | None, **names) -> str:
    return SITE_ERRORS.get(error or "", SITE_DOWN).format(**names)


def _day(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        moment = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return f"<t:{int(moment.timestamp())}:D>"


# --- skins ----------------------------------------------------------------------


def skin_card(skin: dict) -> discord.Embed:
    embed = card(skin["name"], f"by **{skin.get('creator') or 'Unknown'}**", INFO)
    embed.url = skin["url"]
    embed.set_image(url=skin["image_url"])
    score = (skin.get("upvotes") or 0) - (skin.get("downvotes") or 0)
    stats = [f"👍 {skin.get('upvotes', 0):,}", f"👎 {skin.get('downvotes', 0):,}",
             f"⭐ {skin.get('favorites', 0):,}"]
    embed.add_field(name="On bonkverse.io", value=" · ".join(stats) + f" · score {score:+,}")
    added = _day(skin.get("created_at"))
    embed.set_footer(text=f"Skin #{skin['id']}")
    if added:
        embed.add_field(name="Added", value=added)
    return embed


def search_title(query: str, count: int) -> str:
    return f"Top match for “{query}”" + (f" · {count - 1} more below" if count > 1 else "")


# --- players --------------------------------------------------------------------


def player_card(p: dict, discord_id: int | None = None) -> discord.Embed:
    name = p["username"]
    embed = card(name, None, INFO)
    if p.get("profile_url"):
        embed.url = p["profile_url"]

    level = p.get("level") or {}
    if p.get("status") == "private":
        embed.add_field(name="Level", value="Hidden by the player")
    elif level.get("value") is not None:
        peak = level.get("peak")
        extra = f" (peak {peak})" if peak and peak != level["value"] else ""
        seen = _day(level.get("seen_at"))
        embed.add_field(name="Level", value=f"**{level['value']}**{extra}" + (f"\nas of {seen}" if seen else ""))
    if p.get("games") is not None:
        embed.add_field(name="Games in replays", value=f"{p['games']:,}")
    embed.add_field(name="Skins on Bonkverse", value=f"{p.get('skins', 0):,}")

    account = p.get("account") or {}
    joined = account.get("joined")
    if joined:
        embed.add_field(name="Account made", value=f"around {joined[:7]}")
    if discord_id:
        embed.add_field(name="Discord", value=f"<@{discord_id}>")

    skin = p.get("skin") or {}
    if skin.get("thumbnail_url"):
        embed.set_thumbnail(url=skin["thumbnail_url"])
    if p.get("status") == "not_found":
        embed.description = "Not seen in replays yet, but credited on Bonkverse."
    embed.set_footer(text="From bonk.io replays Bonkverse has collected")
    return embed


# --- maps -----------------------------------------------------------------------


def map_card(m: dict) -> discord.Embed:
    embed = card(m.get("name") or "Untitled map", f"by **{m.get('author') or 'Unknown'}**", INFO)
    mode = m.get("mode")
    if mode:
        embed.add_field(name="Mode", value=MODES.get(mode, mode))
    if m.get("upvotes") is not None:
        embed.add_field(name="Votes", value=f"👍 {m['upvotes']:,} · 👎 {m.get('downvotes') or 0:,}")
    if m.get("map_id"):
        embed.add_field(name="Map ID", value=str(m["map_id"]))
    embed.add_field(
        name="Inside",
        value=(
            f"{m.get('shapes', 0):,} shapes · {m.get('bodies', 0):,} bodies · "
            f"{m.get('joints', 0):,} joints\n{m.get('spawns', 0):,} spawns · "
            f"{m.get('cap_zones', 0):,} capture zones"
        ),
        inline=False,
    )
    if m.get("date"):
        embed.set_footer(text=f"Published {m['date'][:10]}")
    return embed
