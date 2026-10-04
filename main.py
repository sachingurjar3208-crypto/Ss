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


@bot.event
async def on_ready():
    """Called when the bot has connected to Discord and is ready."""
    print(f"✓ Logged in as {bot.user}")
    print(f"✓ Synced {len(bot.tree._get_all_commands())} slash commands")


async def load_cogs():
    """Dynamically load all cogs from the cogs directory."""
    cog_files = [
        "cardmaker_cog",
        "economy_cog",
        "squad_cog",
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
