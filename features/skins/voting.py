import discord
from discord.ui import View, Button
from db.pool import get_pool
from datetime import timezone

from config.settings import SKINTOPIA_VOTE_CHANGE_TAX, VOTE_REWARD, VOTE_CHANGE_TAX
from features.economy.service import get_or_create_user, record_balance_change
from features.regions.ranks import has_region_rank

# Tier → numeric score
TIER_MAP = {
    "S": 6,
    "A": 5,
    "B": 4,
    "C": 3,
    "D": 2,
    "F": 1
}


class VoteView(View):
    def __init__(self, skin_id: int):
        super().__init__(timeout=None)  # Must be None for persistent views
        self.skin_id = skin_id

        for tier in ["S", "A", "B", "C", "D", "F"]:
            self.add_item(VoteButton(tier, skin_id))


class VoteButton(Button):
    def __init__(self, tier: str, skin_id: int):
        super().__init__(
            label=tier,
            style=discord.ButtonStyle.primary,
            custom_id=f"vote:{skin_id}:{tier}"  # ✅ Stable across restarts
        )
        self.tier = tier
        self.skin_id = skin_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            message, total_votes = await record_vote(
                self.skin_id, interaction.user.id, self.tier
            )
        except Exception as error:
            print(f"[Vote Error] {error}")
            await interaction.followup.send(
                "❌ Something went wrong while recording your vote.", ephemeral=True
            )
            return

        await interaction.followup.send(message, ephemeral=True)
        if total_votes is not None:
            try:
                # Fetch the current embed so a poller result is never cleared.
                msg = await interaction.channel.fetch_message(interaction.message.id)
                embed = msg.embeds[0]
                for index, field in enumerate(embed.fields):
                    if field.name == "🗳️ Votes":
                        embed.set_field_at(
                            index, name=field.name, value=f"{total_votes} votes",
                            inline=field.inline,
                        )
                        await msg.edit(embed=embed)
                        break
            except discord.HTTPException as error:
                print(f"[Vote Message Error] {error}")


async def record_vote(skin_id: int, discord_id: int, tier: str):
    score = TIER_MAP[tier]
    await get_or_create_user(discord_id)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            skin = await conn.fetchrow(
                "SELECT ends_at, finalized FROM skins WHERE id=$1 FOR UPDATE", skin_id
            )
            if skin is None:
                return "❌ Skin not found.", None
            if skin["finalized"] or skin["ends_at"].replace(tzinfo=timezone.utc) <= discord.utils.utcnow():
                return "❌ Voting has ended.", None

            previous_vote = await conn.fetchval(
                "SELECT tier FROM votes WHERE user_id=$1 AND skin_id=$2",
                discord_id, skin_id,
            )
            if previous_vote == score:
                return f"✅ Your vote is already **{tier} Tier**.", None

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
                return f"❌ Changing your vote costs **{-amount} Bonx**. Your vote stays as it was.", None

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

    return f"✅ You voted **{tier} Tier** ({amount:+} Bonx)", total_votes
