"""The regions of Orbana: /orbana, /regions, /regioninfo, /join_region, /rank,
/regioncup and /appoint.

Owns: the commands and how their replies look. Region names autocomplete, and
/regions has a menu that opens any region's info for whoever picks it.
Does not own: the rules (service.py, ranks.py, titles.py, cup.py).
"""

from datetime import timezone

import discord
from discord import app_commands
from discord.ext import commands

from config.settings import (
    HISTORY_MAX_ENTRIES,
    SKINTOPIA_VOTE_CHANGE_TAX,
    VERI_HISTORY_MAX_ENTRIES,
    VOTE_CHANGE_TAX,
)
from db.pool import get_pool
from utils.ui import BONX, INFO, SUCCESS, OwnedView, card, refuse, send

from .cup import get_cup_standings
from .ranks import CITIZEN, RANK_LADDERS, get_rank_progress
from .service import did_you_mean, join_region, region_names
from .titles import TITLES, appoint, get_region_leader, get_titleholder

RAMIDIA = "Ramidia"


async def region_choices(current: str, *, territories: bool = False) -> list[app_commands.Choice[str]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        names = await region_names(conn)
    if territories:
        names.append(RAMIDIA)
    typed = current.strip().casefold()
    return [app_commands.Choice(name=n, value=n) for n in names if typed in n.casefold()][:25]


def ramidia_card() -> discord.Embed:
    return card(
        RAMIDIA,
        "Occupied territory. The military council maintains its policies "
        "pending the Supreme Leader’s return.\n\n"
        "Provisional head of state: <@1205837634073792572>\n"
        "Absent Supreme Leader: <@1459934008308928655>\n"
        "Resident title: Comrade",
        discord.Color.dark_red(),
    )


async def region_card(name: str) -> discord.Embed | None:
    """Everything /regioninfo shows, or None if there's no such region."""
    if name.strip().casefold() == RAMIDIA.casefold():
        return ramidia_card()

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM regions WHERE LOWER(name)=LOWER($1)", name.strip())
        if row is None:
            return None
        roster = await conn.fetch(
            """
            SELECT u.discord_id, COALESCE(p.tier, 1) AS tier
            FROM users u
            LEFT JOIN region_rank_progress p ON p.discord_id=u.discord_id AND p.region=u.region
            WHERE u.region=$1
            ORDER BY tier DESC, u.discord_id
            """,
            row["name"],
        )

    region = row["name"]
    capital = " 🏛️" if row["is_capital"] else ""
    embed = card(f"{region}{capital}", row["description"], INFO)

    ladder = (CITIZEN, *RANK_LADDERS.get(region, ()))
    if len(ladder) > 1:
        lines = []
        for tier, rank in enumerate(ladder, start=1):
            needs = " · ".join(f"{deed.label} {deed.target:,}" for deed in rank.deeds)
            lines.append(f"`T{tier}` **{rank.title}**" + (f" · {needs}" if needs else ""))
        ranks = "\n".join(lines)
    else:
        ranks = "Citizen is the only rank here."
    embed.add_field(name="Deed ranks", value=ranks, inline=False)

    if region == "Skintopia":
        embed.add_field(
            name="Tier 3 perk",
            value=f"Changing a skin vote costs {SKINTOPIA_VOTE_CHANGE_TAX} Bonx instead of {VOTE_CHANGE_TAX}.",
            inline=False,
        )
    elif region == "Veri":
        embed.add_field(
            name="Tier 3 perk",
            value=f"`/history` shows up to {VERI_HISTORY_MAX_ENTRIES} entries instead of {HISTORY_MAX_ENTRIES}.",
            inline=False,
        )

    title = TITLES.get(region)
    if title:
        holder = await get_titleholder(region)
        leader = await get_region_leader(region) if title.appointed else holder
        embed.add_field(name="Leader", value=f"<@{leader}>" if leader else "Vacant")
        embed.add_field(
            name=f"👑 {title.name}",
            value=f"{f'<@{holder}>' if holder else 'Vacant'}\n*{title.rule}*",
        )

    shown = roster[:15]
    members = [f"<@{m['discord_id']}> · {ladder[m['tier'] - 1].title}" for m in shown]
    if len(roster) > len(shown):
        members.append(f"…and {len(roster) - len(shown)} more")
    embed.add_field(
        name=f"Residents ({len(roster)})",
        value="\n".join(members) or "Nobody yet. Be the first with `/join_region`.",
        inline=False,
    )
    embed.set_footer(text="New to regions? /orbana explains how they work.")
    return embed


class RegionMenu(OwnedView):
    """Anyone can pick from the /regions menu; the region's info is shown
    only to whoever picked it, so the channel isn't flooded."""

    def __init__(self, names: list[str]):
        super().__init__(owner_id=0, timeout=600)
        menu = discord.ui.Select(
            placeholder="Read about a region",
            options=[discord.SelectOption(label=n, value=n) for n in names[:25]],
        )
        menu.callback = self._pick
        self.menu = menu
        self.add_item(menu)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return True

    async def _pick(self, interaction: discord.Interaction):
        embed = await region_card(self.menu.values[0])
        await send(interaction, embed, private=True)


class Regions(commands.Cog):
    @app_commands.command(name="orbana", description="How regions, deeds, ranks and the Region Cup work")
    async def orbana(self, interaction: discord.Interaction):
        embed = card(
            "🗺️ Welcome to Orbana",
            "Orbana is the continent in our Discord story. It’s home to the nation of "
            "Bonkverse, whose capital is Veri. Bonx is its currency.",
            INFO,
        )
        embed.add_field(
            name="1. Pick a region",
            value=(
                "Browse with `/regions` and read one with `/regioninfo`. Skin artists might like "
                "Skintopia; coinflip players might like Arena City. Move in with `/join_region`. "
                "You can move again after 7 days."
            ),
            inline=False,
        )
        embed.add_field(
            name="2. Do deeds, earn ranks",
            value=(
                "Everyone starts as a Citizen. A deed is a goal, like voting on three skins, and "
                "each region has its own. `/rank` shows your next goals and claims ranks you’ve "
                "earned. Some ranks unlock perks, like cheaper vote changes in Skintopia."
            ),
            inline=False,
        )
        embed.add_field(
            name="3. Represent your region",
            value=(
                "The Region Cup is a weekly contest for bragging rights. After 3 days in your "
                "region, claim `/daily` during the week to count. Standings: `/regioncup`."
            ),
            inline=False,
        )
        embed.add_field(
            name="👑 Leadership",
            value=(
                "Each region’s Tier 6 title belongs to one resident at a time. Most regions award "
                "it automatically. In Creativia, Versetralis and Veri, the leader appoints a Tier 5 "
                "resident with `/appoint`."
            ),
            inline=False,
        )
        embed.set_footer(text="All commands: /help · The occupied territory: /regioninfo Ramidia")
        await send(interaction, embed)

    @app_commands.command(name="appoint", description="Leaders: appoint a Tier 5 resident to your region's Tier 6 title")
    @app_commands.describe(user="A Tier 5 resident of your region")
    async def appoint_member(self, interaction: discord.Interaction, user: discord.User):
        if user.bot:
            await refuse(interaction, "Bots can't hold titles. Pick a Tier 5 resident.")
            return
        appointed, message = await appoint(interaction.user.id, user.id)
        if not appointed:
            await refuse(interaction, message)
            return
        await send(interaction, card("👑 Title appointed", message, SUCCESS))

    @app_commands.command(name="regions", description="Every region of Orbana")
    async def regions(self, interaction: discord.Interaction):
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT r.name, r.is_capital, r.description, COUNT(u.discord_id) AS residents
                FROM regions r LEFT JOIN users u ON u.region = r.name
                GROUP BY r.name, r.is_capital, r.description
                ORDER BY r.is_capital DESC, r.name
                """
            )
        lines = [
            f"**{r['name']}**{' 🏛️' if r['is_capital'] else ''} · {r['description']} · "
            f"{r['residents']} resident{'s' if r['residents'] != 1 else ''}"
            for r in rows
        ]
        embed = card("🌍 Regions of Orbana", "\n".join(lines), INFO)
        embed.set_footer(text="Move in with /join_region · How it works: /orbana")
        view = RegionMenu([r["name"] for r in rows] + [RAMIDIA])
        await send(interaction, embed, view=view)
        await view.attach(interaction)

    @app_commands.command(name="join_region", description="Move to a region of Orbana")
    @app_commands.describe(region="Where to move")
    async def join_region(self, interaction: discord.Interaction, region: str):
        joined, message = await join_region(interaction.user.id, region)
        if not joined:
            await refuse(interaction, message)
            return
        embed = card(description=message, color=SUCCESS)
        embed.set_footer(text="See your deeds with /rank")
        await send(interaction, embed)

    @app_commands.command(name="rank", description="Regional rank and progress toward the next one")
    @app_commands.describe(user="Whose rank to show (you, if left empty)")
    async def rank(self, interaction: discord.Interaction, user: discord.User = None):
        target = user or interaction.user
        own_rank = target.id == interaction.user.id
        await interaction.response.defer()
        progress = await get_rank_progress(target.id, claim=own_rank)
        embed = card(
            f"{target.display_name} · {progress.region or 'Unaligned'}",
            f"Tier {progress.tier} · **{progress.rank.title}**",
            INFO,
        )
        embed.set_thumbnail(url=target.display_avatar.url)
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
            embed.set_footer(text="Pick a region with /join_region")
        else:
            embed.add_field(
                name="Regional progression",
                value=(
                    f"{progress.region} has Citizen rank only. Deeds earn ranks in other regions; "
                    "for example, Bonx sent or received with `/give` counts toward Bonktown’s "
                    "Trader rank. Browse with `/regions`."
                ),
                inline=False,
            )
        title = TITLES.get(progress.region)
        if title:
            holder = await get_titleholder(progress.region)
            if holder == target.id:
                embed.description = f"Tier 6 · 👑 **{title.name}**\nDeed rank: {progress.rank.title}"
            else:
                holder_name = f"<@{holder}>" if holder else "Vacant"
                embed.add_field(name=f"👑 {title.name}", value=holder_name, inline=False)
        await send(interaction, embed)

    @app_commands.command(name="regioncup", description="This week's Region Cup standings")
    async def regioncup(self, interaction: discord.Interaction):
        await interaction.response.defer()
        start, end, standings, champion = await get_cup_standings()
        opened = int(start.replace(tzinfo=timezone.utc).timestamp())
        closes = int(end.replace(tzinfo=timezone.utc).timestamp())
        embed = card(
            "🏆 Region Cup",
            f"Ends <t:{closes}:R> · started <t:{opened}:d>\n"
            f"Last week’s champion: **{champion or 'None yet'}**",
            BONX,
        )
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        ranked = [
            f"{medals.get(i, f'`{i}.`')} **{name}** · {float(score):.1f} pts · {members} active"
            for i, (name, members, _, score) in enumerate(standings, start=1)
        ]
        treasuries = sorted(standings, key=lambda row: (-row[2], row[0]))
        embed.add_field(name="Standings", value="\n".join(ranked) or "No regions yet.", inline=False)
        embed.add_field(
            name="Treasury · Bonx from dailies and skin votes",
            value="\n".join(f"**{name}** · {treasury:,}" for name, _, treasury, _ in treasuries)
            or "No regions yet.",
            inline=False,
        )
        embed.add_field(
            name=RAMIDIA,
            value="Occupied territory. Points held in trust pending the Supreme Leader’s return.",
            inline=False,
        )
        embed.set_footer(text="To count: live in your region 3+ days and claim a daily this week.")
        await send(interaction, embed)

    @app_commands.command(name="regioninfo", description="A region's ranks, perks, leaders and residents")
    @app_commands.describe(region="Which region")
    async def regioninfo(self, interaction: discord.Interaction, region: str):
        embed = await region_card(region)
        if embed is None:
            pool = await get_pool()
            async with pool.acquire() as conn:
                names = await region_names(conn)
            await refuse(interaction, did_you_mean(region, [*names, RAMIDIA]))
            return
        await send(interaction, embed)

    @join_region.autocomplete("region")
    async def _join_choices(self, interaction: discord.Interaction, current: str):
        return await region_choices(current)

    @regioninfo.autocomplete("region")
    async def _info_choices(self, interaction: discord.Interaction, current: str):
        return await region_choices(current, territories=True)


async def setup(bot):
    await bot.add_cog(Regions())
