import discord
from discord import app_commands
from discord.ext import commands
import asyncpg
import random
import os
from dotenv import load_dotenv

# =========================
# Load Environment
# =========================
load_dotenv("token.env")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

if not DISCORD_TOKEN or not DATABASE_URL:
    raise RuntimeError("❌ Missing DISCORD_TOKEN or DATABASE_URL")

# =========================
# Discord Setup
# =========================
intents = discord.Intents.default()
client = commands.Bot(command_prefix="!", intents=intents)
tree = client.tree

# =========================
# Database Pool
# =========================
db_pool: asyncpg.Pool | None = None

async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)

# =========================
# Lore / Economy Config
# =========================
STARTING_BONX = 100
SLOT_EMOJIS = ["🍒", "🍋", "🍉", "⭐", "💎"]
MULTIPLIERS = {"🍒": 2, "🍋": 2, "🍉": 3, "⭐": 5, "💎": 10}

# =========================
# Helpers
# =========================
async def get_or_create_user(discord_id: int):
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT * FROM users WHERE discord_id=$1",
            discord_id
        )
        if not user:
            await conn.execute(
                "INSERT INTO users (discord_id) VALUES ($1)",
                discord_id
            )
            user = await conn.fetchrow(
                "SELECT * FROM users WHERE discord_id=$1",
                discord_id
            )
        return user

async def adjust_bonx(discord_id: int, amount: int, reason: str):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET bonx = bonx + $1 WHERE discord_id = $2",
            amount, discord_id
        )
        await conn.execute(
            "INSERT INTO bonx_transactions (discord_id, amount, reason) VALUES ($1, $2, $3)",
            discord_id, amount, reason
        )

# =========================
# Commands
# =========================
@tree.command(name="balance", description="Check your Bonx balance 💰")
async def balance(interaction: discord.Interaction):
    user = await get_or_create_user(interaction.user.id)
    await interaction.response.send_message(
        f"💰 **Bonx Balance:** {user['bonx']}"
    )

# -------------------------
@tree.command(name="profile", description="View your Bonkverse profile 🌍")
async def profile(interaction: discord.Interaction):
    user = await get_or_create_user(interaction.user.id)
    region = user["region"] or "Unaligned"
    await interaction.response.send_message(
        f"🧾 **Bonkverse Profile**\n"
        f"👤 {interaction.user.display_name}\n"
        f"💰 Bonx: {user['bonx']}\n"
        f"🏷️ Rank: {user['rank']}\n"
        f"🌍 Region: {region}"
    )

# -------------------------
@tree.command(name="regions", description="View regions of Orbana")
async def regions(interaction: discord.Interaction):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM regions")
    msg = "\n".join(
        f"**{r['name']}** — {r['description']}" for r in rows
    )
    await interaction.response.send_message(msg)

# -------------------------
@tree.command(name="join_region", description="Join a region of Orbana")
@app_commands.describe(region="Region name")
async def join_region(interaction: discord.Interaction, region: str):
    user = await get_or_create_user(interaction.user.id)

    async with db_pool.acquire() as conn:
        reg = await conn.fetchrow(
            "SELECT * FROM regions WHERE LOWER(name)=LOWER($1)",
            region
        )

    if not reg:
        await interaction.response.send_message("❌ Region not found.")
        return

    if reg["is_capital"]:
        await interaction.response.send_message(
            "🏛️ **Veri is the capital.** Access is restricted by lore."
        )
        return

    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET region=$1 WHERE discord_id=$2",
            reg["name"], interaction.user.id
        )

    await interaction.response.send_message(
        f"🌍 You are now aligned with **{reg['name']}**."
    )

# -------------------------
@tree.command(name="slots", description="Gamble Bonx at the Orbana Market 🎰")
@app_commands.describe(amount="Bonx to bet")
async def slots(interaction: discord.Interaction, amount: int):
    user = await get_or_create_user(interaction.user.id)

    if amount <= 0 or amount > user["bonx"]:
        await interaction.response.send_message("❌ Invalid bet.")
        return

    spin = [random.choice(SLOT_EMOJIS) for _ in range(3)]
    unique = set(spin)

    winnings = -amount
    result = "😢 You lost your bet."

    if len(unique) == 1:
        winnings = amount * MULTIPLIERS[spin[0]]
        result = f"🎉 JACKPOT! {spin[0]} x{MULTIPLIERS[spin[0]]}"
    elif len(unique) == 2:
        winnings = 0
        result = "✨ Two of a kind — bet returned."

    await adjust_bonx(interaction.user.id, winnings, "slots")

    updated = await get_or_create_user(interaction.user.id)

    await interaction.response.send_message(
        f"🎰 | {' | '.join(spin)} |\n"
        f"{result}\n"
        f"💰 New Balance: {updated['bonx']}"
    )

@tree.command(name="help", description="Learn how to use the Bonkverse bot 📖")
async def help_command(interaction: discord.Interaction):
    help_text = (
        "🤖 **Bonkverse.io Bot Help**\n\n"
        "**Economy & Lore**\n"
        "/balance — Check your Bonx 💰\n"
        "/profile — View your Bonkverse identity 🌍\n"
        "/regions — View regions of Orbana\n"
        "/join_region <name> — Align with a region\n\n"
        "**Games & Risk**\n"
        "/slots <amount> — Gamble Bonx at the Orbana Market 🎰\n\n"
        "**Lore Notes**\n"
        "• Bonkverse is the nation\n"
        "• Orbana is the main continent\n"
        "• Veri is the capital (restricted)\n"
        "• Tulip Island is a luxury escape 🌺\n\n"
        "_More features coming soon._"
    )

    await interaction.response.send_message(help_text, ephemeral=False)


# =========================
# Startup
# =========================
@client.event
async def on_ready():
    await init_db()
    await tree.sync()
    print(f"✅ Logged in as {client.user}")

client.run(DISCORD_TOKEN)
