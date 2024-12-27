import io

import discord
from gtts import gTTS
from discord.ext import commands
from discord import app_commands, Interaction, VoiceState
from typing import Dict
import asyncio

from core.model.voice_model import VoiceModel

ttsChannelId = 1270299052617105478

class TTS(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.queue: Dict[int, VoiceModel] = {}
        # key: GuildId, value: MessageChannelId
        self.messageChannel: Dict[int, int] = {}

    async def clear_guild_queue(self, guild_id: int):
        if self.queue.get(guild_id) is not None:
            self.queue[guild_id]["vc"].stop()
            await self.queue[guild_id]["vc"].disconnect()
        self.queue.pop(guild_id, None)
        self.messageChannel.pop(guild_id, None)

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
        await ctx.response.send_message("해당 채널에서 TTS를 수신할게요!")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: VoiceState, after: VoiceState):
        if not member.bot or member.id != self.bot.user.id:
            return
        # 이전 상태에 음성 채널이 있고, 이후 상태에서 채널이 None이면 나간 것
        if before.channel is not None and after.channel is None:
            await self.clear_guild_queue(member.guild.id)

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
            not message.channel.id in self.messageChannel.values()
        ): return

        if message.author.voice is None:
            return

        guild_queue = self.queue[message.guild.id]
        if message.author.voice.channel.id != guild_queue["voice_channel_id"]:
            return

        guild_queue["tts_queue"].append(message.content)

        if not guild_queue["is_playing"]:
            await self.play_tts(message.guild.id)




async def setup(bot: commands.Bot):
    await bot.add_cog(TTS(bot))