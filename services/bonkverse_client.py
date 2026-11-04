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
    *, link, name, creator, skin_code, discord_user_id, bonx_skin_id
) -> SubmitResult:
    """Send one submission. With skin_code, Bonkverse renders it through the skin
    editor and never contacts BonkLeagues; without it, Bonkverse reads the link."""
    payload = {
        "link": link,
        "name": name,
        "creator": creator,
        "skin_code": skin_code,
        "discord_user_id": discord_user_id,
        "bonx_skin_id": bonx_skin_id,
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
        return SubmitResult(bonkverse_skin_id=data["skin"]["id"])

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
