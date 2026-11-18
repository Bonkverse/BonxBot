"""PvP coinflip — the first zero-sum Bonx game.

Unlike the slots, a coinflip moves Bonx between players instead of minting
it, so it has zero effect on economy inflation no matter how much it's used.

Flow:
  /coinflip @user <amount>
    -> challenge message with Accept / Decline buttons (2 minute timer)
    -> Accept: both stakes are escrowed and the winner is paid inside one
       DB transaction (features/economy/service.py resolve_coinflip)
    -> Decline / timeout: nothing moves

Only one pending challenge can exist between a pair of players at a time
(tracked in memory; resets on restart, which just means challenges can be
re-issued — no money ever depends on this set).
"""

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View

from utils.ui import bonx, refuse

from .service import get_or_create_user, resolve_coinflip

CHALLENGE_TIMEOUT_SECONDS = 120

# frozenset({challenger_id, target_id}) — direction-agnostic, so A challenging
# B blocks B challenging A while the first challenge is pending.
_pending_challenges: set = set()


class CoinflipChallenge(View):
    def __init__(self, origin: discord.Interaction, challenger, target, amount: int):
        super().__init__(timeout=CHALLENGE_TIMEOUT_SECONDS)
        self.origin = origin  # the /coinflip interaction; its response IS the challenge message
        self.challenger = challenger
        self.target = target
        self.amount = amount
        self.resolved = False

    @property
    def pair_key(self):
        return frozenset((self.challenger.id, self.target.id))

    def release(self):
        _pending_challenges.discard(self.pair_key)

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.target.id:
            await refuse(
                interaction,
                f"Only {self.target.mention} can accept or decline this challenge.",
                "Not your challenge",
            )
            return False
        return True

    async def on_timeout(self):
        if self.resolved:
            return
        self.release()
        embed = discord.Embed(
            title="🪙 Coinflip expired",
            description=(
                f"{self.challenger.mention}'s challenge to {self.target.mention} "
                f"timed out — no Bonx moved."
            ),
            color=discord.Color.dark_grey()
        )
        try:
            await self.origin.edit_original_response(embed=embed, view=None)
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.resolved:
            await interaction.response.send_message(
                "❌ This challenge was already answered.", ephemeral=True
            )
            return
        self.resolved = True
        self.release()

        # Defer first — the DB work below can outlive the 3s interaction window.
        await interaction.response.defer()
        result = await resolve_coinflip(self.challenger.id, self.target.id, self.amount)

        if not result:
            embed = discord.Embed(
                title="🪙 Coinflip cancelled",
                description=(
                    f"One of you can't cover a **{bonx(self.amount)}** stake anymore, "
                    f"so no Bonx moved."
                ),
                color=discord.Color.red()
            )
            await self.origin.edit_original_response(embed=embed, view=None)
            return

        if result["winner_id"] == self.challenger.id:
            winner, loser = self.challenger, self.target
        else:
            winner, loser = self.target, self.challenger

        embed = discord.Embed(
            title=f"🪙 {result['side']}!",
            description=(
                f"{winner.mention} wins **{bonx(result['amount'] * 2)}**\n"
                f"Better luck next time, {loser.mention}."
            ),
            color=discord.Color.gold()
        )
        embed.add_field(
            name=f"💰 {winner.display_name}",
            value=bonx(result['winner_balance']),
            inline=True
        )
        embed.add_field(
            name=f"💸 {loser.display_name}",
            value=bonx(result['loser_balance']),
            inline=True
        )

        await self.origin.edit_original_response(embed=embed, view=None)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.resolved:
            await interaction.response.send_message(
                "❌ This challenge was already answered.", ephemeral=True
            )
            return
        self.resolved = True
        self.release()

        embed = discord.Embed(
            title="🪙 Coinflip declined",
            description=f"{self.target.mention} declined the challenge — no Bonx moved.",
            color=discord.Color.dark_grey()
        )
        await interaction.response.edit_message(embed=embed, view=None)


class EconomyGames(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="coinflip",
        description="Challenge another player to a 50/50 coinflip for Bonx 🪙"
    )
    @app_commands.describe(
        user="Who you are challenging",
        amount="Stake both players put in — winner takes both"
    )
    async def coinflip(self, interaction: discord.Interaction, user: discord.User, amount: int):
        if user.id == interaction.user.id:
            await refuse(interaction, "You can't flip against yourself. Challenge another player.")
            return

        if user.bot:
            await refuse(interaction, "Bots don't hold Bonx. Challenge a player.")
            return

        if amount <= 0:
            await refuse(interaction, "Stakes start at **1 Bonx**.")
            return

        challenger = await get_or_create_user(interaction.user.id)
        target = await get_or_create_user(user.id)

        if amount > challenger["bonx"]:
            await refuse(
                interaction,
                f"You have **{bonx(challenger['bonx'])}**, so you can stake up to that much.",
                "Not enough Bonx",
            )
            return

        if amount > target["bonx"]:
            await refuse(
                interaction,
                f"{user.mention} only has **{bonx(target['bonx'])}**, so they can't cover "
                f"that stake. Try `/coinflip` with {bonx(target['bonx'])} or less.",
                "They can't cover it",
            )
            return

        pair_key = frozenset((interaction.user.id, user.id))
        if pair_key in _pending_challenges:
            await refuse(
                interaction,
                f"You and {user.mention} already have a coinflip waiting. "
                "It has to be accepted, declined or expire first.",
                "Challenge already open",
            )
            return

        _pending_challenges.add(pair_key)

        embed = discord.Embed(
            title="🪙 Coinflip Challenge",
            description=(
                f"{interaction.user.mention} challenges {user.mention} for "
                f"**{bonx(amount)}** each.\n\n"
                f"Winner takes **{bonx(amount * 2)}**. "
                f"Expires <t:{int(discord.utils.utcnow().timestamp()) + CHALLENGE_TIMEOUT_SECONDS}:R>."
            ),
            color=discord.Color.blue()
        )

        view = CoinflipChallenge(
            origin=interaction,
            challenger=interaction.user,
            target=user,
            amount=amount
        )

        try:
            await interaction.response.send_message(
                content=f"{user.mention} — you've been challenged!",
                embed=embed,
                view=view,
            )
        except discord.HTTPException:
            view.stop()
            view.release()
            raise


async def setup(bot):
    await bot.add_cog(EconomyGames(bot))
