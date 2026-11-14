import discord
from discord import app_commands
from discord.ext import commands

from db.pool import get_pool
from .ranks import CITIZEN, RANK_LADDERS, get_rank_progress
from .service import join_region

# Founding leadership and succession rules from the region proposal.
REGION_LEADERSHIP = {
    "Skintopia": (1193371251968852000, "Most skin submissions"),
    "Bonktown": (1105490825267396680, "Richest active member"),
    "Creativia": (1205837634073792572, "Trial & Pit"),
    "Spherehold": (1137397479776735313, "Longest active streak"),
    "Versetralis": (1410744402976051291, "Trial & Pit"),
    "Veri": (1116043991037775972, "Trial & Pit"),
    "Arena City": (None, "Most coinflip wins this week"),
}


class Regions(commands.Cog):
    @app_commands.command(name="regions", description="View regions of Orbana")
    async def regions(self, interaction: discord.Interaction):
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM regions ORDER BY is_capital DESC, name")

        lines = []
        for region in rows:
            capital = " 🏛️" if region["is_capital"] else ""
            lines.append(f"**{region['name']}**{capital} — {region['description']}")
        await interaction.response.send_message("\n".join(lines))

    @app_commands.command(name="join_region", description="Join a region of Orbana")
    async def join_region(self, interaction: discord.Interaction, region: str):
        await interaction.response.send_message(await join_region(interaction.user.id, region))

    @app_commands.command(name="rank", description="See regional rank and deed progress")
    async def rank(self, interaction: discord.Interaction, user: discord.User = None):
        target = user or interaction.user
        own_rank = target.id == interaction.user.id
        await interaction.response.defer(ephemeral=own_rank)
        progress = await get_rank_progress(target.id, claim=own_rank)
        embed = discord.Embed(
            title=f"{target.display_name} · {progress.region or 'Unaligned'}",
            description=f"Tier {progress.tier} · **{progress.rank.title}**",
        )
        if progress.next_rank and own_rank:
            lines = []
            for deed in progress.next_rank.deeds:
                current = max(0, progress.metrics[deed.metric])
                mark = "✅" if current >= deed.target else "⬜"
                lines.append(f"{mark} {deed.label}: {min(current, deed.target):,}/{deed.target:,}")
            embed.add_field(name=f"Next · {progress.next_rank.title}", value="\n".join(lines))
        elif progress.next_rank:
            embed.add_field(name="Next rank", value=progress.next_rank.title)
        elif progress.region in RANK_LADDERS:
            embed.set_footer(text="Highest deed rank earned")
        elif progress.region is None:
            embed.set_footer(text="Use /join_region to choose a region.")
        await interaction.followup.send(embed=embed, ephemeral=own_rank)

    @app_commands.command(name="regioninfo", description="Read about a region or territory of Orbana")
    async def regioninfo(self, interaction: discord.Interaction, region: str):
        name = region.strip()
        if name.casefold() == "ramidia":
            embed = discord.Embed(
                title="Ramidia",
                description=(
                    "Occupied territory. The military council maintains its policies "
                    "pending the Supreme Leader’s return.\n\n"
                    "Provisional head of state: <@1205837634073792572>\n"
                    "Absent Supreme Leader: <@1459934008308928655>\n"
                    "Resident title: Comrade"
                ),
            )
        else:
            pool = await get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM regions WHERE LOWER(name)=LOWER($1)", name
                )
            if row is None:
                await interaction.response.send_message("❌ Region not found.", ephemeral=True)
                return

            embed = discord.Embed(title=row["name"], description=row["description"])
            if row["is_capital"]:
                embed.add_field(name="Capital", value="Seat of authority", inline=False)
            ladder = (CITIZEN, *RANK_LADDERS.get(row["name"], ()))
            lines = []
            for tier, rank in enumerate(ladder, start=1):
                requirements = " · ".join(
                    f"{deed.label}: {deed.target:,}" for deed in rank.deeds
                )
                detail = f" — {requirements}" if requirements else ""
                lines.append(f"{tier}. **{rank.title}**{detail}")
            embed.add_field(name="Deed ranks", value="\n".join(lines), inline=False)
            leadership = REGION_LEADERSHIP.get(row["name"])
            if leadership:
                leader, succession = leadership
                embed.add_field(
                    name="Founding leadership",
                    value=f"<@{leader}>" if leader else "Vacant",
                    inline=False,
                )
                embed.add_field(name="Proposed succession", value=succession, inline=False)

        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(Regions())
