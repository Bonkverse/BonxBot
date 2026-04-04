import discord
from discord import app_commands, datetime
from discord.ext import commands
import random

from .service import claim_daily, get_or_create_user, adjust_bonx

SLOT_EMOJIS = ["🍒", "🍋", "🍉", "⭐", "💎"]
MULTIPLIERS = {"🍒": 2, "🍋": 2, "🍉": 3, "⭐": 5, "💎": 10}

from discord.ui import View, Button

class LeaderboardPaginator(View):
    def __init__(self, interaction, entries, per_page=10):
        super().__init__(timeout=60)
        self.entries = entries
        self.per_page = per_page
        self.current_page = 0
        self.interaction = interaction

        self.total_pages = (len(entries) - 1) // per_page + 1

        self.prev_button = Button(label="◀️", style=discord.ButtonStyle.secondary)
        self.next_button = Button(label="▶️", style=discord.ButtonStyle.secondary)

        self.prev_button.callback = self.prev_page
        self.next_button.callback = self.next_page

        self.add_item(self.prev_button)
        self.add_item(self.next_button)

    def get_embed(self):
        start = self.current_page * self.per_page
        end = start + self.per_page
        page = self.entries[start:end]

        embed = discord.Embed(
            title="🏆 Bonx Leaderboard",
            color=discord.Color.gold()
        )

        description = ""
        for i, row in enumerate(page, start=start + 1):
            description += f"**#{i}** <@{row['discord_id']}> — 💰 {row['bonx']}\n"

        embed.description = description
        embed.set_footer(text=f"Page {self.current_page+1}/{self.total_pages}")
        return embed

    async def prev_page(self, interaction: discord.Interaction):
        if interaction.user != self.interaction.user:
            await interaction.response.send_message("❌ Not your paginator.", ephemeral=True)
            return

        if self.current_page > 0:
            self.current_page -= 1
            await interaction.response.edit_message(embed=self.get_embed(), view=self)

    async def next_page(self, interaction: discord.Interaction):
        if interaction.user != self.interaction.user:
            await interaction.response.send_message("❌ Not your paginator.", ephemeral=True)
            return

        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            await interaction.response.edit_message(embed=self.get_embed(), view=self)


class Economy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="balance", description="Check your or another user's Bonx balance 💰")
    @app_commands.describe(user="Optional: user to check")
    async def balance(self, interaction: discord.Interaction, user: discord.User = None):
        target = user or interaction.user

        data = await get_or_create_user(target.id)

        await interaction.response.send_message(
            f"💰 **{target.display_name}'s Balance:** {data['bonx']}"
        )

    @app_commands.command(name="profile", description="View your Bonkverse profile 🌍")
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

    @app_commands.command(name="regions", description="View regions of Orbana")
    async def regions(self, interaction: discord.Interaction):
        from db.pool import get_pool
        pool = await get_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM regions")

        msg = "\n".join(
            f"**{r['name']}** — {r['description']}" for r in rows
        )

        await interaction.response.send_message(msg)

    @app_commands.command(name="join_region", description="Join a region of Orbana")
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

    @app_commands.command(name="slots", description="Gamble Bonx at the Orbana Market 🎰")
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

    @app_commands.command(name="give", description="Give Bonx to another user 💸")
    async def give(self, interaction: discord.Interaction, user: discord.User, amount: int):
        if user.id == interaction.user.id:
            await interaction.response.send_message("❌ You can't give Bonx to yourself.")
            return

        sender = await get_or_create_user(interaction.user.id)

        if amount <= 0 or amount > sender["bonx"]:
            await interaction.response.send_message("❌ Invalid amount.")
            return

        # Deduct from sender
        await adjust_bonx(interaction.user.id, -amount, "give")

        # Add to receiver
        await get_or_create_user(user.id)
        await adjust_bonx(user.id, amount, "receive")

        await interaction.response.send_message(
            f"💸 You sent **{amount} Bonx** to {user.mention}!"
        )

    @app_commands.command(name="daily", description="Claim your daily Bonx 🎁")
    async def daily(self, interaction: discord.Interaction):
        success, data = await claim_daily(interaction.user.id)

        if not success:
            remaining = data - datetime.utcnow()
            hours = int(remaining.total_seconds() // 3600)

            await interaction.response.send_message(
                f"⏳ You already claimed your daily reward.\nTry again in {hours} hours."
            )
            return

        await interaction.response.send_message(
            "🎁 You claimed **1000 Bonx**!"
        )
    
    @app_commands.command(name="admin_add", description="Add Bonx to a user (Admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def admin_add(self, interaction: discord.Interaction, user: discord.User, amount: int):
        if amount <= 0:
            await interaction.response.send_message("❌ Amount must be positive.")
            return

        await get_or_create_user(user.id)
        await adjust_bonx(user.id, amount, "admin_add")

        await interaction.response.send_message(
            f"🛠️ Added **{amount} Bonx** to {user.mention}"
        )

    @app_commands.command(name="leaderboard", description="View the top Bonx holders 🏆")
    async def leaderboard(self, interaction: discord.Interaction):
        from db.pool import get_pool
        pool = await get_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT discord_id, bonx FROM users ORDER BY bonx DESC"
            )

        if not rows:
            await interaction.response.send_message("No data yet.")
            return

        paginator = LeaderboardPaginator(interaction, rows)

        await interaction.response.send_message(
            embed=paginator.get_embed(),
            view=paginator
        )


async def setup(bot):
    await bot.add_cog(Economy(bot))