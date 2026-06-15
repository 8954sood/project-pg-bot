import discord
from discord.ext import commands
from dotenv import load_dotenv
import ctypes.util
import logging
import os

from core.local import LocalCore
from core.utile import KoreanTranslator
import platform


class ContextFormatter(logging.Formatter):
    context_fields = (
        "guild_id",
        "channel_id",
        "voice_channel_id",
        "before_voice_channel_id",
        "after_voice_channel_id",
        "user_id",
        "tts_engine",
        "ai_model",
        "queue_size",
        "fallback",
        "reason",
        "text_length",
        "text_preview",
    )

    def format(self, record):
        message = super().format(record)
        context = " ".join(
            f"{field}={getattr(record, field)!r}"
            for field in self.context_fields
            if hasattr(record, field)
        )
        return f"{message} {context}" if context else message


load_dotenv()
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
for handler in logging.getLogger().handlers:
    handler.setFormatter(
        ContextFormatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
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
    discord.opus.load_opus(os.environ.get('OPUS_PATH'))

bot = commands.Bot(command_prefix='-', description=description, intents=intents)

@bot.event
async def on_ready():
    await LocalCore.init_tables()
    if bot.tree.translator is None:
        await bot.tree.set_translator(KoreanTranslator())

    for cog in os.listdir("./cogs"):
        if cog.endswith(".py"):
            if cog == "__init__.py":
                continue
            try:
                await bot.load_extension(f'cogs.{cog.lower()[:-3]}')
                logger.info("Cog loaded: %s", cog)
            except Exception:
                logger.exception("Failed to load cog: %s", cog)

    bot.tree.copy_global_to(guild=discord.Object(id=1074259285825032213))
    await bot.tree.sync()
    logger.info("Logged in as %s (ID: %s)", bot.user, bot.user.id)


token = os.environ.get('BOT_TOKEN')
bot.run(token)
