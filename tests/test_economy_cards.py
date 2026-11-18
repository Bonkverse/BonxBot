"""How the Bonx commands look (features/economy/cards.py) and the two lookups
they share: leaderboard place and whether a streak is still alive.

The card tests need nothing. The leaderboard test needs TEST_DATABASE_URL and
uses a temporary `users` table, so it never touches real rows.
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone

import discord
import pytest

from features.economy import cards
from features.economy.commands import leaderboard_place, live_streak
from utils.ui import BONX, INFO, MUTED, SUCCESS

NOW = datetime(2026, 11, 18, 7, 0, tzinfo=timezone.utc)


def fields(embed):
    return {field.name: field.value for field in embed.fields}


# --- /balance -------------------------------------------------------------------


def test_balance_shows_place():
    embed = cards.balance_card("The Saucy Tulip", None, 12345, 4, 61)
    assert embed.description == "💰 **12,345 Bonx**"
    assert embed.author.name == "The Saucy Tulip"
    assert embed.footer.text == "#4 of 61 on /leaderboard"
    assert embed.color == BONX


# --- /slots ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "spin, mult, net, title, verdict, color",
    [
        (["💎", "💎", "💎"], 10, 900, "🎰 JACKPOT", "Three 💎 pays **10×**", BONX),
        (["🍒", "🍒", "🍋"], 1.3, 30, "🎰 Orbana Market", "Two of a kind pays **1.3×**", SUCCESS),
        (["🍒", "🍋", "⭐"], 0, -100, "🎰 Orbana Market", "No match", MUTED),
    ],
)
def test_slots(spin, mult, net, title, verdict, color):
    embed = cards.slots_card(spin, mult, 100, net, 5000)
    assert embed.title == title
    assert embed.description.endswith(verdict)
    assert embed.color == color
    assert fields(embed)["Result"] == f"{net:+,} Bonx"
    assert fields(embed)["Balance"] == "5,000 Bonx"


# --- /daily ------------------------------------------------------------------------


def test_daily_points_to_tomorrow():
    embed = cards.daily_card(1300, 4, step=100, cap=6, next_claim=NOW)
    assert embed.description.splitlines()[:2] == [
        "**+1,300 Bonx** · 🔥 4-day streak",
        "Tomorrow is day 5: +100 more.",
    ]


def test_daily_when_maxed():
    embed = cards.daily_card(1600, 9, step=100, cap=6, next_claim=NOW)
    assert "Streak maxed" in embed.description


# --- /profile ------------------------------------------------------------------------


def profile(**changes):
    base = dict(
        name="Jorge", avatar=None, own=True, bonk_name="The Saucy Tulip", alts=1,
        balance=12345, place=4, total=61, streak=5, best_streak=12, skins=14,
        region="Skintopia", rank="Stylist · Tier 3",
    )
    return cards.Profile(**{**base, **changes})


def test_profile_shows_both_worlds():
    embed = cards.profile_card(profile())
    assert embed.description == "🎮 bonk.io: **The Saucy Tulip** (+1 alt)"
    assert fields(embed) == {
        "💰 Bonx": "12,345 Bonx\n#4 on the leaderboard",
        "🔥 Daily streak": "5 days\nBest: 12",
        "🎨 Skins submitted": "14",
        "🌍 Region": "Skintopia",
        "🏷️ Rank": "Stylist · Tier 3",
    }
    assert embed.color == INFO


def test_profile_hints_only_on_your_own():
    mine = cards.profile_card(profile(bonk_name=None, alts=0, region=None))
    theirs = cards.profile_card(profile(bonk_name=None, alts=0, region=None, own=False))

    assert "/link" in mine.description and "/link" not in theirs.description
    assert fields(mine)["🌍 Region"] == "Pick one with `/join_region`."
    assert fields(theirs)["🌍 Region"] == "Unaligned"


def test_lapsed_streak():
    embed = cards.profile_card(profile(streak=0))
    assert fields(embed)["🔥 Daily streak"] == "None right now\nBest: 12"


# --- /history and /leaderboard --------------------------------------------------------


def test_history_pages():
    lines = [f"line {i}" for i in range(23)]
    last = cards.history_page(lines, 2, 10)
    assert last.description == "line 20\nline 21\nline 22"
    assert last.footer.text == "Newest first · 21–23 of 23 · Page 3/3"

    single = cards.history_page(lines[:4], 0, 10)
    assert single.footer.text == "Newest first · 1–4 of 4"


def test_leaderboard_marks_medals_and_you():
    rows = [{"discord_id": i, "bonx": 1000 * (5 - i)} for i in range(1, 5)]
    embed = cards.leaderboard_page(rows, 0, 0, 3, viewer_id=4, viewer_place=4)
    assert embed.description.splitlines() == [
        "🥇 <@1> · 4,000 Bonx",
        "🥈 <@2> · 3,000 Bonx",
        "🥉 <@3> · 2,000 Bonx",
        "**`#4` <@4> · 1,000 Bonx** ◂ you",
    ]
    assert embed.footer.text == "Page 1/3 · You're #4"


# --- lookups ------------------------------------------------------------------------


def test_streak_lapses_after_48_hours():
    now = discord.utils.utcnow().replace(tzinfo=None)
    assert live_streak({"last_daily": now - timedelta(hours=30), "daily_streak": 5}) == 5
    assert live_streak({"last_daily": now - timedelta(hours=49), "daily_streak": 5}) == 0
    assert live_streak({"last_daily": None, "daily_streak": 0}) == 0


URL = os.getenv("TEST_DATABASE_URL")


@pytest.mark.skipif(not URL, reason="needs TEST_DATABASE_URL")
def test_leaderboard_place_breaks_ties_like_the_leaderboard():
    import asyncpg

    async def run():
        conn = await asyncpg.connect(URL)
        try:
            await conn.execute("CREATE TEMP TABLE users (discord_id BIGINT PRIMARY KEY, bonx INT)")
            await conn.executemany(
                "INSERT INTO users VALUES ($1, $2)", [(10, 500), (20, 900), (30, 500), (40, 100)]
            )
            return [await leaderboard_place(conn, i) for i in (20, 10, 30, 40, 99)]
        finally:
            await conn.close()

    assert asyncio.run(run()) == [(1, 4), (2, 4), (3, 4), (4, 4), (None, 4)]
