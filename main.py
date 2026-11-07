"""Main entry point for the Discord cricket bot."""

import asyncio
import os
from pathlib import Path

# Optional .env file next to main.py (DISCORD_TOKEN=..., ADMIN_GUILD_ID=..., OWNER_ID=...).
# Loaded BEFORE bot_guard is imported, so the IDs in it are used. Never push .env to GitHub.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"))
except ImportError:
    pass

import discord
from discord.ext import commands

# Import card_image early to trigger font startup checks
import card_image as _card_image_startup_check
import security
from bot_guard import ADMIN_GUILD_ID, GuardedTree, MaintenancePrefixBlocked, maintenance_prefix_check
import logging
logging.basicConfig(level=logging.WARNING)

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.dm_messages = True
intents.members = True      # Star Pass: needed to see when the Premium role is given (enable "Server Members Intent" in the Developer Portal)

# GuardedTree blocks every slash command during /maintenance; the check below does the same for text commands.
# chunk_guilds_at_startup=False: don't download the member list of EVERY server at start
# (that hits Discord's gateway limit and delays "Logged in" by minutes). Only the main
# server is loaded (squad_cog.on_ready), which is all the Premium role sync needs.
bot = commands.Bot(command_prefix="", intents=intents, tree_cls=GuardedTree, chunk_guilds_at_startup=False)
bot.add_check(maintenance_prefix_check)

COGS_DIR = Path(__file__).parent


_synced = False


@bot.event
async def on_ready():
    """Called when the bot has connected to Discord and is ready."""
    global _synced
    print(f"✓ Logged in as {bot.user}")

    # Slash commands only show up in Discord after they are SYNCED. Without this,
    # newly added commands (e.g. /countrylogoadder) never appear. Runs once per start.
    if not _synced:
        _synced = True
        # Admin commands exist ONLY in your server (bot_guard.ADMIN_GUILD_ID), so sync that server too.
        try:
            synced = await bot.tree.sync(guild=discord.Object(id=ADMIN_GUILD_ID))
            print(f"✓ Synced {len(synced)} admin slash commands to server {ADMIN_GUILD_ID}")
        except Exception as e:
            print(f"✗ Admin slash command sync failed: {e}")
        try:
            synced = await bot.tree.sync()  # global (can take a few minutes to show up everywhere)
            print(f"✓ Synced {len(synced)} global slash commands")
        except Exception as e:
            print(f"✗ Global slash command sync failed: {e}")


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    """Text commands blocked by maintenance were already answered: stay quiet. Everything else = default."""
    if isinstance(error, MaintenancePrefixBlocked):
        return
    # security.py turns cooldowns / bad input / bugs into short friendly messages.
    await security.handle_command_error(ctx, error)


async def load_cogs():
    """Dynamically load all cogs from the cogs directory."""
    cog_files = [
        "cardmaker_cog",
        "cardstats_cog",
        "style2bulk_cog",
        "economy_cog",
        "pack_cog",
        "draft_cog",
        "quest_cog",
        "starpass_cog",
        "summary_theme_cog",
        "squad_cog",
        "stadium_cog",
        "gif_cog",
        "log_cog",
        "admin_cog",
        "stats_cog",
        "ping_cog",
        "maintenance_cog",
        "cooldown_cog",
        "ranked_cog",
        "announce_cog",
        "playstyle_info_cog",
        "sigbox_cog",
        "auction_cog",
        "spawn_cog",
        # Add other cogs here as needed
    ]
    for cog_name in cog_files:
        try:
            await bot.load_extension(cog_name)
            print(f"✓ Loaded cog: {cog_name}")
        except Exception as e:
            print(f"✗ Failed to load cog {cog_name}: {e}")


def init_databases():
    """Create every table on a NEW server / fresh database (safe on an old one: only
    CREATE TABLE IF NOT EXISTS). Without this a fresh deploy fails with
    "no such table: users" on the first command."""
    for mod, fn in (("economy", "init_economy_db"), ("card_db", "init_card_db"),
                    ("match_records", "init_match_records_db"), ("media", "init_media_db"),
                    ("gif_store", "init_gif_db")):
        try:
            getattr(__import__(mod), fn)()
            print(f"✓ Database ready: {mod}")
        except Exception as e:
            print(f"✗ Database init failed ({mod}): {type(e).__name__}: {e}")


async def main():
    """Run the bot."""
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise ValueError("DISCORD_TOKEN environment variable not set")
    
    init_databases()
    async with bot:
        await load_cogs()
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
