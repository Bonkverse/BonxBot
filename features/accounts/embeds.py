"""The look every account message shares: command replies (commands.py) and
the DMs the link check sends (verify.py)."""

import discord

INFO = discord.Color.blurple()
SUCCESS = discord.Color.green()
PROBLEM = discord.Color.red()


def account_embed(title: str, description: str, color=INFO) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=color)


def problem_embed(text: str) -> discord.Embed:
    return account_embed("Can't do that", text, PROBLEM)
