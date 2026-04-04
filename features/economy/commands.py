import discord
from discord import app_commands
from discord.ext import commands
import random

from .service import get_or_create_user, adjust_bonx

SLOT_EMOJIS = ["🍒", "🍋", "🍉", "⭐", "💎"]
MULTIPLIERS = {"🍒": 2, "🍋": 2, "🍉": 3, "⭐": 5, "💎": 10}


class Economy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="balance")
    async def balance(self, interaction: discord.Interaction):
        user = await get_or_create_user(interaction.user.id)
        await interaction.response.send_message(
            f"💰 **Bonx Balance:** {user['bonx']}"
        )

    @app_commands.command(name="profile")
    async def profile(self, interaction: discord.Interaction):
        user = await get_or_create_user(interaction.user.id)
        region = user["region"] or "Unaligned"

        await interaction.response.send_message(
            f"🧾 **Bonkverse Profile**\n"
            f"👤 {interaction.user.display_name}\n"
            f"💰 Bonx: {user['bonx']}\n"
            f"🏷️ Rank: {user['rank']}\n"
            f"🌍 Region: {region}"
        )

    @app_commands.command(name="regions")
    async def regions(self, interaction: discord.Interaction):
        from db.pool import get_pool
        pool = await get_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM regions")

        msg = "\n".join(
            f"**{r['name']}** — {r['description']}" for r in rows
        )

        await interaction.response.send_message(msg)

    @app_commands.command(name="join_region")
    async def join_region(self, interaction: discord.Interaction, region: str):
        from db.pool import get_pool
        pool = await get_pool()

        user = await get_or_create_user(interaction.user.id)

        async with pool.acquire() as conn:
            reg = await conn.fetchrow(
                "SELECT * FROM regions WHERE LOWER(name)=LOWER($1)",
                region
            )

        if not reg:
            await interaction.response.send_message("❌ Region not found.")
            return

        if reg["is_capital"]:
            await interaction.response.send_message(
                "🏛️ **Veri is the capital.** Access is restricted."
            )
            return

        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET region=$1 WHERE discord_id=$2",
                reg["name"], interaction.user.id
            )

        await interaction.response.send_message(
            f"🌍 You joined **{reg['name']}**"
        )

    @app_commands.command(name="slots")
    async def slots(self, interaction: discord.Interaction, amount: int):
        user = await get_or_create_user(interaction.user.id)

        if amount <= 0 or amount > user["bonx"]:
            await interaction.response.send_message("❌ Invalid bet.")
            return

        spin = [random.choice(SLOT_EMOJIS) for _ in range(3)]
        unique = set(spin)

        winnings = -amount
        result = "😢 You lost."

        if len(unique) == 1:
            winnings = amount * MULTIPLIERS[spin[0]]
            result = f"🎉 JACKPOT! {spin[0]}"
        elif len(unique) == 2:
            winnings = 0
            result = "✨ Bet returned."

        await adjust_bonx(interaction.user.id, winnings, "slots")

        updated = await get_or_create_user(interaction.user.id)

        await interaction.response.send_message(
            f"🎰 {' | '.join(spin)}\n{result}\n💰 {updated['bonx']}"
        )


async def setup(bot):
    await bot.add_cog(Economy(bot))