from dataclasses import dataclass

from db.pool import get_pool


@dataclass(frozen=True)
class Deed:
    metric: str
    target: int
    label: str


@dataclass(frozen=True)
class Rank:
    title: str
    deeds: tuple[Deed, ...] = ()


CITIZEN = Rank("Citizen")
RANK_LADDERS = {
    "Skintopia": (
        Rank("Sketcher", (Deed("votes", 3, "Skins voted on"),)),
        Rank("Stylist", (Deed("skins", 1, "Skins submitted"),)),
        Rank("Master Artist", (Deed("skins", 3, "Skins submitted"),)),
        Rank("Grand Curator", (
            Deed("votes", 8, "Skins voted on"), Deed("skins", 5, "Skins submitted"),
        )),
    ),
    "Bonktown": (
        Rank("Trader", (Deed("transfers", 1, "Transfers completed"),)),
        Rank("Merchant", (Deed("bonx", 2500, "Bonx balance"),)),
        Rank("Tycoon", (Deed("bonx", 10000, "Bonx balance"),)),
        Rank("Magnate", (
            Deed("bonx", 25000, "Bonx balance"), Deed("vote_taxes", 1, "Vote-change fees paid"),
        )),
    ),
    "Creativia": (
        Rank("Courtier", (Deed("dailies", 3, "Daily claims"),)),
        Rank("Landed Knight", (Deed("votes", 5, "Skins voted on"),)),
        Rank("Steward", (Deed("streak", 7, "Best daily streak"),)),
        Rank("Regent", (Deed("streak", 30, "Best daily streak"),)),
    ),
    "Spherehold": (
        Rank("Recruit", (Deed("dailies", 3, "Daily claims"),)),
        Rank("Shieldman", (Deed("streak", 7, "Best daily streak"),)),
        Rank("Warden", (Deed("streak", 14, "Best daily streak"), Deed("wins", 1, "Coinflips won"))),
        Rank("Centurion", (Deed("streak", 30, "Best daily streak"), Deed("wins", 5, "Coinflips won"))),
    ),
    "Versetralis": (
        Rank("Intern", (Deed("votes", 3, "Skins voted on"),)),
        Rank("Junior Analyst", (Deed("reasons", 3, "Distinct ledger reasons"),)),
        Rank("Analyst", (Deed("streak", 7, "Best daily streak"),)),
        Rank("Senior Analyst", (Deed("flips", 5, "Coinflips played"),)),
    ),
    "Veri": (
        Rank("Resident", (Deed("dailies", 3, "Daily claims"),)),
        Rank("Elector", (Deed("votes", 5, "Skins voted on"),)),
        Rank("Senator", (Deed("streak", 14, "Best daily streak"), Deed("vote_taxes", 1, "Vote-change fees paid"))),
        Rank("Consul", (Deed("streak", 30, "Best daily streak"), Deed("votes", 10, "Skins voted on"))),
    ),
    "Arena City": (
        Rank("Squire", (Deed("flips", 3, "Coinflips played"),)),
        Rank("Fighter", (Deed("wins", 3, "Coinflips won"),)),
        Rank("Duelist", (Deed("wins", 10, "Coinflips won"),)),
        Rank("Gladiator", (Deed("wins", 25, "Coinflips won"),)),
    ),
}


@dataclass(frozen=True)
class RankProgress:
    region: str | None
    tier: int
    rank: Rank
    next_rank: Rank | None
    metrics: dict[str, int]


async def get_rank_progress(discord_id: int, *, claim: bool = False):
    """Read regional progress, optionally recording eligible tiers for the owner."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            member = await conn.fetchrow(
                "SELECT region, bonx, best_daily_streak FROM users WHERE discord_id=$1 FOR UPDATE",
                discord_id,
            )
            region = member["region"] if member else None
            ladder = (CITIZEN, *RANK_LADDERS.get(region, ()))
            if len(ladder) == 1:
                return RankProgress(region, 1, CITIZEN, None, {})

            tier = await conn.fetchval(
                "SELECT tier FROM region_rank_progress WHERE discord_id=$1 AND region=$2",
                discord_id, region,
            ) or 1
            ledger = await conn.fetchrow(
                """
                SELECT count(*) FILTER (WHERE reason='daily') AS dailies,
                       count(*) FILTER (WHERE reason IN ('give', 'receive')) AS transfers,
                       count(*) FILTER (WHERE reason='vote_change_tax') AS vote_taxes,
                       count(*) FILTER (WHERE reason='coinflip_stake') AS flips,
                       count(*) FILTER (WHERE reason='coinflip_win') AS wins,
                       count(DISTINCT reason) AS reasons
                FROM bonx_transactions WHERE discord_id=$1
                """,
                discord_id,
            )
            activity = await conn.fetchrow(
                """
                SELECT (SELECT count(DISTINCT skin_id) FROM votes WHERE user_id=$1) AS votes,
                       (SELECT count(*) FROM skins WHERE submitter_id=$1) AS skins
                """,
                discord_id,
            )
            metrics = dict(ledger) | dict(activity) | {
                "bonx": member["bonx"],
                "streak": member["best_daily_streak"],
            }

            if claim:
                earned = tier
                for rank in ladder[tier:]:
                    if not all(metrics[deed.metric] >= deed.target for deed in rank.deeds):
                        break
                    earned += 1
                if earned > tier:
                    await conn.execute(
                        """
                        INSERT INTO region_rank_progress (discord_id, region, tier)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (discord_id, region) DO UPDATE SET tier=EXCLUDED.tier
                        """,
                        discord_id, region, earned,
                    )
                    tier = earned

    next_rank = ladder[tier] if tier < len(ladder) else None
    return RankProgress(region, tier, ladder[tier - 1], next_rank, metrics)
