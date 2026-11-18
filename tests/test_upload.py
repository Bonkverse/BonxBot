"""Upload-only (features/site/upload.py): /upload, Apps → Upload to Bonkverse,
"@BonxBot upload", and the "Who made this skin?" question when BonkLeagues
credits someone else. BonkLeagues, bonkverse.io and accounts are faked."""

import asyncio
from types import SimpleNamespace

import discord
import pytest

from features.site import collect, upload
from services import bonkverse_client as site
from tests.test_collect import BOT, FakeMessage

MINE = ["The Saucy Tulip", "Tulip Alt"]
AUTHORS = {  # link -> who BonkLeagues says made it
    "https://bonkleagues.io/s/somepoop": "Somepoop",
    "https://bonkleagues.io/s/mine": "the saucy tulip",
    "https://bonkleagues.io/s/blank": "",
    "https://bonkleagues.io/s/old": "the saucy tulip",
    "https://bonkleagues.io/s/bad": "the saucy tulip",
}


def run(coro):
    return asyncio.run(coro)


def ref(name):
    return collect.SkinRef(link=f"https://bonkleagues.io/s/{name}")


@pytest.fixture
def world(monkeypatch):
    sent = []

    async def accounts_of(discord_id):
        return [{"bonk_username": n} for n in MINE] if discord_id == 1 else []

    async def fetch(link):
        if "gone" in link:
            return None
        return {"name": "Reality.exe", "author": AUTHORS.get(link), "skinCode": "CODE"}

    async def submit_skin(**kwargs):
        sent.append(kwargs)
        if "bad" in kwargs["link"]:
            return site.SubmitResult(error="502: render_failed")
        return site.SubmitResult(bonkverse_skin_id=5653, created="old" not in kwargs["link"])

    async def get_skin(skin_id):
        return site.SiteReply(error="unreachable")  # falls back to what we know

    monkeypatch.setattr(upload.accounts, "accounts_of", accounts_of)
    monkeypatch.setattr(upload, "fetch_skin_data", fetch)
    monkeypatch.setattr(site, "submit_skin", submit_skin)
    monkeypatch.setattr(site, "get_skin", get_skin)
    return sent


# --- the rule (same as /submit_skin and the website) -----------------------------------


@pytest.mark.parametrize(
    "author, chosen, made_it, expected",
    [
        ("the saucy tulip", None, None, ("The Saucy Tulip", "submitter")),  # your own: no question
        ("TULIP ALT", None, None, ("Tulip Alt", "submitter")),
        ("", None, None, ("The Saucy Tulip", "submitter")),  # BonkLeagues lists nobody
        ("Somepoop", None, None, None),  # someone else: ask
        ("Somepoop", "Tulip Alt", True, ("Tulip Alt", "submitter")),
        ("Somepoop", None, False, ("Somepoop", "bonkleagues")),
    ],
)
def test_decide(author, chosen, made_it, expected):
    pending = upload.Pending(1, "l", {"author": author}, MINE)
    assert pending.decide(chosen, made_it) == expected


# --- uploading ---------------------------------------------------------------------------


def test_your_own_skin_uploads_without_a_question(world):
    embed, view = run(upload.start(1, ref("mine")))
    assert view is None
    assert embed.title == "⬆️ Uploaded to bonkverse.io"
    assert embed.description == "**Reality.exe** by **The Saucy Tulip** (you)"
    assert (world[0]["creator"], world[0]["creator_source"], world[0]["bonx_skin_id"]) == (
        "The Saucy Tulip", "submitter", None,
    )


def test_someone_elses_skin_asks_first_and_uploads_nothing_yet(world):
    async def go():
        return await upload.start(1, ref("somepoop"))

    embed, view = run(go())
    assert embed.title == "Who made this skin?"
    assert "BonkLeagues credits **Reality.exe** to **Somepoop**. Is that you?" in embed.description
    assert isinstance(view, upload.CreatorCheck)
    assert world == []


