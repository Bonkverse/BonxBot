"""Client for the replay video service (ReplayRenderer's server/render-server.mjs,
its own Railway service): POST /render turns a replay into an MP4.

Never raises: every call returns a RenderReply. Set REPLAY_RENDER_URL (its
Railway private address, e.g. http://replay-video.railway.internal:8090) and
REPLAY_RENDER_TOKEN (the service's RENDER_TOKEN).
"""

from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass

import aiohttp

logger = logging.getLogger(__name__)


def service_url(raw: str) -> str:
    """The service's base URL. A bare domain (as Railway shows it) gets https://."""
    url = raw.strip().rstrip("/")
    if url and "://" not in url:
        url = f"https://{url}"
    return url


RENDER_URL = service_url(os.getenv("REPLAY_RENDER_URL", ""))
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
    except (aiohttp.ClientError, TimeoutError) as err:
        logger.warning("replay video service unreachable at %s: %r", RENDER_URL, err)
        return RenderReply(error="unreachable")
    if isinstance(data, dict) and isinstance(data.get("video"), dict):
        try:
            video = base64.b64decode(data["video"]["mp4_base64"])
        except (KeyError, ValueError, TypeError):
            return RenderReply(error="render_failed")
        return RenderReply(replay=data.get("replay") or {}, video=video)
    if isinstance(data, dict) and data.get("error"):
        return RenderReply(error=str(data["error"]))
    logger.warning("replay video service at %s sent a reply that isn't its JSON", RENDER_URL)
    return RenderReply(error="unreachable")


@dataclass
class FeedReply:
    replays: list[dict] | None = None  # search-result shaped, oldest first
    scanned_to: dict | None = None  # {"cycle", "id"}: where to continue
    more: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


async def replays_with(names: list[str], after: dict | None, limit: int = 3) -> FeedReply:
    """New replays after `after` with any of these registered players
    (POST /replays/with). Without `after`: only where "now" is."""
    if not is_configured():
        return FeedReply(error="not_configured")
    body = {"names": names, "after": after, "limit": limit}
    headers = {"Authorization": f"Bearer {RENDER_TOKEN}"}
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session,
            session.post(f"{RENDER_URL}/replays/with", json=body, headers=headers) as resp,
        ):
            data = await resp.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        logger.warning("replay feed lookup failed: %r", err)
        return FeedReply(error="unreachable")
    if isinstance(data, dict) and isinstance(data.get("replays"), list):
        return FeedReply(replays=data["replays"], scanned_to=data.get("scanned_to"), more=bool(data.get("more")))
    return FeedReply(error=str((data or {}).get("error") or "unreachable") if isinstance(data, dict) else "unreachable")
