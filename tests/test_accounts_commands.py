"""What the account commands reply (features/accounts/commands.py).

Every reply is an embed. Results go in the channel; /link's instructions and
every problem are only for the person who ran the command. The service is faked: no
database needed.
"""

import asyncio
from datetime import datetime

import discord
import pytest

from features.accounts import commands, service

TULIP = 1009876543210987654


class FakeResponse:
    def __init__(self):
        self.sent = None

    async def send_message(self, content=None, **kwargs):
        self.sent = {"content": content, **kwargs}

    def is_done(self):
        return self.sent is not None


class FakeUser:
    id = TULIP
    mention = f"<@{TULIP}>"


class FakeInteraction:
    def __init__(self):
        self.user = FakeUser()
        self.response = FakeResponse()


class FakeMember:
    id = 42
    mention = "<@42>"


def call(command, *args, **kwargs):
    interaction = FakeInteraction()
    cog = commands.Accounts(bot=None)
    asyncio.run(command.callback(cog, interaction, *args, **kwargs))
    sent = interaction.response.sent
    assert sent["content"] is None
    assert isinstance(sent["embed"], discord.Embed)
    return sent, sent["embed"]


def _row(name, main=False):
    return {"bonk_username": name, "is_main": main, "bonk_id": None, "source": "levelbot",
            "verified_at": None, "created_at": datetime(2025, 9, 1)}


def _fake(monkeypatch, name, result):
    async def fake(*args, **kwargs):
        return result

    monkeypatch.setattr(service, name, fake)


@pytest.fixture
def bot_account(monkeypatch):
    monkeypatch.setenv("BONK_BOT_USERNAME", "BonkverseBot")


# --- /link --------------------------------------------------------------------------


def test_link_instructions_are_private(monkeypatch, bot_account):
    _fake(monkeypatch, "start_claim", service.ClaimStart(claim_id=1, minutes_left=10))

    sent, embed = call(commands.Accounts.link, "The Saucy Tulip")

    assert sent["ephemeral"] is True
    assert embed.title == "🔗 Prove it's your account"
    assert "Log in to bonk.io as **The Saucy Tulip**" in embed.description
    assert "Send a friend request to **BonkverseBot**" in embed.description
    assert "within **10 min**" in embed.description


def test_link_problems_are_private(monkeypatch, bot_account):
    _fake(monkeypatch, "start_claim", service.ClaimStart(problem="**Somepoop** is linked to another Discord account."))

    sent, embed = call(commands.Accounts.link, "Somepoop")

    assert sent["ephemeral"] is True
    assert embed.title == "Can't do that"
    assert embed.color == discord.Color.red()


def test_link_bad_name_is_private(bot_account):
    sent, embed = call(commands.Accounts.link, "   ")

    assert sent["ephemeral"] is True
    assert embed.description == "Type your bonk.io username."


def test_link_not_set_up_is_private(monkeypatch):
    monkeypatch.delenv("BONK_BOT_USERNAME", raising=False)

    sent, embed = call(commands.Accounts.link, "The Saucy Tulip")

    assert sent["ephemeral"] is True
    assert "isn't set up yet" in embed.description


# --- everything else is in the channel, pinging no one ------------------------------------


def test_accounts_lists_main_first(monkeypatch):
    _fake(monkeypatch, "accounts_of", [_row("The Saucy Tulip", main=True), _row("Tulip Alt")])

    sent, embed = call(commands.Accounts.accounts)

    assert sent["ephemeral"] is False
    assert embed.description == "⭐ **The Saucy Tulip** (main)\n• **Tulip Alt**"
    assert embed.footer.text == "Change your main with /setmain, add an alt with /link."


def test_accounts_when_none(monkeypatch):
    _fake(monkeypatch, "accounts_of", [])

    sent, embed = call(commands.Accounts.accounts)

    assert sent["ephemeral"] is False
    assert embed.description == "None linked yet. Use `/link`."


@pytest.mark.parametrize(
    "command, service_name, ok_title",
    [
        (commands.Accounts.setmain, "set_main", "⭐ Main account"),
        (commands.Accounts.unlink, "unlink", "Unlinked"),
    ],
)
def test_setmain_and_unlink(monkeypatch, command, service_name, ok_title):
    _fake(monkeypatch, service_name, "Tulip Alt")
    sent, embed = call(command, "tulip alt")
    assert (sent["ephemeral"], embed.title, embed.color) == (False, ok_title, discord.Color.green())

    _fake(monkeypatch, service_name, None)
    sent, embed = call(command, "Somepoop")
    assert (sent["ephemeral"], embed.title) == (True, "Can't do that")  # only they see mistakes
    assert "**Somepoop** isn't one of your linked accounts" in embed.description


def test_whois_by_member(monkeypatch):
    _fake(monkeypatch, "accounts_of", [_row("Somepoop", main=True)])

    sent, embed = call(commands.Accounts.whois, member=FakeMember())

    assert sent["ephemeral"] is False
    assert embed.description == "<@42> → **Somepoop** (main)"
    assert sent["allowed_mentions"].users is False  # names them without pinging


def test_whois_by_name(monkeypatch):
    _fake(monkeypatch, "owner_of", {"bonk_username": "Somepoop", "discord_user_id": 42})

    sent, embed = call(commands.Accounts.whois, bonk_username="somepoop")

    assert sent["ephemeral"] is False
    assert embed.description == "**Somepoop** → <@42>"


def test_whois_needs_something():
    sent, embed = call(commands.Accounts.whois)

    assert sent["ephemeral"] is True
    assert embed.title == "Can't do that"
