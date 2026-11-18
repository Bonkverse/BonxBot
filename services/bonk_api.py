"""bonk.io's account scripts, for the bot's own bonk.io account.

Owns: logging the bot account in (`login_legacy.php`), reading its incoming
friend requests and deleting them (`friends.php`), and turning every failure
into an error code instead of an exception.
Does not own: deciding which request verifies whom (features/accounts/).

Same requests as bonk.io's web client (and Bonkverse's integrations/bonk_api.py):
form POSTs to https://bonk2.io/scripts/<script> with the browser's headers.
Answers are JSON: {"r": "success", ...} or {"r": "fail", "e": "<code>"}.
The password is sent to bonk.io only; never logged or stored.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import aiohttp

logger = logging.getLogger(__name__)

BASE_URL = "https://bonk2.io/scripts"
TIMEOUT = aiohttp.ClientTimeout(total=10)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
    ),
    "Origin": "https://bonk.io",
    "Referer": "https://bonk.io/",
    "Accept": "application/json, text/plain, */*",
}

# Our error codes. bonk.io's own codes pass through as-is ("token" = the
# token expired, "ratelimited" = slow down, "password" = wrong password...).
UNAVAILABLE = "unavailable"  # no answer, an HTTP error, or not JSON


@dataclass(frozen=True)
class FriendRequest:
    """An incoming friend request: who sent it and when (bonk.io's text date)."""

    bonk_id: int
    name: str
    date: str | None  # "2026-11-10 21:39:07"; see features/accounts/matching.py


@dataclass(frozen=True)
class LoginResult:
    token: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class RequestsResult:
    requests: tuple[FriendRequest, ...] = ()
    error: str | None = None


async def login(username: str, password: str) -> LoginResult:
    # BONK: the web client sends these three fields; remember=false gives a
    # session token that lasts about a day.
    data, error = await _post(
        "login_legacy.php", {"username": username, "password": password, "remember": "false"}
    )
    if error:
        return LoginResult(error=error)
    if data.get("r") != "success" or not data.get("token"):
        return LoginResult(error=_code(data))
    return LoginResult(token=data["token"])


async def friend_requests(token: str) -> RequestsResult:
    """Incoming friend requests to the logged-in account."""
    data, error = await _post("friends.php", {"token": token, "task": "getfriends"})
    if error:
        return RequestsResult(error=error)
    if data.get("r") != "success":
        return RequestsResult(error=_code(data))
    return RequestsResult(requests=_parse_requests(data.get("requests")))


async def delete_request(token: str, bonk_id: int) -> str | None:
    """Decline a friend request; None when it worked, else an error code."""
    # BONK: task "deleterequest" with theirid (Demystify-Bonk; unconfirmed by capture).
    data, error = await _post(
        "friends.php", {"token": token, "task": "deleterequest", "theirid": str(bonk_id)}
    )
    if error:
        return error
    return None if data.get("r") == "success" else _code(data)


async def _post(script: str, form: dict) -> tuple[dict, str | None]:
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT, headers=HEADERS) as session:
            async with session.post(f"{BASE_URL}/{script}", data=form) as response:
                if response.status >= 400:
                    logger.warning("bonk.io %s answered HTTP %s", script, response.status)
                    return {}, UNAVAILABLE
                data = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        logger.warning("bonk.io %s unreachable: %s", script, type(exc).__name__)
        return {}, UNAVAILABLE
    if not isinstance(data, dict):
        return {}, UNAVAILABLE
    return data, None


def _code(data: dict) -> str:
    # BONK: failures carry the code in "e".
    return str(data.get("e") or data.get("error") or "rejected")


def _parse_requests(entries) -> tuple[FriendRequest, ...]:
    parsed = []
    for entry in entries or ():
        if not isinstance(entry, dict):
            continue
        try:
            bonk_id = int(entry.get("id"))
        except (TypeError, ValueError):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            continue
        date = entry.get("date")
        parsed.append(FriendRequest(bonk_id, name, date if isinstance(date, str) else None))
    return tuple(parsed)
