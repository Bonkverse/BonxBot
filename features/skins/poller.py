import asyncio
import traceback
from datetime import datetime

from db.pool import get_pool

from . import cards, notify
from .bonkverse_sync import sync_pending
from .service import TIER_CHANNELS, finalize_skin

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
                    SELECT id, skin_name, skin_url, creator, vote_message_id, ends_at, finalized
                    FROM skins
                    WHERE ends_at <= $1 AND finalized = FALSE
                """, now)

            print(f"🔍 Found {len(rows)} skins to finalize")

            for row in rows:
                print(f"➡️ Finalizing skin: {row['skin_name']} (ID: {row['id']})")
                print(f"   ends_at: {row['ends_at']} | finalized: {row['finalized']}")

                result = await finalize_skin(row["id"])
                if result is None:
                    continue

                print(f"✅ Result: {result}")

                # 🔹 Update original message
                channel = bot.get_channel(VOTING_CHANNEL_ID)
                if not channel:
                    print("❌ Could not find voting channel")
                    continue

                msg = await channel.fetch_message(row["vote_message_id"])

                embed = cards.finish_vote_card(
                    msg.embeds[0], result["tier"], result["avg"], result["total"]
                )
                await msg.edit(embed=embed, view=None)
                await notify.safely(notify.vote_closed(row["id"], result, msg.jump_url))

                print("✏️ Updated voting message")

                # 🔥 Send to tier channel: the link first (Discord shows the skin
                # image for it), then the result.
                tier_channel_id = TIER_CHANNELS.get(result["tier"])
                tier_channel = bot.get_channel(tier_channel_id) if tier_channel_id else None
                if tier_channel:
                    await tier_channel.send(row["skin_url"])
                    await tier_channel.send(embed=cards.tier_card(
                        row["skin_name"], row["creator"], row["skin_url"],
                        result["tier"], result["avg"], result["total"],
                    ))
                    print("📢 Sent to tier channel")
                else:
                    print(f"❌ Tier channel not found for {result['tier']}")

        except Exception:
            print("🔥 POLLER ERROR:")
            traceback.print_exc()

        # Separate try: a Bonkverse problem must never stop vote finalization.
        try:
            await sync_pending()
        except Exception:
            print("🔥 BONKVERSE SYNC ERROR:")
            traceback.print_exc()

        await asyncio.sleep(60)