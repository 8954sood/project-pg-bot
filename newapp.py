import discord
from discord.ext import commands
from dotenv import load_dotenv
import os

load_dotenv()
description = '''made 바비호바#6800'''

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix='-', description=description, intents=intents)
path = "./db.sqlite"

@bot.event
async def on_ready():
    bot.db = ""
    for cog in os.listdir("./cogs"):
        if cog.endswith(".py"):
            if cog == "__init__.py":
                continue
            try:
                await bot.load_extension(f'cogs.{cog.lower()[:-3]}')
                print(f'{cog} cog loaded.')
            except Exception as e:
                print(f'Failed to load {cog} cog: {e}')

    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')


token = os.environ.get('BOT_TOKEN')
bot.run(token)