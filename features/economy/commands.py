import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime
import random

from .service import claim_daily, get_or_create_user, adjust_bonx

# Weighted reel symbols — common symbols (cherry, lemon) hit often,
# rare symbols (diamond) hit seldom. This makes 2-matches frequent
# enough to feel rewarding and 3-diamonds genuinely exciting.
SYMBOL_WEIGHTS = {
    "🍒": 35,
    "🍋": 30,
    "🍉": 20,
    "⭐": 10,
    "💎": 5,
}
SLOT_SYMBOLS = list(SYMBOL_WEIGHTS.keys())
SLOT_WEIGHTS = list(SYMBOL_WEIGHTS.values())

# Payout multipliers for three-of-a-kind. Rarer symbols pay more.
THREE_KIND_MULTIPLIERS = {
    "🍒": 3,
    "🍋": 3,
    "🍉": 3,
    "⭐": 5,
    "💎": 10,
}

# Two-of-a-kind now pays 1.5x (small win) instead of just returning the bet
TWO_KIND_MULTIPLIER = 1.5


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


def format_time_remaining(delta) -> str:
    """Format a timedelta as a human-readable 'X hours Y minutes' string.

    Handles the edge case where total time is under an hour — previously
    `int(seconds // 3600)` truncated to 0 and showed 'Try again in 0 hours.'
    """
    total_seconds = int(delta.total_seconds())
    if total_seconds <= 0:
        return "a moment"

    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60

    if hours == 0:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    if minutes == 0:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    return f"{hours}h {minutes}m"


def spin_slots():
    """Spin three weighted reels and return (symbols, winnings_multiplier, result_text).

    Multiplier is applied to the bet amount:
      * 3-of-a-kind: pays THREE_KIND_MULTIPLIERS[symbol] x bet (net win = mult-1 x bet)
      * 2-of-a-kind: pays 1.5x bet (net win = 0.5x bet)
      * all different: loses bet (net = -bet)
    """
    spin = random.choices(SLOT_SYMBOLS, weights=SLOT_WEIGHTS, k=3)
    unique = set(spin)

    if len(unique) == 1:
        mult = THREE_KIND_MULTIPLIERS[spin[0]]
        return spin, mult, f"🎉 JACKPOT! Three {spin[0]} — {mult}x payout!"
    if len(unique) == 2:
        return spin, TWO_KIND_MULTIPLIER, "✨ Two of a kind — small win!"
    return spin, 0, "😢 No match."


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

        await get_or_create_user(interaction.user.id)

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

        spin, multiplier, result = spin_slots()

        # Net change: payout - original bet
        # mult=0 (loss) -> -amount
        # mult=1.5 (two-match) -> +0.5*amount
        # mult=3/5/10 (jackpot) -> +(mult-1)*amount
        winnings = int(amount * multiplier) - amount

        await adjust_bonx(interaction.user.id, winnings, "slots")

        updated = await get_or_create_user(interaction.user.id)

        # Show net change with sign for clarity
        if winnings > 0:
            change_text = f"+{winnings}"
        else:
            change_text = str(winnings)

        await interaction.response.send_message(
            f"🎰 {' | '.join(spin)}\n{result}\n"
            f"💰 {change_text} Bonx → Balance: {updated['bonx']}"
        )

    @app_commands.command(name="give", description="Give Bonx to another user 💸")
    async def give(self, interaction: discord.Interaction, user: discord.User, amount: int):
        if user.id == interaction.user.id:
            await interaction.response.send_message("❌ You can't give Bonx to yourself.")
            return

        if user.bot:
            await interaction.response.send_message("❌ You can't give Bonx to a bot.")
            return

        sender = await get_or_create_user(interaction.user.id)

        if amount <= 0 or amount > sender["bonx"]:
            await interaction.response.send_message("❌ Invalid amount.")
            return

        await adjust_bonx(interaction.user.id, -amount, "give")

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
            await interaction.response.send_message(
                f"⏳ You already claimed your daily reward.\n"
                f"Try again in {format_time_remaining(remaining)}."
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