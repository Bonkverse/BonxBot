"""/skin, /player and /mapdecode (features/site/) and the Bonkverse read
client (services/bonkverse_client.py). The client test runs a tiny local
HTTP server standing in for bonkverse.io; nothing leaves the machine."""

import asyncio

import discord
import pytest
from aiohttp import web

from features.site import cards, commands
from services import bonkverse_client as site
from utils import ui

SKIN = {
    "id": 5653, "name": "Reality.exe", "creator": "Somepoop",
    "url": "https://bonkverse.io/skins/detail/5653",
    "image_url": "https://bonkverse.io/media/skins/5653.png",
    "upvotes": 40, "downvotes": 3, "favorites": 12, "created_at": "2025-03-01T12:00:00+00:00",
}


def fields(embed):
    return {f.name: f.value for f in embed.fields}


# --- cards -----------------------------------------------------------------------


def test_skin_card():
    embed = cards.skin_card(SKIN)
    assert (embed.title, embed.url) == ("Reality.exe", SKIN["url"])
    assert embed.image.url == SKIN["image_url"]
    assert fields(embed)["On bonkverse.io"] == "👍 40 · 👎 3 · ⭐ 12 · score +37"
    assert embed.footer.text == "Skin #5653"


def test_search_title():
    assert cards.search_title("hot dog", 5) == "Top match for “hot dog” · 4 more below"
    assert cards.search_title("hot dog", 1) == "Top match for “hot dog”"


def test_player_card_with_discord_link():
    player = {
        "username": "The Saucy Tulip", "status": "found", "games": 1234,
        "level": {"value": 41, "peak": 41, "seen_at": "2026-11-01T00:00:00.000Z"},
        "profile_url": "https://bonkverse.io/players/The%20Saucy%20Tulip/",
        "account": {"dbid": 123, "joined": "2019-06-02"}, "skins": 3,
        "skin": {"thumbnail_url": "https://bonkverse.io/thumb.png"},
    }
    embed = cards.player_card(player, discord_id=42)
    f = fields(embed)
    assert f["Level"].startswith("**41**\nas of <t:")
    assert f["Games in replays"] == "1,234"
    assert f["Skins on Bonkverse"] == "3"
    assert f["Account made"] == "around 2019-06"
    assert f["Discord"] == "<@42>"
    assert embed.thumbnail.url == "https://bonkverse.io/thumb.png"


def test_private_level_and_replay_less_creator():
    private = cards.player_card({"username": "x", "status": "private", "skins": 0})
    assert fields(private)["Level"] == "Hidden by the player"

    creator = cards.player_card({"username": "Somepoop", "status": "not_found", "skins": 1})
    assert "Not seen in replays yet" in creator.description
    assert "Level" not in fields(creator)


def test_map_card():
    embed = cards.map_card({
        "name": "Simple 1v1", "author": "Chaz", "mode": "ard", "date": "2020-01-01T00:00",
        "map_id": 42, "upvotes": 10, "downvotes": 2, "shapes": 3, "bodies": 1, "joints": 0,
        "spawns": 2, "cap_zones": 0,
    })
    f = fields(embed)
    assert (embed.title, f["Mode"], f["Votes"]) == ("Simple 1v1", "Death Arrows", "👍 10 · 👎 2")
    assert embed.footer.text == "Published 2020-01-01"


def test_problems_name_the_fix():
    assert "bonk.io map code" in cards.site_problem("missing_code")
    assert cards.site_problem("player_not_found", name="nobody").startswith("Bonkverse hasn't seen **nobody**")
    assert cards.site_problem("something_new") == cards.SITE_DOWN
    assert cards.site_problem(None) == cards.SITE_DOWN


# --- client ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, path",
    [("The Saucy Tulip", "The%20Saucy%20Tulip"), (".", "~."), ("..", "~.."), ("a/b", "a%2Fb")],
)
def test_player_path(name, path):
    assert site.player_path(name) == path


