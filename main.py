"""Main entry point for the Discord cricket bot."""

import asyncio
import os
from pathlib import Path

import discord
from discord.ext import commands

# Import card_image early to trigger font startup checks
import card_image as _card_image_startup_check

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.dm_messages = True

bot = commands.Bot(command_prefix="", intents=intents)

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
        try:
            guild_id = os.getenv("GUILD_ID")  # optional: your test server id -> commands appear instantly
            if guild_id:
                guild = discord.Object(id=int(guild_id))
                bot.tree.copy_global_to(guild=guild)
                synced = await bot.tree.sync(guild=guild)
                print(f"✓ Synced {len(synced)} slash commands to server {guild_id}")
            synced = await bot.tree.sync()  # global (can take a few minutes to show up everywhere)
            print(f"✓ Synced {len(synced)} global slash commands")
        except Exception as e:
            print(f"✗ Slash command sync failed: {e}")


async def load_cogs():
    """Dynamically load all cogs from the cogs directory."""
    cog_files = [
        "cardmaker_cog",
        "economy_cog",
        "squad_cog",
        "stadium_cog",
        "admin_cog",
        # Add other cogs here as needed
    ]
    for cog_name in cog_files:
        try:
            await bot.load_extension(cog_name)
            print(f"✓ Loaded cog: {cog_name}")
        except Exception as e:
            print(f"✗ Failed to load cog {cog_name}: {e}")


async def main():
    """Run the bot."""
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise ValueError("DISCORD_TOKEN environment variable not set")
    
    async with bot:
        await load_cogs()
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
