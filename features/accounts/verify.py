"""The background check that turns friend requests into linked accounts.

Owns: the bot account's bonk.io session (log in when needed, again when the
token expires), checking its friend requests every POLL_SECONDS while any
/link claim is pending, linking what they prove, deleting used and stale
requests, expiring old claims, and DMing people the result.
Does not own: the matching rule (matching.py) or the tables (service.py).

The bot account's login comes from BONK_BOT_USERNAME and BONK_BOT_PASSWORD.
bonk.io limits logins per IP, so the token is kept and reused until bonk.io
says it expired ("token"), and a "ratelimited" answer pauses checks.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

import discord

from features.accounts import service
from features.accounts.embeds import PROBLEM, SUCCESS, account_embed, problem_embed
from features.accounts.matching import match_requests
from services import bonk_api

logger = logging.getLogger(__name__)

POLL_SECONDS = 30
RATE_LIMIT_PAUSE_SECONDS = 5 * 60


def bot_account_name() -> str | None:
    """The bonk.io account people send friend requests to, or None if not set up."""
    return os.getenv("BONK_BOT_USERNAME") or None


class _Session:
    """The bot account's bonk.io token, logged in only when needed."""

    def __init__(self):
        self.token: str | None = None

    async def get(self) -> str | None:
        if self.token:
            return self.token
        username, password = os.getenv("BONK_BOT_USERNAME"), os.getenv("BONK_BOT_PASSWORD")
        if not username or not password:
            logger.warning("link check: BONK_BOT_USERNAME / BONK_BOT_PASSWORD not set")
            return None
        result = await bonk_api.login(username, password)
        if result.error:
            logger.warning("link check: bot account login failed: %s", result.error)
            return None
        self.token = result.token
        return self.token


async def start_link_checker(bot):
    """Run forever; started once from bot.py's on_ready."""
    session = _Session()
    while True:
        pause = POLL_SECONDS
        try:
            pause = await _check_once(bot, session)
        except Exception:
            logger.exception("link check failed; trying again shortly")
        await asyncio.sleep(pause)


async def _check_once(bot, session: _Session) -> int:
    """One pass; returns how long to wait before the next."""
    for row in await service.expire_claims():
        await _dm(
            bot,
            row["discord_user_id"],
            account_embed(
                "⌛ Link timed out",
                f"No friend request from **{row['bonk_username']}** arrived in time. "
                "Run `/link` again to retry.",
                PROBLEM,
            ),
        )

    claims = await service.pending_claims()
    if not claims:
        return POLL_SECONDS
    token = await session.get()
    if not token:
        return POLL_SECONDS

    result = await bonk_api.friend_requests(token)
    if result.error == "token":  # expired: log in again once
        session.token = None
        token = await session.get()
        if not token:
            return POLL_SECONDS
        result = await bonk_api.friend_requests(token)
    if result.error == "ratelimited":
        logger.warning("link check: bonk.io rate-limited friends.php; pausing")
        return RATE_LIMIT_PAUSE_SECONDS
    if result.error:
        logger.warning("link check: friend requests unavailable: %s", result.error)
        return POLL_SECONDS

    matches, delete = match_requests(claims, result.requests, datetime.now(timezone.utc))
    for match in matches:
        outcome = await service.complete_claim(match)
        if outcome.linked:
            role = "your main account" if outcome.is_main else "an alt"
            await _dm(
                bot,
                match.claim.discord_user_id,
                account_embed(
                    "✅ Account linked",
                    f"**{match.bonk_username}** is linked as {role}. See `/accounts`.",
                    SUCCESS,
                ),
            )
        elif outcome.problem:
            await _dm(bot, match.claim.discord_user_id, problem_embed(outcome.problem))

    for bonk_id in delete:
        error = await bonk_api.delete_request(token, bonk_id)
        if error:
            logger.warning("link check: couldn't delete request from %s: %s", bonk_id, error)
    return POLL_SECONDS


async def _dm(bot, discord_user_id: int, embed: discord.Embed):
    """Tell someone how their /link went; fine if their DMs are closed."""
    try:
        user = bot.get_user(discord_user_id) or await bot.fetch_user(discord_user_id)
        await user.send(embed=embed)
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        logger.info("link check: couldn't DM %s", discord_user_id)
