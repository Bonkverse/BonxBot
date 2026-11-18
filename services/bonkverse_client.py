"""Client for Bonkverse's bot API (POST /api/bot/skins/ on bonkverse.io).

Never raises: every call returns a SubmitResult saying whether the skin is now
on Bonkverse, failed for good (don't retry), or should be retried later.
"""

import os
from dataclasses import dataclass

import aiohttp

BONKVERSE_API_URL = os.getenv("BONKVERSE_API_URL", "https://bonkverse.io").rstrip("/")
BONXBOT_API_TOKEN = os.getenv("BONXBOT_API_TOKEN", "")
TIMEOUT = aiohttp.ClientTimeout(total=60)  # Bonkverse renders the skin before answering

# Errors about one skin (its code or link), not Bonkverse being down.
PER_SKIN_ERRORS = {"render_failed", "fetch_failed"}


@dataclass
class SubmitResult:
    bonkverse_skin_id: int | None = None
    created: bool = False  # False when bonkverse.io already had this skin
    error: str | None = None
    retryable: bool = False
    unauthorized: bool = False
    # Bonkverse itself is unreachable or overloaded (as opposed to one skin failing),
    # so there is no point sending the rest of a batch right now.
    service_down: bool = False

    @property
    def ok(self):
        return self.bonkverse_skin_id is not None


def is_configured() -> bool:
    return bool(BONXBOT_API_TOKEN)


async def submit_skin(
    *, link, name, creator, skin_code, discord_user_id, bonx_skin_id, creator_source=None
) -> SubmitResult:
    """Send one submission. With skin_code, Bonkverse renders it through the skin
    editor and never contacts BonkLeagues; without it, Bonkverse reads the link.

    creator_source ("submitter" / "bonkleagues", or None for older rows) says
    where the creator name came from; Bonkverse trusts it from the bot."""
    payload = {
        "link": link,
        "name": name,
        "creator": creator,
        "skin_code": skin_code,
        "discord_user_id": discord_user_id,
        "bonx_skin_id": bonx_skin_id,
        "creator_source": creator_source,
    }
    headers = {"Authorization": f"Bearer {BONXBOT_API_TOKEN}"}

    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.post(
                f"{BONKVERSE_API_URL}/api/bot/skins/", json=payload, headers=headers
            ) as resp:
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = {}
                status = resp.status
    except Exception as e:
        return SubmitResult(
            error=f"network: {type(e).__name__}", retryable=True, service_down=True
        )

    if status in (200, 201) and isinstance(data, dict) and data.get("skin"):
        return SubmitResult(bonkverse_skin_id=data["skin"]["id"], created=status == 201)

    error = data.get("error") if isinstance(data, dict) else None
    if status == 401:
        # Our token is wrong or unset on Bonkverse: a config problem, not this skin's fault.
        return SubmitResult(error="401: unauthorized", retryable=True, unauthorized=True)
    # 429 and 5xx clear up on their own; any other 4xx will fail the same way again.
    retryable = status == 429 or status >= 500
    service_down = retryable and error not in PER_SKIN_ERRORS
    return SubmitResult(
        error=f"{status}: {error or 'no error code'}",
        retryable=retryable,
        service_down=service_down,
    )


# --- reads (GET /api/bot/skins/…, players/…, POST maps/decode/) -----------------
#
# Replies use the public API's envelope: {"ok": true, "data": …} or
# {"ok": false, "error": "<code>"}. Like submit_skin, these never raise.

READ_TIMEOUT = aiohttp.ClientTimeout(total=20)


@dataclass
class SiteReply:
    data: dict | None = None
    error: str | None = None  # Bonkverse's error code, or "unreachable"
    detail: str = ""  # the map decoder's explanation, on map_rejected

    @property
    def ok(self):
        return self.data is not None


def player_path(username: str) -> str:
    """Bonkverse writes the names "." and ".." as "~." and "~.." in paths."""
    from urllib.parse import quote

    name = username.strip()
    if name in (".", ".."):
        name = "~" + name
    return quote(name, safe="~")


async def _read(method: str, path: str, *, params=None, body=None, timeout=None) -> SiteReply:
    headers = {"Authorization": f"Bearer {BONXBOT_API_TOKEN}"}
    try:
        async with aiohttp.ClientSession(timeout=timeout or READ_TIMEOUT) as session:
            async with session.request(
                method, f"{BONKVERSE_API_URL}/api/bot/{path}",
                params=params, json=body, headers=headers,
            ) as resp:
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = None
    except Exception:
        return SiteReply(error="unreachable")
    if isinstance(data, dict) and data.get("ok") and isinstance(data.get("data"), dict):
        return SiteReply(data=data["data"])
    if isinstance(data, dict) and data.get("error"):
        return SiteReply(error=str(data["error"]), detail=str(data.get("detail") or ""))
    return SiteReply(error="unreachable")


async def search_skins(query: str, limit: int = 5) -> SiteReply:
    return await _read("GET", "skins/search/", params={"q": query, "limit": str(limit)})


async def random_skin() -> SiteReply:
    return await _read("GET", "skins/random/")


async def get_skin(skin_id: int) -> SiteReply:
    return await _read("GET", f"skins/{int(skin_id)}/")


async def get_player(username: str) -> SiteReply:
    return await _read("GET", f"players/{player_path(username)}/")


async def decode_map(code: str) -> SiteReply:
    return await _read("POST", "maps/decode/", body={"code": code})


COLLECT_TIMEOUT = aiohttp.ClientTimeout(total=60)  # may upload and render first


async def collect(
    bonk_username: str, discord_user_id: int, *, link: str | None = None, skin_id: int | None = None
) -> SiteReply:
    """Add a skin (BonkLeagues link or Bonkverse id) to a player's bonkverse.io
    collection, uploading it first if the site doesn't have it."""
    body = {"bonk_username": bonk_username, "discord_user_id": str(discord_user_id)}
    if skin_id is not None:
        body["skin_id"] = int(skin_id)
    else:
        body["link"] = link
    return await _read("POST", "collection/", body=body, timeout=COLLECT_TIMEOUT)
