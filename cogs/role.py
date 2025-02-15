import asyncio

import discord
from discord.ext import commands
import re

from core.local.local_core import LocalCore

GUILD_ID = 1074259285825032213
HEX_CHANNEL_ID = 1074297167071678516  # 헥스코드 입력 전용 채널 ID
REFERENCE_ROLE_ID = 1077942162257354822  # 기준 역할의 ID (이 역할 바로 아래에 새 역할 생성)

class Role(commands.Cog):
    def __init__(self, bot: discord.ext.commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        await self.bot.process_commands(message)
        if (
            message.author.bot or
            isinstance(message.channel, discord.channel.DMChannel) or
            message.guild.id != GUILD_ID or
            message.channel.id != HEX_CHANNEL_ID
        ): return

        word = await message.channel.send(embed=discord.Embed(title="역할을 생성 중입니다."))

        hex_pattern = r'^#?([A-Fa-f0-9]{6})$'
        match = re.fullmatch(hex_pattern, message.content.upper().strip())
        if not match:
            return await word.edit(
                    embed=discord.Embed(title="HEX 코드가 아닙니다", description='#FFFFFF 처럼 지원하는 HEX 코드를 적어주세요.',
                                        colour=0xff0000))

        hex_value = match.group(1)
        # 헥스코드를 정수로 변환 후 discord.Color 생성
        role_color = discord.Color(int(hex_value, 16))
        role_name = f"#{hex_value}"
        guild = message.guild

        # 동일한 이름의 역할이 이미 존재하는지 확인 (대소문자 구분 없이)
        existing_role = discord.utils.find(lambda r: r.name.lower() == role_name.lower(), guild.roles)
        if existing_role is not None:
            # 기존 역할이 있다면 해당 역할을 유저에게 지급합니다.
            try:
                await message.author.add_roles(existing_role, reason="이미 존재하는 헥스 역할 지급")
            except discord.DiscordException as e:
                await message.channel.send(f"역할 지급 중 오류 발생: {e}")
                return

            # 유저가 이미 가진 다른 헥스 색상 역할이 있다면, 본인만 사용 중인 경우 삭제
            member = guild.get_member(message.author.id)
            if member is not None:
                for role in member.roles:
                    if role.id == existing_role.id:
                        continue
                    if re.fullmatch(r'^#[A-Fa-f0-9]{6}$', role.name):
                        # 해당 역할을 가진 멤버가 본인뿐이라면 삭제
                        if len(role.members) == 1:
                            try:
                                await role.delete(reason="본인만 사용 중인 기존 헥스 컬러 역할 제거")
                            except discord.DiscordException as e:
                                await word.edit(
                                    embed=discord.Embed(title="에러 로그", description=f"기존 역할 삭제 중 오류 발생 : {e}",
                                                        colour=0xff0000))

            await word.edit(embed=discord.Embed(title="역할 부여 완료", description=f"{existing_role.mention} 역할이 정상적으로 부여되었습니다.",
                                                colour=0x34e718))
            return

        # 역할 생성 (이 예제에서는 역할 이름을 '#RRGGBB'로 설정)
        try:
            new_role = await guild.create_role(name=f"#{hex_value}", color=role_color)
        except discord.DiscordException as e:
            await word.edit(
                embed=discord.Embed(title="에러 로그", description=f"{e}",
                                    colour=0xff0000))
            return

        # 기준 역할 가져오기
        reference_role = guild.get_role(REFERENCE_ROLE_ID)
        if reference_role is None:
            await word.edit(
                embed=discord.Embed(title="에러 로그", description="기준 역할을 찾을 수 없습니다.",
                                    colour=0xff0000))
            return

        # 새 역할을 기준 역할 바로 아래로 이동시키기
        # (역할 위치는 숫자가 클수록 상위에 있으므로, 기준 역할의 위치보다 1 낮게 설정)
        target_position = reference_role.position - 1
        try:
            await guild.edit_role_positions({new_role: target_position})
        except discord.DiscordException as e:
            await word.edit(
                embed=discord.Embed(title="에러 로그", description=f"역할 위치 조정 중 오류 발생 : {e}",
                                    colour=0xff0000))
            return

        member = guild.get_member(message.author.id)
        if member is None:
            return

        for role in member.roles:
            if role.id == new_role.id:
                continue
            if re.fullmatch(r'^#[A-Fa-f0-9]{6}$', role.name):
                # 해당 역할을 가진 멤버가 오직 본인뿐이라면
                if len(role.members) == 1:
                    try:
                        await role.delete(reason="본인만 사용 중인 기존 헥스 컬러 역할 제거")
                    except discord.DiscordException as e:
                        await word.edit(
                            embed=discord.Embed(title="에러 로그", description=f"기존 역할 삭제 중 오류 발생 : {e}",
                                                colour=0xff0000))
                        return

        await message.author.add_roles(new_role)
        await word.edit(embed=discord.Embed(title="역할 부여 완료", description=f"{new_role.mention} 역할이 정상적으로 부여되었습니다.",
                                            colour=0x34e718))

async def setup(bot):
    await bot.add_cog(Role(bot))