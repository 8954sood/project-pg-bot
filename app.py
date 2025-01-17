import discord
from discord.ext import commands
import aiosqlite
import asyncio
from dotenv import load_dotenv
import os
import datetime
from traceback import format_exception
import traceback
import aiohttp

load_dotenv()
description = '''made 바비호바#6800'''

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.guilds = True
intents.voice_states = True
intents.presences = True

bot = commands.Bot(command_prefix='-', description=description, intents=intents)
path = "./db.sqlite"

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')

async def db_select(member):
    """
    discord.Member 가 db_select에 들어옴
    """
    async with aiosqlite.connect(path) as db:
        query = "SELECT * FROM users WHERE author = %s"
        tu = (member.id,)
        cursor = await db.execute(f"SELECT * FROM users WHERE author = ?", tu)
        row = await cursor.fetchone()
    if row == None:
        return {"result": False}
    else:
        return {"result": True, "db": row}
async def db_insert(member, role):
    async with aiosqlite.connect(path) as db:
        sql = "INSERT INTO users (author, role, rolename) VALUES (?, ?, ?)"
        val = (member.id, role.id, role.name)
        await db.execute(sql, val)
        await db.commit()
async def db_edit(member, role):
    """
    edit 전에 select 통해서 미리 존재 확인후 기존역할 지운뒤, 새로운 역할 만들고 edit
    """
    async with aiosqlite.connect(path) as db:
        sql = "UPDATE users SET role = ?, rolename = ? WHERE author = ?"
        val = (role.id, role.name, member.id)
        await db.execute(sql, val)
        await db.commit()

@bot.command()
@commands.has_permissions(manage_roles=True, ban_members=True)
async def delete(ctx, member: discord.Member):
    if (
        ctx.author.bot or
        isinstance(ctx.channel,  discord.channel.DMChannel) or
        ctx.guild.id != 1074259285825032213
    ): return
    async with aiosqlite.connect(path) as db:
        sql = "DELETE FROM users WHERE author = ?"
        val = (member.id,)
        await db.execute(sql, val)
        await db.commit()
    return await ctx.send("끝")

@bot.event
async def on_message(message: discord.Message):
    await bot.process_commands(message)
    if (
        message.author.bot or
        isinstance(message.channel, discord.channel.DMChannel) or
        message.guild.id != 1074259285825032213 or
        message.channel.id != 1077585174323273738
    ): return

    word = await message.channel.send(embed=discord.Embed(title="역할을 생성 중입니다."))

    try:
        hexs= message.content
        if hexs[0] != "#": 
            return await word.edit(embed=discord.Embed(title="HEX 코드가 아닙니다", description='#FFFFFF 처럼 지원하는 HEX 코드를 적어주세요.', colour=0xff0000))

        try:
            colour = int(hexs[1:], 16)
        except:
            return await word.edit(embed=discord.Embed(title="HEX 코드가 아닙니다", description='#FFFFFF 처럼 지원하는 HEX 코드를 적어주세요.', colour=0xff0000))

        result = await db_select(message.author)

        if result['result'] == True:
            if str(result['db'][2]) == hexs:
                return await word.edit(embed=discord.Embed(title="같은 색상 역할을 소지중입니다.", colour=0xff0000))
            role = message.guild.get_role(int(result['db'][1]))
            
            if len(role.members) == 1:
                await role.delete()
            else:
                await message.author.remove_roles(role)
            await asyncio.sleep(1)

        for i in range(0, len(message.guild.roles)):
            if str(message.guild.roles[i]) == f"{hexs}":
                gu = message.guild.roles[i]
                # await role_check(gu, gu.id)
                await message.author.add_roles(gu)
                if result['result'] == True:
                    await db_edit(message.author, gu)
                else:
                    await db_insert(message.author. gu)
                await word.edit(embed=discord.Embed(title="역할 부여 완료", description=f"{gu.mention} 역할이 정상적으로 부여되었습니다.", colour=0x34e718))
                return

        try:
            role = await message.guild.create_role(name=f"{hexs}", colour=colour)
            await asyncio.sleep(1)

            position = message.guild.get_role(1077942162257354822).position - 2
            await role.edit(position=position)
            await asyncio.sleep(1)
            await message.author.add_roles(role)
            if result['result'] == True:
                await db_edit(message.author, role)
            else:
                await db_insert(message.author, role)
            await word.edit(embed=discord.Embed(title="역할 부여 완료", description=f"{role.mention} 역할이 정상적으로 부여되었습니다.", colour=0x34e718))
        except commands.MissingPermissions:
            return await word.edit(embed=discord.Embed(title="봇의 권환이 부족합니다", description="이 봇의 권환을 최상단으로 올려주세요.", colour=0xff0000))
        except commands.CommandInvokeError:
            return await word.edit(embed=discord.Embed(title="HEX 코드가 아닙니다", description='#FFFFFF 처럼 지원하는 HEX 코드를 적어주세요.', colour=0xff0000))
        except Exception as E:
            await word.edit(embed=discord.Embed(description=f"에러 로그 : {E}", colour=0xff0000))
    except Exception as Error:
        await word.edit(embed=discord.Embed(description=f"상단 에러 로그 : {Error}", colour=0xff0000))



    
    



token = os.environ.get('BOT_TOKEN')
bot.run(token)