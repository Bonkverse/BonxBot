"""/replay (features/replays/commands.py) and the replay video service client
(services/replay_renderer.py). The service is a local stand-in server."""

import asyncio
import base64
from types import SimpleNamespace

import discord
import pytest
from aiohttp import web

from features.replays import commands as replays
from services import replay_renderer as renderer

SUMMARY = {
    "id": "1234567", "cycle": 6, "map": "Simple 1v1", "author": "Chaz", "mode": "Classic",
    "day": "2026-11-01", "seconds": 15,
    "players": [
        {"name": "FlamingFight", "team": 2, "level": 69, "guest": False},
        {"name": "Very_big_small", "team": 3, "level": 12, "guest": False},
        {"name": "Guest123", "team": 3, "level": 0, "guest": True},
    ],
}


@pytest.mark.parametrize(
    "typed, expected",
    [
        ("1234567", ("1234567", None)),
        ("#1234567", ("1234567", None)),
        ("6:1234567", ("1234567", 6)),
        ("6 / 1234567", ("1234567", 6)),
        ("https://replays.bonkverse.io/watch/6/1234567?t=3", ("1234567", 6)),
        ("hot dog", None),
    ],
)
def test_parse_replay(typed, expected):
    assert replays.parse_replay(typed) == expected


def test_card_groups_teams_and_links_to_the_replay_site():
    embed = replays.replay_card(SUMMARY, "https://replays.bonkverse.io/watch/6/1234567")
    assert embed.title == "Simple 1v1 by Chaz"
    assert embed.url == "https://replays.bonkverse.io/watch/6/1234567"
    assert embed.description.splitlines() == [
        "🔴 Red: **FlamingFight** · Lv 69",
        "🔵 Blue: **Very_big_small** · Lv 12, Guest123 (guest)",
    ]
    fields = {f.name: f.value for f in embed.fields}
    assert (fields["Mode"], fields["Length"], fields["Played"]) == ("Classic", "0:15", "~2026-11-01")
    assert embed.footer.text == "Replay #1234567 · cycle 6"


def test_free_for_all_lists_players():
    ffa = {**SUMMARY, "players": [{"name": "A", "team": 1, "level": 3}, {"name": "B", "team": 1, "level": 4}]}
    assert replays.player_lines(ffa["players"]) == "⚪ **A** · Lv 3\n⚪ **B** · Lv 4"


# --- client ----------------------------------------------------------------------------


def test_client(monkeypatch):
    seen = {}

    async def render(request):
        seen["auth"] = request.headers.get("Authorization")
        body = await request.json()
        seen["body"] = body
        if body["replay_id"] == "404":
            return web.json_response({"error": "replay_not_found"}, status=404)
        if body["replay_id"] == "500":
            return web.Response(text="<html>bad gateway</html>", status=502)
        return web.json_response({
            "replay": SUMMARY,
            "video": {"mp4_base64": base64.b64encode(b"fake mp4").decode(), "bytes": 8, "render_ms": 2600},
        })

    async def run():
        app = web.Application()
        app.router.add_post("/render", render)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        monkeypatch.setattr(renderer, "RENDER_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setattr(renderer, "RENDER_TOKEN", "t0ken")
        try:
            return (
                await renderer.render("1234567", 6),
                await renderer.render("404"),
                await renderer.render("500"),
            )
        finally:
            await runner.cleanup()

    ok, missing, broken = asyncio.run(run())
    assert (ok.video, ok.replay["map"]) == (b"fake mp4", "Simple 1v1")
    assert seen["auth"] == "Bearer t0ken"
    assert seen["body"] == {"replay_id": "500"}  # no cycle sent when none was given
    assert missing.error == "replay_not_found"
    assert broken.error == "unreachable"


def test_not_configured(monkeypatch):
    monkeypatch.setattr(renderer, "RENDER_URL", "")
    assert asyncio.run(renderer.render("1")).error == "not_configured"


def test_watch_url():
    assert renderer.watch_url("1234567", 6) == "https://replays.bonkverse.io/watch/6/1234567"


# --- the command --------------------------------------------------------------------------


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
        self.user = SimpleNamespace(id=1)
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self.deleted = False

    async def delete_original_response(self):
        self.deleted = True


def call(text):
    interaction = FakeInteraction()
    asyncio.run(replays.Replays.replay.callback(replays.Replays(), interaction, text))
    return interaction


def test_posts_the_video_with_the_embed(monkeypatch):
    async def ok(replay_id, cycle=None):
        return renderer.RenderReply(replay=SUMMARY, video=b"fake mp4")

    monkeypatch.setattr(renderer, "render", ok)
    sent = call("6:1234567").followup.sent
    assert sent["embed"].title == "Simple 1v1 by Chaz"
    assert sent["file"].filename == "replay-1234567.mp4"


def test_problems_are_private(monkeypatch):
    async def missing(replay_id, cycle=None):
        return renderer.RenderReply(error="replay_not_found")

    monkeypatch.setattr(renderer, "render", missing)
    interaction = call("999")
    assert interaction.deleted is True  # the public "thinking…" goes away
    assert interaction.followup.sent["ephemeral"] is True
    assert "no replay with that number" in interaction.followup.sent["embed"].description

    unreadable = call("hot dog")
    assert unreadable.response.sent["ephemeral"] is True


@pytest.mark.parametrize(
    "raw, url",
    [
        ("replay-video-service-production.up.railway.app", "https://replay-video-service-production.up.railway.app"),
        (" https://x.up.railway.app/ ", "https://x.up.railway.app"),
        ("http://video.railway.internal:8080", "http://video.railway.internal:8080"),
        ("", ""),
    ],
)
def test_service_url(raw, url):
    assert renderer.service_url(raw) == url
