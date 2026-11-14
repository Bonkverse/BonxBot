from db.pool import get_pool

TIER_CHANNELS = {
    "S": 1341722826947694664,
    "A": 1341722934015692850,
    "B": 1341723027364384831,
    "C": 1341723129885495356,
    "D": 1341723256868311040,
    "F": 1341723356810055752
}


def compute_tier(avg):
    if avg >= 5.5: return "S"
    if avg >= 4.5: return "A"
    if avg >= 3.5: return "B"
    if avg >= 2.5: return "C"
    if avg >= 1.5: return "D"
    return "F"


async def finalize_skin(skin_id):
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            skin = await conn.fetchrow(
                "SELECT finalized FROM skins WHERE id=$1 FOR UPDATE", skin_id
            )
            if skin is None or skin["finalized"]:
                return None

            votes = await conn.fetch(
                "SELECT tier FROM votes WHERE skin_id=$1", skin_id
            )
            total = len(votes)
            avg = sum(vote["tier"] for vote in votes) / total if total else 0
            tier = compute_tier(avg)

            await conn.execute(
                """
                UPDATE skins
                SET finalized = TRUE, ranktier = $1, average_score = $2, total_votes = $3
                WHERE id = $4
                """,
                tier, avg, total, skin_id,
            )

    return {"tier": tier, "avg": avg, "total": total}
