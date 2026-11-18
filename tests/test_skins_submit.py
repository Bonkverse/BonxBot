"""/submit_skin with linked accounts (features/skins/commands.py).

Discord is faked; database and Bonkverse calls are faked or skipped.
"""

import asyncio

import discord
import pytest

from features.accounts import service as accounts
from features.skins import commands
from services import bonkverse_client

TULIP = 1009876543210987654
DATA = {"name": "Reality.exe", "author": "Somepoop", "skinCode": "CODE", "finalUrl": "https://x"}


class FakeResponse:
    def __init__(self):
        self.sent = None
        self.edited = None
        self.deferred = False
        self.modal = None

    async def send_message(self, content=None, **kwargs):
        self.sent = {"content": content, **kwargs}

    def is_done(self):
        return self.sent is not None or self.edited is not None or self.deferred

    async def edit_message(self, **kwargs):
        self.edited = kwargs

    async def defer(self):
        self.deferred = True

    async def send_modal(self, modal):
        self.modal = modal


class FakeFollowup:
    def __init__(self):
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append({"content": content, **kwargs})


class FakeInteraction:
    def __init__(self, data=None):
        self.user = type("U", (), {"id": TULIP})()
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self.data = data or {}


def labels(view):
    return [item.label for item in view.children if isinstance(item, discord.ui.Button)]


def picker(view):
    found = [item for item in view.children if isinstance(item, discord.ui.Select)]
    return found[0] if found else None


def click(view, label):
    [button] = [b for b in view.children if isinstance(b, discord.ui.Button) and b.label == label]
    interaction = FakeInteraction()
    asyncio.get_event_loop().run_until_complete(button.callback(interaction))
    return interaction


@pytest.fixture
def loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


def make_view(loop, author="Somepoop", mine=("The Saucy Tulip",)):
    submitted = []

    async def submit(interaction, data):
        submitted.append(dict(data))

    async def build():
        return commands.SubmitPreviewView(
            skin_data={**DATA, "author": author, "original_link": "https://bonkleagues.io/s/3BMOQWd"},
            user_id=TULIP,
            my_accounts=list(mine),
            submit_callback=submit,
        )

    return loop.run_until_complete(build()), submitted


# --- not linked -------------------------------------------------------------------------


def test_not_linked_is_told_to_link_privately(loop, monkeypatch):
    async def none(discord_id):
        return []

    monkeypatch.setattr(accounts, "accounts_of", none)
    interaction = FakeInteraction()
    cog = commands.Skins(bot=None)

    loop.run_until_complete(commands.Skins.submit_skin.callback(cog, interaction, "https://bonkleagues.io/s/x"))

    sent = interaction.response.sent
    assert sent["ephemeral"] is True
    assert sent["embed"].title == "🔗 Link your bonk.io account first"
    assert "`/link`" in sent["embed"].description
    assert interaction.response.deferred is False  # BonkLeagues never fetched


# --- the preview --------------------------------------------------------------------------


def test_own_skin_goes_straight_to_submit(loop):
    view, submitted = make_view(loop, author="the saucy tulip")

    assert labels(view) == ["✅ Submit", "✏️ Edit name", "✖️ Cancel"]
    embed = view.embed()
    assert embed.title == "Ready to submit?"
    assert [f.name for f in embed.fields] == ["Skin", "Creator"]
    assert embed.fields[0].value.startswith("[Reality.exe](")
    assert embed.fields[1].value == "The Saucy Tulip (you)"

    click(view, "✅ Submit")
    assert submitted[0]["creator"] == "The Saucy Tulip"
    assert submitted[0]["creator_source"] == "submitter"


def test_no_author_credits_me(loop):
    view, _ = make_view(loop, author=None)

    assert view.decision == ("The Saucy Tulip", "submitter")
    assert labels(view) == ["✅ Submit", "✏️ Edit name", "✖️ Cancel"]


def test_someone_elses_skin_asks_first(loop):
    view, _ = make_view(loop)

    assert labels(view) == ["✅ I made this", "🎨 Made by Somepoop", "✖️ Cancel"]
    embed = view.embed()
    assert embed.title == "Who made this skin?"
    assert "BonkLeagues credits **Reality.exe** to **Somepoop**. Is that you?" in embed.description
    assert "Only a skin's creator can edit or delete it" in embed.description


