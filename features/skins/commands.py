import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta

from db.pool import get_pool
from .voting import VoteView

VOTING_CHANNEL_ID = 1339872418772160512


class Skins(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="submit_skin")
    async def submit_skin(self, interaction: discord.Interaction, skin_name: str, url: str):
        pool = await get_pool()

        # ends_at = datetime.utcnow() + timedelta(hours=24)
        ends_at = datetime.utcnow() + timedelta(minutes=1)

        async with pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT id FROM skins WHERE image_url=$1",
                url
            )

            if existing:
                await interaction.response.send_message("❌ Duplicate submission.")
                return

            row = await conn.fetchrow("""
                INSERT INTO skins (skin_name, image_url, submitter_id, ends_at, finalized)
                VALUES ($1, $2, $3, $4, FALSE)
                RETURNING id
            """, skin_name, url, interaction.user.id, ends_at)

            skin_id = row["id"]

        await interaction.response.send_message("✅ Skin submitted!")

        channel = self.bot.get_channel(VOTING_CHANNEL_ID)

        embed = discord.Embed(
            title=f"🖼️ {skin_name}",
            description=f"{url}\n🕒 Ends <t:{int(ends_at.timestamp())}:R>",
            color=discord.Color.blue()
        )

        msg = await channel.send(embed=embed, view=VoteView(skin_id))

        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE skins SET vote_message_id=$1 WHERE id=$2",
                msg.id, skin_id
            )


async def setup(bot):
    await bot.add_cog(Skins(bot))