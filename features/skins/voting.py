import discord
from discord.ui import View, Button
from db.pool import get_pool

TIER_MAP = {
    "S": 6,
    "A": 5,
    "B": 4,
    "C": 3,
    "D": 2,
    "F": 1
}


class VoteView(View):
    def __init__(self, skin_id):
        super().__init__(timeout=None)
        self.skin_id = skin_id

        for tier in ["S", "A", "B", "C", "D", "F"]:
            self.add_item(VoteButton(tier, skin_id))


class VoteButton(Button):
    def __init__(self, tier, skin_id):
        super().__init__(label=tier, style=discord.ButtonStyle.primary)
        self.tier = tier
        self.skin_id = skin_id

    async def callback(self, interaction: discord.Interaction):
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check if voting is still open
            skin = await conn.fetchrow(
                "SELECT ends_at FROM skins WHERE id=$1",
                self.skin_id
            )

            if not skin or skin["ends_at"] < discord.utils.utcnow():
                await interaction.response.send_message(
                    "❌ Voting has ended.", ephemeral=True
                )
                return

            # Remove old vote
            await conn.execute(
                "DELETE FROM votes WHERE user_id=$1 AND skin_id=$2",
                interaction.user.id, self.skin_id
            )

            # Insert new vote
            await conn.execute(
                "INSERT INTO votes (skin_id, user_id, tier) VALUES ($1, $2, $3)",
                self.skin_id, interaction.user.id, TIER_MAP[self.tier]
            )

        await interaction.response.send_message(
            f"✅ Voted **{self.tier} Tier**", ephemeral=True
        )