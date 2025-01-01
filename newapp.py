import discord
from discord.ext import commands
from dotenv import load_dotenv
import os
import ctypes.util

from core.local import LocalCore
import platform

load_dotenv()
description = '''made 바비호바#6800'''

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.messages = True
intents.guilds = True
intents.voice_states = True

# Opus 라이브러리 로드
if platform.system() == "Darwin":
    discord.opus.load_opus(os.environ.get('OPUS_PATH'))

bot = commands.Bot(command_prefix='-', description=description, intents=intents)

@bot.event
async def on_ready():
    await LocalCore.init_tables()

    for cog in os.listdir("./cogs"):
        if cog.endswith(".py"):
            if cog == "__init__.py":
                continue
            try:
                await bot.load_extension(f'cogs.{cog.lower()[:-3]}')
                print(f'{cog} cog loaded.')
            except Exception as e:
                print(f'Failed to load {cog} cog: {e}')

    bot.tree.copy_global_to(guild=discord.Object(id=1074259285825032213))
    await bot.tree.sync()
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')


token = os.environ.get('BOT_TOKEN')
bot.run(token)