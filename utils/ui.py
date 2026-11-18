"""How BonxBot looks and talks. Every reply should come through here.

Colors
    BONX     gold    anything about Bonx: balances, payouts, the leaderboard
    INFO     blurple lookups and lore: profiles, regions, help
    SUCCESS  green   something you asked for went through
    PROBLEM  red     something you asked for didn't happen
    MUTED    grey    cancelled, expired, declined

Who sees what
    Problems are private (only the person who ran the command sees them), so
    a typo doesn't clutter the channel. Results people want to show off
    (wins, transfers, profiles, votes) are public.

Voice
    Say what happened and, for problems, how to fix it. One line where one
    line works. Use the game's words: Bonx, bonk.io name, BL link, region,
    deed, tier. No "Oops!", no "Thanks for using", no exclamation marks on
    routine replies.
"""

from __future__ import annotations

from datetime import datetime, timezone

import discord

BONX = discord.Color.gold()
INFO = discord.Color.blurple()
SUCCESS = discord.Color.green()
PROBLEM = discord.Color.red()
MUTED = discord.Color.light_grey()

NO_PINGS = discord.AllowedMentions.none()


def bonx(amount: int) -> str:
    """`1234` -> `1,234 Bonx`. Signed amounts keep their sign: `+300 Bonx`."""
    return f"{amount:,} Bonx"


def signed_bonx(amount: int) -> str:
    return f"{amount:+,} Bonx"


def when(moment: datetime, style: str = "R") -> str:
    """A Discord timestamp, shown in each reader's own time zone.

    Naive datetimes are UTC, which is how the database stores them."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return f"<t:{int(moment.timestamp())}:{style}>"


def card(title: str | None = None, description: str | None = None, color=INFO) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=color)


def problem(text: str, title: str = "Can't do that") -> discord.Embed:
    return card(title, text, PROBLEM)


async def send(
    interaction: discord.Interaction,
    embed: discord.Embed,
    *,
    private: bool = False,
    view: discord.ui.View | None = None,
    content: str | None = None,
    mentions: discord.AllowedMentions = NO_PINGS,
) -> None:
    """Reply to a slash command or button, whether or not it was deferred."""
    kwargs = {"embed": embed, "ephemeral": private, "allowed_mentions": mentions}
    if view is not None:
        kwargs["view"] = view
    if content is not None:
        kwargs["content"] = content
    if interaction.response.is_done():
        if private and _thinking_in_public(interaction):
            # The first followup would replace the public "thinking…" message
            # and stay public, so remove it and send the private reply after.
            try:
                await interaction.delete_original_response()
            except discord.HTTPException:
                pass
        await interaction.followup.send(**kwargs)
    else:
        await interaction.response.send_message(**kwargs)


def _thinking_in_public(interaction: discord.Interaction) -> bool:
    """Whether the command was deferred with a "BonxBot is thinking…" message.

    (Button presses defer as a message update, which leaves nothing to remove.)"""
    kind = getattr(interaction.response, "type", None)
    return kind == discord.InteractionResponseType.deferred_channel_message


async def refuse(interaction: discord.Interaction, text: str, title: str = "Can't do that") -> None:
    """A private problem reply: what's wrong and how to fix it."""
    await send(interaction, problem(text, title), private=True)


class OwnedView(discord.ui.View):
    """Buttons only the person who ran the command can press, which switch
    themselves off when they time out instead of failing silently.

    Set `self.message` after sending (or let `attach` do it) so the timeout
    can grey the buttons out."""

    not_yours = "These buttons belong to whoever ran the command."

    def __init__(self, owner_id: int, *, timeout: float | None = 120):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.message: discord.Message | None = None
        # custom_ids of items anyone may use (e.g. ⭐ Collect, which acts for the presser)
        self.open_to_everyone: set[str] = set()

    async def attach(self, interaction: discord.Interaction) -> None:
        """Remember the message this view was sent on."""
        try:
            self.message = await interaction.original_response()
        except discord.HTTPException:
            self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        data = getattr(interaction, "data", None) or {}
        if data.get("custom_id") in self.open_to_everyone:
            return True
        if interaction.user.id != self.owner_id:
            await refuse(interaction, self.not_yours, "Not your buttons")
            return False
        return True

    def disable_all(self) -> None:
        for item in self.children:
            if hasattr(item, "disabled"):
                item.disabled = True

    async def on_timeout(self) -> None:
        self.disable_all()
        if self.message is None:
            return
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            pass  # deleted, or the channel is gone; nothing to tidy up
