import discord
from discord.ext import commands
from discord import app_commands


class Misc(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="help", description="Learn how to use the Bonkverse bot 📖")
    async def help(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🤖 BonxBot Help",
            description="New here? Use `/orbana` to learn what regions, ranks, and the Cup are for.",
            color=discord.Color.blurple()
        )

        # 💰 Economy
        embed.add_field(
            name="💰 Economy",
            value=(
                "/balance [user] — Check Bonx balance\n"
                "/leaderboard — View top players 🏆\n"
                "/give <user> <amount> — Send Bonx to someone 💸\n"
                "/daily — Claim daily reward, streaks pay more 🎁\n"
                "/history [count] — See your recent Bonx transactions 🧾"
            ),
            inline=False
        )

        # 🔗 bonk.io accounts
        embed.add_field(
            name="🔗 bonk.io Accounts",
            value=(
                "/link <username> — Link your bonk.io account (again to add an alt)\n"
                "/accounts — Your linked accounts\n"
                "/setmain <username> — Choose your main account\n"
                "/unlink <username> — Remove a linked account\n"
                "/whois [member] [username] — Who's who between Discord and bonk.io"
            ),
            inline=False
        )

        # 🌍 Identity & Lore
        embed.add_field(
            name="🌍 Regions & Ranks",
            value=(
                "/profile — View your identity 🕵️\n"
                "/rank [user] — View regional rank and deed progress\n"
                "/regions — View regions\n"
                "/regioncup — View the weekly regional standings\n"
                "/regioninfo <name> — Region lore and leadership\n"
                "/join_region <name> — Join a region 📍\n"
                "/appoint <user> — Regional leaders appoint an eligible Tier 5 resident"
            ),
            inline=False
        )

        # 🎰 Games
        embed.add_field(
            name="🎰 Games",
            value=(
                "/slots <amount> — Gamble Bonx at the Orbana Market\n"
                "/coinflip <user> <amount> — 50/50 duel, winner takes both 🪙"
            ),
            inline=False
        )

        # 🛠️ Admin
        embed.add_field(
            name="🛠️ Admin",
            value="/admin_add <user> <amount> — Add Bonx (Admin only)",
            inline=False
        )

        embed.set_footer(text="🗳️ Rating a new skin earns 100 Bonx — changing your vote normally costs 25 Bonx.")

        await interaction.response.send_message(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        if self.bot.user in message.mentions:
            await message.channel.send(
                "👋 Hi I'm **BonxBot**!\n👉 Use `/help` to see what I can do."
            )


async def setup(bot):
    await bot.add_cog(Misc(bot))