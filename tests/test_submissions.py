"""/submissions: the queries (features/skins/submissions.py, real Postgres via
TEST_DATABASE_URL), the layouts (cards.py) and the browser (browse.py)."""

import asyncio
from datetime import datetime, timedelta

import discord
import pytest

from features.skins import browse, cards
from features.skins import submissions as subs
from tests.test_accounts_service import TULIP, URL, _sql, fresh_db  # noqa: F401

OTHER = 222
ENDS = datetime(2026, 11, 1, 12, 0)  # noqa: DTZ001 - the database stores naive UTC

needs_db = pytest.mark.skipif(not URL, reason="set TEST_DATABASE_URL to run database tests")


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


async def _tables():
    await _sql(
        """
        CREATE TABLE skins (
            id SERIAL PRIMARY KEY, skin_name TEXT, skin_url TEXT, final_url TEXT,
            skin_code TEXT, creator TEXT, creator_source TEXT, submitter_id BIGINT,
            ends_at TIMESTAMP, finalized BOOLEAN DEFAULT FALSE, vote_message_id BIGINT,
            ranktier TEXT, average_score FLOAT, total_votes INT, bonkverse_skin_id INT
        )
        """
    )
    await _sql("CREATE TABLE votes (skin_id INT, user_id BIGINT, tier INT)")


async def _skin(name, creator, submitter, *, tier=None, avg=None, votes=0, bonkverse=None):
    rows = await _sql(
        "INSERT INTO skins (skin_name, skin_url, creator, submitter_id, ends_at, finalized,"
        " ranktier, average_score, total_votes, bonkverse_skin_id, vote_message_id)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 555) RETURNING id",
        name, f"https://bonkleagues.io/s/{name}", creator, submitter, ENDS, tier is not None,
        tier, avg, votes, bonkverse,
    )
    return rows[0]["id"]


async def _votes(skin_id, *points):
    for user, p in enumerate(points):
        await _sql("INSERT INTO votes VALUES ($1, $2, $3)", skin_id, user, p)


@pytest.fixture
def data():
    async def go():
        await _tables()
        ids = {
            "hot dog": await _skin("Hot Dog", "The Saucy Tulip", TULIP, tier="S", avg=5.8, votes=5, bonkverse=10),
            "tulip": await _skin("Tulip Classic", "the saucy tulip", TULIP, tier="B", avg=3.9, votes=4),
            "reality": await _skin("Reality.exe", "Somepoop", TULIP, tier="A", avg=4.6, votes=3),
            "new": await _skin("Hot Dog 2", "The Saucy Tulip", TULIP),
            "theirs": await _skin("Elsewhere", "Somepoop", OTHER, tier="C", avg=2.7, votes=2),
        }
        await _votes(ids["hot dog"], 6, 6, 6, 5, 6)
        await _votes(ids["new"], 6, 5)
        return ids

    return run(go())


# --- queries ---------------------------------------------------------------------


@needs_db
def test_summary_for_a_member(data):
    s = run(subs.summary(subs.Scope(submitter_id=TULIP)))
    assert (s.total, s.in_voting, s.finished, s.on_bonkverse) == (4, 1, 3, 1)
    assert s.tiers == {"S": 1, "B": 1, "A": 1}
    assert s.average == pytest.approx((5.8 + 3.9 + 4.6) / 3)
    assert s.votes == 7
    assert s.best["skin_name"] == "Hot Dog"


@needs_db
def test_creator_scope_ignores_case_and_spans_submitters(data):
    ids = run(subs.skin_ids(subs.Scope(creator="THE SAUCY TULIP")))
    assert ids == [data["new"], data["tulip"], data["hot dog"]]  # newest first
    assert run(subs.summary(subs.Scope(creator="somepoop"))).total == 2


@needs_db
def test_find_by_number_then_name(data):
    mine = subs.Scope(submitter_id=TULIP)
    assert run(subs.find(mine, f"#{data['reality']}")) == data["reality"]
    assert run(subs.find(mine, "hot dog")) == data["hot dog"]  # exact name beats "Hot Dog 2"
    assert run(subs.find(mine, "dog 2")) == data["new"]
    assert run(subs.find(mine, f"{data['theirs']}")) is None  # someone else's skin
    assert run(subs.find(mine, "100%")) is None


@needs_db
def test_detail_has_a_live_tier_while_voting(data):
    live = run(subs.detail(data["new"]))
    assert live["breakdown"] == {"S": 1, "A": 1, "B": 0, "C": 0, "D": 0, "F": 0}
    assert (live["current_tier"], live["current_average"]) == ("S", 5.5)  # 5.5 rounds up to S

    done = run(subs.detail(data["hot dog"]))
    assert "current_tier" not in done
    assert done["live_votes"] == 5


@needs_db
def test_autocomplete_names(data):
    choices = run(subs.name_choices(subs.Scope(submitter_id=TULIP), "dog"))
    assert [c[1] for c in choices] == [data["new"], data["hot dog"]]
    assert choices[0][0] == f"Hot Dog 2 · #{data['new']}"


@needs_db
def test_creator_autocomplete_merges_case(data):
    names = [c.name for c in run(browse.creator_choices("tulip"))]
    assert len(names) == 1 and names[0].casefold() == "the saucy tulip"


