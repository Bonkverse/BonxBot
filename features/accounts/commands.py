"""Linking bonk.io accounts to Discord: /link, /accounts, /setmain, /unlink, /whois.

Owns: the commands and what they say. Proof works by friend request: /link
starts a 10-minute claim, the user sends a friend request to the bot's bonk.io
account, and verify.py links the account when it arrives. The first account
linked is the main (the name skins are submitted as); later ones are alts.
Does not own: the rules (service.py) or the background check (verify.py).
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from features.accounts import service
from features.accounts.matching import name_problem
from features.accounts.verify import bot_account_name


class Accounts(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="link", description="Link your bonk.io account (or add an alt) 🔗")
    @app_commands.describe(bonk_username="Your bonk.io username, as you log in with it")
    async def link(self, interaction: discord.Interaction, bonk_username: str):
        bot_name = bot_account_name()
        if not bot_name:
            await interaction.response.send_message(
                "Linking isn't set up yet. Ask a staff member.", ephemeral=True
            )
            return
        problem = name_problem(bonk_username)
        if problem:
            await interaction.response.send_message(problem, ephemeral=True)
            return

        start = await service.start_claim(interaction.user.id, bonk_username)
        if start.problem:
            await interaction.response.send_message(start.problem, ephemeral=True)
            return

        embed = discord.Embed(
            title="🔗 Prove it's your account",
            description=(
                f"1. Log in to bonk.io as **{bonk_username}**.\n"
                f"2. Send a friend request to **{bot_name}**.\n"
                f"3. Do it within **{start.minutes_left} min**. I'll DM you when it's linked "
                "(or check `/accounts`).\n\n"
                "The request gets declined automatically, that's normal."
            ),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="accounts", description="See your linked bonk.io accounts")
    async def accounts(self, interaction: discord.Interaction):
        rows = await service.accounts_of(interaction.user.id)
        if not rows:
            await interaction.response.send_message(
                "No bonk.io accounts linked yet. Use `/link`.", ephemeral=True
            )
            return
        lines = [
            f"{'⭐ ' if row['is_main'] else '• '}**{row['bonk_username']}**"
            + (" (main)" if row["is_main"] else "")
            for row in rows
        ]
        footer = "Change your main with `/setmain`, add an alt with `/link`."
        await interaction.response.send_message("\n".join(lines) + f"\n\n{footer}", ephemeral=True)

    @app_commands.command(name="setmain", description="Choose which linked account is your main")
    @app_commands.describe(bonk_username="One of your linked accounts")
    async def setmain(self, interaction: discord.Interaction, bonk_username: str):
        name = await service.set_main(interaction.user.id, bonk_username)
        text = (
            f"⭐ **{name}** is now your main account."
            if name
            else "That account isn't linked to you. See `/accounts`."
        )
        await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(name="unlink", description="Remove one of your linked accounts")
    @app_commands.describe(bonk_username="One of your linked accounts")
    async def unlink(self, interaction: discord.Interaction, bonk_username: str):
        name = await service.unlink(interaction.user.id, bonk_username)
        text = (
            f"Unlinked **{name}**."
            if name
            else "That account isn't linked to you. See `/accounts`."
        )
        await interaction.response.send_message(text, ephemeral=True)

    @setmain.autocomplete("bonk_username")
    @unlink.autocomplete("bonk_username")
    async def _own_accounts(self, interaction: discord.Interaction, current: str):
        rows = await service.accounts_of(interaction.user.id)
        return [
            app_commands.Choice(name=row["bonk_username"], value=row["bonk_username"])
            for row in rows
            if current.casefold() in row["bonk_username"].casefold()
        ][:25]

    @app_commands.command(name="whois", description="Who's who between Discord and bonk.io")
    @app_commands.describe(member="A Discord member", bonk_username="A bonk.io username")
    async def whois(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
        bonk_username: str | None = None,
    ):
        if member is None and not bonk_username:
            await interaction.response.send_message(
                "Give a Discord member or a bonk.io username.", ephemeral=True
            )
            return
        if member is not None:
            rows = await service.accounts_of(member.id)
            if not rows:
                text = f"{member.mention} hasn't linked a bonk.io account."
            else:
                names = ", ".join(
                    f"**{r['bonk_username']}**" + (" (main)" if r["is_main"] else "") for r in rows
                )
                text = f"{member.mention} → {names}"
        else:
            owner = await service.owner_of(bonk_username)
            text = (
                f"**{owner['bonk_username']}** → <@{owner['discord_user_id']}>"
                if owner
                else f"No one has linked **{bonk_username}**."
            )
        await interaction.response.send_message(
            text, allowed_mentions=discord.AllowedMentions.none()
        )


async def setup(bot):
    await bot.add_cog(Accounts(bot))
