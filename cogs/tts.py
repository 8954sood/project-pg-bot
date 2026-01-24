import os

import discord
from discord.ext import commands
from discord import app_commands, Interaction, VoiceState, TextChannel, Member
from typing import Dict, List, Optional, Set
import asyncio

from core.local import LocalCore
from core.model import VoiceModel, TTSQueueModel
from core.utile import is_admin
from core.tts_engines.gtts_engine import GTTSEngine
from core.tts_engines.ai_stream_engine import AIStreamEngine
from core.local.ttsengine.dto import TTSEngineOption

class TTS(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.queue: Dict[int, VoiceModel] = {}
        # key: GuildId, value: MessageChannelId
        self.messageChannel: Dict[int, int] = {}
        # key: GuildId, value: MessageChannelId
        self.defaultChannel: Dict[int, int] = {}
        # Key: DmChannelID, value: GuildId
        self.dmChannel: Dict[int, int] = {}
        self.voice_option: Dict[int, str] = {}
        self.tts_engine_option: Dict[int, TTSEngineOption] = {}
        self.tts_engine_allow: Set[int] = set()

        self.gtts_engine = GTTSEngine(lambda user_id: self.voice_option.get(user_id, "ko"))
        self.ai_ws_url = os.environ.get("AI_TTS_WS_URL", "")
        self.ai_engine = AIStreamEngine(ai_ws_url=self.ai_ws_url) if self.ai_ws_url else None

        asyncio.run_coroutine_threadsafe(self.load_local_default_channel(), self.bot.loop)
        asyncio.run_coroutine_threadsafe(self.load_local_voice_option(), self.bot.loop)
        asyncio.run_coroutine_threadsafe(self.load_local_tts_engine_option(), self.bot.loop)
        asyncio.run_coroutine_threadsafe(self.load_local_tts_engine_allow(), self.bot.loop)


    async def load_local_default_channel(self):
        local_default_channels = await LocalCore.ttsDataSource.get_all()
        for i in local_default_channels:
            self.defaultChannel[i.guild_id] = i.channel_id
        print("로컬에서 TTS 기본 채널 불러옴.")
        print(self.defaultChannel)

    async def load_local_voice_option(self):
        local_voice_options = await LocalCore.voiceOptionDataSource.get_all()
        for i in local_voice_options:
            self.voice_option[i.user_id] = i.lang
        print("로컬에서 TTS 유저 설정 불러옴.")
        print(self.voice_option)

    async def load_local_tts_engine_option(self):
        local_engine_options = await LocalCore.ttsEngineOptionDataSource.get_all()
        for i in local_engine_options:
            self.tts_engine_option[i.user_id] = i

    async def load_local_tts_engine_allow(self):
        local_allow = await LocalCore.ttsEngineAllowDataSource.get_all()
        self.tts_engine_allow = set([i.user_id for i in local_allow])

    async def clear_guild_queue(self, guild_id: int):
        if self.queue.get(guild_id) is not None:
            self.queue[guild_id]["vc"].stop()
            await self.queue[guild_id]["vc"].disconnect()
        self.queue.pop(guild_id, None)
        self.messageChannel.pop(guild_id, None)
        for key, value in self.dmChannel.items():
            if value == guild_id:
                del self.dmChannel[key]

    def _ensure_guild_state(self, guild: discord.Guild) -> Optional[VoiceModel]:
        existing = self.queue.get(guild.id)
        if existing and existing.get("vc") and existing["vc"].is_connected():
            return existing

        vc = guild.voice_client
        if vc is None:
            return None

        self.queue[guild.id] = {
            "guild_id": guild.id,
            "voice_channel_id": vc.channel.id if vc.channel else 0,
            "tts_queue": [],
            "vc": vc,
        }
        return self.queue[guild.id]

    def _get_user_engine(self, user_id: int) -> tuple:
        option = self.tts_engine_option.get(user_id)
        if option is None:
            return "gtts", None
        return option.engine, option.model_name

    def _is_engine_change_allowed(self, user_id: int) -> bool:
        return user_id in self.tts_engine_allow

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

        if ctx.guild.voice_client is None or ctx.guild.voice_client.channel.id != ctx.user.voice.channel.id:
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

    @app_commands.command()
    @app_commands.choices(lang=[
        app_commands.Choice(name='한국어', value="ko"),
        app_commands.Choice(name='영어', value="en"),
        app_commands.Choice(name='일본어', value="ja"),
        app_commands.Choice(name='스페인어', value="es"),
        app_commands.Choice(name='프랑스어', value="fr"),
        app_commands.Choice(name='러시아어', value="ru"),

    ])
    async def voice_option(self, ctx: Interaction, lang: app_commands.Choice[str]):
        user_voice_option = await LocalCore.voiceOptionDataSource.get_voice_option(ctx.user.id)

        if user_voice_option is None:
            await LocalCore.voiceOptionDataSource.insert(ctx.user.id, lang.value)
        else:
            await LocalCore.voiceOptionDataSource.update(ctx.user.id, lang.value)
        self.voice_option[ctx.user.id] = lang.value

        await ctx.response.send_message(f"TTS의 음성 설정이 {lang.name}로 변경되었어요.")

    @app_commands.command(name="tts_engine")
    @app_commands.choices(engine=[
        app_commands.Choice(name="gtts", value="gtts"),
        app_commands.Choice(name="ai", value="ai"),
    ])
    async def tts_engine(self, ctx: Interaction, engine: app_commands.Choice[str], model_name: Optional[str] = None):
        if not self._is_engine_change_allowed(ctx.user.id):
            return await ctx.response.send_message("You are not allowed to change TTS engine.", ephemeral=True)

        if engine.value == "ai" and not model_name:
            return await ctx.response.send_message("AI engine requires model_name.", ephemeral=True)

        model_to_save = model_name if engine.value == "ai" else None
        await LocalCore.ttsEngineOptionDataSource.upsert(ctx.user.id, engine.value, model_to_save)
        self.tts_engine_option[ctx.user.id] = TTSEngineOption(
            user_id=ctx.user.id,
            engine=engine.value,
            model_name=model_to_save,
        )
        await ctx.response.send_message("TTS engine updated.", ephemeral=True)

    @commands.command()
    async def check_state(self, ctx: commands.Context):
        if ctx.author.id != 464712715487805442:
            return

        return await ctx.send(str(self.queue))

    @commands.command(name="tts_allow")
    @commands.is_owner()
    async def tts_allow(self, ctx: commands.Context, action: str, user: Optional[discord.User] = None):
        action = (action or "").lower()

        if action == "list":
            if not self.tts_engine_allow:
                return await ctx.send("No users are allowed to change TTS engine.")
            allowed_ids = sorted(self.tts_engine_allow)
            return await ctx.send("Allowed users: " + ", ".join(str(uid) for uid in allowed_ids))

        if action in ("add", "remove"):
            if user is None:
                return await ctx.send("Usage: -tts_allow add|remove <user>")
            if action == "add":
                await LocalCore.ttsEngineAllowDataSource.add(user.id)
                self.tts_engine_allow.add(user.id)
                return await ctx.send(f"Allowed: {user.id}")
            await LocalCore.ttsEngineAllowDataSource.remove(user.id)
            self.tts_engine_allow.discard(user.id)
            return await ctx.send(f"Removed: {user.id}")

        return await ctx.send("Usage: -tts_allow add|remove <user> | -tts_allow list")

    @app_commands.command(name="dm설정")
    async def dm_setting(self, ctx: Interaction):
        if (
            ctx.guild is None or
            self.queue.get(ctx.guild.id, None) is None
        ):
            return await ctx.response.send_message("해당 설정은 TTS가 사용중인 길드에서만 가능합니다.")

        channel = await ctx.user.create_dm()
        self.dmChannel[channel.id] = ctx.guild.id
        await channel.send("TTS를 해당 DM 채널에서도 수신할게요!")
        return await ctx.response.send_message("설정이 완료되었습니다! DM 채널에서 사용해보세요!", ephemeral=True)


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
                guild_queue = self.queue.get(member.guild.id)
                if guild_queue and guild_queue.get("vc"):
                    return await guild_queue["vc"].disconnect()
                return

            if (
                member.id == self.bot.user.id and
                after.channel is not None
            ):
                guild_queue = self.queue.get(member.guild.id)
                if guild_queue:
                    guild_queue["voice_channel_id"] = after.channel.id


            # 유저가 채널을 나갈 경우, 봇이 해당 채널에 있는지 확인 후 나가기.
            join_member_list = before.channel.members
            if (
                self.bot.user.id in list(map(lambda x: x.id, join_member_list)) and
                sum(1 for member in join_member_list if not member.bot) == 0
            ):
                guild_queue = self.queue.get(member.guild.id)
                if guild_queue and guild_queue.get("vc"):
                    return await guild_queue["vc"].disconnect()
                return

    async def play_tts(self, guild_id: int):
        voice_model = self.queue[guild_id]
        if len(voice_model["tts_queue"]) == 0:
            return
        if voice_model["vc"].is_playing():
            return

        """
        추후 TTS 음성 처리에서 비동기 처리 필요, 현재는 서버가 단 하나여서 임시로 동기 처리.
        """
        tts_queue_model: TTSQueueModel = voice_model["tts_queue"].pop(0)
        engine, model_name = self._get_user_engine(tts_queue_model["user_id"])

        if engine == "ai":
            if self.ai_engine and model_name:
                try:
                    source = await self.ai_engine.create_discord_source(
                        text=tts_queue_model["text"],
                        model_name=model_name,
                    )
                except Exception as e:
                    print(f"AI engine failed. Falling back to gTTS: {e}")
                    engine = "gtts"
            else:
                print("AI engine unavailable or model missing. Falling back to gTTS.")
                engine = "gtts"
        if engine == "gtts":
            tts_fp = await self.gtts_engine.synth(
                text=tts_queue_model["text"],
                user_id=tts_queue_model["user_id"],
            )
            source = discord.FFmpegPCMAudio(tts_fp, pipe=True)
        voice_model["vc"].play(
            source,
            after=lambda e: asyncio.run_coroutine_threadsafe(self.safe_play_tts(guild_id), self.bot.loop),
        )

    async def safe_play_tts(self, guild_id: int):
        try:
            await self.play_tts(guild_id)
        except Exception as e:
            print(f"TTS 재생 중 오류 발생: {e}")
            
    def _has_image_attachment(self, message: discord.Message) -> bool:
        for attachment in message.attachments:
            content_type = attachment.content_type or ""
            if content_type.startswith("image/"):
                return True
            filename = (attachment.filename or "").lower()
            if filename.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")):
                return True
        return False

    def _normalize_tts_text(self, text: str) -> str:
        stripped = text.strip()
        if stripped and all(ch == "." for ch in stripped):
            count = min(3, stripped.count("."))
            return "점" * count
        if stripped and all(ch == "?" for ch in stripped):
            count = min(3, stripped.count("?"))
            return "물음표" * count
        return text.replace("?", "물음표").replace(".", "(점)")

    def _build_tts_text(self, message: discord.Message) -> str:
        text = self._normalize_tts_text(message.clean_content or "")
        if self._has_image_attachment(message):
            if text:
                return f"(이미지){text}"
            return "(이미지)"
        return text



    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        await self.bot.process_commands(message)
        if (
            message.author.bot or
            (
                not message.channel.id in self.messageChannel.values() and
                not message.channel.id in self.defaultChannel.values() and
                not message.channel.id in self.dmChannel.keys()
            )
        ): return

        # if self.dmChannel.get(message.channel.id, None) is not None:
        if isinstance(message.channel, discord.channel.DMChannel):
            print("dm message received")
            guild_id = self.dmChannel.get(message.channel.id, None)
            if guild_id is None:
                del self.dmChannel[message.channel.id]
                return
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                return

            guild_queue = self._ensure_guild_state(guild)
            if guild_queue is None:
                return

            member = guild.get_member(message.author.id)
            if not member:
                member = await guild.fetch_member(message.author.id)

            if member.voice is None:
                del self.dmChannel[message.channel.id]
                return

            if member.voice.channel.id != guild_queue["voice_channel_id"]:
                return

            tts_text = self._build_tts_text(message)
            if not tts_text:
                return

            guild_queue["tts_queue"].append({
                "text": tts_text,
                "user_id": message.author.id,
            })
            if not guild_queue["vc"].is_playing():
                await self.play_tts(guild_id)
            return
        
        if message.author.voice is None:
            return

        if self.queue.get(message.guild.id, None) is None:
            guild_queue = self._ensure_guild_state(message.guild)
            if guild_queue is None:
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
                }

        guild_queue = self.queue[message.guild.id]
        if message.author.voice.channel.id != guild_queue["voice_channel_id"]:
            return

        tts_text = self._build_tts_text(message)
        if not tts_text:
            return

        guild_queue["tts_queue"].append({
            "text": tts_text,
            "user_id": message.author.id,
        })
        if not guild_queue["vc"].is_playing():
            await self.play_tts(message.guild.id)

async def setup(bot: commands.Bot):
    await bot.add_cog(TTS(bot))