# --- cards ------------------------------------------------------------------------


def row(**changes):
    base = {
        "id": 7, "skin_name": "Hot Dog", "creator": "The Saucy Tulip", "finalized": True,
        "ends_at": ENDS, "ranktier": "S", "average_score": 5.8, "total_votes": 5,
        "bonkverse_skin_id": 10, "skin_url": "https://bonkleagues.io/s/x", "live_votes": 5,
    }
    return {**base, **changes}


def test_list_lines():
    assert cards.submission_line(row()) == "`#7` **Hot Dog** · 🟣 S · 5.80 · 5 votes"
    assert cards.submission_line(row(finalized=False, live_votes=1)) == "`#7` **Hot Dog** · 🗳️ voting, 1 vote so far"
    assert cards.submission_line(row(total_votes=0)) == "`#7` **Hot Dog** · no votes"


def test_list_card_stats():
    s = subs.Summary(total=4, in_voting=1, finished=3, tiers={"S": 1, "A": 1, "B": 1},
                     average=4.77, votes=7, best={"skin_name": "Hot Dog", "ranktier": "S", "average_score": 5.8})
    embed = cards.submissions_card("Your submissions", s, [row()], 0, 2)
    lines = embed.description.splitlines()
    assert lines[0] == "**4** skins · 7 votes received · 1 in voting"
    assert lines[1] == "Average **4.77** · Best: **Hot Dog** (S, 5.80)"
    assert lines[2] == "🟣 S 1 · 🔵 A 1 · 🟢 B 1 · 🟡 C 0 · 🟠 D 0 · 🔴 F 0"
    assert embed.footer.text == "Page 1/2 · Pick a skin below for details"


def test_detail_card():
    skin = row(submitter_id=TULIP, breakdown={"S": 4, "A": 1, "B": 0, "C": 0, "D": 0, "F": 0})
    embed = cards.submission_card(
        skin, position=0, total=4, site_url="https://bonkverse.io/skins/detail/10",
        image_url="https://bonkverse.io/media/skins/10.png", vote_url="https://discord.com/channels/1/2/3",
        show_submitter=True,
    )
    f = {x.name: x.value for x in embed.fields}
    assert embed.description == f"by **The Saucy Tulip** · submitted by <@{TULIP}>"
    assert f["Result"] == "🟣 **S Tier** · 5.80 average from 5 votes"
    assert f["Links"] == (
        "[Bonkverse](https://bonkverse.io/skins/detail/10) · "
        "[BonkLeagues](https://bonkleagues.io/s/x) · [Vote post](https://discord.com/channels/1/2/3)"
    )
    assert embed.image.url.endswith("/10.png")
    assert embed.footer.text == "Skin #7 · 1 of 4"


def test_detail_while_voting():
    skin = row(finalized=False, ends_at=ENDS + timedelta(hours=5), current_tier="A",
               current_average=5.5, live_votes=2,
               breakdown={"S": 1, "A": 1, "B": 0, "C": 0, "D": 0, "F": 0})
    f = {x.name: x.value for x in cards.submission_card(
        skin, position=1, total=4, site_url=None, image_url=None, vote_url=None, show_submitter=False
    ).fields}
    assert f["🗳️ In voting"].endswith("Right now: **A** (5.50) from 2 votes")
    assert f["Links"] == "[BonkLeagues](https://bonkleagues.io/s/x)"


# --- browser ------------------------------------------------------------------------


def test_opening_a_skin_moves_the_list_to_its_page():
    async def go():
        view = browse.SubmissionsBrowser(
            1, scope=None, who="x", ids=list(range(100, 75, -1)), summary=None,
            guild_id=None, show_submitter=False,
        )
        view.open_at(13)
        first = (view.mode, view.index, view.page)
        view.open_at(99)
        return first, view.index

    first, clamped = asyncio.run(go())
    assert first == ("detail", 13, 1)
    assert clamped == 24  # clamped to the last skin


def test_scope_choices():
    class Who:
        def __init__(self, id, name="Member"):
            self.id, self.display_name = id, name

    interaction = type("I", (), {"user": Who(1)})()
    assert browse.scope_for(interaction, None, None)[1] == "Your submissions"
    assert browse.scope_for(interaction, Who(1), None)[1] == "Your submissions"
    scope, who, show = browse.scope_for(interaction, Who(2, "Tulip"), None)
    assert (scope.submitter_id, who, show) == (2, "Tulip's submissions", False)
    scope, who, show = browse.scope_for(interaction, None, " Somepoop ")
    assert (scope.creator, who, show) == ("Somepoop", "Skins by Somepoop", True)


def test_both_member_and_player_is_refused():
    class Resp:
        sent = None

        def is_done(self):
            return False

        async def send_message(self, **kw):
            self.sent = kw

    interaction = type("I", (), {"user": type("U", (), {"id": 1})(), "response": Resp()})()
    member = type("M", (), {"id": 2, "display_name": "x"})()
    asyncio.run(browse.Browse.submissions.callback(browse.Browse(), interaction, member, "Somepoop", None))
    assert interaction.response.sent["ephemeral"] is True
    assert isinstance(interaction.response.sent["embed"], discord.Embed)
