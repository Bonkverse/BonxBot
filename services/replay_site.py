"""Client for the replay site's public search (replays.bonkverse.io, GET
/api/search, ReplayRenderer's server/api-routes.mjs). No token: it's the same
API the site's own search page uses.

Never raises: every call returns a SearchReply.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import aiohttp

from services.replay_renderer import REPLAY_SITE_URL

logger = logging.getLogger(__name__)
TIMEOUT = aiohttp.ClientTimeout(total=20)
PAGE_SIZE = 20  # the site's page size
MODES = {"b": "Classic", "ar": "Arrows", "ard": "Death Arrows", "sp": "Grapple", "v": "VTOL", "f": "Football", "bs": "Simple"}


@dataclass
class SearchReply:
    results: list[dict] = field(default_factory=list)
    total: int = 0
    capped: bool = False  # more than the site counts (10,000+)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def search_params(
    *, players: list[str], match: str = "any", map_: str | None = None,
    mode: str | None = None, oldest: bool = False, page: int = 1,
) -> list[tuple[str, str]]:
    """Query string pairs for /api/search. Players are exact names (`with`)."""
    params = [("with", p) for p in players[:5]]
    params += [("match", match), ("sort", "oldest" if oldest else "newest"), ("page", str(page))]
    if map_:
        params.append(("map", map_))
    if mode:
        params.append(("mode", mode))
    return params


async def search(params: list[tuple[str, str]]) -> SearchReply:
    try:
        async with (
            aiohttp.ClientSession(timeout=TIMEOUT) as session,
            session.get(f"{REPLAY_SITE_URL}/api/search", params=params) as resp,
        ):
            data = await resp.json(content_type=None) if resp.status < 500 else None
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        logger.warning("replay search failed: %r", err)
        return SearchReply(error="unreachable")
    if not isinstance(data, dict) or "results" not in data:
        return SearchReply(error="unreachable")
    return SearchReply(results=data["results"], total=data.get("total", 0), capped=bool(data.get("capped")))
