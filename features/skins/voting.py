"""Tier voting on submitted skins: the S–F buttons under each vote post.

Owns: recording a vote (first vote pays Bonx, changing it costs a fee) and
keeping the post's vote count current. Replies are private to the voter.
Does not own: how replies look (cards.py) or closing the vote (poller.py).
"""

import logging
from datetime import timezone

import discord
from discord.ui import Button, View

from config.settings import SKINTOPIA_VOTE_CHANGE_TAX, VOTE_CHANGE_TAX, VOTE_REWARD
from db.pool import get_pool
from features.economy.service import get_or_create_user, record_balance_change
from features.regions.ranks import has_region_rank

from . import cards

logger = logging.getLogger(__name__)

# Tier → numeric score
TIER_MAP = cards.TIER_POINTS


class VoteView(View):
    def __init__(self, skin_id: int):
        super().__init__(timeout=None)  # Must be None for persistent views
        self.skin_id = skin_id

        for tier in cards.TIERS:
            self.add_item(VoteButton(tier, skin_id))


class VoteButton(Button):
    def __init__(self, tier: str, skin_id: int):
        super().__init__(
            label=tier,
            style=discord.ButtonStyle.primary,
            custom_id=f"vote:{skin_id}:{tier}",  # stable across restarts
        )
        self.tier = tier
        self.skin_id = skin_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            reply, total_votes = await record_vote(self.skin_id, interaction.user.id, self.tier)
        except Exception:
            logger.exception("vote on skin %s failed", self.skin_id)
            await interaction.followup.send(embed=cards.vote_failed(), ephemeral=True)
            return

        await interaction.followup.send(embed=reply, ephemeral=True)
        if total_votes is not None:
            try:
                # Fetch the current embed so a poller result is never cleared.
                msg = await interaction.channel.fetch_message(interaction.message.id)
                await msg.edit(embed=cards.set_vote_count(msg.embeds[0], total_votes))
            except discord.HTTPException:
                logger.warning("couldn't update the vote count on skin %s", self.skin_id)


async def record_vote(skin_id: int, discord_id: int, tier: str):
    """(private reply, new vote count or None if nothing changed)."""
    score = TIER_MAP[tier]
    await get_or_create_user(discord_id)
    pool = await get_pool()

    async with pool.acquire() as conn, conn.transaction():
        skin = await conn.fetchrow(
            "SELECT ends_at, finalized FROM skins WHERE id=$1 FOR UPDATE", skin_id
        )
        if skin is None:
            return cards.missing(), None
        if skin["finalized"] or skin["ends_at"].replace(tzinfo=timezone.utc) <= discord.utils.utcnow():
            return cards.closed(), None

        previous_vote = await conn.fetchval(
            "SELECT tier FROM votes WHERE user_id=$1 AND skin_id=$2",
            discord_id, skin_id,
        )
        if previous_vote == score:
            return cards.already(tier), None

        if previous_vote is None:
            amount, reason = VOTE_REWARD, "skin_vote"
        else:
            discounted = await has_region_rank(
                conn, discord_id, "Skintopia", 3, lock_member=True
            )
            fee = SKINTOPIA_VOTE_CHANGE_TAX if discounted else VOTE_CHANGE_TAX
            amount, reason = -fee, "vote_change_tax"

        balance = await record_balance_change(conn, discord_id, amount, reason)
        if balance is None:
            return cards.cant_afford(-amount), None

        if previous_vote is None:
            await conn.execute(
                "INSERT INTO votes (skin_id, user_id, tier) VALUES ($1, $2, $3)",
                skin_id, discord_id, score,
            )
        else:
            await conn.execute(
                "UPDATE votes SET tier=$3 WHERE skin_id=$1 AND user_id=$2",
                skin_id, discord_id, score,
            )
        total_votes = await conn.fetchval(
            "SELECT COUNT(*) FROM votes WHERE skin_id=$1", skin_id
        )

    reply = cards.voted(tier, amount) if previous_vote is None else cards.changed(tier, -amount)
    return reply, total_votes
