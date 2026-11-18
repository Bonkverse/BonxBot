"""/help and the reply to an @mention.

Owns: the command guide. /help is posted in the channel and
has a topic dropdown instead of one long list. Staff commands only show for
members who can use them.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from config.settings import SLOT_MAX_BET, VOTE_CHANGE_TAX, VOTE_REWARD
from features.site import collect
from utils.ui import INFO, OwnedView, card, send


def _lines(*commands: tuple[str, str]) -> str:
    return "\n".join(f"`{usage}` {what}" for usage, what in commands)


# (key, emoji, label, one-line summary, body)
TOPICS = [
    (
        "start", "🧭", "Start here", "What BonxBot does",
        (
            "BonxBot runs the Bonkverse Discord: **Bonx** (our currency), **skin tier votes**, "
            "the **regions of Orbana**, and **bonk.io account links**.\n\n"
            "New? Claim `/daily`, link your bonk.io account with `/link`, "
            "and pick a region after reading `/orbana`.\n\n"
            "Pick a topic below for its commands."
        ),
    ),
    (
        "bonx", "💰", "Bonx", "Balance, daily, sending, history",
        _lines(
            ("/daily", "Claim Bonx once a day. Streaks pay more."),
            ("/balance [user]", "Bonx and leaderboard place."),
            ("/profile [user]", "Bonx, streak, skins, region and bonk.io name."),
            ("/give <user> <amount>", "Send Bonx to a player."),
            ("/history [count]", "Your recent transactions."),
            ("/leaderboard", "Who has the most Bonx."),
        ),
    ),
    (
        "bonkverse", "🔎", "Bonkverse", "Skins, players and maps",
        _lines(
            ("/skin search <query>", "Find a skin on bonkverse.io by name, creator or tag."),
            ("/skin random", "A random skin."),
            ("/skin id <number>", "A skin by its bonkverse.io number."),
            ("/player <username>", "A bonk.io player's level, games and skins."),
            ("/mapdecode <code>", "What's inside a bonk.io map code (or attach it as a .txt)."),
            ("/replay <number or link>", "A bonk.io replay from the archive, as a video."),
            ("/replays [player] [member] [map] [mode] [against]", (
                "Find replays: yours, a member's or any player's. Pick one to watch it as a video."
            )),
            ("/collect <link>", "Add a skin to your bonkverse.io collection. Not on the site yet? It's uploaded."),
            ("/upload <link>", "Just put a skin on bonkverse.io: no tier vote, no collection."),
        )
        + "\n\nAlso: **@BonxBot** with a skin link (or replying to one) collects it, and "
        "**@BonxBot upload** just uploads it. On any message: **Apps → Add to my collection** "
        "or **Apps → Upload to Bonkverse**.",
    ),
    (
        "games", "🎰", "Games", "Slots and coinflips",
        _lines(
            ("/slots <amount>", f"Spin at the Orbana Market. Bets up to {SLOT_MAX_BET:,}."),
            ("/coinflip <user> <amount>", "50/50 duel. Both stake, winner takes both."),
        ),
    ),
    (
        "skins", "🎨", "Skins", "Submitting and tier votes",
        _lines(
            ("/submit_skin <link>", (
                "Submit a skin from its BonkLeagues link. It goes up "
                "for tier votes and onto bonkverse.io."
            )),
            ("/skin_dms <on|off>", "DMs when your skin is live on bonkverse.io and when its vote closes."),
            ("/submissions [member] [player] [skin]", (
                "Browse skins submitted through BonxBot: yours, a member's, or a bonk.io "
                "creator's, with stats and each skin's votes."
            )),
        )
        + f"\n\nVote S–F on skins in the voting channel. Your first vote on a skin earns "
        f"**{VOTE_REWARD} Bonx**; changing it costs {VOTE_CHANGE_TAX}. "
        "Skins are credited to your linked bonk.io account, so `/link` first.",
    ),
    (
        "regions", "🌍", "Regions", "Orbana, ranks, the Region Cup",
        _lines(
            ("/orbana", "How regions, deeds and ranks work."),
            ("/regions", "Every region, with a menu to read about each."),
            ("/regioninfo <region>", "A region's ranks, perks, leaders and residents."),
            ("/join_region <region>", "Move in. 7-day cooldown between moves."),
            ("/rank [user]", "Your rank and the deeds for the next one."),
            ("/regioncup", "This week's Region Cup standings."),
            ("/appoint <user>", "Leaders appoint their region's Tier 6 title."),
        ),
    ),
    (
        "accounts", "🔗", "bonk.io accounts", "Linking, alts, who's who",
        _lines(
            ("/link <bonk.io name>", (
                "Prove an account is yours with a friend request. Run it again to add an alt."
            )),
            ("/accounts", "Your linked accounts."),
            ("/setmain <name>", "Choose the account skins are credited to."),
            ("/unlink <name>", "Remove a linked account."),
            ("/whois [member] [name]", "Who's who between Discord and bonk.io."),
        ),
    ),
    (
        "staff", "🛠️", "Staff", "Server staff only",
        _lines(("/admin_add <user> <amount>", "Add Bonx to a player.")),
    ),
]
TOPIC = {key: (emoji, label, summary, body) for key, emoji, label, summary, body in TOPICS}


def topics_for(is_staff: bool) -> list[str]:
    return [key for key, *_ in TOPICS if key != "staff" or is_staff]


def help_card(key: str) -> discord.Embed:
    emoji, label, _, body = TOPIC[key]
    title = "📖 BonxBot" if key == "start" else f"{emoji} {label}"
    embed = card(title, body, INFO)
    if key != "start":
        embed.set_footer(text="[ ] is optional, < > is required")
    return embed


class HelpView(OwnedView):
    def __init__(self, owner_id: int, keys: list[str]):
        super().__init__(owner_id, timeout=300)
        menu = discord.ui.Select(
            placeholder="Pick a topic",
            options=[
                discord.SelectOption(label=TOPIC[k][1], value=k, emoji=TOPIC[k][0], description=TOPIC[k][2])
                for k in keys
            ],
        )
        menu.callback = self._pick
        self.menu = menu
        self.add_item(menu)

    async def _pick(self, interaction: discord.Interaction):
        key = self.menu.values[0]
        for option in self.menu.options:
            option.default = option.value == key
        await interaction.response.edit_message(embed=help_card(key), view=self)


def is_staff(interaction: discord.Interaction) -> bool:
    permissions = getattr(interaction, "permissions", None)
    return bool(permissions and permissions.administrator)


class Misc(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="help", description="What BonxBot can do, by topic 📖")
    async def help(self, interaction: discord.Interaction):
        view = HelpView(interaction.user.id, topics_for(is_staff(interaction)))
        await send(interaction, help_card("start"), view=view)
        await view.attach(interaction)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or self.bot.user not in message.mentions:
            return
        if await collect.handle_mention(message, self.bot.user):
            return  # it had skin links: they were added to a collection
        await message.channel.send(
            embed=card(description="👋 I'm **BonxBot**. Try `/help` to see what I do.", color=INFO),
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot):
    await bot.add_cog(Misc(bot))
