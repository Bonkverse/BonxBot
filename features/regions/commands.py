from datetime import timezone

import discord
from discord import app_commands
from discord.ext import commands

from config.settings import HISTORY_MAX_ENTRIES, SKINTOPIA_VOTE_CHANGE_TAX, VERI_HISTORY_MAX_ENTRIES, VOTE_CHANGE_TAX
from db.pool import get_pool
from .cup import get_cup_standings
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
        await interaction.response.defer()
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
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="regioncup", description="See this week's Region Cup standings")
    async def regioncup(self, interaction: discord.Interaction):
        await interaction.response.defer()
        start, end, standings, champion = await get_cup_standings()
        opened = int(start.replace(tzinfo=timezone.utc).timestamp())
        closes = int(end.replace(tzinfo=timezone.utc).timestamp())
        embed = discord.Embed(
            title="🏆 Region Cup",
            description=(
                f"<t:{opened}:f> → <t:{closes}:f>\n"
                f"Last week’s champion: **{champion or 'None'}**"
            ),
            color=discord.Color.gold(),
        )
        ranked = [
            f"{i}. **{name}** · {float(score):.1f} pts · {members} active"
            for i, (name, members, _, score) in enumerate(standings, start=1)
        ]
        treasuries = sorted(standings, key=lambda row: (-row[2], row[0]))
        embed.add_field(name="Standings", value="\n".join(ranked) or "No regions yet.", inline=False)
        embed.add_field(
            name="Treasury · Bonx earned from dailies and skin votes",
            value="\n".join(f"**{name}** · {treasury:,}" for name, _, treasury, _ in treasuries)
            or "No regions yet.",
            inline=False,
        )
        embed.add_field(
            name="Ramidia",
            value="Occupied territory. Points held in trust pending the Supreme Leader’s return.",
            inline=False,
        )
        embed.set_footer(text="Join a region for three days and claim a daily this week to count.")
        await interaction.followup.send(embed=embed)

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
                if row is not None:
                    roster = await conn.fetch(
                        """
                        SELECT u.discord_id, COALESCE(p.tier, 1) AS tier
                        FROM users u
                        LEFT JOIN region_rank_progress p ON p.discord_id=u.discord_id AND p.region=u.region
                        WHERE u.region=$1
                        ORDER BY tier DESC, u.discord_id
                        LIMIT 16
                        """,
                        row["name"],
                    )
            if row is None:
                await interaction.response.send_message("❌ Region not found.")
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
            if row["name"] == "Skintopia":
                embed.add_field(
                    name="Tier 3 perk",
                    value=f"Vote changes cost {SKINTOPIA_VOTE_CHANGE_TAX} Bonx (usually {VOTE_CHANGE_TAX}).",
                    inline=False,
                )
            elif row["name"] == "Veri":
                embed.add_field(
                    name="Tier 3 perk",
                    value=f"Request {VERI_HISTORY_MAX_ENTRIES} /history entries (usually {HISTORY_MAX_ENTRIES}).",
                    inline=False,
                )

            members = [f"<@{member['discord_id']}> · {ladder[member['tier'] - 1].title}"
                       for member in roster[:15]]
            if len(roster) > 15:
                members.append("Showing 15 residents.")
            embed.add_field(name="Residents", value="\n".join(members) or "No residents yet.", inline=False)
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
