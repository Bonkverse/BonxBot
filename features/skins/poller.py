import asyncio
from datetime import datetime, timezone

import discord

from db.pool import get_pool
from .service import finalize_skin, TIER_CHANNELS

VOTING_CHANNEL_ID = 1339872418772160512


async def start_poller(bot):
    await bot.wait_until_ready()

    while not bot.is_closed():
        pool = await get_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, skin_name, image_url, vote_message_id
                FROM skins
                WHERE ends_at <= $1 AND finalized = FALSE
            """, datetime.now(timezone.utc))

        for row in rows:
            result = await finalize_skin(row["id"])

            # 🔹 Update original message
            channel = bot.get_channel(VOTING_CHANNEL_ID)
            msg = await channel.fetch_message(row["vote_message_id"])

            embed = msg.embeds[0]
            embed.add_field(
                name="🏆 Result",
                value=f"{result['tier']} Tier ({result['avg']:.2f})\nVotes: {result['total']}",
                inline=False
            )

            await msg.edit(embed=embed, view=None)

            # 🔥 NEW: Send to tier channel
            tier_channel_id = TIER_CHANNELS.get(result["tier"])
            if tier_channel_id:
                tier_channel = bot.get_channel(tier_channel_id)

                if tier_channel:
                    result_embed = discord.Embed(
                        title=f"🏆 {row['skin_name']} ranked {result['tier']} Tier!",
                        color=discord.Color.green()
                    )
                    result_embed.add_field(
                        name="📊 Score",
                        value=f"{result['avg']:.2f}",
                        inline=True
                    )
                    result_embed.add_field(
                        name="🗳️ Votes",
                        value=f"{result['total']}",
                        inline=True
                    )

                    await tier_channel.send(f"🔗 {row['image_url']}")
                    await tier_channel.send(embed=result_embed)

        await asyncio.sleep(60)