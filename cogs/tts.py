import io

import discord
from gtts import gTTS
from discord.ext import commands
from discord import app_commands, Interaction, VoiceState, TextChannel, Member
from typing import Dict, List
import asyncio

from core.local import LocalCore
from core.model.voice_model import VoiceModel
from core.utile import is_admin

class TTS(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.queue: Dict[int, VoiceModel] = {}
        # key: GuildId, value: MessageChannelId
        self.messageChannel: Dict[int, int] = {}
        # key: GuildId, value: MessageChannelId
        self.defaultChannel: Dict[int, int] = {}
        asyncio.run_coroutine_threadsafe(self.load_local_default_channel(), self.bot.loop)


    async def load_local_default_channel(self):
        local_default_channels = await LocalCore.ttsDataSource.get_all()
        for i in local_default_channels:
            self.defaultChannel[i.guild_id] = i.channel_id
        print("로컬에서 TTS 기본 채널 불러옴.")
        print(self.defaultChannel)

    async def clear_guild_queue(self, guild_id: int):
        if self.queue.get(guild_id) is not None:
            self.queue[guild_id]["vc"].stop()
            await self.queue[guild_id]["vc"].disconnect()
        self.queue.pop(guild_id, None)
        self.messageChannel.pop(guild_id, None)

    @app_commands.command()
    @is_admin()
    async def set_default_channel(self, ctx: Interaction, channel: TextChannel):
        self.defaultChannel[ctx.guild.id] = channel.id
        local_tts_info = await LocalCore.ttsDataSource.get(ctx.guild.id)
        if local_tts_info is None:
            await LocalCore.ttsDataSource.insert(ctx.guild.id, channel.id)
        else:
            await LocalCore.ttsDataSource.update(ctx.guild.id, channel.id)
        await ctx.response.send_message(f"TTS 수신 채널이 {channel.mention}로 설정되었습니다.", ephemeral=True)

    @app_commands.command()
    async def join(self, ctx: Interaction):


        if ctx.user.voice is None:
            await ctx.response.send_message("채널에 입장 후 사용해주세요.")
            return

        await self.clear_guild_queue(ctx.guild.id)

        if not ctx.guild.voice_client:
            vc = await ctx.user.voice.channel.connect()
        else:
            vc = ctx.guild.voice_client

        self.queue[ctx.guild.id] = {
            "guild_id": ctx.guild.id,
            "voice_channel_id": ctx.user.voice.channel.id,
            "tts_queue": [],
            "vc": vc,
            "is_playing": False,
        }
        self.messageChannel[ctx.guild.id] = ctx.channel.id
        await ctx.response.send_message("해당 채널에서도 TTS를 수신할게요!")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: VoiceState, after: VoiceState):
        print(member.display_name)
        # 이전 상태에 음성 채널이 있고, 이후 상태에서 채널이 None이면 나간 것
        if before.channel is not None and after.channel is None and member.id == self.bot.user.id:
            return await self.clear_guild_queue(member.guild.id)

        # 유저가 채널 이동 또는 채널을 나간 경우
        if before.channel is not None:
            if (
                after.channel is not None and
                member.id == self.bot.user.id and
                sum(1 for member in after.channel.members if not member.bot) == 0
            ):
                return await self.queue[member.guild.id]["vc"].disconnect()

            # 유저가 채널을 나갈 경우, 봇이 해당 채널에 있는지 확인 후 나가기.
            join_member_list = before.channel.members
            if (
                self.bot.user.id in list(map(lambda x: x.id, join_member_list)) and
                sum(1 for member in join_member_list if not member.bot) == 0
            ):
                return await self.queue[member.guild.id]["vc"].disconnect()

    async def play_tts(self, guild_id: int):
        voice_model = self.queue[guild_id]
        if len(voice_model["tts_queue"]) == 0:
            voice_model["is_playing"] = False
            return

        """
        추후 TTS 음성 처리에서 비동기 처리 필요, 현재는 서버가 단 하나여서 임시로 동기 처리.
        """
        message = voice_model["tts_queue"].pop(0)
        tts = gTTS(text=message, lang="ko")
        tts_fp = io.BytesIO()
        tts.write_to_fp(tts_fp)
        tts_fp.seek(0)  # 스트림의 시작 위치로 이동

        source = discord.FFmpegPCMAudio(tts_fp, pipe=True)
        voice_model["is_playing"] = True
        voice_model["vc"].play(
            source,
            after=lambda e: asyncio.run_coroutine_threadsafe(self.play_tts(guild_id), self.bot.loop),
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        await self.bot.process_commands(message)
        if (
            message.author.bot or
            isinstance(message.channel, discord.channel.DMChannel) or
            (
                not message.channel.id in self.messageChannel.values() and
                not message.channel.id in self.defaultChannel.values()
            )
        ): return

        if message.author.voice is None:
            return

        if self.queue.get(message.guild.id, None) is None:
            await self.clear_guild_queue(message.guild.id)

            if not message.guild.voice_client:
                vc = await message.author.voice.channel.connect()
            else:
                vc = message.guild.voice_client

            self.queue[message.guild.id] = {
                "guild_id": message.guild.id,
                "voice_channel_id": message.author.voice.channel.id,
                "tts_queue": [],
                "vc": vc,
                "is_playing": False,
            }

        guild_queue = self.queue[message.guild.id]
        if message.author.voice.channel.id != guild_queue["voice_channel_id"]:
            return

        guild_queue["tts_queue"].append(message.content)

        if not guild_queue["is_playing"]:
            await self.play_tts(message.guild.id)




async def setup(bot: commands.Bot):
    await bot.add_cog(TTS(bot))