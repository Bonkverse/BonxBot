import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta

from db.pool import get_pool
from .voting import VoteView
from services.skin_fetcher import fetch_skin_data

from discord.ui import View, Button, Modal, TextInput

VOTING_CHANNEL_ID = 1339872418772160512


class EditSkinModal(Modal, title="✏️ Edit Skin Info"):
    def __init__(self, parent_view):
        super().__init__()
        self.parent_view = parent_view

        self.name_input = TextInput(
            label="Skin Name",
            default=parent_view.skin_data.get("name", ""),
            required=True,
            max_length=100
        )

        self.creator_input = TextInput(
            label="Creator",
            default=parent_view.skin_data.get("author", ""),
            required=True,
            max_length=100
        )

        self.add_item(self.name_input)
        self.add_item(self.creator_input)

    async def on_submit(self, interaction: discord.Interaction):
        # 🔥 Update stored data
        self.parent_view.skin_data["name"] = self.name_input.value
        self.parent_view.skin_data["author"] = self.creator_input.value

        # 🔥 Rebuild preview embed
        embed = discord.Embed(
            title="Thanks for using Bonkverse!",
            description="Here's your updated skin info:",
            color=discord.Color.orange()
        )

        embed.add_field(name="Name", value=self.name_input.value, inline=False)
        embed.add_field(name="Creator", value=self.creator_input.value, inline=False)

        embed.set_footer(text="Submit if correct, or edit again.")

        await interaction.response.edit_message(embed=embed, view=self.parent_view)

# =========================
# Preview UI
# =========================
class SubmitPreviewView(View):
    def __init__(self, skin_data, user_id, submit_callback):
        super().__init__(timeout=120)
        self.skin_data = skin_data
        self.user_id = user_id
        self.submit_callback = submit_callback

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Not your submission.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✅ Submit", style=discord.ButtonStyle.success)
    async def submit(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer()
        await self.submit_callback(interaction, self.skin_data)
        self.stop()

    @discord.ui.button(label="✏️ Edit", style=discord.ButtonStyle.secondary)
    async def edit(self, interaction: discord.Interaction, button: Button):
        modal = EditSkinModal(self)
        await interaction.response.send_modal(modal)


# =========================
# Cog
# =========================
class Skins(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # 🔥 NEW FLOW
    @app_commands.command(name="submit_skin", description="Submit a skin using a Bonkleagues link")
    async def submit_skin(self, interaction: discord.Interaction, link: str):
        await interaction.response.defer()

        data = await fetch_skin_data(link)

        if not data:
            await interaction.followup.send("❌ Failed to fetch skin data.")
            return

        skin_name = data.get("name", "Unknown")
        creator = data.get("author", "Unknown")
        final_url = link

        embed = discord.Embed(
            title="Thanks for using Bonkverse!",
            description="Here's what I found on your submission:",
            color=discord.Color.blue()
        )

        embed.add_field(name="Skin Name", value=skin_name, inline=False)
        embed.add_field(name="Creator", value=creator, inline=False)

        embed.set_footer(text="Submit if correct, or edit if needed.")

        view = SubmitPreviewView(
            skin_data=data,
            user_id=interaction.user.id,
            submit_callback=self.finalize_submission
        )

        await interaction.followup.send(embed=embed, view=view)

    # 🔥 FINALIZE (replaces old logic)
    async def finalize_submission(self, interaction: discord.Interaction, data):
        pool = await get_pool()

        skin_name = data.get("name", "Unknown")
        creator = data.get("author", "Unknown")
        url = data.get("finalUrl")

        ends_at = datetime.utcnow() + timedelta(minutes=1)

        async with pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT id FROM skins WHERE image_url=$1",
                url
            )

            if existing:
                await interaction.followup.send("❌ Duplicate submission.")
                return

            row = await conn.fetchrow("""
                INSERT INTO skins (skin_name, image_url, creator, submitter_id, ends_at, finalized)
                VALUES ($1, $2, $3, $4, $5, FALSE)
                RETURNING id
            """, skin_name, url, creator, interaction.user.id, ends_at)

            skin_id = row["id"]

        channel = await self.bot.fetch_channel(VOTING_CHANNEL_ID)

        tier_labels = [
            "🟣 **S Tier (6 pts)**",
            "🔵 **A Tier (5 pts)**",
            "🟢 **B Tier (4 pts)**",
            "🟡 **C Tier (3 pts)**",
            "🟠 **D Tier (2 pts)**",
            "🔴 **F Tier (1 pt)**"
        ]

        embed = discord.Embed(
            title=f"🖼️ {skin_name}",
            description=f"{url}\n🕒 Ends <t:{int(ends_at.timestamp())}:R>",
            color=discord.Color.blue()
        )

        embed.add_field(
            name="🗳️ Vote",
            value="\n".join(tier_labels),
            inline=False
        )

        await channel.send("**Skin to be ranked:** " +url)

        msg = await channel.send(embed=embed, view=VoteView(skin_id))

        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE skins SET vote_message_id=$1 WHERE id=$2",
                msg.id, skin_id
            )

        await interaction.followup.send("✅ Skin submitted for voting!")


async def setup(bot):
    await bot.add_cog(Skins(bot))