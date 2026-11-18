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

load_dotenv("token.env")

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    await init_db()
    await ensure_schema()  # adds the Bonkverse sync columns to `skins` if missing
    await ensure_economy_schema()  # adds the daily-streak column to `users` if missing

    await ensure_region_schema()
    await ensure_accounts_schema()  # linked bonk.io accounts (LevelBot's table + claims)

    # Load cogs
    await bot.load_extension("features.economy.commands")
    await bot.load_extension("features.economy.coinflip")
    await bot.load_extension("features.misc.commands")
    await bot.load_extension("features.regions.commands")
    await bot.load_extension("features.skins.commands")
    await bot.load_extension("features.accounts.commands")

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


bot.run(TOKEN)

