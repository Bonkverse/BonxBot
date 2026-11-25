"""Which friend request verifies which /link claim. No database, no Discord.

A claim says "Discord user D says they own bonk.io account N", started at T.
It is proven when the bot's account receives a friend request from N dated
at or after T (less a little clock slack). Names match ignoring case but not
spaces: bonk.io names are unique case-insensitively and can contain spaces.

Only ASCII letters fold, exactly what Postgres lower() does to the names the
database compares (service.py). Python's casefold() also folds "ſ" to "s",
"ß" to "ss" and "ﬁ" to "fi", which lower() leaves alone: a claim for
"Boſsman" looked unowned to the database but matched Bossman's friend
request, linking Bossman to the claimer's Discord. bonk.io names are ASCII
(letters, digits, spaces, underscores), so /link refuses anything else.

Requests nobody can still use are deleted, so an old request can never prove a
later claim: a matched one at once, an unmatched one once it's older than a
claim can last.
"""

from __future__ import annotations

import os
import string
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

CLAIM_MINUTES = 10
CLOCK_SLACK = timedelta(seconds=60)
NAME_MAX_LENGTH = 64

# BONK: friend request dates look like "2026-11-10 21:39:07" with no zone.
# They appear to be US Eastern server time (verification notes, 2026-11-10;
# unconfirmed), so it's configurable.
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
DATE_ZONE = ZoneInfo(os.getenv("BONK_FRIEND_DATE_TZ", "America/New_York"))


@dataclass(frozen=True)
class Claim:
    id: int
    discord_user_id: int
    bonk_username: str
    started_at: datetime  # aware, UTC


@dataclass(frozen=True)
class Match:
    claim: Claim
    bonk_id: int
    bonk_username: str  # bonk.io's own spelling, from the request


_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def name_key(name: str) -> str:
    """How bonk.io names are compared: ASCII case ignored, everything else kept.

    Equal keys mean the database's lower() sees the same name too.
    """
    return name.translate(_ASCII_LOWER)


def name_problem(name: str) -> str | None:
    """Why `name` can't be a bonk.io username, in words for the user, or None."""
    if not name or not name.strip():
        return "Type your bonk.io username."
    if len(name) > NAME_MAX_LENGTH:
        return f"bonk.io usernames are at most {NAME_MAX_LENGTH} characters."
    if any(not ch.isprintable() for ch in name):
        return "That username has characters bonk.io doesn't allow."
    if not name.isascii():
        return "bonk.io usernames only use letters, numbers, spaces and underscores (no accents or symbols)."
    return None


def request_time(date: str | None) -> datetime | None:
    """A friend request's date as an aware UTC datetime, or None if unreadable."""
    if not date:
        return None
    try:
        local = datetime.strptime(date.strip(), DATE_FORMAT)
    except ValueError:
        return None
    return local.replace(tzinfo=DATE_ZONE).astimezone(timezone.utc)


def match_requests(claims, requests, now: datetime):
    """(matches, request ids to delete) for the pending claims and current requests.

    Each claim takes the first request from its name sent after it started;
    each request proves at most one claim. Requests with no readable date never
    prove anything (they're deleted once seen).
    """
    matches = []
    used = set()
    for claim in sorted(claims, key=lambda c: c.started_at):
        for request in requests:
            if request.bonk_id in used:
                continue
            if name_key(request.name) != name_key(claim.bonk_username):
                continue
            sent = request_time(request.date)
            if sent is None or sent < claim.started_at - CLOCK_SLACK:
                continue
            matches.append(Match(claim, request.bonk_id, request.name))
            used.add(request.bonk_id)
            break

    stale_before = now - timedelta(minutes=CLAIM_MINUTES) - CLOCK_SLACK
    delete = [
        r.bonk_id
        for r in requests
        if r.bonk_id in used or (request_time(r.date) or stale_before) <= stale_before
    ]
    return matches, delete
