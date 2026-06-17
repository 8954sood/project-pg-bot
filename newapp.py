import discord
from discord.ext import commands
import logging

from core.bot.loader import load_extensions, sync_commands
from core.bot.logging import setup_logging
from core.bot.settings import load_settings
from core.local import LocalCore
from core.utile import KoreanTranslator
import platform


settings = load_settings()
setup_logging()
logger = logging.getLogger(__name__)
description = '''made 바비호바#6800'''

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.messages = True
intents.guilds = True
intents.voice_states = True
intents.presences = True

# Opus 라이브러리 로드
if platform.system() == "Darwin":
    discord.opus.load_opus(settings.opus_path)

bot = commands.Bot(command_prefix='-', description=description, intents=intents)

@bot.event
async def on_ready():
    await LocalCore.init_tables()
    if bot.tree.translator is None:
        await bot.tree.set_translator(KoreanTranslator())

    await load_extensions(bot)
    await sync_commands(bot, settings.guild_id)
    logger.info("Logged in as %s (ID: %s)", bot.user, bot.user.id)


bot.run(settings.token)
