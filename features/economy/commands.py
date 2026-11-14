import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone
import random

from config.settings import (
    SLOT_TWO_KIND_MULTIPLIER,
    SLOT_MAX_BET,
    SLOT_COOLDOWN_SECONDS,
    DAILY_STREAK_STEP,
    DAILY_STREAK_CAP,
    HISTORY_MAX_ENTRIES,
    HISTORY_DEFAULT,
    VERI_HISTORY_MAX_ENTRIES,
)
from features.regions.ranks import get_rank_progress, has_region_rank

from .service import (
    claim_daily,
    get_or_create_user,
    adjust_bonx,
    transfer_bonx,
)

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

# Two-of-a-kind pays SLOT_TWO_KIND_MULTIPLIER (from config.settings).
# It used to be 1.5x, which gave players +7.69% per spin on average —
# the market was printing money. 1.3x puts the edge at ~3.5% against
# the player, a normal house margin.


from discord.ui import View, Button


class LeaderboardPaginator(View):
    """Leaderboard pages are fetched from the database on demand.

    The old version loaded the entire users table on every /leaderboard
    call and paginated in memory. Now only the visible page (LIMIT/OFFSET)
    is fetched per button press.
    """

    def __init__(self, interaction, per_page=10):
        super().__init__(timeout=60)
        self.per_page = per_page
        self.current_page = 0
        self.interaction = interaction
        self.total_users = 0

        self.prev_button = Button(label="◀️", style=discord.ButtonStyle.secondary)
        self.next_button = Button(label="▶️", style=discord.ButtonStyle.secondary)

        self.prev_button.callback = self.prev_page
        self.next_button.callback = self.next_page

        self.add_item(self.prev_button)
        self.add_item(self.next_button)

    @property
    def total_pages(self):
        return max(1, (self.total_users + self.per_page - 1) // self.per_page)

    async def load_count(self):
        from db.pool import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            self.total_users = await conn.fetchval("SELECT COUNT(*) FROM users")

    async def fetch_page(self, page: int):
        from db.pool import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            return await conn.fetch(
                "SELECT discord_id, bonx FROM users "
                "ORDER BY bonx DESC, discord_id ASC "
                "LIMIT $1 OFFSET $2",
                self.per_page, page * self.per_page
            )

    def get_embed(self, page_rows):
        start = self.current_page * self.per_page

        embed = discord.Embed(
            title="🏆 Bonx Leaderboard",
            color=discord.Color.gold()
        )

        description = ""
        for i, row in enumerate(page_rows, start=start + 1):
            description += f"**#{i}** <@{row['discord_id']}> — 💰 {row['bonx']}\n"

        if not description:
            description = "*No entries on this page.*"

        embed.description = description
        embed.set_footer(text=f"Page {self.current_page+1}/{self.total_pages}")
        return embed

    async def prev_page(self, interaction: discord.Interaction):
        if interaction.user != self.interaction.user:
            await interaction.response.send_message("❌ Not your paginator.", ephemeral=True)
            return

        if self.current_page > 0:
            self.current_page -= 1
            rows = await self.fetch_page(self.current_page)
            await interaction.response.edit_message(embed=self.get_embed(rows), view=self)

    async def next_page(self, interaction: discord.Interaction):
        if interaction.user != self.interaction.user:
            await interaction.response.send_message("❌ Not your paginator.", ephemeral=True)
            return

        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            rows = await self.fetch_page(self.current_page)
            await interaction.response.edit_message(embed=self.get_embed(rows), view=self)


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
      * 2-of-a-kind: pays SLOT_TWO_KIND_MULTIPLIER x bet (net win = mult-1 x bet)
      * all different: loses bet (net = -bet)
    """
    spin = random.choices(SLOT_SYMBOLS, weights=SLOT_WEIGHTS, k=3)
    unique = set(spin)

    if len(unique) == 1:
        mult = THREE_KIND_MULTIPLIERS[spin[0]]
        return spin, mult, f"🎉 JACKPOT! Three {spin[0]} — {mult}x payout!"
    if len(unique) == 2:
        return spin, SLOT_TWO_KIND_MULTIPLIER, "✨ Two of a kind — small win!"
    return spin, 0, "😢 No match."


# Friendly labels for ledger reasons shown by /history. Unknown reasons
# (anything a future feature adds) fall back to the raw string.
REASON_LABELS = {
    "daily": "Daily reward",
    "give": "Sent Bonx",
    "receive": "Received Bonx",
    "slots": "Orbana Market slots",
    "admin_add": "Admin grant",
    "skin_vote": "Skin vote reward",
    "vote_change_tax": "Vote change tax",
    "coinflip_stake": "Coinflip stake",
    "coinflip_win": "Coinflip win",
}


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
        progress = await get_rank_progress(interaction.user.id, claim=True)
        region = progress.region or "Unaligned"
        founding_tag = "\n🏛️ Founding Resident of Veri" if user["veri_founding_resident"] else ""

        await interaction.response.send_message(
            f"🧾 **Bonkverse Profile**\n"
            f"👤 {interaction.user.display_name}\n"
            f"💰 Bonx: {user['bonx']}\n"
            f"🏷️ Rank: {progress.rank.title}\n"
            f"🌍 Region: {region}{founding_tag}"
        )

    @app_commands.command(name="slots", description="Gamble Bonx at the Orbana Market 🎰")
    @app_commands.describe(amount=f"Bet per spin (max {SLOT_MAX_BET})")
    @app_commands.checks.cooldown(1, SLOT_COOLDOWN_SECONDS)
    async def slots(self, interaction: discord.Interaction, amount: int):
        if amount <= 0:
            await interaction.response.send_message("❌ Invalid bet.")
            return

        if amount > SLOT_MAX_BET:
            await interaction.response.send_message(
                f"🎰 The Orbana Market caps bets at **{SLOT_MAX_BET} Bonx** per spin."
            )
            return

        spin, multiplier, result = spin_slots()

        # Net change: payout - original bet
        # mult=0 (loss) -> -amount
        # mult=1.3 (two-match) -> +0.3*amount
        # mult=3/5/10 (jackpot) -> +(mult-1)*amount
        winnings = int(amount * multiplier) - amount

        await get_or_create_user(interaction.user.id)
        balance = await adjust_bonx(interaction.user.id, winnings, "slots", minimum=amount)
        if balance is None:
            await interaction.response.send_message("❌ You don't have enough Bonx for that bet.")
            return

        # Show net change with sign for clarity
        if winnings > 0:
            change_text = f"+{winnings}"
        else:
            change_text = str(winnings)

        await interaction.response.send_message(
            f"🎰 {' | '.join(spin)}\n{result}\n"
            f"💰 {change_text} Bonx → Balance: {balance}"
        )

    @slots.error
    async def slots_error(self, interaction: discord.Interaction, error):
        # Without this, a cooldown hit just looks like "interaction failed".
        if isinstance(error, app_commands.CommandOnCooldown):
            await interaction.response.send_message(
                f"⏳ The reels are still spinning — try again in {error.retry_after:.0f}s."
            )
        else:
            raise error

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

        await get_or_create_user(user.id)

        # Transfer + both ledger rows happen in one DB transaction now;
        # a crash can no longer leave the sender minus Bonx that never arrived.
        ok = await transfer_bonx(interaction.user.id, user.id, amount)
        if not ok:
            await interaction.response.send_message(
                "❌ You don't have enough Bonx anymore."
            )
            return

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

        amount = data["amount"]
        streak = data["streak"]

        msg = f"🎁 You claimed **{amount} Bonx**! (streak: {streak})"

        if streak <= DAILY_STREAK_CAP:
            msg += (
                f"\n🔥 Come back tomorrow for day {streak + 1} "
                f"(+{DAILY_STREAK_STEP} bonus Bonx)."
            )
        else:
            msg += "\n🔥 Streak maxed out — you're earning the top daily payout."

        await interaction.response.send_message(msg)

    @app_commands.command(name="history", description="See your recent Bonx transactions 🧾")
    @app_commands.describe(
        count=f"Entries to show (up to {HISTORY_MAX_ENTRIES}, or {VERI_HISTORY_MAX_ENTRIES} for Veri Tier 3+)"
    )
    async def history(
        self,
        interaction: discord.Interaction,
        count: app_commands.Range[int, 1, VERI_HISTORY_MAX_ENTRIES] = HISTORY_DEFAULT
    ):
        from db.pool import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            if count > HISTORY_MAX_ENTRIES and not await has_region_rank(
                conn, interaction.user.id, "Veri", 3
            ):
                await interaction.response.send_message(
                    f"📜 You can request up to {HISTORY_MAX_ENTRIES} entries. "
                    f"Veri residents at Tier 3 or higher can request {VERI_HISTORY_MAX_ENTRIES}.",
                )
                return

            rows = await conn.fetch(
                "SELECT amount, reason, created_at FROM bonx_transactions "
                "WHERE discord_id=$1 ORDER BY id DESC LIMIT $2",
                interaction.user.id, count,
            )

        if not rows:
            await interaction.response.send_message(
                "📭 No transactions yet — try `/daily` to get started!"
            )
            return

        lines = []
        for row in rows:
            label = REASON_LABELS.get(row["reason"], row["reason"] or "Unknown")[:40]
            when = f"<t:{int(row['created_at'].replace(tzinfo=timezone.utc).timestamp())}:R>"
            lines.append(f"**{row['amount']:+}** — {label} · {when}")

        embeds = []
        for start in range(0, len(lines), HISTORY_MAX_ENTRIES):
            batch = lines[start:start + HISTORY_MAX_ENTRIES]
            title = f"🧾 Recent Bonx transactions · {start + 1}–{start + len(batch)} of {len(lines)}"
            embeds.append(discord.Embed(
                title=title, description="\n".join(batch), color=discord.Color.blurple()
            ))

        await interaction.response.send_message(embeds=embeds)

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
        paginator = LeaderboardPaginator(interaction)
        await paginator.load_count()

        if paginator.total_users == 0:
            await interaction.response.send_message("No data yet.")
            return

        rows = await paginator.fetch_page(0)

        await interaction.response.send_message(
            embed=paginator.get_embed(rows),
            view=paginator
        )


async def setup(bot):
    await bot.add_cog(Economy(bot))
