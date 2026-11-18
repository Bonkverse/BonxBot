"""The look every account message shares: command replies (commands.py) and
the DMs the link check sends (verify.py). Colors come from utils/ui.py."""

import discord

from utils.ui import INFO, PROBLEM, SUCCESS, card, problem

__all__ = ["INFO", "PROBLEM", "SUCCESS", "account_embed", "problem_embed"]


def account_embed(title: str, description: str, color=INFO) -> discord.Embed:
    return card(title, description, color)


def problem_embed(text: str) -> discord.Embed:
    return problem(text)