def test_client_reads_the_envelope(monkeypatch):
    seen = {}

    async def search(request):
        seen["auth"] = request.headers.get("Authorization")
        seen["q"] = request.query.get("q")
        return web.json_response({"ok": True, "data": {"query": "tulip", "skins": [SKIN]}})

    async def missing(request):
        return web.json_response({"ok": False, "error": "skin_not_found"}, status=404)

    async def rejected(request):
        body = await request.json()
        seen["code"] = body["code"]
        return web.json_response({"ok": False, "error": "map_rejected", "detail": "bad"}, status=422)

    async def broken(request):
        return web.Response(text="<html>502</html>", status=502)

    async def run():
        app = web.Application()
        app.router.add_get("/api/bot/skins/search/", search)
        app.router.add_get("/api/bot/skins/1/", missing)
        app.router.add_post("/api/bot/maps/decode/", rejected)
        app.router.add_get("/api/bot/skins/random/", broken)
        runner = web.AppRunner(app)
        await runner.setup()
        server = web.TCPSite(runner, "127.0.0.1", 0)
        await server.start()
        port = server._server.sockets[0].getsockname()[1]
        monkeypatch.setattr(site, "BONKVERSE_API_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setattr(site, "BONXBOT_API_TOKEN", "t0ken")
        try:
            return (
                await site.search_skins("tulip"),
                await site.get_skin(1),
                await site.decode_map("abc"),
                await site.random_skin(),
            )
        finally:
            await runner.cleanup()

    found, missing_skin, map_reply, down = asyncio.run(run())
    assert found.ok and found.data["skins"][0]["id"] == 5653
    assert (seen["auth"], seen["q"], seen["code"]) == ("Bearer t0ken", "tulip", "abc")
    assert (missing_skin.ok, missing_skin.error) == (False, "skin_not_found")
    assert (map_reply.error, map_reply.detail) == ("map_rejected", "bad")
    assert down.error == "unreachable"


def test_client_when_the_site_is_unreachable(monkeypatch):
    monkeypatch.setattr(site, "BONKVERSE_API_URL", "http://127.0.0.1:9")  # nothing listens
    assert asyncio.run(site.random_skin()).error == "unreachable"


# --- commands --------------------------------------------------------------------


class FakeResponse:
    def __init__(self):
        self.sent = None
        self.type = None

    def is_done(self):
        return self.type is not None

    async def send_message(self, **kwargs):
        self.sent = kwargs
        self.type = discord.InteractionResponseType.channel_message

    async def defer(self, **kwargs):
        self.type = discord.InteractionResponseType.deferred_channel_message


class FakeFollowup:
    def __init__(self):
        self.sent = None

    async def send(self, **kwargs):
        self.sent = kwargs


class FakeInteraction:
    def __init__(self):
        self.user = type("U", (), {"id": 1})()
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self.deleted = False

    async def delete_original_response(self):
        self.deleted = True

    async def original_response(self):
        return None


def test_no_matches_is_private_even_after_thinking(monkeypatch):
    async def nothing(query, limit):
        return site.SiteReply(data={"query": query, "skins": []})

    monkeypatch.setattr(site, "search_skins", nothing)
    interaction = FakeInteraction()
    asyncio.run(commands.Site.skin_search.callback(commands.Site(), interaction, "zzzz"))

    assert interaction.deleted is True  # the public "thinking…" is removed
    assert interaction.followup.sent["ephemeral"] is True
    assert interaction.followup.sent["embed"].title == "No matches"


def test_search_shows_the_top_match_and_a_menu(monkeypatch):
    other = {**SKIN, "id": 1, "name": "Reality.exe v2"}

    async def two(query, limit):
        return site.SiteReply(data={"query": query, "skins": [SKIN, other]})

    monkeypatch.setattr(site, "search_skins", two)
    interaction = FakeInteraction()
    asyncio.run(commands.Site.skin_search.callback(commands.Site(), interaction, "reality"))

    sent = interaction.followup.sent
    assert interaction.deleted is False
    assert sent["ephemeral"] is False
    assert sent["embed"].title == "Reality.exe"
    assert [o.label for o in sent["view"].menu.options] == ["Reality.exe", "Reality.exe v2"]


def test_mapdecode_without_a_code_never_calls_the_site():
    interaction = FakeInteraction()
    asyncio.run(commands.Site.mapdecode.callback(commands.Site(), interaction, None, None))

    assert interaction.response.sent["ephemeral"] is True
    assert "bonk.io map code" in interaction.response.sent["embed"].description


def test_thinking_check_ignores_button_updates():
    interaction = FakeInteraction()
    interaction.response.type = discord.InteractionResponseType.deferred_message_update
    assert ui._thinking_in_public(interaction) is False
