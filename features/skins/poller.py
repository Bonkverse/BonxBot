import asyncio
from datetime import datetime, timezone
import traceback

import discord

from db.pool import get_pool
from .service import finalize_skin, TIER_CHANNELS

VOTING_CHANNEL_ID = 1339872418772160512


async def start_poller(bot):
    await bot.wait_until_ready()
    print("🚀 Poller started!")

    while not bot.is_closed():
        try:
            now = datetime.utcnow()
            print(f"\n🧪 Poll tick at {now}")

            pool = await get_pool()

            async with pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT id, skin_name, skin_url, vote_message_id, ends_at, finalized
                    FROM skins
                    WHERE ends_at <= $1 AND finalized = FALSE
                """, now)

            print(f"🔍 Found {len(rows)} skins to finalize")

            for row in rows:
                print(f"➡️ Finalizing skin: {row['skin_name']} (ID: {row['id']})")
                print(f"   ends_at: {row['ends_at']} | finalized: {row['finalized']}")

                result = await finalize_skin(row["id"])

                print(f"✅ Result: {result}")

                # 🔹 Update original message
                channel = bot.get_channel(VOTING_CHANNEL_ID)
                if not channel:
                    print("❌ Could not find voting channel")
                    continue

                msg = await channel.fetch_message(row["vote_message_id"])

                embed = msg.embeds[0]
                embed.add_field(
                    name="🏆 Result",
                    value=f"{result['tier']} Tier ({result['avg']:.2f})\nVotes: {result['total']}",
                    inline=False
                )

                await msg.edit(embed=embed, view=None)

                print("✏️ Updated voting message")

                # 🔥 Send to tier channel
                tier_channel_id = TIER_CHANNELS.get(result["tier"])

                if tier_channel_id:
                    tier_channel = bot.get_channel(tier_channel_id)

                    if tier_channel:
                        result_embed = discord.Embed(
                            title=f"🏆 {row['skin_name']} by {row['creator']} ranked {result['tier']} Tier!",
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

                        await tier_channel.send(f"🔗 {row['skin_url']}")
                        await tier_channel.send(embed=result_embed)

                        print("📢 Sent to tier channel")
                    else:
                        print(f"❌ Tier channel not found for {result['tier']}")

        except Exception as e:
            print("🔥 POLLER ERROR:")
            traceback.print_exc()

        await asyncio.sleep(60)