"""/replays (features/replays/search.py) and the replay feed
(features/replays/feed.py), with their clients. The replay site and the video
service are faked; the feed's database tests need TEST_DATABASE_URL."""

import asyncio
from types import SimpleNamespace

import discord
import pytest
from aiohttp import web

from features.replays import feed, search
from services import replay_renderer as renderer
from services import replay_site as site
from tests.test_accounts_service import URL, _sql, fresh_db  # noqa: F401

ROW = {
    "id": "1234567", "cycle": 6, "mapid": "77", "mapname": "Simple 1v1", "mode": "ard",
    "frames": 450, "estimated_day": "2026-11-01",
    "players": [
        {"username": "The Saucy Tulip", "team": 2, "guest": False},
        {"username": "Somepoop", "team": 3, "guest": False},
    ],
}


def rows(n):
    return [{**ROW, "id": str(1000 + i)} for i in range(n)]


# --- the site client -------------------------------------------------------------------


def test_search_params():
    params = site.search_params(players=["A", "B"], match="all", map_="Simple", mode="ard", oldest=True, page=3)
    assert params == [
        ("with", "A"), ("with", "B"), ("match", "all"), ("sort", "oldest"), ("page", "3"),
        ("map", "Simple"), ("mode", "ard"),
    ]


def serve(routes):
    async def start():
        app = web.Application()
        for method, path, handler in routes:
            app.router.add_route(method, path, handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site_ = web.TCPSite(runner, "127.0.0.1", 0)
        await site_.start()
        return runner, f"http://127.0.0.1:{site_._server.sockets[0].getsockname()[1]}"
    return start


def test_clients_against_stand_in_servers(monkeypatch):
    seen = {}

    async def api_search(request):
        seen["with"] = request.query.getall("with")
        return web.json_response({"results": rows(2), "total": 2, "capped": False})

    async def replays_with(request):
        body = await request.json()
        seen["names"], seen["after"] = body["names"], body["after"]
        return web.json_response({"replays": rows(1), "scanned_to": {"cycle": 6, "id": "1000"}, "more": False})

    async def run():
        runner, base = await serve([
            ("GET", "/api/search", api_search), ("POST", "/replays/with", replays_with),
        ])()
        monkeypatch.setattr(site, "REPLAY_SITE_URL", base)
        monkeypatch.setattr(renderer, "RENDER_URL", base)
        monkeypatch.setattr(renderer, "RENDER_TOKEN", "t")
        try:
            return (
                await site.search(site.search_params(players=["A", "B"])),
                await renderer.replays_with(["a"], {"cycle": 6, "id": "1"}),
            )
        finally:
            await runner.cleanup()

    found, fresh = asyncio.run(run())
    assert (found.total, len(found.results), seen["with"]) == (2, 2, ["A", "B"])
    assert (fresh.replays[0]["id"], fresh.scanned_to) == ("1000", {"cycle": 6, "id": "1000"})
    assert (seen["names"], seen["after"]) == (["a"], {"cycle": 6, "id": "1"})


# --- results ---------------------------------------------------------------------------


def test_result_line():
    line = search.result_line(ROW, {"the saucy tulip"})
    first, second = line.split("\n")
    assert first == (
        "[`6:1234567`](https://replays.bonkverse.io/watch/6/1234567) **Simple 1v1**"
        " · Death Arrows · 0:15 · ~2026-11-01"
    )
    assert second == "🔴**The Saucy Tulip**, 🔵Somepoop"


def test_pages_of_ten_from_site_pages_of_twenty(monkeypatch):
    asked = []

    async def fake_search(params):
        page = int(dict(params)["page"])
        asked.append(page)
        return site.SearchReply(results=rows(40)[(page - 1) * 20:page * 20], total=35)

    monkeypatch.setattr(site, "search", fake_search)

    async def go():
        view = search.ReplayResults(1, title="Your replays", query={"players": ["A"]}, highlight=set())
        await view.load()
        first = [r["id"] for r in view.rows]
        view.page = 1
        await view.load()
        second = [r["id"] for r in view.rows]
        view.page = 3
        await view.load()
        last = [r["id"] for r in view.rows]
        return first, second, last, view.pages, view.embed().footer.text

    first, second, last, pages, footer = asyncio.run(go())
    assert (first[0], second[0], last[0]) == ("1000", "1010", "1030")
    assert asked == [1, 1, 2]
    assert pages == 4
    assert footer == "35 replays · Page 4/4 · Pick one to watch it as a video"


# --- whose replays ---------------------------------------------------------------------


def test_whose(monkeypatch):
    async def accounts_of(discord_id):
        return [{"bonk_username": "The Saucy Tulip"}, {"bonk_username": "Tulip Alt"}] if discord_id == 1 else []

    monkeypatch.setattr(search.accounts, "accounts_of", accounts_of)
    me = SimpleNamespace(id=1, display_name="Jorge", mention="<@1>")
    other = SimpleNamespace(id=2, display_name="Sam", mention="<@2>")
    interaction = SimpleNamespace(user=me)

    assert asyncio.run(search.whose(interaction, None, None)) == (["The Saucy Tulip", "Tulip Alt"], "Your replays")
    assert asyncio.run(search.whose(interaction, None, " Somepoop ")) == (["Somepoop"], "Replays with Somepoop")
    assert "hasn't linked" in asyncio.run(search.whose(interaction, other, None))
    assert "/link" in asyncio.run(search.whose(SimpleNamespace(user=other), None, None))


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


class FakeInteraction:
    def __init__(self):
        self.user = SimpleNamespace(id=1, display_name="Jorge", mention="<@1>")
        self.response = FakeResponse()
        self.followup = SimpleNamespace(sent=None)
        self.deleted = False

        async def send(**kwargs):
            self.followup.sent = kwargs

        self.followup.send = send

    async def delete_original_response(self):
        self.deleted = True

    async def original_response(self):
        return None


def test_against_uses_the_main_account_and_both_names(monkeypatch):
    asked = []

    async def accounts_of(discord_id):
        return [{"bonk_username": "The Saucy Tulip"}, {"bonk_username": "Tulip Alt"}]

    async def fake_search(params):
        asked.append(params)
        return site.SearchReply(results=rows(1), total=1)

    monkeypatch.setattr(search.accounts, "accounts_of", accounts_of)
    monkeypatch.setattr(site, "search", fake_search)
    interaction = FakeInteraction()
    asyncio.run(search.ReplaySearch.replays.callback(
        search.ReplaySearch(), interaction, None, None, "Simple", None, "Somepoop", False
    ))
    params = asked[0]
    assert [v for k, v in params if k == "with"] == ["The Saucy Tulip", "Somepoop"]
    assert ("match", "all") in params and ("map", "Simple") in params
    assert interaction.followup.sent["embed"].title == "🎬 Your replays vs Somepoop on Simple"


def test_nothing_found_is_private(monkeypatch):
    async def none(params):
        return site.SearchReply(results=[], total=0)

    monkeypatch.setattr(site, "search", none)
    interaction = FakeInteraction()
    asyncio.run(search.ReplaySearch.replays.callback(
        search.ReplaySearch(), interaction, "Nobody", None, None, None, None, False
    ))
    assert interaction.followup.sent["ephemeral"] is True
    assert interaction.followup.sent["embed"].title == "No replays"


# --- the feed ---------------------------------------------------------------------------


def test_hourly_cap():
    now = [0.0]
    cap = feed.HourlyCap(2, clock=lambda: now[0])
    assert [cap.allow(), cap.allow(), cap.allow()] == [True, True, False]
    now[0] = 3601
    assert cap.allow() is True


def test_who_played_names_linked_players_only():
    linked = {"the saucy tulip": 42}
    replay = {"players": [
        {"username": "The Saucy Tulip", "guest": False},
        {"username": "Somepoop", "guest": False},
        {"username": "the saucy tulip", "guest": True},  # a guest using the name
    ]}
    assert feed.who_played(replay, linked) == "🎮 Played by <@42> (The Saucy Tulip)"
    assert feed.who_played({"players": []}, linked) == ""


class FakeChannel:
    def __init__(self):
        self.posts = []

    async def send(self, **kwargs):
        self.posts.append(kwargs)


@pytest.mark.skipif(not URL, reason="set TEST_DATABASE_URL to run database tests")
def test_feed_starts_from_now_then_posts_and_moves_on(monkeypatch):
    calls = []
    script = [
        renderer.FeedReply(replays=[], scanned_to={"cycle": 6, "id": "100"}),
        renderer.FeedReply(replays=[{**ROW, "id": "101"}, {**ROW, "id": "102"}, {**ROW, "id": "103"}],
                           scanned_to={"cycle": 6, "id": "150"}),
    ]

    async def replays_with(names, after, limit=3):
        calls.append((names, after))
        return script.pop(0)

    async def video_post(replay_id, cycle):
        if replay_id == "102":
            return "render failed"
        return discord.Embed(title=f"replay {replay_id}"), discord.File(__file__)

    monkeypatch.setattr(renderer, "replays_with", replays_with)
    monkeypatch.setattr(feed, "video_post", video_post)
    channel = FakeChannel()
    cap = feed.HourlyCap(10)

    async def go():
        await _sql(
            "CREATE TABLE discord_bonk_accounts (discord_user_id BIGINT, bonk_username TEXT, is_main BOOLEAN)"
        )
        await _sql("INSERT INTO discord_bonk_accounts VALUES (42, 'The Saucy Tulip', TRUE)")
        await feed.ensure_schema()
        first = await feed.tick(channel, cap)
        second = await feed.tick(channel, cap)
        return first, second, await feed.load_after()

    first, second, after = asyncio.get_event_loop().run_until_complete(go())
    assert (first, second) == (0, 2)  # 102 failed to render and was skipped
    assert calls[0] == (["the saucy tulip"], None)
    assert calls[1][1] == {"cycle": 6, "id": "100"}
    assert after == {"cycle": 6, "id": "150"}
    assert [p["embed"].title for p in channel.posts] == ["replay 101", "replay 103"]
    assert channel.posts[0]["content"] == "🎮 Played by <@42> (The Saucy Tulip)"
    assert channel.posts[0]["allowed_mentions"].users is False  # never pings


@pytest.mark.skipif(not URL, reason="set TEST_DATABASE_URL to run database tests")
def test_feed_cap_skips_extra_replays_and_moves_past_them(monkeypatch):
    async def replays_with(names, after, limit=3):
        return renderer.FeedReply(replays=rows(3), scanned_to={"cycle": 6, "id": "999"})

    async def video_post(replay_id, cycle):
        return discord.Embed(title=replay_id), discord.File(__file__)

    monkeypatch.setattr(renderer, "replays_with", replays_with)
    monkeypatch.setattr(feed, "video_post", video_post)
    channel = FakeChannel()

    async def go():
        await _sql("CREATE TABLE discord_bonk_accounts (discord_user_id BIGINT, bonk_username TEXT)")
        await feed.ensure_schema()
        await feed.save_after({"cycle": 6, "id": "1"})
        posted = await feed.tick(channel, feed.HourlyCap(1))
        return posted, await feed.load_after()

    assert asyncio.get_event_loop().run_until_complete(go()) == (1, {"cycle": 6, "id": "999"})
