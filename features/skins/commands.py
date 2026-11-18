import asyncio

import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta

from db.pool import get_pool
from features.accounts import service as accounts
from features.accounts.embeds import account_embed
from .creator import BONKLEAGUES, author_is_mine, creator_for
from .voting import VoteView
from .bonkverse_sync import sync_skin
from services.skin_fetcher import fetch_skin_data

from discord.ui import View, Button, Modal, Select, TextInput

VOTING_CHANNEL_ID = 1339872418772160512

# Keeps background Bonkverse pushes alive until they finish (asyncio only holds weak refs).
_background_tasks = set()


def _short(text: str, limit: int) -> str:
    """Discord caps button labels (80) and select labels (100)."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


# =========================
# ✏️ Edit Modal (name only: the creator comes from linked accounts)
# =========================
class EditSkinModal(Modal, title="✏️ Edit Skin Name"):
    def __init__(self, parent_view):
        super().__init__()
        self.parent_view = parent_view

        self.name_input = TextInput(
            label="Skin Name",
            default=parent_view.skin_data.get("name", ""),
            required=True,
            max_length=100
        )
        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction):
        self.parent_view.skin_data["name"] = self.name_input.value
        await self.parent_view.refresh(interaction)


# =========================
# 👀 Preview UI
# =========================
class SubmitPreviewView(View):
    """The submission preview: who made it (asked when BonkLeagues credits
    someone else), which linked account to credit, then Submit."""

    def __init__(self, skin_data, user_id, my_accounts, submit_callback):
        super().__init__(timeout=180)
        self.skin_data = skin_data
        self.user_id = user_id
        self.my_accounts = my_accounts  # linked bonk.io names, main first
        self.chosen = my_accounts[0]
        self.made_it = None  # the answer to "Did you make this?", once asked
        self.submit_callback = submit_callback
        self._build()

    # --- state -------------------------------------------------------------

    @property
    def author(self):
        return self.skin_data.get("author")

    @property
    def decision(self):
        return creator_for(self.my_accounts, self.chosen, self.author, self.made_it)

    def embed(self) -> discord.Embed:
        decision = self.decision
        link = self.skin_data.get("original_link")
        if decision is None:
            embed = discord.Embed(
                title="Who made this skin?",
                description=(
                    f"BonkLeagues credits this skin to **{self.author}**. Did you make it?\n\n"
                    "If someone else made it, it's credited to them and you're listed as "
                    "the uploader. Only a skin's creator can edit or delete it on Bonkverse."
                ),
                color=discord.Color.gold(),
            )
            embed.add_field(name="__**Name**__", value=self.skin_data.get("name", "Unknown"), inline=False)
        else:
            creator, source = decision
            note = " (BonkLeagues author)" if source == BONKLEAGUES else " (you)"
            embed = discord.Embed(
                title="Thanks for using Bonkverse!",
                description="Here's your skin info:",
                color=discord.Color.blue(),
            )
            embed.add_field(name="__**Name**__", value=self.skin_data.get("name", "Unknown"), inline=False)
            embed.add_field(name="__**Creator**__", value=f"{creator}{note}", inline=False)
            embed.set_footer(text="Submit if correct, or edit the name.")
        embed.add_field(name="__**Link**__", value=f"[Open Skin]({link})", inline=False)
        return embed

    def _build(self):
        """Lay out the buttons for the current state."""
        self.clear_items()
        decision = self.decision

        if decision is None:
            self._button("✅ I made this", discord.ButtonStyle.success, self._made_it_yes)
            self._button(_short(f"🎨 Made by {self.author}", 80), discord.ButtonStyle.secondary, self._made_it_no)
        else:
            self._button("✅ Submit", discord.ButtonStyle.success, self._submit)
            self._button("✏️ Edit name", discord.ButtonStyle.secondary, self._edit)
            if not author_is_mine(self.my_accounts, self.author):
                self._button("↩️ Who made it?", discord.ButtonStyle.secondary, self._ask_again)

        credits_me = decision is None or decision[1] != BONKLEAGUES
        if len(self.my_accounts) > 1 and credits_me and not author_is_mine(self.my_accounts, self.author):
            picker = Select(
                placeholder="Credit which of your accounts?",
                options=[
                    discord.SelectOption(label=_short(name, 100), value=name, default=name == self.chosen)
                    for name in self.my_accounts[:25]
                ],
                row=1,
            )
            picker.callback = self._pick_account
            self.add_item(picker)

    def _button(self, label, style, callback):
        button = Button(label=label, style=style)
        button.callback = callback
        self.add_item(button)

    async def refresh(self, interaction: discord.Interaction):
        self._build()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    # --- actions -------------------------------------------------------------

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ Not your submission.", ephemeral=True
            )
            return False
        return True

    async def _made_it_yes(self, interaction):
        self.made_it = True
        await self.refresh(interaction)

    async def _made_it_no(self, interaction):
        self.made_it = False
        await self.refresh(interaction)

    async def _ask_again(self, interaction):
        self.made_it = None
        await self.refresh(interaction)

    async def _pick_account(self, interaction):
        self.chosen = interaction.data["values"][0]
        await self.refresh(interaction)

    async def _edit(self, interaction):
        await interaction.response.send_modal(EditSkinModal(self))

    async def _submit(self, interaction):
        decision = self.decision
        if decision is None:  # can't happen: Submit only shows once decided
            await self.refresh(interaction)
            return
        self.skin_data["creator"], self.skin_data["creator_source"] = decision
        await interaction.response.defer()
        await self.submit_callback(interaction, self.skin_data)
        self.stop()


# =========================
# 🧠 Skins Cog
# =========================
class Skins(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # 🔥 SUBMIT FLOW (linked account → API → Preview)
    @app_commands.command(
        name="submit_skin",
        description="Submit a skin using a Bonkleagues link"
    )
    async def submit_skin(self, interaction: discord.Interaction, link: str):
        rows = await accounts.accounts_of(interaction.user.id)
        if not rows:
            embed = account_embed(
                "🔗 Link your bonk.io account first",
                "Skins are credited to your bonk.io account, so link it before submitting: "
                "run `/link` with your bonk.io username.",
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        my_accounts = [row["bonk_username"] for row in rows]  # main first

        await interaction.response.defer()

        data = await fetch_skin_data(link)

        if not data:
            await interaction.followup.send("❌ Failed to fetch skin data.")
            return

        # 🔥 Store original link
        data["original_link"] = link

        view = SubmitPreviewView(
            skin_data=data,
            user_id=interaction.user.id,
            my_accounts=my_accounts,
            submit_callback=self.finalize_submission
        )

        await interaction.followup.send(embed=view.embed(), view=view)

    # 🔥 FINALIZE (actual submission)
    async def finalize_submission(self, interaction: discord.Interaction, data):
        pool = await get_pool()

        skin_name = data.get("name", "Unknown")
        creator = data["creator"]
        creator_source = data["creator_source"]
        link = data.get("original_link")  # ✅ clean link

        # ends_at = datetime.utcnow() + timedelta(minutes=1)
        ends_at = datetime.utcnow() + timedelta(hours=24)

        async with pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT id FROM skins WHERE skin_url=$1",
                link
            )

            if existing:
                await interaction.followup.send("❌ Duplicate submission.")
                return

            final_url = data.get("finalUrl")
            skin_code = data.get("skinCode")

            row = await conn.fetchrow("""
                INSERT INTO skins (
                    skin_name,
                    skin_url,
                    final_url,
                    skin_code,
                    creator,
                    creator_source,
                    submitter_id,
                    ends_at,
                    finalized
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, FALSE)
                RETURNING id
            """, skin_name, link, final_url, skin_code, creator, creator_source,
                interaction.user.id, ends_at)

            skin_id = row["id"]

        # Send it to bonkverse.io in the background; if that fails, the poller retries.
        task = asyncio.create_task(sync_skin(skin_id))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

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
            title=f"{skin_name} by {creator}",
            description=f"🔗 {link}\n🕒 Ends <t:{int(ends_at.timestamp())}:R>",
            color=discord.Color.blue()
        )

        embed.add_field(
            name="🗳️ Vote",
            value="\n".join(tier_labels),
            inline=False
        )

        # Clean message (no giant encoded URL anymore)
        await channel.send(f"**Skin to be ranked:** {link}")

        msg = await channel.send(embed=embed, view=VoteView(skin_id))

        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE skins SET vote_message_id=$1 WHERE id=$2",
                msg.id, skin_id
            )

        await interaction.followup.send("✅ Skin submitted for voting!")


async def setup(bot):
    await bot.add_cog(Skins(bot))
