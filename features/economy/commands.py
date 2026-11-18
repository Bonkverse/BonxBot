"""The Bonx commands: /balance, /profile, /slots, /give, /daily, /history,
/admin_add and /leaderboard.

Owns: running each command and choosing who sees the reply. Results are
public; problems are private (utils/ui.refuse).
Does not own: how replies look (cards.py) or the balance rules (service.py).
"""

import random
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button

from config.settings import (
    DAILY_STREAK_CAP,
    DAILY_STREAK_STEP,
    HISTORY_DEFAULT,
    HISTORY_MAX_ENTRIES,
    SLOT_COOLDOWN_SECONDS,
    SLOT_MAX_BET,
    SLOT_TWO_KIND_MULTIPLIER,
    VERI_HISTORY_MAX_ENTRIES,
)
from db.pool import get_pool
from features.accounts import service as accounts
from features.regions.ranks import get_rank_progress, has_region_rank
from features.regions.titles import TITLES, get_titleholder
from utils.ui import BONX, OwnedView, bonx, card, refuse, send, when

from . import cards
from .service import (
    STREAK_WINDOW_HOURS,
    adjust_bonx,
    claim_daily,
    get_or_create_user,
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

HISTORY_PAGE = 10
LEADERBOARD_PAGE = 10

# Friendly labels for ledger reasons shown by /history. Unknown reasons
# (anything a future feature adds) fall back to the raw string.
REASON_LABELS = {
    "daily": "Daily reward",
    "give": "Sent Bonx",
    "receive": "Received Bonx",
    "slots": "Orbana Market slots",
    "admin_add": "Staff grant",
    "skin_vote": "Skin vote reward",
    "vote_change_tax": "Vote change fee",
    "coinflip_stake": "Coinflip stake",
    "coinflip_win": "Coinflip win",
}


def spin_slots():
    """Spin three weighted reels and return (symbols, payout multiplier).

    The multiplier is applied to the bet:
      * 3-of-a-kind: THREE_KIND_MULTIPLIERS[symbol] x bet
      * 2-of-a-kind: SLOT_TWO_KIND_MULTIPLIER x bet
      * all different: 0 (the bet is lost)
    """
    spin = random.choices(SLOT_SYMBOLS, weights=SLOT_WEIGHTS, k=3)
    unique = set(spin)
    if len(unique) == 1:
        return spin, THREE_KIND_MULTIPLIERS[spin[0]]
    if len(unique) == 2:
        return spin, SLOT_TWO_KIND_MULTIPLIER
    return spin, 0


# --- shared lookups ----------------------------------------------------------------


async def leaderboard_place(conn, discord_id: int) -> tuple[int | None, int]:
    """(place, how many players) in /leaderboard order: most Bonx, then oldest ID."""
    total = await conn.fetchval("SELECT COUNT(*) FROM users")
    place = await conn.fetchval(
        """
        SELECT 1 + (SELECT COUNT(*) FROM users o
                    WHERE o.bonx > u.bonx OR (o.bonx = u.bonx AND o.discord_id < u.discord_id))
        FROM users u WHERE u.discord_id = $1
        """,
        discord_id,
    )
    return place, total


def live_streak(user) -> int:
    """The streak only counts while the next daily can still continue it."""
    last = user["last_daily"]
    if not last or not user["daily_streak"]:
        return 0
    lapsed = discord.utils.utcnow().replace(tzinfo=None) - last
    return user["daily_streak"] if lapsed.total_seconds() < STREAK_WINDOW_HOURS * 3600 else 0


# --- views ---------------------------------------------------------------------------


class Pager(OwnedView):
    """◀️ ▶️ page buttons; subclasses say how to draw a page."""

    def __init__(self, owner_id: int, pages: int):
        super().__init__(owner_id, timeout=180)
        self.page = 0
        self.pages = pages
        self.prev_button = self._button("◀️", self._prev)
        self.next_button = self._button("▶️", self._next)
        self._sync()

    def _button(self, label, callback, style=discord.ButtonStyle.secondary):
        button = Button(label=label, style=style)
        button.callback = callback
        self.add_item(button)
        return button

    def _sync(self):
        self.prev_button.disabled = self.page == 0
        self.next_button.disabled = self.page >= self.pages - 1

    async def render(self) -> discord.Embed:
        raise NotImplementedError

    async def show(self, interaction: discord.Interaction, page: int):
        self.page = max(0, min(page, self.pages - 1))
        self._sync()
        await interaction.response.edit_message(embed=await self.render(), view=self)

    async def _prev(self, interaction):
        await self.show(interaction, self.page - 1)

    async def _next(self, interaction):
        await self.show(interaction, self.page + 1)


class HistoryPager(Pager):
    def __init__(self, owner_id: int, lines: list[str]):
        self.lines = lines
        super().__init__(owner_id, -(-len(lines) // HISTORY_PAGE))

    async def render(self):
        return cards.history_page(self.lines, self.page, HISTORY_PAGE)


class LeaderboardPager(Pager):
    """Pages are fetched from the database on demand (LIMIT/OFFSET), never the
    whole users table."""

    def __init__(self, owner_id: int, total: int, viewer_place: int | None):
        self.total = total
        self.viewer_place = viewer_place
        super().__init__(owner_id, max(1, -(-total // LEADERBOARD_PAGE)))
        if viewer_place:
            self._button("Jump to me", self._me, discord.ButtonStyle.primary)

    async def render(self):
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT discord_id, bonx FROM users ORDER BY bonx DESC, discord_id ASC "
                "LIMIT $1 OFFSET $2",
                LEADERBOARD_PAGE, self.page * LEADERBOARD_PAGE,
            )
        return cards.leaderboard_page(
            rows, self.page * LEADERBOARD_PAGE, self.page, self.pages, self.owner_id, self.viewer_place
        )

    async def _me(self, interaction):
        await self.show(interaction, (self.viewer_place - 1) // LEADERBOARD_PAGE)


# --- commands -------------------------------------------------------------------------


class Economy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="balance", description="Check your or another player's Bonx 💰")
    @app_commands.describe(user="Whose balance to check (you, if left empty)")
    async def balance(self, interaction: discord.Interaction, user: discord.User = None):
        target = user or interaction.user
        data = await get_or_create_user(target.id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            place, total = await leaderboard_place(conn, target.id)
        embed = cards.balance_card(
            target.display_name, target.display_avatar.url, data["bonx"], place, total
        )
        await send(interaction, embed)

    @app_commands.command(name="profile", description="A player's Bonx, streak, skins, region and bonk.io account 🌍")
    @app_commands.describe(user="Whose profile to show (you, if left empty)")
    async def profile(self, interaction: discord.Interaction, user: discord.User = None):
        target = user or interaction.user
        own = target.id == interaction.user.id
        await interaction.response.defer()

        data = await get_or_create_user(target.id)
        progress = await get_rank_progress(target.id, claim=own)
        linked = await accounts.accounts_of(target.id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            place, total = await leaderboard_place(conn, target.id)
            skins = await conn.fetchval("SELECT COUNT(*) FROM skins WHERE submitter_id=$1", target.id)

        rank = f"{progress.rank.title} · Tier {progress.tier}"
        if progress.region in TITLES and await get_titleholder(progress.region) == target.id:
            rank = f"👑 {TITLES[progress.region].name} · Tier 6"

        embed = cards.profile_card(cards.Profile(
            name=target.display_name,
            avatar=target.display_avatar.url,
            own=own,
            bonk_name=linked[0]["bonk_username"] if linked else None,
            alts=max(0, len(linked) - 1),
            balance=data["bonx"],
            place=place,
            total=total,
            streak=live_streak(data),
            best_streak=data["best_daily_streak"],
            skins=skins or 0,
            region=progress.region,
            rank=rank,
            founding_resident=bool(data["veri_founding_resident"]),
        ))
        await send(interaction, embed)

    @app_commands.command(
        name="slots",
        description="Gamble Bonx at the Orbana Market 🎰",
        extras={"cooldown": "The reels are still spinning."},
    )
    @app_commands.describe(amount=f"Bet per spin (max {SLOT_MAX_BET:,})")
    @app_commands.checks.cooldown(1, SLOT_COOLDOWN_SECONDS)
    async def slots(self, interaction: discord.Interaction, amount: int):
        if amount <= 0:
            await refuse(interaction, "Bets start at **1 Bonx**.")
            return

        if amount > SLOT_MAX_BET:
            await refuse(
                interaction,
                f"The Orbana Market caps bets at **{bonx(SLOT_MAX_BET)}** a spin. "
                f"Try `/slots {SLOT_MAX_BET}`.",
            )
            return

        spin, multiplier = spin_slots()
        # Net change: payout minus the bet. A loss is -amount; 1.3x is +0.3*amount.
        net = int(amount * multiplier) - amount

        user = await get_or_create_user(interaction.user.id)
        balance = await adjust_bonx(interaction.user.id, net, "slots", minimum=amount)
        if balance is None:
            await refuse(
                interaction,
                f"That bet needs **{bonx(amount)}** and you have **{bonx(user['bonx'])}**.",
                "Not enough Bonx",
            )
            return

        await send(interaction, cards.slots_card(spin, multiplier, amount, net, balance))

    @app_commands.command(name="give", description="Send Bonx to another player 💸")
    @app_commands.describe(user="Who gets the Bonx", amount="How much to send")
    async def give(self, interaction: discord.Interaction, user: discord.User, amount: int):
        if user.id == interaction.user.id:
            await refuse(interaction, "You can't send Bonx to yourself. Pick another player.")
            return

        if user.bot:
            await refuse(interaction, "Bots don't hold Bonx. Pick a player.")
            return

        if amount <= 0:
            await refuse(interaction, "Send at least **1 Bonx**.")
            return

        sender = await get_or_create_user(interaction.user.id)

        if amount > sender["bonx"]:
            await refuse(
                interaction,
                f"You have **{bonx(sender['bonx'])}**, so you can send up to that much.",
                "Not enough Bonx",
            )
            return

        await get_or_create_user(user.id)

        # Transfer + both ledger rows happen in one DB transaction;
        # a crash can't leave the sender minus Bonx that never arrived.
        ok = await transfer_bonx(interaction.user.id, user.id, amount)
        if not ok:
            await refuse(
                interaction,
                "Your balance changed before the transfer went through, so nothing was sent. "
                "Check `/balance` and try again.",
                "Not enough Bonx",
            )
            return

        after = await get_or_create_user(interaction.user.id)
        embed = cards.give_card(interaction.user.mention, user.mention, amount, after["bonx"])
        # Ping the receiver (only them) so they know Bonx arrived.
        await send(
            interaction, embed, content=user.mention,
            mentions=discord.AllowedMentions(users=[user]),
        )

    @app_commands.command(name="daily", description="Claim your daily Bonx 🎁")
    async def daily(self, interaction: discord.Interaction):
        success, data = await claim_daily(interaction.user.id)

        if not success:
            await refuse(
                interaction,
                f"Your next daily is ready {when(data)} ({when(data, 't')}).",
                "Daily already claimed",
            )
            return

        next_claim = discord.utils.utcnow() + timedelta(hours=24)
        embed = cards.daily_card(
            data["amount"], data["streak"],
            step=DAILY_STREAK_STEP, cap=DAILY_STREAK_CAP, next_claim=next_claim,
        )
        await send(interaction, embed)

    @app_commands.command(name="history", description="See your recent Bonx transactions 🧾")
    @app_commands.describe(
        count=f"Entries to show (up to {HISTORY_MAX_ENTRIES}, or {VERI_HISTORY_MAX_ENTRIES} for Veri Tier 3+)"
    )
    async def history(
        self,
        interaction: discord.Interaction,
        count: app_commands.Range[int, 1, VERI_HISTORY_MAX_ENTRIES] = HISTORY_DEFAULT,
    ):
        pool = await get_pool()
        async with pool.acquire() as conn:
            if count > HISTORY_MAX_ENTRIES and not await has_region_rank(
                conn, interaction.user.id, "Veri", 3
            ):
                await refuse(
                    interaction,
                    f"You can see up to **{HISTORY_MAX_ENTRIES}** entries. Veri residents at "
                    f"Tier 3 or higher can see {VERI_HISTORY_MAX_ENTRIES}. "
                    f"Try `/history {HISTORY_MAX_ENTRIES}`.",
                    "Too many entries",
                )
                return

            rows = await conn.fetch(
                "SELECT amount, reason, created_at FROM bonx_transactions "
                "WHERE discord_id=$1 ORDER BY id DESC LIMIT $2",
                interaction.user.id, count,
            )

        if not rows:
            await send(
                interaction,
                card("🧾 No transactions yet", "Claim `/daily` to get your first Bonx.", BONX),
                private=True,
            )
            return

        lines = [
            cards.history_line(
                row["amount"],
                REASON_LABELS.get(row["reason"], row["reason"] or "Unknown")[:40],
                row["created_at"],
            )
            for row in rows
        ]
        pager = HistoryPager(interaction.user.id, lines)
        if pager.pages == 1:
            await send(interaction, await pager.render())
            return
        await send(interaction, await pager.render(), view=pager)
        await pager.attach(interaction)

    @app_commands.command(name="admin_add", description="Add Bonx to a player (staff only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def admin_add(self, interaction: discord.Interaction, user: discord.User, amount: int):
        if amount <= 0:
            await refuse(interaction, "Add at least **1 Bonx**.")
            return

        await get_or_create_user(user.id)
        balance = await adjust_bonx(user.id, amount, "admin_add")
        await send(interaction, cards.grant_card(user.mention, amount, balance))

    @app_commands.command(name="leaderboard", description="The players with the most Bonx 🏆")
    async def leaderboard(self, interaction: discord.Interaction):
        pool = await get_pool()
        async with pool.acquire() as conn:
            place, total = await leaderboard_place(conn, interaction.user.id)

        if not total:
            await send(
                interaction,
                card("🏆 Bonx Leaderboard", "Nobody has Bonx yet. Claim `/daily` to be first.", BONX),
            )
            return

        pager = LeaderboardPager(interaction.user.id, total, place)
        await send(interaction, await pager.render(), view=pager)
        await pager.attach(interaction)


async def setup(bot):
    await bot.add_cog(Economy(bot))