def test_made_by_them_credits_the_author(loop):
    view, submitted = make_view(loop)

    interaction = click(view, "🎨 Made by Somepoop")
    assert interaction.response.edited["embed"].fields[1].value == "Somepoop (BonkLeagues author)"
    assert labels(view) == ["✅ Submit", "✏️ Edit name", "↩️ Who made it?", "✖️ Cancel"]

    click(view, "✅ Submit")
    assert (submitted[0]["creator"], submitted[0]["creator_source"]) == ("Somepoop", "bonkleagues")


def test_i_made_this_credits_me_and_can_be_changed(loop):
    view, _ = make_view(loop)

    click(view, "✅ I made this")
    assert view.decision == ("The Saucy Tulip", "submitter")

    click(view, "↩️ Who made it?")
    assert labels(view) == ["✅ I made this", "🎨 Made by Somepoop", "✖️ Cancel"]


def test_alts_can_pick_which_account_is_credited(loop):
    view, submitted = make_view(loop, mine=("The Saucy Tulip", "Tulip Alt"))

    assert picker(view) is not None
    assert [o.value for o in picker(view).options] == ["The Saucy Tulip", "Tulip Alt"]
    interaction = FakeInteraction(data={"values": ["Tulip Alt"]})
    loop.run_until_complete(picker(view).callback(interaction))

    click(view, "✅ I made this")
    click(view, "✅ Submit")
    assert submitted[0]["creator"] == "Tulip Alt"


def test_no_account_picker_when_crediting_the_author(loop):
    view, _ = make_view(loop, mine=("The Saucy Tulip", "Tulip Alt"))

    click(view, "🎨 Made by Somepoop")

    assert picker(view) is None


def test_author_is_my_alt_credits_the_alt_without_asking(loop):
    view, _ = make_view(loop, author="TULIP ALT", mine=("The Saucy Tulip", "Tulip Alt"))

    assert view.decision == ("Tulip Alt", "submitter")
    assert picker(view) is None
    assert labels(view) == ["✅ Submit", "✏️ Edit name", "✖️ Cancel"]


@pytest.mark.parametrize("author", ["Somepoop", None])
def test_cancel_drops_the_submission(loop, author):
    view, submitted = make_view(loop, author=author)

    interaction = click(view, "✖️ Cancel")

    edited = interaction.response.edited
    assert edited["view"] is None  # buttons gone
    assert edited["embed"].title == "Submission cancelled"
    assert edited["embed"].description == "**Reality.exe** wasn't submitted."
    assert view.is_finished()
    assert submitted == []


def test_edit_changes_only_the_name(loop):
    view, _ = make_view(loop, author=None)

    interaction = click(view, "✏️ Edit name")

    modal = interaction.response.modal
    assert modal.children == [modal.name_input]  # no creator field any more


def test_others_cant_use_the_buttons(loop):
    view, _ = make_view(loop)
    stranger = FakeInteraction()
    stranger.user = type("U", (), {"id": 1})()

    allowed = loop.run_until_complete(view.interaction_check(stranger))

    assert allowed is False
    assert stranger.response.sent["ephemeral"] is True
    assert "/submit_skin" in stranger.response.sent["embed"].description


def test_long_author_names_fit_the_button(loop):
    view, _ = make_view(loop, author="x" * 100)

    assert len(labels(view)[1]) == 80


# --- what reaches Bonkverse -----------------------------------------------------------------


def test_client_sends_creator_source(loop, monkeypatch):
    sent = {}

    class FakeSession:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def post(self, url, json, headers):
            sent.update(json)
            return FakeResp()

    class FakeResp:
        status = 201

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def json(self, content_type=None):
            return {"skin": {"id": 5}}

    monkeypatch.setattr(bonkverse_client.aiohttp, "ClientSession", FakeSession)

    result = loop.run_until_complete(
        bonkverse_client.submit_skin(
            link="l", name="n", creator="Somepoop", skin_code="c",
            discord_user_id=TULIP, bonx_skin_id=1, creator_source="bonkleagues",
        )
    )

    assert result.ok
    assert sent["creator_source"] == "bonkleagues"
    assert sent["discord_user_id"] == TULIP

