import asyncio

import discord
from discord.ext import commands

from core.local.local_core import LocalCore


class Role(commands.Cog):
    def __init__(self, bot: discord.ext.commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        await self.bot.process_commands(message)
        if (
            message.author.bot or
            isinstance(message.channel, discord.channel.DMChannel) or
            message.guild.id != 1074259285825032213 or
            message.channel.id != 1077585174323273738
        ): return

        word = await message.channel.send(embed=discord.Embed(title="역할을 생성 중입니다."))

        try:
            hexs = message.content
            if hexs[0] != "#":
                return await word.edit(
                    embed=discord.Embed(title="HEX 코드가 아닙니다", description='#FFFFFF 처럼 지원하는 HEX 코드를 적어주세요.',
                                        colour=0xff0000))

            try:
                colour = int(hexs[1:], 16)
            except:
                return await word.edit(
                    embed=discord.Embed(title="HEX 코드가 아닙니다", description='#FFFFFF 처럼 지원하는 HEX 코드를 적어주세요.',
                                        colour=0xff0000))

            user = await LocalCore.userDataSource.get_user_by_user_id(message.author.id)

            if user is not None:
                if str(user.rolename) == hexs:
                    return await word.edit(embed=discord.Embed(title="같은 색상 역할을 소지중입니다.", colour=0xff0000))
                role = message.guild.get_role(int(user.role))

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
                    if user is not None:
                        await LocalCore.userDataSource.update_user(message.author.id, gu.id, gu.name)
                    else:
                        await LocalCore.userDataSource.insert_user(message.author.id, gu.id, gu.name)
                    await word.edit(
                        embed=discord.Embed(title="역할 부여 완료", description=f"{gu.mention} 역할이 정상적으로 부여되었습니다.",
                                            colour=0x34e718))
                    return

            try:
                role = await message.guild.create_role(name=f"{hexs}", colour=colour)
                await asyncio.sleep(1)

                position = message.guild.get_role(1077942162257354822).position - 2
                await role.edit(position=position)
                await asyncio.sleep(1)
                await message.author.add_roles(role)
                if user is not None:
                    await LocalCore.userDataSource.update_user(message.author.id, role.id, role.name)
                else:
                    await LocalCore.userDataSource.insert_user(message.author.id, role.id, role.name)
                await word.edit(embed=discord.Embed(title="역할 부여 완료", description=f"{role.mention} 역할이 정상적으로 부여되었습니다.",
                                                    colour=0x34e718))
            except commands.MissingPermissions:
                return await word.edit(
                    embed=discord.Embed(title="봇의 권환이 부족합니다", description="이 봇의 권환을 최상단으로 올려주세요.", colour=0xff0000))
            except commands.CommandInvokeError:
                return await word.edit(
                    embed=discord.Embed(title="HEX 코드가 아닙니다", description='#FFFFFF 처럼 지원하는 HEX 코드를 적어주세요.',
                                        colour=0xff0000))
            except Exception as E:
                await word.edit(embed=discord.Embed(description=f"에러 로그 : {E}", colour=0xff0000))
        except Exception as Error:
            await word.edit(embed=discord.Embed(description=f"상단 에러 로그 : {Error}", colour=0xff0000))


async def setup(bot):
    await bot.add_cog(Role(bot))