class FakeResponse:
    def __init__(self):
        self.edited = []
        self.sent = None
        self.type = None

    def is_done(self):
        return self.type is not None

    async def edit_message(self, **kwargs):
        self.edited.append(kwargs)
        self.type = "update"

    async def send_message(self, **kwargs):
        self.sent = kwargs
        self.type = "sent"


class FakeInteraction:
    def __init__(self, user_id=1, values=None):
        self.user = SimpleNamespace(id=user_id)
        self.response = FakeResponse()
        self.final = None
        self.data = {"values": values or []}

    async def edit_original_response(self, **kwargs):
        self.final = kwargs


def answer(button_label, *, pick=None):
    async def go():
        _, view = await upload.start(1, ref("somepoop"))
        if pick:
            view.picker._values = [pick]
            await view._pick(FakeInteraction(values=[pick]))
        button = next(b for b in view.children if (getattr(b, "label", None) or "").startswith(button_label))
        interaction = FakeInteraction()
        await button.callback(interaction)
        return interaction, view

    return run(go())


def test_made_by_them_credits_the_author(world):
    interaction, view = answer("🎨 Made by Somepoop")
    assert (world[0]["creator"], world[0]["creator_source"]) == ("Somepoop", "bonkleagues")
    assert interaction.final["embed"].title == "⬆️ Uploaded to bonkverse.io"
    assert interaction.final["view"] is None
    assert all(item.disabled for item in view.children)  # greyed out before uploading


def test_i_made_this_credits_the_account_picked(world):
    answer("✅ I made this", pick="Tulip Alt")
    assert (world[0]["creator"], world[0]["creator_source"]) == ("Tulip Alt", "submitter")


def test_cancel_uploads_nothing(world):
    interaction, _ = answer("✖️ Cancel")
    assert world == []
    assert interaction.response.edited[-1]["embed"].title == "Upload cancelled"


def test_only_the_uploader_can_answer(world):
    async def go():
        _, view = await upload.start(1, ref("somepoop"))
        stranger = FakeInteraction(user_id=2)
        return await view.interaction_check(stranger), stranger.response.sent

    allowed, sent = run(go())
    assert allowed is False
    assert "/upload" in sent["embed"].description


def test_problems(world):
    assert run(upload.start(2, ref("mine")))[0].title == "Link your bonk.io account first"
    assert run(upload.start(1, collect.SkinRef(skin_id=5)))[0].title == "Not a BonkLeagues link"
    assert run(upload.start(1, ref("gone")))[0].description.startswith("BonkLeagues didn't send that skin")
    assert run(upload.start(1, ref("bad")))[0].description == "bonkverse.io couldn't draw that skin."
    assert run(upload.start(1, ref("old")))[0].title == "Already on bonkverse.io"


# --- "@BonxBot upload" --------------------------------------------------------------------


def test_mention_uploads_yours_and_asks_about_theirs(world, monkeypatch):
    async def no_collecting(*args):
        raise AssertionError("upload mentions must not collect")

    monkeypatch.setattr(collect, "add_many", no_collecting)
    message = FakeMessage("<@999> upload https://bonkleagues.io/s/somepoop https://bonkleagues.io/s/mine")
    assert run(collect.handle_mention(message, BOT)) is True

    question, done = message.replies
    assert question["embed"].title == "Who made this skin?"
    assert isinstance(question["view"], discord.ui.View)
    assert [e.title for e in done["embeds"]] == ["⬆️ Uploaded to bonkverse.io"]
    assert [s["link"] for s in world] == ["https://bonkleagues.io/s/mine"]


@pytest.mark.parametrize(
    "text, wants",
    [("<@999> upload this", True), ("<@999> UPLOAD", True), ("<@999> uploads", False), ("<@999> add", False)],
)
def test_upload_word(text, wants):
    assert collect.wants_upload(SimpleNamespace(content=text)) is wants
