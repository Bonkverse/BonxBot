"""Client for the replay video service (ReplayRenderer's server/render-server.mjs,
its own Railway service): POST /render turns a replay into an MP4.

Never raises: every call returns a RenderReply. Set REPLAY_RENDER_URL (its
Railway private address, e.g. http://replay-video.railway.internal:8090) and
REPLAY_RENDER_TOKEN (the service's RENDER_TOKEN).
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass

import aiohttp

RENDER_URL = os.getenv("REPLAY_RENDER_URL", "").rstrip("/")
RENDER_TOKEN = os.getenv("REPLAY_RENDER_TOKEN", "")
REPLAY_SITE_URL = os.getenv("REPLAY_SITE_URL", "https://replays.bonkverse.io").rstrip("/")
# Rendering takes a few seconds, but jobs queue one at a time behind others.
TIMEOUT = aiohttp.ClientTimeout(total=150)


@dataclass
class RenderReply:
    replay: dict | None = None  # summary: map, author, mode, day, seconds, players
    video: bytes | None = None
    error: str | None = None  # the service's code, "not_configured" or "unreachable"

    @property
    def ok(self) -> bool:
        return self.video is not None


def is_configured() -> bool:
    return bool(RENDER_URL and RENDER_TOKEN)


def watch_url(replay_id: str, cycle: int | None) -> str:
    if cycle is None:
        return f"{REPLAY_SITE_URL}/?q={replay_id}"
    return f"{REPLAY_SITE_URL}/watch/{cycle}/{replay_id}"


async def render(replay_id: str, cycle: int | None = None) -> RenderReply:
    if not is_configured():
        return RenderReply(error="not_configured")
    body = {"replay_id": str(replay_id)}
    if cycle is not None:
        body["cycle"] = int(cycle)
    headers = {"Authorization": f"Bearer {RENDER_TOKEN}"}
    try:
        async with (
            aiohttp.ClientSession(timeout=TIMEOUT) as session,
            session.post(f"{RENDER_URL}/render", json=body, headers=headers) as resp,
        ):
            try:
                data = await resp.json(content_type=None)
            except ValueError:  # not JSON (a proxy's error page)
                data = None
    except (aiohttp.ClientError, TimeoutError):
        return RenderReply(error="unreachable")
    if isinstance(data, dict) and isinstance(data.get("video"), dict):
        try:
            video = base64.b64decode(data["video"]["mp4_base64"])
        except (KeyError, ValueError, TypeError):
            return RenderReply(error="render_failed")
        return RenderReply(replay=data.get("replay") or {}, video=video)
    if isinstance(data, dict) and data.get("error"):
        return RenderReply(error=str(data["error"]))
    return RenderReply(error="unreachable")
