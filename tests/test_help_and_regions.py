"""/help (features/misc/commands.py) and the region menu and cards
(features/regions/commands.py). No database needed."""

import asyncio

import discord

from features.misc import commands as misc
from features.regions import commands as regions
from utils.ui import INFO


class FakeResponse:
    def __init__(self):
        self.sent = None
        self.edited = None

    def is_done(self):
        return self.sent is not None

    async def send_message(self, **kwargs):
        self.sent = kwargs

    async def edit_message(self, **kwargs):
        self.edited = kwargs


class FakeInteraction:
    def __init__(self, user_id=1, admin=False):
        self.user = type("U", (), {"id": user_id})()
        self.permissions = discord.Permissions(administrator=admin)
        self.response = FakeResponse()

    async def original_response(self):
        return None


def test_staff_topic_only_for_staff():
    assert "staff" not in misc.topics_for(misc.is_staff(FakeInteraction()))
    assert misc.topics_for(misc.is_staff(FakeInteraction(admin=True)))[-1] == "staff"


def test_every_topic_fits_an_embed():
    for key, *_ in misc.TOPICS:
        embed = misc.help_card(key)
        assert len(embed.description) <= 4096
        assert embed.color == INFO


def test_help_is_public_and_starts_on_the_overview():
    interaction = FakeInteraction()
    asyncio.run(misc.Misc.help.callback(misc.Misc(bot=None), interaction))

    sent = interaction.response.sent
    assert sent["ephemeral"] is False
    assert sent["embed"].title == "📖 BonxBot"
    labels = [option.label for option in sent["view"].menu.options]
    assert labels == ["Start here", "Bonx", "Bonkverse", "Games", "Skins", "Regions", "bonk.io accounts"]


def test_picking_a_topic_swaps_the_page():
    async def pick():
        view = misc.HelpView(1, misc.topics_for(False))
        view.menu._values = ["games"]  # what Discord fills in on a pick
        interaction = FakeInteraction()
        await view._pick(interaction)
        return interaction.response.edited, view

    edited, view = asyncio.run(pick())
    assert edited["embed"].title == "🎰 Games"
    assert "/coinflip" in edited["embed"].description
    assert [o.value for o in view.menu.options if o.default] == ["games"]


def test_anyone_can_use_the_regions_menu():
    async def check():
        view = regions.RegionMenu(["Skintopia", "Veri"])
        return await view.interaction_check(FakeInteraction(user_id=999))

    assert asyncio.run(check()) is True


def test_ramidia_needs_no_database():
    embed = asyncio.run(regions.region_card(" ramidia "))
    assert embed.title == "Ramidia"
    assert "Resident title: Comrade" in embed.description
