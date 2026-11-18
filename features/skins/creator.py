"""Who is credited as a submitted skin's creator. No database, no Discord.

The same rule as Bonkverse's upload page (skins.submitters.resolve_creator on
bonkverse.io), so a skin is credited the same way wherever it's submitted:

- BonkLeagues lists no author, or one of the submitter's own linked accounts
  (case ignored, spaces kept, like bonk.io names): credit that account, no
  question asked.
- Someone else: ask. "I made this" credits the account the submitter picked;
  "Made by <author>" credits the BonkLeagues author, and the submitter is still
  recorded (Discord id) on Bonkverse.

The source travels to Bonkverse as `creator_source`, which it trusts from the bot.
"""

from __future__ import annotations

SUBMITTER = "submitter"
BONKLEAGUES = "bonkleagues"


def creator_for(
    my_accounts: list[str], chosen: str, bonkleagues_author: str | None, made_it: bool | None
) -> tuple[str, str] | None:
    """(creator, creator_source), or None when the submitter has to be asked.

    `my_accounts` are the submitter's linked bonk.io names; `chosen` is the one
    they picked (their main unless they changed it).
    """
    author = bonkleagues_author or ""
    if not author.strip():
        return chosen, SUBMITTER
    for name in my_accounts:
        if name.casefold() == author.casefold():
            return name, SUBMITTER
    if made_it is True:
        return chosen, SUBMITTER
    if made_it is False:
        return author, BONKLEAGUES
    return None


def author_is_mine(my_accounts: list[str], bonkleagues_author: str | None) -> bool:
    """Whether the BonkLeagues author is blank or one of the submitter's accounts."""
    return creator_for(my_accounts, my_accounts[0], bonkleagues_author, None) is not None
