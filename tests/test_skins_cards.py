"""How vote posts, results and vote replies look (features/skins/cards.py)."""

from datetime import datetime, timezone

from features.skins import cards
from utils.ui import MUTED, PROBLEM

ENDS = datetime(2026, 11, 19, 7, 0)  # noqa: DTZ001 - the database stores naive UTC


def fields(embed):
    return {field.name: field.value for field in embed.fields}


def test_vote_post():
    embed = cards.vote_card("Reality.exe", "Somepoop", "https://bonkleagues.io/s/x", ENDS)
    assert embed.title == "Reality.exe"
    assert embed.description.startswith("by **Somepoop** · [Open on BonkLeagues](https://bonkleagues.io/s/x)")
    assert fields(embed) == {"Tiers": "**S** 6 · **A** 5 · **B** 4 · **C** 3 · **D** 2 · **F** 1", "🗳️ Votes": "0"}
    assert "+100 Bonx" in embed.footer.text


def test_vote_count_updates_and_old_posts_get_one():
    embed = cards.vote_card("Reality.exe", "Somepoop", "https://x", ENDS)
    assert fields(cards.set_vote_count(embed, 7))["🗳️ Votes"] == "7"

    old = cards.card("Reality.exe", "old post")
    old.add_field(name="🗳️ Vote", value="tier list")
    assert fields(cards.set_vote_count(old, 3))["🗳️ Votes"] == "3"


def test_finished_vote():
    embed = cards.vote_card("Reality.exe", "Somepoop", "https://x", ENDS.replace(tzinfo=timezone.utc))
    done = cards.finish_vote_card(embed, "A", 4.6153, 13)

    assert fields(done)["🏆 Result"] == "**A Tier** · 4.62 average from 13 votes"
    assert fields(done)["🗳️ Votes"] == "13"
    assert "Voting ended" in done.description
    assert done.color == cards.TIER_COLORS["A"]


def test_tier_channel_post():
    embed = cards.tier_card("Reality.exe", "Somepoop", "https://x", "S", 6.0, 1)
    assert embed.url == "https://x"
    assert embed.description == "by **Somepoop**\n**S Tier** · 6.00 average from 1 vote"


def test_vote_replies():
    assert cards.voted("S", 100).description == "🗳️ You voted **S**. +100 Bonx"
    assert cards.changed("A", 25).description == "🗳️ Changed your vote to **A**. −25 Bonx"
    assert cards.already("S").color == MUTED
    assert cards.cant_afford(25).color == PROBLEM
    assert "**25 Bonx**" in cards.cant_afford(25).description
