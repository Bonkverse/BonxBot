import discord
from discord.ui import View, Button
from db.pool import get_pool
from datetime import timezone

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
        super().__init__(timeout=None)
        self.skin_id = skin_id

        # Create buttons for each tier
        for tier in ["S", "A", "B", "C", "D", "F"]:
            self.add_item(VoteButton(tier, skin_id))


class VoteButton(Button):
    def __init__(self, tier: str, skin_id: int):
        super().__init__(
            label=tier,
            style=discord.ButtonStyle.primary
        )
        self.tier = tier
        self.skin_id = skin_id

    async def callback(self, interaction: discord.Interaction):
        pool = await get_pool()

        try:
            async with pool.acquire() as conn:
                # 🔹 Fetch voting deadline
                skin = await conn.fetchrow(
                    "SELECT ends_at, vote_message_id FROM skins WHERE id=$1",
                    self.skin_id
                )

                if not skin:
                    await interaction.response.send_message(
                        "❌ Skin not found.", ephemeral=True
                    )
                    return

                ends_at = skin["ends_at"].replace(tzinfo=timezone.utc)

                if ends_at < discord.utils.utcnow():
                    await interaction.response.send_message(
                        "❌ Voting has ended.", ephemeral=True
                    )
                    return

                # 🔹 Remove previous vote
                await conn.execute(
                    "DELETE FROM votes WHERE user_id=$1 AND skin_id=$2",
                    interaction.user.id,
                    self.skin_id
                )

                # 🔹 Insert new vote
                await conn.execute(
                    """
                    INSERT INTO votes (skin_id, user_id, tier)
                    VALUES ($1, $2, $3)
                    """,
                    self.skin_id,
                    interaction.user.id,
                    TIER_MAP[self.tier]
                )

                # 🔥 NEW: Get total votes
                total_votes = await conn.fetchval(
                    "SELECT COUNT(*) FROM votes WHERE skin_id=$1",
                    self.skin_id
                )

                vote_message_id = skin["vote_message_id"]

            # 🔹 Update embed
            channel = interaction.channel
            msg = await channel.fetch_message(vote_message_id)

            embed = msg.embeds[0]

            # Remove old vote count field if exists
            embed.clear_fields()

            embed.add_field(
                name="🗳️ Votes",
                value=f"{total_votes} votes",
                inline=False
            )

            # (Re-add tier info)
            embed.add_field(
                name="🗳️ Vote",
                value=(
                    "🟣 **S Tier (6 pts)**\n"
                    "🔵 **A Tier (5 pts)**\n"
                    "🟢 **B Tier (4 pts)**\n"
                    "🟡 **C Tier (3 pts)**\n"
                    "🟠 **D Tier (2 pts)**\n"
                    "🔴 **F Tier (1 pt)**"
                ),
                inline=False
            )

            await msg.edit(embed=embed)

            # ✅ Response
            await interaction.response.send_message(
                f"✅ You voted **{self.tier} Tier**",
                ephemeral=True
            )

        except Exception as e:
            print(f"[Vote Error] {e}")
            await interaction.response.send_message(
                "❌ Something went wrong while recording your vote.",
                ephemeral=True
            )