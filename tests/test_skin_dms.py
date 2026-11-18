"""DMs about skin submissions (features/skins/notify.py) and the bonk.io level
on /profile. The database test needs TEST_DATABASE_URL."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from features.economy import cards as economy_cards
from features.economy import commands as economy
from features.skins import notify
from services import bonkverse_client as site
from tests.test_accounts_service import TULIP, URL, _sql, fresh_db  # noqa: F401

NOW = datetime(2026, 11, 18, 12, 0)  # noqa: DTZ001 - the database stores naive UTC


# --- who hears about it -----------------------------------------------------------


def test_only_recent_submissions_are_announced():
    submitted_today = NOW + timedelta(hours=20)  # voting ends 24h after submitting
    assert notify.is_recent(submitted_today, NOW) is True
    assert notify.is_recent(NOW - timedelta(days=2), NOW) is True
    assert notify.is_recent(NOW - timedelta(days=5), NOW) is False  # an old backlog retry
    assert notify.is_recent(None, NOW) is False


@pytest.mark.parametrize(
    "error, reason",
    [
        ("422: invalid_skin_code", "its skin code couldn't be read"),
        ("502: render_failed", "Bonkverse couldn't draw it"),
        ("400: something_new", "Bonkverse turned it down"),
        (None, "Bonkverse turned it down"),
    ],
)
def test_failure_reasons(error, reason):
    assert notify.failure_reason(error) == reason


def test_messages():
    live = notify.live_card("Hot Dog", "https://bonkverse.io/skins/detail/9", "https://x/9.png")
    assert live.description == "**Hot Dog** is live: https://bonkverse.io/skins/detail/9"
    assert live.image.url == "https://x/9.png"

    failed = notify.failed_card("Hot Dog", "502: render_failed")
    assert "wasn't added: Bonkverse couldn't draw it. It still counts in the tier vote." in failed.description

    result = notify.result_card("Hot Dog", "A", 4.615, 13, "https://discord.com/channels/1/2/3")
    assert result.title == "🏆 Voting closed on Hot Dog"
    assert result.description == "**A Tier** · 4.62 average from 13 votes\nhttps://discord.com/channels/1/2/3"


class FakeUser:
    def __init__(self):
        self.sent = []

    async def send(self, embed):
        self.sent.append(embed)


class FakeBot:
    def __init__(self):
        self.user = FakeUser()

    def get_user(self, user_id):
        return self.user


@pytest.mark.skipif(not URL, reason="set TEST_DATABASE_URL to run database tests")
def test_dms_respect_the_opt_out_and_recency(monkeypatch):
    bot = FakeBot()
    monkeypatch.setattr(notify, "_bot", bot)

    async def no_site(skin_id):
        return site.SiteReply(error="unreachable")

    monkeypatch.setattr(site, "get_skin", no_site)
    now = datetime.now(UTC).replace(tzinfo=None)  # the database stores naive UTC
    recent = now + timedelta(hours=20)
    old = now - timedelta(days=10)

    async def go():
        await _sql("CREATE TABLE users (discord_id BIGINT PRIMARY KEY)")
        await notify.ensure_schema()
        await _sql(
            "CREATE TABLE skins (id SERIAL PRIMARY KEY, skin_name TEXT, submitter_id BIGINT,"
            " ends_at TIMESTAMP)"
        )
        await _sql(
            "INSERT INTO skins (skin_name, submitter_id, ends_at) VALUES"
            " ('Hot Dog', $1, $2), ('Ancient', $1, $3), ('Nobody', NULL, $2)",
            TULIP, recent, old,
        )
        await notify.skin_live(1, 50)  # sent (no users row yet: DMs default on)
        await notify.skin_live(2, 51)  # too old
        await notify.skin_live(3, 52)  # no submitter
        await _sql("INSERT INTO users (discord_id, skin_dms) VALUES ($1, FALSE)", TULIP)
        await notify.vote_closed(1, {"tier": "S", "avg": 6.0, "total": 1}, None)  # opted out

    asyncio.get_event_loop().run_until_complete(go())

    assert [e.title for e in bot.user.sent] == ["✅ Your skin is on bonkverse.io"]
    assert bot.user.sent[0].description.endswith("/skins/detail/50")  # fallback link when the site is down


def test_no_bot_means_no_dms(monkeypatch):
    monkeypatch.setattr(notify, "_bot", None)
    assert asyncio.run(notify._recipient(1)) is None  # never touches the database


# --- /profile level ------------------------------------------------------------------


def test_profile_shows_the_level():
    profile = economy_cards.Profile(
        name="Jorge", avatar=None, own=True, bonk_name="The Saucy Tulip", alts=0, balance=0,
        place=None, total=None, streak=0, best_streak=0, skins=0, region=None, rank="", level=41,
    )
    assert economy_cards.profile_card(profile).description == "🎮 bonk.io: **The Saucy Tulip** · Level 41"


def test_level_lookup(monkeypatch):
    async def found(name):
        return site.SiteReply(data={"username": name, "level": {"value": 41}})

    async def hidden(name):
        return site.SiteReply(data={"username": name, "status": "private", "level": None})

    async def slow(name):
        await asyncio.sleep(10)

    monkeypatch.setattr(site, "get_player", found)
    assert asyncio.run(economy.bonk_level("The Saucy Tulip")) == 41
    assert asyncio.run(economy.bonk_level(None)) is None
    monkeypatch.setattr(site, "get_player", hidden)
    assert asyncio.run(economy.bonk_level("x")) is None

    monkeypatch.setattr(site, "get_player", slow)
    monkeypatch.setattr(economy.asyncio, "wait_for", _instant_timeout)
    assert asyncio.run(economy.bonk_level("x")) is None


async def _instant_timeout(coro, timeout):
    coro.close()
    raise asyncio.TimeoutError
