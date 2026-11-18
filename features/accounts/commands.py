"""Linking bonk.io accounts to Discord: /link, /accounts, /setmain, /unlink, /whois.

Owns: the commands and what they say. Proof works by friend request: /link
starts a 10-minute claim, the user sends a friend request to the bot's bonk.io
account, and verify.py links the account when it arrives. The first account
linked is the main (the name skins are submitted as); later ones are alts.
Does not own: the rules (service.py) or the background check (verify.py).

Every reply is an embed in the channel, except /link's: its instructions and
problems are only for the person verifying. Replies never ping anyone.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from features.accounts import service
from features.accounts.embeds import SUCCESS, account_embed, problem_embed
from features.accounts.matching import name_problem
from features.accounts.verify import bot_account_name


async def _reply(interaction: discord.Interaction, embed: discord.Embed, *, private: bool = False):
    await interaction.response.send_message(
        embed=embed, ephemeral=private, allowed_mentions=discord.AllowedMentions.none()
    )


class Accounts(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="link", description="Link your bonk.io account (or add an alt) 🔗")
    @app_commands.describe(bonk_username="Your bonk.io username, as you log in with it")
    async def link(self, interaction: discord.Interaction, bonk_username: str):
        bot_name = bot_account_name()
        if not bot_name:
            await _reply(
                interaction, problem_embed("Linking isn't set up yet. Ask a staff member."), private=True
            )
            return
        problem = name_problem(bonk_username)
        if problem:
            await _reply(interaction, problem_embed(problem), private=True)
            return

        start = await service.start_claim(interaction.user.id, bonk_username)
        if start.problem:
            await _reply(interaction, problem_embed(start.problem), private=True)
            return

        embed = account_embed(
            "🔗 Prove it's your account",
            (
                f"1. Log in to bonk.io as **{bonk_username}**.\n"
                f"2. Send a friend request to **{bot_name}**.\n"
                f"3. Do it within **{start.minutes_left} min**.\n\n"
                "I'll DM you when it's linked (or check `/accounts`). "
                "The request gets declined automatically, that's normal."
            ),
        )
        await _reply(interaction, embed, private=True)

    @app_commands.command(name="accounts", description="See your linked bonk.io accounts")
    async def accounts(self, interaction: discord.Interaction):
        rows = await service.accounts_of(interaction.user.id)
        if not rows:
            embed = account_embed("🔗 Your bonk.io accounts", "None linked yet. Use `/link`.")
        else:
            lines = [
                f"⭐ **{row['bonk_username']}** (main)" if row["is_main"]
                else f"• **{row['bonk_username']}**"
                for row in rows
            ]
            embed = account_embed("🔗 Your bonk.io accounts", "\n".join(lines))
            embed.set_footer(text="Change your main with /setmain, add an alt with /link.")
        await _reply(interaction, embed)

    @app_commands.command(name="setmain", description="Choose which linked account is your main")
    @app_commands.describe(bonk_username="One of your linked accounts")
    async def setmain(self, interaction: discord.Interaction, bonk_username: str):
        name = await service.set_main(interaction.user.id, bonk_username)
        if name:
            embed = account_embed("⭐ Main account", f"**{name}** is now your main account.", SUCCESS)
        else:
            embed = problem_embed("That account isn't linked to you. See `/accounts`.")
        await _reply(interaction, embed)

    @app_commands.command(name="unlink", description="Remove one of your linked accounts")
    @app_commands.describe(bonk_username="One of your linked accounts")
    async def unlink(self, interaction: discord.Interaction, bonk_username: str):
        name = await service.unlink(interaction.user.id, bonk_username)
        if name:
            embed = account_embed("Unlinked", f"**{name}** is no longer linked to you.", SUCCESS)
        else:
            embed = problem_embed("That account isn't linked to you. See `/accounts`.")
        await _reply(interaction, embed)

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
            await _reply(interaction, problem_embed("Give a Discord member or a bonk.io username."))
            return
        if member is not None:
            rows = await service.accounts_of(member.id)
            if not rows:
                text = f"{member.mention} hasn't linked a bonk.io account."
            else:
                text = f"{member.mention} → " + ", ".join(
                    f"**{r['bonk_username']}**" + (" (main)" if r["is_main"] else "") for r in rows
                )
        else:
            owner = await service.owner_of(bonk_username)
            text = (
                f"**{owner['bonk_username']}** → <@{owner['discord_user_id']}>"
                if owner
                else f"No one has linked **{bonk_username}**."
            )
        await _reply(interaction, account_embed("🔎 Who is it?", text))


async def setup(bot):
    await bot.add_cog(Accounts(bot))
