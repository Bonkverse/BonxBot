"""Adding skins to a bonkverse.io collection from Discord (features/site/collect.py)
and the ⭐ Collect button on /skin cards. bonkverse.io and accounts are faked."""

import asyncio
from types import SimpleNamespace

import discord
import pytest

from features.site import collect
from features.site import commands as site_commands
from services import bonkverse_client as site

BL = "https://bonkleagues.io/s/AbC1234"
SKIN = {
    "id": 5653, "name": "Reality.exe", "creator": "Somepoop",
    "url": "https://bonkverse.io/skins/detail/5653", "image_url": "https://bonkverse.io/media/skins/5653.png",
}


# --- finding links -------------------------------------------------------------------


def test_finds_both_kinds_of_link_in_order_without_repeats():
    text = (
        f"check this <{BL}> and https://bonkverse.io/skins/detail/12, also {BL}. "
        "and https://www.bonkleagues.io/s/Other1!"
    )
    assert collect.find_skins(text) == [
        collect.SkinRef(link=BL),
        collect.SkinRef(skin_id=12),
        collect.SkinRef(link="https://www.bonkleagues.io/s/Other1"),
    ]


def test_finds_links_in_previews_and_caps_the_count():
    many = " ".join(f"https://bonkleagues.io/s/x{i}" for i in range(9))
    assert len(collect.find_skins(many)) == collect.MAX_PER_MESSAGE
    assert collect.find_skins(None, "", "nothing here") == []


@pytest.mark.parametrize(
    "given, ref",
    [
        ("#5653", collect.SkinRef(skin_id=5653)),
        (" 42 ", collect.SkinRef(skin_id=42)),
        (BL, collect.SkinRef(link=BL)),
        ("https://bonkverse.io/skins/detail/7", collect.SkinRef(skin_id=7)),
        ("hot dog", None),
    ],
)
def test_parse_one(given, ref):
    assert collect.parse_one(given) == ref


# --- adding ----------------------------------------------------------------------------


@pytest.fixture
def linked(monkeypatch):
    calls = []

    async def main_account(discord_id):
        return "The Saucy Tulip" if discord_id == 1 else None

    async def fake_collect(bonk_username, discord_user_id, *, link=None, skin_id=None):
        calls.append((bonk_username, discord_user_id, link, skin_id))
        if link and "broken" in link:
            return site.SiteReply(error="fetch_failed")
        return site.SiteReply(data={
            "skin": SKIN, "uploaded": bool(link), "already_collected": skin_id == 5653,
            "collection_size": 3, "collection_url": "https://bonkverse.io/my-profile/",
        })

    monkeypatch.setattr(collect.accounts, "main_account", main_account)
    monkeypatch.setattr(site, "collect", fake_collect)
    return calls


def test_new_link_is_uploaded_and_added(linked):
    worked, embed = asyncio.run(collect.add(1, collect.SkinRef(link=BL)))
    assert worked is True
    assert embed.title == "⭐ Added to your collection"
    assert "It wasn't on bonkverse.io yet, so it's been uploaded too." in embed.description
    assert embed.footer.text == "3 skins in your collection · bonk.io: The Saucy Tulip"
    assert linked == [("The Saucy Tulip", 1, BL, None)]


def test_already_collected(linked):
    _, embed = asyncio.run(collect.add(1, collect.SkinRef(skin_id=5653)))
    assert embed.title == "Already in your collection"


def test_needs_a_linked_account(linked):
    worked, embed = asyncio.run(collect.add(2, collect.SkinRef(link=BL)))
    assert worked is False
    assert embed.title == "Link your bonk.io account first"
    assert linked == []  # bonkverse.io never asked


def test_site_errors_explain(linked):
    _, embed = asyncio.run(collect.add(1, collect.SkinRef(link="https://bonkleagues.io/s/broken")))
    assert embed.description.startswith("BonkLeagues didn't send that skin")


def test_unlinked_is_said_once_for_many_links(linked):
    refs = [collect.SkinRef(link=f"https://bonkleagues.io/s/x{i}") for i in range(3)]
    assert len(asyncio.run(collect.add_many(2, refs))) == 1


# --- @BonxBot ---------------------------------------------------------------------------


class FakeChannel:
    def __init__(self, messages=None):
        self.messages = messages or {}

    async def fetch_message(self, message_id):
        return self.messages[message_id]

    def typing(self):
        class Typing:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        return Typing()


class FakeMessage:
    def __init__(self, content="", *, author_id=1, reference=None, channel=None, embeds=()):
        self.content = content
        self.author = SimpleNamespace(id=author_id)
        self.reference = reference
        self.channel = channel or FakeChannel()
        self.embeds = list(embeds)
        self.replies = []

    async def reply(self, **kwargs):
        self.replies.append(kwargs)


BOT = SimpleNamespace(id=999)


def test_mention_with_a_link(linked):
    message = FakeMessage(f"<@999> add {BL}")
    assert asyncio.run(collect.handle_mention(message, BOT)) is True
    assert message.replies[0]["embeds"][0].title == "⭐ Added to your collection"
    assert message.replies[0]["mention_author"] is False


def test_mention_replying_to_a_link(linked):
    original = FakeMessage(f"look at my skin {BL}", author_id=7)
    message = FakeMessage("<@999>", reference=SimpleNamespace(resolved=original, message_id=5))
    assert asyncio.run(collect.handle_mention(message, BOT)) is True
    assert linked[0][2] == BL  # added for the person who pinged, not the poster
    assert linked[0][1] == 1


def test_reply_we_cannot_read_gets_a_hint(linked):
    hidden = FakeMessage("", author_id=7)  # no Message Content intent: Discord sends no text
    channel = FakeChannel({5: hidden})
    message = FakeMessage("<@999>", reference=SimpleNamespace(resolved=None, message_id=5), channel=channel)
    assert asyncio.run(collect.handle_mention(message, BOT)) is True
    assert "Apps → Add to my collection" in message.replies[0]["embed"].description


def test_plain_mention_falls_through_to_the_greeting(linked):
    assert asyncio.run(collect.handle_mention(FakeMessage("<@999> hi"), BOT)) is False


# --- Apps menu and ⭐ Collect -------------------------------------------------------------


class FakeResponse:
    def __init__(self):
        self.sent = None
        self.type = None

    def is_done(self):
        return self.type is not None

    async def send_message(self, **kwargs):
        self.sent = kwargs
        self.type = discord.InteractionResponseType.channel_message


def test_apps_menu_without_links_is_private():
    interaction = SimpleNamespace(user=SimpleNamespace(id=1), response=FakeResponse())
    asyncio.run(collect.collect_from_message.callback(interaction, FakeMessage("no links")))
    assert interaction.response.sent["ephemeral"] is True
    assert interaction.response.sent["embed"].title == "No skin links"


def test_anyone_can_press_collect_but_not_the_rest():
    async def check():
        view = site_commands.SkinMatches(1, [SKIN, {**SKIN, "id": 1, "name": "Other"}])
        collect_id = next(iter(view.open_to_everyone))
        stranger = SimpleNamespace(user=SimpleNamespace(id=2), response=FakeResponse(),
                                   data={"custom_id": collect_id})
        menu_press = SimpleNamespace(user=SimpleNamespace(id=2), response=FakeResponse(),
                                     data={"custom_id": view.menu.custom_id})
        return await view.interaction_check(stranger), await view.interaction_check(menu_press), view

    collect_ok, menu_ok, view = asyncio.run(check())
    assert (collect_ok, menu_ok) == (True, False)
    assert view.shown_skin_id == 5653
