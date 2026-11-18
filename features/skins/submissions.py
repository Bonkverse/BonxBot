"""Reading skin submissions back: what /submissions lists and shows.

Owns: the queries over the bot's `skins` and `votes` tables for one scope,
either everything a Discord member submitted or everything credited to a
bonk.io creator name (ignoring case, like bonk.io).
Does not own: how it looks (cards.py) or the command (browse.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from db.pool import get_pool

from .service import compute_tier

PAGE_SIZE = 10


@dataclass(frozen=True)
class Scope:
    """Whose skins: a Discord member's submissions, or a creator's skins."""

    submitter_id: int | None = None
    creator: str | None = None

    def where(self, first_arg: int = 1, table: str = "") -> tuple[str, list]:
        """SQL condition and its argument; `table` prefixes the columns ("s.")."""
        if self.submitter_id is not None:
            return f"{table}submitter_id = ${first_arg}", [self.submitter_id]
        return f"LOWER({table}creator) = LOWER(${first_arg})", [self.creator.strip()]


@dataclass
class Summary:
    total: int = 0
    in_voting: int = 0
    finished: int = 0
    tiers: dict = field(default_factory=dict)  # "S" -> count, finished skins only
    average: float | None = None  # mean of finished skins' scores
    votes: int = 0  # votes received, all skins
    on_bonkverse: int = 0
    best: dict | None = None  # the highest-scoring finished skin


async def summary(scope: Scope) -> Summary:
    where, args = scope.where()
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE NOT finalized) AS in_voting,
                   COUNT(*) FILTER (WHERE finalized) AS finished,
                   AVG(average_score) FILTER (WHERE finalized AND total_votes > 0) AS average,
                   COUNT(*) FILTER (WHERE bonkverse_skin_id IS NOT NULL) AS on_bonkverse
            FROM skins WHERE {where}
            """,
            *args,
        )
        tiers = await conn.fetch(
            f"SELECT ranktier, COUNT(*) AS n FROM skins WHERE {where} AND finalized "
            "GROUP BY ranktier",
            *args,
        )
        joined, _ = scope.where(table="s.")
        votes = await conn.fetchval(
            f"SELECT COUNT(*) FROM votes v JOIN skins s ON s.id = v.skin_id WHERE {joined}",
            *args,
        )
        best = await conn.fetchrow(
            f"SELECT id, skin_name, ranktier, average_score FROM skins WHERE {where} "
            "AND finalized AND total_votes > 0 ORDER BY average_score DESC, id LIMIT 1",
            *args,
        )
    return Summary(
        total=row["total"],
        in_voting=row["in_voting"],
        finished=row["finished"],
        tiers={r["ranktier"]: r["n"] for r in tiers if r["ranktier"]},
        average=float(row["average"]) if row["average"] is not None else None,
        votes=votes or 0,
        on_bonkverse=row["on_bonkverse"],
        best=dict(best) if best else None,
    )


async def skin_ids(scope: Scope) -> list[int]:
    """Every skin in the scope, newest first: the order of the list and of ◀️ ▶️."""
    where, args = scope.where()
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(f"SELECT id FROM skins WHERE {where} ORDER BY id DESC", *args)
    return [r["id"] for r in rows]


LIST_COLUMNS = (
    "id, skin_name, creator, finalized, ends_at, ranktier, average_score, total_votes, "
    "bonkverse_skin_id, skin_url"
)


async def rows_for(ids: list[int]) -> list[dict]:
    """The list rows for these ids, in the same order."""
    if not ids:
        return []
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {LIST_COLUMNS}, "
            "(SELECT COUNT(*) FROM votes v WHERE v.skin_id = skins.id) AS live_votes "
            "FROM skins WHERE id = ANY($1::int[])",
            ids,
        )
    by_id = {r["id"]: dict(r) for r in rows}
    return [by_id[i] for i in ids if i in by_id]


async def detail(skin_id: int) -> dict | None:
    """One skin with its vote breakdown ("S" -> count) and current average."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {LIST_COLUMNS}, submitter_id, vote_message_id, creator_source "
            "FROM skins WHERE id = $1",
            skin_id,
        )
        if row is None:
            return None
        votes = await conn.fetch(
            "SELECT tier, COUNT(*) AS n FROM votes WHERE skin_id = $1 GROUP BY tier", skin_id
        )
    skin = dict(row)
    points = {"S": 6, "A": 5, "B": 4, "C": 3, "D": 2, "F": 1}
    by_points = {v["tier"]: v["n"] for v in votes}
    skin["breakdown"] = {tier: by_points.get(p, 0) for tier, p in points.items()}
    total = sum(skin["breakdown"].values())
    skin["live_votes"] = total
    if total and not skin["finalized"]:
        current = sum(points[t] * n for t, n in skin["breakdown"].items()) / total
        skin["current_average"] = current
        skin["current_tier"] = compute_tier(current)
    return skin


_ID = re.compile(r"^#?(\d+)$")


async def find(scope: Scope, text: str) -> int | None:
    """A skin in the scope by its number ("#123" or "123") or name: an exact
    name first, then the newest name containing the text."""
    where, args = scope.where(2)
    text = text.strip()
    pool = await get_pool()
    async with pool.acquire() as conn:
        number = _ID.match(text)
        if number:
            found = await conn.fetchval(
                f"SELECT id FROM skins WHERE id = $1 AND {where}", int(number.group(1)), *args
            )
            if found:
                return found
        return await conn.fetchval(
            f"""
            SELECT id FROM skins
            WHERE {where} AND skin_name ILIKE '%' || $1 || '%'
            ORDER BY LOWER(skin_name) = LOWER($1) DESC, id DESC
            LIMIT 1
            """,
            text.replace("%", r"\%").replace("_", r"\_"),
            *args,
        )


async def name_choices(scope: Scope, typed: str) -> list[tuple[str, int]]:
    """(label, id) for autocomplete: names in the scope containing what's typed."""
    where, args = scope.where(2)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, skin_name FROM skins
            WHERE {where} AND skin_name ILIKE '%' || $1 || '%'
            ORDER BY id DESC LIMIT 25
            """,
            typed.strip().replace("%", r"\%").replace("_", r"\_"),
            *args,
        )
    return [(f"{r['skin_name'] or 'Unnamed'} · #{r['id']}"[:100], r["id"]) for r in rows]
