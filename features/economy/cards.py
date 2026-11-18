"""What the Bonx commands look like: /balance, /profile, /slots, /daily,
/give, /history, /leaderboard and /admin_add.

Owns: turning numbers into embeds. Pure functions, so tests can check every
layout without Discord or a database.
Does not own: fetching the numbers (commands.py, service.py) or the shared
colors and formatting (utils/ui.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import discord

from utils.ui import BONX, INFO, MUTED, SUCCESS, bonx, card, signed_bonx, when

MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def place_text(place: int | None, total: int | None) -> str | None:
    if not place or not total:
        return None
    return f"#{place:,} of {total:,} on /leaderboard"


# --- /balance -------------------------------------------------------------------


def balance_card(name: str, avatar: str | None, balance: int, place: int | None, total: int | None):
    embed = card(description=f"💰 **{bonx(balance)}**", color=BONX)
    embed.set_author(name=name, icon_url=avatar)
    footer = place_text(place, total)
    if footer:
        embed.set_footer(text=footer)
    return embed


# --- /slots -----------------------------------------------------------------------


def slots_card(spin: list[str], multiplier: float, bet: int, net: int, balance: int):
    reels = " │ ".join(spin)
    if len(set(spin)) == 1:
        title, verdict, color = "🎰 JACKPOT", f"Three {spin[0]} pays **{multiplier:g}×**", BONX
    elif multiplier:
        title, verdict, color = "🎰 Orbana Market", f"Two of a kind pays **{multiplier:g}×**", SUCCESS
    else:
        title, verdict, color = "🎰 Orbana Market", "No match", MUTED
    embed = card(title, f"# {reels}\n{verdict}", color)
    embed.add_field(name="Bet", value=bonx(bet))
    embed.add_field(name="Result", value=signed_bonx(net))
    embed.add_field(name="Balance", value=bonx(balance))
    return embed


# --- /daily -------------------------------------------------------------------------


def daily_card(amount: int, streak: int, *, step: int, cap: int, next_claim: datetime):
    lines = [f"**+{bonx(amount)}** · 🔥 {streak}-day streak"]
    if streak <= cap:
        lines.append(f"Tomorrow is day {streak + 1}: +{step:,} more.")
    else:
        lines.append("Streak maxed. You're on the top daily payout.")
    lines.append(f"Next claim {when(next_claim)}.")
    return card("🎁 Daily claimed", "\n".join(lines), BONX)


# --- /give and /admin_add ---------------------------------------------------------


def give_card(sender: str, receiver: str, amount: int, sender_balance: int):
    embed = card(description=f"💸 {sender} sent **{bonx(amount)}** to {receiver}", color=SUCCESS)
    embed.set_footer(text=f"Your balance: {bonx(sender_balance)}")
    return embed


def grant_card(receiver: str, amount: int, balance: int):
    return card(
        description=f"🛠️ Added **{bonx(amount)}** to {receiver}. They now have {bonx(balance)}.",
        color=SUCCESS,
    )


# --- /profile -----------------------------------------------------------------------


@dataclass(frozen=True)
class Profile:
    name: str
    avatar: str | None
    own: bool  # looking at your own profile (shows hints)
    bonk_name: str | None
    alts: int
    balance: int
    place: int | None
    total: int | None
    streak: int  # 0 when it has lapsed
    best_streak: int
    skins: int
    region: str | None
    rank: str
    founding_resident: bool = False
    level: int | None = None  # bonk.io level from bonkverse.io, when it answered


def profile_card(p: Profile) -> discord.Embed:
    if p.bonk_name:
        alts = f" (+{p.alts} alt{'s' if p.alts != 1 else ''})" if p.alts else ""
        level = f" · Level {p.level}" if p.level is not None else ""
        identity = f"🎮 bonk.io: **{p.bonk_name}**{level}{alts}"
    elif p.own:
        identity = "🎮 No bonk.io account linked yet. Use `/link`."
    else:
        identity = "🎮 No bonk.io account linked"

    embed = card(description=identity, color=INFO)
    embed.set_author(name=p.name, icon_url=p.avatar)
    if p.avatar:
        embed.set_thumbnail(url=p.avatar)

    place = f"\n#{p.place:,} on the leaderboard" if p.place else ""
    embed.add_field(name="💰 Bonx", value=f"{bonx(p.balance)}{place}")
    streak = f"{p.streak} day{'s' if p.streak != 1 else ''}" if p.streak else "None right now"
    embed.add_field(name="🔥 Daily streak", value=f"{streak}\nBest: {p.best_streak}")
    embed.add_field(name="🎨 Skins submitted", value=f"{p.skins:,}")

    if p.region:
        embed.add_field(name="🌍 Region", value=p.region)
        embed.add_field(name="🏷️ Rank", value=p.rank)
    else:
        hint = "Pick one with `/join_region`." if p.own else "Unaligned"
        embed.add_field(name="🌍 Region", value=hint)
    if p.founding_resident:
        embed.set_footer(text="🏛️ Founding Resident of Veri")
    return embed


# --- /history ------------------------------------------------------------------------


def history_line(amount: int, label: str, created_at: datetime) -> str:
    return f"`{amount:+,}` {label} · {when(created_at)}"


def history_page(lines: list[str], page: int, per_page: int) -> discord.Embed:
    pages = max(1, -(-len(lines) // per_page))
    chunk = lines[page * per_page:(page + 1) * per_page]
    embed = card("🧾 Bonx history", "\n".join(chunk), BONX)
    shown = f"{page * per_page + 1}–{page * per_page + len(chunk)} of {len(lines)}"
    embed.set_footer(text=f"Newest first · {shown}" + (f" · Page {page + 1}/{pages}" if pages > 1 else ""))
    return embed


# --- /leaderboard --------------------------------------------------------------------


def leaderboard_page(rows, start: int, page: int, pages: int, viewer_id: int, viewer_place: int | None):
    lines = []
    for place, row in enumerate(rows, start=start + 1):
        badge = MEDALS.get(place, f"`#{place}`")
        entry = f"{badge} <@{row['discord_id']}> · {bonx(row['bonx'])}"
        lines.append(f"**{entry}** ◂ you" if row["discord_id"] == viewer_id else entry)
    embed = card("🏆 Bonx Leaderboard", "\n".join(lines) or "Nobody on this page.", BONX)
    footer = f"Page {page + 1}/{pages}"
    if viewer_place:
        footer += f" · You're #{viewer_place:,}"
    embed.set_footer(text=footer)
    return embed
