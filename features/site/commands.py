"""Bonkverse in Discord: /skin search|random|id, /player and /mapdecode.

Owns: the commands. Data comes from bonkverse.io's bot API
(services/bonkverse_client.py); layouts are in cards.py. Results are public;
problems (no match, site down) are private.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from features.accounts import service as accounts
from services import bonkverse_client as site
from utils.ui import OwnedView, refuse, send

from . import cards
from . import collect as collecting

SEARCH_RESULTS = 5
MAX_ATTACHMENT_BYTES = 512 * 1024


class CollectButton(discord.ui.Button):
    """⭐ Collect: anyone can press it to add the skin shown to their own collection."""

    def __init__(self):
        super().__init__(label="⭐ Collect", style=discord.ButtonStyle.success, custom_id=f"collect:{id(self)}")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        ref = collecting.SkinRef(skin_id=self.view.shown_skin_id)
        _, embed = await collecting.add(interaction.user.id, ref)
        await send(interaction, embed, private=True)


class SkinView(OwnedView):
    """A skin card's buttons: ⭐ Collect for everyone, the rest for whoever ran it."""

    def __init__(self, owner_id: int, skin_id: int):
        super().__init__(owner_id, timeout=600)
        self.shown_skin_id = skin_id
        button = CollectButton()
        self.open_to_everyone.add(button.custom_id)
        self.add_item(button)


class SkinMatches(SkinView):
    """A dropdown to flip between the other matches of a /skin search."""

    def __init__(self, owner_id: int, skins: list[dict]):
        super().__init__(owner_id, skins[0]["id"])
        self.skins = {str(s["id"]): s for s in skins}
        menu = discord.ui.Select(
            placeholder="Other matches",
            options=[
                discord.SelectOption(
                    label=s["name"][:100],
                    description=f"by {s.get('creator') or 'Unknown'}"[:100],
                    value=str(s["id"]),
                    default=i == 0,
                )
                for i, s in enumerate(skins)
            ],
        )
        menu.callback = self._pick
        self.menu = menu
        self.add_item(menu)

    async def _pick(self, interaction: discord.Interaction):
        chosen = self.menu.values[0]
        self.shown_skin_id = int(chosen)
        for option in self.menu.options:
            option.default = option.value == chosen
        await interaction.response.edit_message(embed=cards.skin_card(self.skins[chosen]), view=self)


class AnotherSkin(SkinView):
    """🎲 under /skin random: swap in another random skin."""

    def __init__(self, owner_id: int, skin_id: int):
        super().__init__(owner_id, skin_id)
        button = discord.ui.Button(label="🎲 Another", style=discord.ButtonStyle.secondary)
        button.callback = self._again
        self.add_item(button)

    async def _again(self, interaction: discord.Interaction):
        reply = await site.random_skin()
        if not reply.ok:
            await refuse(interaction, cards.site_problem(reply.error))
            return
        self.shown_skin_id = reply.data["skin"]["id"]
        await interaction.response.edit_message(embed=cards.skin_card(reply.data["skin"]), view=self)


async def map_code_from(code: str | None, file: discord.Attachment | None) -> str | None:
    """The map string from the option, or from an attached text file."""
    if code and code.strip():
        return code.strip()
    if file is None or file.size > MAX_ATTACHMENT_BYTES:
        return None
    try:
        return (await file.read()).decode("utf-8", errors="ignore").strip() or None
    except discord.HTTPException:
        return None


class Site(commands.Cog):
    skin = app_commands.Group(name="skin", description="Skins from bonkverse.io")

    @skin.command(name="search", description="Find a skin on bonkverse.io by name, creator or tag")
    @app_commands.describe(query="What to look for, like hot dog or Somepoop")
    async def skin_search(self, interaction: discord.Interaction, query: app_commands.Range[str, 1, 100]):
        await interaction.response.defer(thinking=True)
        reply = await site.search_skins(query, SEARCH_RESULTS)
        if not reply.ok:
            await refuse(interaction, cards.site_problem(reply.error))
            return
        skins = reply.data["skins"]
        if not skins:
            await refuse(
                interaction,
                f"No skins match **{query}**. Try fewer words, or the creator's name.",
                "No matches",
            )
            return
        content = cards.search_title(query, len(skins))
        view = SkinMatches(interaction.user.id, skins) if len(skins) > 1 else SkinView(interaction.user.id, skins[0]["id"])
        await send(interaction, cards.skin_card(skins[0]), view=view, content=content)
        await view.attach(interaction)

    @skin.command(name="random", description="A random skin from bonkverse.io")
    async def skin_random(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        reply = await site.random_skin()
        if not reply.ok:
            await refuse(interaction, cards.site_problem(reply.error))
            return
        view = AnotherSkin(interaction.user.id, reply.data["skin"]["id"])
        await send(interaction, cards.skin_card(reply.data["skin"]), view=view)
        await view.attach(interaction)

    @skin.command(name="id", description="A skin by its bonkverse.io number")
    @app_commands.describe(number="The number in the skin's bonkverse.io link")
    async def skin_id(self, interaction: discord.Interaction, number: app_commands.Range[int, 1]):
        await interaction.response.defer(thinking=True)
        reply = await site.get_skin(number)
        if not reply.ok:
            await refuse(interaction, cards.site_problem(reply.error))
            return
        view = SkinView(interaction.user.id, reply.data["skin"]["id"])
        await send(interaction, cards.skin_card(reply.data["skin"]), view=view)
        await view.attach(interaction)

    @app_commands.command(name="player", description="A bonk.io player's level, games and skins")
    @app_commands.describe(username="Their bonk.io username")
    async def player(self, interaction: discord.Interaction, username: app_commands.Range[str, 1, 32]):
        await interaction.response.defer(thinking=True)
        reply = await site.get_player(username)
        if not reply.ok:
            await refuse(interaction, cards.site_problem(reply.error, name=username.strip()))
            return
        owner = await accounts.owner_of(reply.data["username"])
        discord_id = owner["discord_user_id"] if owner else None
        await send(interaction, cards.player_card(reply.data, discord_id))

    @app_commands.command(name="mapdecode", description="What's inside a bonk.io map code")
    @app_commands.describe(code="The map code", file="Or a .txt file with the code, for long maps")
    async def mapdecode(
        self,
        interaction: discord.Interaction,
        code: str | None = None,
        file: discord.Attachment | None = None,
    ):
        map_code = await map_code_from(code, file)
        if not map_code:
            await refuse(interaction, cards.site_problem("missing_code"))
            return
        await interaction.response.defer(thinking=True)
        reply = await site.decode_map(map_code)
        if not reply.ok:
            text = cards.site_problem(reply.error)
            if reply.error == "map_rejected" and reply.detail:
                text += f"\n-# {reply.detail[:200]}"
            await refuse(interaction, text)
            return
        await send(interaction, cards.map_card(reply.data["map"]))


async def setup(bot):
    await bot.add_cog(Site())
