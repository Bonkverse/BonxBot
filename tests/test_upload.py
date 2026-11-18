"""Upload-only (features/site/upload.py): /upload, Apps → Upload to Bonkverse,
and "@BonxBot upload". BonkLeagues, bonkverse.io and accounts are faked."""

import asyncio
from types import SimpleNamespace

import pytest

from features.site import collect, upload
from services import bonkverse_client as site
from tests.test_collect import BOT, FakeMessage

BL = "https://bonkleagues.io/s/AbC1234"
MINE = ["The Saucy Tulip", "Tulip Alt"]


@pytest.mark.parametrize(
    "author, expected",
    [
        ("the saucy tulip", ("The Saucy Tulip", "submitter")),  # your own: credited to you
        ("TULIP ALT", ("Tulip Alt", "submitter")),
        ("Somepoop", ("Somepoop", "bonkleagues")),  # someone else's: credited to them
        ("", (None, None)),  # unknown: left for its maker to claim
        (None, (None, None)),
    ],
)
def test_credit(author, expected):
    assert upload.credit(MINE, author) == expected


@pytest.fixture
def world(monkeypatch):
    sent = []

    async def accounts_of(discord_id):
        return [{"bonk_username": n} for n in MINE] if discord_id == 1 else []

    async def fetch(link):
        if "gone" in link:
            return None
        return {"name": "Reality.exe", "author": "Somepoop", "skinCode": "CODE"}

    async def submit_skin(**kwargs):
        sent.append(kwargs)
        if "bad" in kwargs["link"]:
            return site.SubmitResult(error="502: render_failed")
        return site.SubmitResult(bonkverse_skin_id=5653, created="old" not in kwargs["link"])

    async def get_skin(skin_id):
        return site.SiteReply(data={"skin": {
            "id": skin_id, "name": "Reality.exe", "creator": "Somepoop",
            "url": f"https://bonkverse.io/skins/detail/{skin_id}", "image_url": "https://x/5653.png",
        }})

    monkeypatch.setattr(upload.accounts, "accounts_of", accounts_of)
    monkeypatch.setattr(upload, "fetch_skin_data", fetch)
    monkeypatch.setattr(site, "submit_skin", submit_skin)
    monkeypatch.setattr(site, "get_skin", get_skin)
    return sent


def run(coro):
    return asyncio.run(coro)


def test_upload_credits_the_bonkleagues_author_and_records_you(world):
    worked, embed = run(upload.upload(1, collect.SkinRef(link=BL)))
    assert worked is True
    assert embed.title == "⬆️ Uploaded to bonkverse.io"
    assert embed.description == "**Reality.exe** by **Somepoop**"
    assert "/submit_skin" in embed.footer.text  # no tier vote, says how to get one
    assert world[0]["skin_code"] == "CODE"
    assert (world[0]["creator"], world[0]["creator_source"], world[0]["discord_user_id"]) == (
        "Somepoop", "bonkleagues", 1,
    )
    assert world[0]["bonx_skin_id"] is None  # not a tier submission


def test_already_on_bonkverse(world):
    _, embed = run(upload.upload(1, collect.SkinRef(link="https://bonkleagues.io/s/old")))
    assert embed.title == "Already on bonkverse.io"


def test_problems(world):
    assert run(upload.upload(2, collect.SkinRef(link=BL)))[1].title == "Link your bonk.io account first"
    assert run(upload.upload(1, collect.SkinRef(skin_id=5)))[1].title == "Not a BonkLeagues link"
    gone = run(upload.upload(1, collect.SkinRef(link="https://bonkleagues.io/s/gone")))[1]
    assert gone.description.startswith("BonkLeagues didn't send that skin")
    bad = run(upload.upload(1, collect.SkinRef(link="https://bonkleagues.io/s/bad")))[1]
    assert bad.description == "bonkverse.io couldn't draw that skin."


def test_mention_with_upload_uploads_instead_of_collecting(world, monkeypatch):
    collected = []

    async def add_many(discord_id, refs):
        collected.append(refs)
        return []

    monkeypatch.setattr(collect, "add_many", add_many)
    message = FakeMessage(f"<@999> upload {BL}")
    assert run(collect.handle_mention(message, BOT)) is True
    assert collected == []
    assert message.replies[0]["embeds"][0].title == "⬆️ Uploaded to bonkverse.io"


@pytest.mark.parametrize(
    "text, wants",
    [("<@999> upload this", True), ("<@999> UPLOAD", True), ("<@999> uploads", False), ("<@999> add", False)],
)
def test_upload_word(text, wants):
    assert collect.wants_upload(SimpleNamespace(content=text)) is wants
