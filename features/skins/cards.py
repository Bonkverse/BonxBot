"""What skin submissions and tier votes look like.

Owns: the submission preview, the vote post, its final result and the
tier-channel post, and the private replies to a vote. Pure functions.
Does not own: the voting rules (voting.py) or submitting (commands.py).
"""

from __future__ import annotations

from datetime import datetime

import discord

from config.settings import SKINTOPIA_VOTE_CHANGE_TAX, VOTE_CHANGE_TAX, VOTE_REWARD
from utils.ui import BONX, INFO, MUTED, PROBLEM, SUCCESS, bonx, card, when

TIERS = ["S", "A", "B", "C", "D", "F"]
TIER_POINTS = {"S": 6, "A": 5, "B": 4, "C": 3, "D": 2, "F": 1}
TIER_COLORS = {
    "S": discord.Color.purple(),
    "A": discord.Color.blue(),
    "B": discord.Color.green(),
    "C": discord.Color.gold(),
    "D": discord.Color.orange(),
    "F": discord.Color.red(),
}
VOTES_FIELD = "🗳️ Votes"


# --- submission preview -----------------------------------------------------------


def who_made_it_card(name: str, author: str, link: str) -> discord.Embed:
    embed = card(
        "Who made this skin?",
        f"BonkLeagues credits **{name}** to **{author}**. Is that you?\n\n"
        "If someone else made it, they get the credit and you're listed as the uploader. "
        "Only a skin's creator can edit or delete it on bonkverse.io.",
        INFO,
    )
    embed.add_field(name="Skin", value=f"[{name}]({link})", inline=False)
    return embed


def ready_card(name: str, creator: str, credited_to_author: bool, link: str) -> discord.Embed:
    whose = "BonkLeagues author" if credited_to_author else "you"
    embed = card("Ready to submit?", None, INFO)
    embed.add_field(name="Skin", value=f"[{name}]({link})")
    embed.add_field(name="Creator", value=f"{creator} ({whose})")
    embed.set_footer(text="Submit to post it for tier votes and add it to bonkverse.io.")
    return embed


def cancelled_card(name: str, *, timed_out: bool = False) -> discord.Embed:
    if timed_out:
        return card(
            "Submission timed out",
            f"**{name}** wasn't submitted. Run `/submit_skin` again when you're ready.",
            MUTED,
        )
    return card("Submission cancelled", f"**{name}** wasn't submitted.", MUTED)


def submitted_card(name: str, creator: str, jump_url: str, ends_at: datetime) -> discord.Embed:
    return card(
        "Submitted for voting",
        f"**{name}** by **{creator}** is up for tier votes: {jump_url}\nVoting ends {when(ends_at)}.",
        SUCCESS,
    )


# --- the vote post ------------------------------------------------------------------


def vote_card(name: str, creator: str, link: str, ends_at: datetime, votes: int = 0) -> discord.Embed:
    embed = card(
        name,
        f"by **{creator}** · [Open on BonkLeagues]({link})\nVoting ends {when(ends_at)}",
        INFO,
    )
    embed.add_field(name="Tiers", value=" · ".join(f"**{t}** {TIER_POINTS[t]}" for t in TIERS))
    embed.add_field(name=VOTES_FIELD, value=str(votes))
    embed.set_footer(
        text=f"First vote: +{VOTE_REWARD} Bonx · Changing it costs {VOTE_CHANGE_TAX} "
        f"({SKINTOPIA_VOTE_CHANGE_TAX} for Skintopia Tier 3+)"
    )
    return embed


def set_vote_count(embed: discord.Embed, votes: int) -> discord.Embed:
    """Update the count on a vote post. Posts made before this field existed get it added."""
    for index, field in enumerate(embed.fields):
        if field.name == VOTES_FIELD:
            embed.set_field_at(index, name=VOTES_FIELD, value=str(votes), inline=field.inline)
            return embed
    embed.add_field(name=VOTES_FIELD, value=str(votes))
    return embed


def result_text(tier: str, average: float, votes: int) -> str:
    return f"**{tier} Tier** · {average:.2f} average from {votes} vote{'s' if votes != 1 else ''}"


def finish_vote_card(embed: discord.Embed, tier: str, average: float, votes: int) -> discord.Embed:
    """The vote post once voting closes: result on top, buttons removed by the caller."""
    embed = set_vote_count(embed, votes)
    embed.add_field(name="🏆 Result", value=result_text(tier, average, votes), inline=False)
    embed.color = TIER_COLORS.get(tier, INFO)
    embed.description = (embed.description or "").replace("Voting ends", "Voting ended")
    return embed


def tier_card(name: str, creator: str, link: str, tier: str, average: float, votes: int) -> discord.Embed:
    embed = card(f"{name}", f"by **{creator}**\n{result_text(tier, average, votes)}", TIER_COLORS.get(tier, INFO))
    embed.url = link
    return embed


# --- private replies to a vote -----------------------------------------------------


def voted(tier: str, amount: int) -> discord.Embed:
    return card(description=f"🗳️ You voted **{tier}**. +{bonx(amount)}", color=BONX)


def changed(tier: str, fee: int) -> discord.Embed:
    return card(description=f"🗳️ Changed your vote to **{tier}**. −{bonx(fee)}", color=BONX)


def already(tier: str) -> discord.Embed:
    return card(description=f"Your vote is already **{tier}**. Nothing changed.", color=MUTED)


def cant_afford(fee: int) -> discord.Embed:
    return card(
        description=f"Changing your vote costs **{bonx(fee)}**, which you don't have right now. "
        "Your vote stays as it was.",
        color=PROBLEM,
    )


def closed() -> discord.Embed:
    return card(description="Voting on this skin has ended.", color=MUTED)


def missing() -> discord.Embed:
    return card(description="This skin isn't up for voting anymore.", color=MUTED)


def vote_failed() -> discord.Embed:
    return card(
        description="Your vote didn't go through and no Bonx moved. Try again in a minute.",
        color=PROBLEM,
    )
