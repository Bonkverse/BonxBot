import discord
from discord.ext import commands
import os
from dotenv import load_dotenv

from db.pool import init_db
from features.regions.service import ensure_region_schema
from features.skins.poller import start_poller
from features.skins.voting import VoteView
from features.skins.bonkverse_sync import ensure_schema
from features.economy.service import ensure_economy_schema
from features.accounts.service import ensure_accounts_schema
from features.accounts.verify import start_link_checker
from features.skins import notify as skin_notify
from features.site.collect import message_content_enabled
from features.site.feed import FeedCollect, start_skin_feed
from utils.errors import on_app_command_error

load_dotenv("token.env")

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
# Needed to read a message someone replies to when they @BonxBot to collect
# its skin; enable it in the Developer Portal first (features/site/collect.py).
intents.message_content = message_content_enabled()
bot = commands.Bot(command_prefix="!", intents=intents)
bot.tree.on_error = on_app_command_error  # cooldowns, staff-only, crashes


@bot.event
async def on_ready():
    await init_db()
    await ensure_schema()  # adds the Bonkverse sync columns to `skins` if missing
    await ensure_economy_schema()  # adds the daily-streak column to `users` if missing

    await ensure_region_schema()
    await ensure_accounts_schema()  # linked bonk.io accounts (LevelBot's table + claims)
    await skin_notify.ensure_schema()  # users.skin_dms (opt-out for skin DMs)
    skin_notify.attach(bot)

    # Load cogs
    await bot.load_extension("features.economy.commands")
    await bot.load_extension("features.economy.coinflip")
    await bot.load_extension("features.misc.commands")
    await bot.load_extension("features.regions.commands")
    await bot.load_extension("features.skins.commands")
    await bot.load_extension("features.skins.browse")  # /submissions
    await bot.load_extension("features.skins.notify")  # /skin_dms
    await bot.load_extension("features.accounts.commands")
    await bot.load_extension("features.site.commands")  # /skin, /player, /mapdecode
    await bot.load_extension("features.site.collect")  # /collect, Apps menu, @mentions
    await bot.load_extension("features.site.upload")  # /upload, Apps menu, "@BonxBot upload"

    await bot.tree.sync()

    print(f"✅ Logged in as {bot.user}")

    # ♻️ Re-register persistent VoteViews for all active (unfinalized) skins.
    # This is critical: without this, buttons on messages sent before a restart
    # will show "This interaction failed" because the View objects are gone.
    from db.pool import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        active_skins = await conn.fetch(
            "SELECT id FROM skins WHERE finalized = FALSE AND ends_at > NOW()"
        )

    for row in active_skins:
        bot.add_view(VoteView(row["id"]))

    print(f"♻️ Re-registered {len(active_skins)} active vote view(s)")

    print("🚀 Starting skins poller...")
    bot.loop.create_task(start_poller(bot))
    bot.loop.create_task(start_link_checker(bot))  # /link friend-request checks
    bot.add_dynamic_items(FeedCollect)  # ⭐ under #skin-feed posts, across restarts
    bot.loop.create_task(start_skin_feed(bot))  # off unless SKIN_FEED_CHANNEL_ID is set


bot.run(TOKEN)

