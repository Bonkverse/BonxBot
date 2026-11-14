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
            description="Welcome to **Bonkverse** — here’s everything you can do:",
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

        # 🌍 Identity & Lore
        embed.add_field(
            name="🌍 Bonkverse Identity & Lore (WIP)",
            value=(
                "/profile — View your identity 🕵️\n"
                "/regions — View regions\n"
                "/regioninfo <name> — Region lore and leadership\n"
                "/join_region <name> — Join a region 📍"
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

        # 🧠 Lore
        embed.add_field(
            name="📖 Bonkverse Lore",
            value=(
                "• Bonkverse = the nation\n"
                "• Orbana = the continent\n"
                "• Veri = capital (open to residents)\n"
                "• Tulip Island = luxury escape 🌺"
            ),
            inline=False
        )

        embed.set_footer(text="🗳️ Rating a new skin earns 100 Bonx — changing your vote costs 25!")

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