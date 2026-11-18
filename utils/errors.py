"""What a slash command says when it can't run: on cooldown, missing a
permission, or crashed. Registered on the command tree in bot.py, so it
covers every command; without it Discord just shows "This interaction failed".

A command can set its own cooldown line with
`@app_commands.command(..., extras={"cooldown": "The reels are still spinning."})`.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands

from utils.ui import refuse

logger = logging.getLogger(__name__)

CRASHED = (
    "Something broke on our side, and nothing was changed. Try again in a minute. "
    "If it keeps happening, let a staff member know which command you ran."
)


def explain(interaction: discord.Interaction, error: app_commands.AppCommandError) -> tuple[str, str]:
    """(title, text) for the private reply."""
    if isinstance(error, app_commands.CommandOnCooldown):
        command = interaction.command
        lead = (command.extras.get("cooldown") if command else None) or "Slow down a little."
        return "On cooldown", f"{lead} Try again in {max(1, round(error.retry_after))}s."
    if isinstance(error, (app_commands.MissingPermissions, app_commands.CheckFailure)):
        return "Staff only", "That command is for server staff."
    return "That didn't work", CRASHED


async def on_app_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    if not isinstance(error, (app_commands.CommandOnCooldown, app_commands.CheckFailure)):
        name = interaction.command.qualified_name if interaction.command else "?"
        logger.error("/%s failed", name, exc_info=error)
    title, text = explain(interaction, error)
    try:
        await refuse(interaction, text, title)
    except discord.HTTPException:
        pass  # the interaction expired; the log line above is all we can do
