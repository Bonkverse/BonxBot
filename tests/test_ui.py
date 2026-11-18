"""The shared look (utils/ui.py), the error handler (utils/errors.py) and the
region-name suggestions. No database or Discord connection needed."""

import asyncio
from datetime import datetime, timezone

import discord
import pytest
from discord import app_commands

from features.regions.service import did_you_mean
from utils import errors, ui

REGIONS = ["Arena City", "Bonktown", "Creativia", "Skintopia", "Spherehold", "Veri"]


class FakeResponse:
    def __init__(self, done=False):
        self.done = done
        self.sent = None

    def is_done(self):
        return self.done

    async def send_message(self, **kwargs):
        self.sent = kwargs


class FakeFollowup:
    def __init__(self):
        self.sent = None

    async def send(self, **kwargs):
        self.sent = kwargs


class FakeInteraction:
    def __init__(self, user_id=1, done=False, command=None):
        self.user = type("U", (), {"id": user_id})()
        self.response = FakeResponse(done)
        self.followup = FakeFollowup()
        self.command = command


def run(coro):
    return asyncio.run(coro)


# --- formatting ----------------------------------------------------------------


def test_bonx_amounts_get_commas():
    assert ui.bonx(12345) == "12,345 Bonx"
    assert ui.signed_bonx(300) == "+300 Bonx"
    assert ui.signed_bonx(-1500) == "-1,500 Bonx"


def test_naive_times_are_utc():
    assert ui.when(datetime(2026, 11, 18, 7, 21)) == "<t:1794986460:R>"
    aware = datetime(2026, 11, 18, 7, 21, tzinfo=timezone.utc)
    assert ui.when(aware, "f") == "<t:1794986460:f>"


# --- sending ---------------------------------------------------------------------


def test_refusals_are_private_and_red():
    interaction = FakeInteraction()
    run(ui.refuse(interaction, "Send at least **1 Bonx**."))

    sent = interaction.response.sent
    assert sent["ephemeral"] is True
    assert sent["embed"].color == ui.PROBLEM
    assert sent["embed"].description == "Send at least **1 Bonx**."
    assert sent["allowed_mentions"].users is False  # names people without pinging


def test_deferred_replies_go_through_the_followup():
    interaction = FakeInteraction(done=True)
    run(ui.send(interaction, ui.card("Hi")))

    assert interaction.response.sent is None
    assert interaction.followup.sent["embed"].title == "Hi"


# --- buttons -------------------------------------------------------------------


def test_only_the_owner_can_press():
    async def check():
        view = ui.OwnedView(owner_id=1)
        stranger = FakeInteraction(user_id=2)
        allowed = await view.interaction_check(stranger)
        return allowed, stranger.response.sent

    allowed, sent = run(check())
    assert allowed is False
    assert sent["ephemeral"] is True
    assert sent["embed"].title == "Not your buttons"


def test_buttons_switch_off_when_they_time_out():
    class FakeMessage:
        view = None

        async def edit(self, view):
            self.view = view

    async def expire():
        view = ui.OwnedView(owner_id=1)
        view.add_item(discord.ui.Button(label="▶️"))
        view.message = FakeMessage()
        await view.on_timeout()
        return view.message.view

    edited = run(expire())
    assert [item.disabled for item in edited.children] == [True]


# --- errors ----------------------------------------------------------------------


class FakeCommand:
    name = qualified_name = "slots"

    def __init__(self, extras=None):
        self.extras = extras or {}


def test_cooldowns_use_the_commands_own_line():
    interaction = FakeInteraction(command=FakeCommand({"cooldown": "The reels are still spinning."}))
    error = app_commands.CommandOnCooldown(app_commands.Cooldown(1, 5), 3.4)

    assert errors.explain(interaction, error) == (
        "On cooldown",
        "The reels are still spinning. Try again in 3s.",
    )


def test_cooldowns_without_a_line():
    interaction = FakeInteraction(command=FakeCommand())
    error = app_commands.CommandOnCooldown(app_commands.Cooldown(1, 5), 0.2)

    assert errors.explain(interaction, error)[1] == "Slow down a little. Try again in 1s."


def test_staff_only():
    interaction = FakeInteraction(command=FakeCommand())
    error = app_commands.MissingPermissions(["administrator"])

    assert errors.explain(interaction, error) == ("Staff only", "That command is for server staff.")


def test_crashes_get_a_private_reply_not_interaction_failed():
    interaction = FakeInteraction(command=FakeCommand())
    error = app_commands.CommandInvokeError(FakeCommand(), RuntimeError("db down"))

    run(errors.on_app_command_error(interaction, error))

    sent = interaction.response.sent
    assert sent["ephemeral"] is True
    assert sent["embed"].description == errors.CRASHED


# --- region names ----------------------------------------------------------------


@pytest.mark.parametrize(
    "typed, expected",
    [
        ("skintopa", "There's no region called **skintopa**. Did you mean **Skintopia**?"),
        ("arena", "There's no region called **arena**. Did you mean **Arena City**?"),
        ("bonk town", "There's no region called **bonk town**. Did you mean **Bonktown**?"),
        ("zzz", "There's no region called **zzz**. `/regions` lists them all."),
    ],
)
def test_did_you_mean(typed, expected):
    assert did_you_mean(typed, REGIONS) == expected
