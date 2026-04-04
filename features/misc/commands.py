import discord
from discord.ext import commands
from discord import app_commands


class Misc(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="help", description="Learn how to use the Bonkverse bot 📖")
    async def help(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🤖 Bonkverse Bot Help",
            description="Everything you need to know to get started",
            color=discord.Color.blurple()
        )

        embed.add_field(
            name="💰 Economy & Lore",
            value=(
                "/balance — Check your Bonx 💰\n"
                "/profile — View your identity 🕵️\n"
                "/regions — View regions 🌍\n"
                "/join_region <name> — Join a region 📍"
            ),
            inline=False
        )

        embed.add_field(
            name="🎰 Games & Risk",
            value="/slots <amount> — Gamble Bonx at the Orbana Market 🎰",
            inline=False
        )

        embed.add_field(
            name="🌍 Lore",
            value=(
                "• Bonkverse = the nation\n"
                "• Orbana = the continent\n"
                "• Veri = capital (restricted)\n"
                "• Tulip Island = luxury escape hub 🌺"
            ),
            inline=False
        )

        embed.set_footer(text="More features coming soon... 👀")

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