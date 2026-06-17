import logging

import discord
from discord.ext import commands


logger = logging.getLogger(__name__)

EXTENSIONS = (
    "ui.tts.cog",
    "ui.sleep_timer.cog",
    "ui.role.cog",
    "ui.emoji.cog",
    "ui.cog_manager.cog",
)


async def load_extensions(bot: commands.Bot) -> None:
    for extension in EXTENSIONS:
        try:
            await bot.load_extension(extension)
            logger.info("Cog loaded: %s", extension)
        except Exception:
            logger.exception("Failed to load cog: %s", extension)


async def sync_commands(bot: commands.Bot, guild_id: int) -> None:
    bot.tree.copy_global_to(guild=discord.Object(id=guild_id))
    await bot.tree.sync()

