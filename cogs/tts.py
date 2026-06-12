import asyncio
import logging
import os
from typing import Dict, Optional, Set, Tuple

import discord
from discord import app_commands, Interaction, VoiceState, TextChannel, Member
from discord.ext import commands

from core.local import LocalCore
from core.model import VoiceModel, TTSQueueModel
from core.tts_engines.ai_stream_engine import AIStreamEngine
from core.tts_engines.gtts_engine import GTTSEngine
from core.local.ttsengine.dto import TTSEngineOption
from core.utile import is_admin

logger = logging.getLogger(__name__)


class TTS(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.queue: Dict[int, VoiceModel] = {}
        self.play_locks: Dict[int, asyncio.Lock] = {}
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
        self.max_queue_size = int(os.getenv("TTS_MAX_QUEUE_SIZE", "50"))
        self.max_text_length = int(os.getenv("TTS_MAX_TEXT_LENGTH", "300"))
        self.gtts_timeout = float(os.getenv("TTS_GTTS_TIMEOUT_SECONDS", "10"))

        asyncio.run_coroutine_threadsafe(self.load_local_default_channel(), self.bot.loop)
        asyncio.run_coroutine_threadsafe(self.load_local_voice_option(), self.bot.loop)
        asyncio.run_coroutine_threadsafe(self.load_local_tts_engine_option(), self.bot.loop)
        asyncio.run_coroutine_threadsafe(self.load_local_tts_engine_allow(), self.bot.loop)


    async def load_local_default_channel(self):
        local_default_channels = await LocalCore.ttsDataSource.get_all()
        for i in local_default_channels:
            self.defaultChannel[i.guild_id] = i.channel_id
        logger.info("Loaded local TTS default channels", extra={"channel_count": len(self.defaultChannel)})

    async def load_local_voice_option(self):
        local_voice_options = await LocalCore.voiceOptionDataSource.get_all()
        for i in local_voice_options:
            self.voice_option[i.user_id] = i.lang
        logger.info("Loaded local TTS voice options", extra={"user_count": len(self.voice_option)})

    async def load_local_tts_engine_option(self):
        local_engine_options = await LocalCore.ttsEngineOptionDataSource.get_all()
        for i in local_engine_options:
            self.tts_engine_option[i.user_id] = i

    async def load_local_tts_engine_allow(self):
        local_allow = await LocalCore.ttsEngineAllowDataSource.get_all()
        self.tts_engine_allow = set([i.user_id for i in local_allow])

    async def clear_guild_queue(self, guild_id: int, *, reason: str = "unknown"):
        voice_model = self.queue.get(guild_id)
        vc = voice_model.get("vc") if voice_model else None
        queue_size = len(voice_model["tts_queue"]) if voice_model else 0
        logger.info(
            "Clearing guild TTS queue",
            extra={"guild_id": guild_id, "queue_size": queue_size, "reason": reason},
        )

        # Remove routing state first so a stop-triggered playback callback cannot
        # start another queued item while disconnect is in progress.
        self.queue.pop(guild_id, None)
        self.messageChannel.pop(guild_id, None)
        for key in [key for key, value in self.dmChannel.items() if value == guild_id]:
            self.dmChannel.pop(key, None)

        if vc is not None:
            try:
                if vc.is_playing():
                    vc.stop()
            except Exception:
                logger.exception(
                    "Voice playback stop failed",
                    extra={"guild_id": guild_id, "reason": reason},
                )

            try:
                if vc.is_connected():
                    logger.info(
                        "Voice disconnect starting",
                        extra={"guild_id": guild_id, "reason": reason},
                    )
                    await vc.disconnect()
                    logger.info(
                        "Voice disconnect completed",
                        extra={"guild_id": guild_id, "reason": reason},
                    )
            except Exception:
                logger.exception(
                    "Voice disconnect failed",
                    extra={"guild_id": guild_id, "reason": reason},
                )

    def _get_play_lock(self, guild_id: int) -> asyncio.Lock:
        lock = self.play_locks.get(guild_id)
        if lock is None:
            lock = asyncio.Lock()
            self.play_locks[guild_id] = lock
        return lock

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

    async def _create_tts_source(
        self,
        *,
        guild_id: int,
        queue_item: TTSQueueModel,
    ) -> Tuple[discord.AudioSource, str]:
        user_id = queue_item["user_id"]
        text = queue_item["text"]
        engine, model_name = self._get_user_engine(user_id)
        logger.debug(
            "TTS source creation starting",
            extra={
                "guild_id": guild_id,
                "user_id": user_id,
                "tts_engine": engine,
                "ai_model": model_name,
                "text_length": len(text),
            },
        )

        if engine == "ai":
            if self.ai_engine and model_name:
                try:
                    source = await self.ai_engine.create_discord_source(
                        text=text,
                        model_name=model_name,
                    )
                    if source is None:
                        raise RuntimeError("AI TTS returned no audio source")
                    logger.info(
                        "TTS source creation succeeded",
                        extra={
                            "guild_id": guild_id,
                            "user_id": user_id,
                            "tts_engine": "ai",
                            "ai_model": model_name,
                            "fallback": False,
                        },
                    )
                    return source, "ai"
                except Exception:
                    logger.exception(
                        "AI TTS failed; falling back to gTTS",
                        extra={
                            "guild_id": guild_id,
                            "user_id": user_id,
                            "tts_engine": "ai",
                            "ai_model": model_name,
                            "fallback": True,
                        },
                    )
            else:
                logger.warning(
                    "AI TTS unavailable; falling back to gTTS",
                    extra={
                        "guild_id": guild_id,
                        "user_id": user_id,
                        "tts_engine": "ai",
                        "ai_model": model_name,
                        "fallback": True,
                    },
                )

        try:
            tts_fp = await asyncio.wait_for(
                self.gtts_engine.synth(text=text, user_id=user_id),
                timeout=self.gtts_timeout,
            )
            source = discord.FFmpegPCMAudio(tts_fp, pipe=True)
            if source is None:
                raise RuntimeError("gTTS returned no audio source")
            logger.info(
                "TTS source creation succeeded",
                extra={
                    "guild_id": guild_id,
                    "user_id": user_id,
                    "tts_engine": "gtts",
                    "ai_model": model_name,
                    "fallback": engine == "ai",
                },
            )
            return source, "gtts"
        except asyncio.TimeoutError as exc:
            logger.error(
                "gTTS source creation timed out",
                extra={
                    "guild_id": guild_id,
                    "user_id": user_id,
                    "tts_engine": "gtts",
                    "fallback": engine == "ai",
                },
            )
            raise RuntimeError("gTTS source creation timed out") from exc
        except Exception as exc:
            logger.exception(
                "gTTS source creation failed",
                extra={
                    "guild_id": guild_id,
                    "user_id": user_id,
                    "tts_engine": "gtts",
                    "fallback": engine == "ai",
                },
            )
            raise RuntimeError("All TTS engines failed") from exc

    def _make_after_callback(self, guild_id: int):
        def after(error: Optional[Exception]):
            if error:
                logger.error(
                    "TTS playback callback error",
                    exc_info=(type(error), error, error.__traceback__),
                    extra={"guild_id": guild_id},
                )
            else:
                logger.info("TTS playback completed", extra={"guild_id": guild_id})

            try:
                future = asyncio.run_coroutine_threadsafe(
                    self.safe_play_tts(guild_id),
                    self.bot.loop,
                )
            except Exception:
                logger.exception(
                    "Failed to schedule next TTS playback",
                    extra={"guild_id": guild_id},
                )
                return

            def done_callback(done_future):
                try:
                    done_future.result()
                except Exception:
                    logger.exception(
                        "safe_play_tts failed from playback callback",
                        extra={"guild_id": guild_id},
                    )

            future.add_done_callback(done_callback)

        return after

    async def _enqueue_tts(
        self,
        *,
        guild_id: int,
        voice_model: VoiceModel,
        text: str,
        user_id: int,
        channel_id: int,
    ) -> bool:
        text = text.strip()
        if not text:
            return False
        if len(text) > self.max_text_length:
            logger.debug(
                "TTS text truncated",
                extra={
                    "guild_id": guild_id,
                    "channel_id": channel_id,
                    "user_id": user_id,
                    "text_length": len(text),
                },
            )
            text = text[:self.max_text_length]

        queue_size = len(voice_model["tts_queue"])
        if queue_size >= self.max_queue_size:
            logger.warning(
                "TTS queue full; dropping new message",
                extra={
                    "guild_id": guild_id,
                    "channel_id": channel_id,
                    "user_id": user_id,
                    "queue_size": queue_size,
                },
            )
            return False

        voice_model["tts_queue"].append({"text": text, "user_id": user_id})
        logger.debug(
            "TTS message queued",
            extra={
                "guild_id": guild_id,
                "channel_id": channel_id,
                "user_id": user_id,
                "queue_size": len(voice_model["tts_queue"]),
                "text_length": len(text),
                "text_preview": text[:30],
            },
        )
        return True

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
        guild_id = ctx.guild.id
        user_id = ctx.user.id
        requested_channel_id = ctx.user.voice.channel.id if ctx.user.voice else None
        logger.info(
            "TTS join requested",
            extra={
                "guild_id": guild_id,
                "channel_id": ctx.channel.id,
                "user_id": user_id,
                "voice_channel_id": requested_channel_id,
            },
        )
        if ctx.user.voice is None:
            await ctx.response.send_message("채널에 입장 후 사용해주세요.")
            return

        if ctx.guild.voice_client is None or ctx.guild.voice_client.channel.id != ctx.user.voice.channel.id:
            await self.clear_guild_queue(
                guild_id,
                reason="manual_join_other_channel",
            )

        try:
            if not ctx.guild.voice_client:
                vc = await ctx.user.voice.channel.connect()
                logger.info(
                    "Voice channel connection succeeded",
                    extra={
                        "guild_id": guild_id,
                        "voice_channel_id": ctx.user.voice.channel.id,
                        "user_id": user_id,
                    },
                )
            else:
                vc = ctx.guild.voice_client
                logger.info(
                    "Reusing existing voice client",
                    extra={
                        "guild_id": guild_id,
                        "voice_channel_id": vc.channel.id if vc.channel else None,
                        "user_id": user_id,
                    },
                )
        except Exception:
            logger.exception(
                "Voice channel connection failed",
                extra={
                    "guild_id": guild_id,
                    "voice_channel_id": ctx.user.voice.channel.id,
                    "user_id": user_id,
                },
            )
            await ctx.response.send_message("음성 채널 연결에 실패했습니다.", ephemeral=True)
            return

        existing = self.queue.get(guild_id)
        if existing and existing.get("vc") is vc:
            existing["voice_channel_id"] = ctx.user.voice.channel.id
        else:
            self.queue[guild_id] = {
                "guild_id": guild_id,
                "voice_channel_id": ctx.user.voice.channel.id,
                "tts_queue": [],
                "vc": vc,
            }
        self.messageChannel[guild_id] = ctx.channel.id
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
        guild_id = member.guild.id
        before_channel_id = before.channel.id if before.channel else None
        after_channel_id = after.channel.id if after.channel else None
        logger.debug(
            "Voice state updated",
            extra={
                "guild_id": guild_id,
                "user_id": member.id,
                "before_voice_channel_id": before_channel_id,
                "after_voice_channel_id": after_channel_id,
            },
        )

        if before.channel is not None and after.channel is None and member.id == self.bot.user.id:
            logger.info(
                "Bot voice disconnect detected",
                extra={"guild_id": guild_id, "voice_channel_id": before_channel_id},
            )
            return await self.clear_guild_queue(guild_id, reason="bot_disconnected")

        if before.channel is not None:
            if (
                after.channel is not None and
                member.id == self.bot.user.id and
                sum(1 for member in after.channel.members if not member.bot) == 0
            ):
                logger.info(
                    "Empty voice channel detected after bot move",
                    extra={"guild_id": guild_id, "voice_channel_id": after_channel_id},
                )
                return await self.clear_guild_queue(guild_id, reason="voice_channel_empty")

            if (
                member.id == self.bot.user.id and
                after.channel is not None
            ):
                logger.info(
                    "Bot voice channel move detected",
                    extra={
                        "guild_id": guild_id,
                        "before_voice_channel_id": before_channel_id,
                        "after_voice_channel_id": after_channel_id,
                    },
                )
                guild_queue = self.queue.get(guild_id)
                if guild_queue:
                    guild_queue["voice_channel_id"] = after.channel.id

            join_member_list = before.channel.members
            if (
                self.bot.user.id in list(map(lambda x: x.id, join_member_list)) and
                sum(1 for member in join_member_list if not member.bot) == 0
            ):
                logger.info(
                    "Empty voice channel detected",
                    extra={"guild_id": guild_id, "voice_channel_id": before_channel_id},
                )
                return await self.clear_guild_queue(guild_id, reason="voice_channel_empty")

    async def play_tts(self, guild_id: int):
        async with self._get_play_lock(guild_id):
            while True:
                voice_model = self.queue.get(guild_id)
                if voice_model is None:
                    logger.debug("TTS playback skipped: guild state missing", extra={"guild_id": guild_id})
                    return

                vc = voice_model.get("vc")
                if vc is None:
                    logger.warning("TTS playback skipped: voice client missing", extra={"guild_id": guild_id})
                    return
                if not vc.is_connected():
                    logger.warning(
                        "TTS playback skipped: voice client disconnected",
                        extra={"guild_id": guild_id},
                    )
                    return
                if not voice_model["tts_queue"]:
                    logger.debug("TTS playback skipped: queue empty", extra={"guild_id": guild_id})
                    return
                if vc.is_playing():
                    logger.debug(
                        "TTS playback skipped: already playing",
                        extra={"guild_id": guild_id, "queue_size": len(voice_model["tts_queue"])},
                    )
                    return

                queue_item = voice_model["tts_queue"][0]
                try:
                    source, used_engine = await self._create_tts_source(
                        guild_id=guild_id,
                        queue_item=queue_item,
                    )
                    if source is None:
                        raise RuntimeError("TTS source is missing")
                except Exception:
                    logger.exception(
                        "TTS source creation failed; skipping queue item",
                        extra={
                            "guild_id": guild_id,
                            "user_id": queue_item["user_id"],
                            "queue_size": len(voice_model["tts_queue"]),
                        },
                    )
                    voice_model["tts_queue"].pop(0)
                    continue

                current_voice_model = self.queue.get(guild_id)
                if (
                    current_voice_model is not voice_model
                    or current_voice_model.get("vc") is not vc
                    or not vc.is_connected()
                    or vc.is_playing()
                ):
                    try:
                        source.cleanup()
                    except Exception:
                        logger.exception(
                            "TTS source cleanup failed after voice state changed",
                            extra={"guild_id": guild_id},
                        )
                    logger.warning(
                        "TTS playback cancelled because voice state changed",
                        extra={"guild_id": guild_id, "tts_engine": used_engine},
                    )
                    return

                try:
                    vc.play(source, after=self._make_after_callback(guild_id))
                except Exception:
                    try:
                        source.cleanup()
                    except Exception:
                        logger.exception(
                            "TTS source cleanup failed after playback start error",
                            extra={"guild_id": guild_id},
                        )
                    voice_model["tts_queue"].pop(0)
                    logger.exception(
                        "TTS playback start failed; skipping queue item",
                        extra={
                            "guild_id": guild_id,
                            "user_id": queue_item["user_id"],
                            "tts_engine": used_engine,
                            "queue_size": len(voice_model["tts_queue"]),
                        },
                    )
                    continue

                voice_model["tts_queue"].pop(0)
                logger.info(
                    "TTS playback started",
                    extra={
                        "guild_id": guild_id,
                        "voice_channel_id": vc.channel.id if vc.channel else None,
                        "user_id": queue_item["user_id"],
                        "tts_engine": used_engine,
                        "queue_size": len(voice_model["tts_queue"]),
                    },
                )
                return

    async def safe_play_tts(self, guild_id: int):
        try:
            await self.play_tts(guild_id)
        except Exception:
            logger.exception("Unexpected TTS playback failure", extra={"guild_id": guild_id})
            
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
            logger.debug(
                "DM TTS message received",
                extra={"channel_id": message.channel.id, "user_id": message.author.id},
            )
            guild_id = self.dmChannel.get(message.channel.id, None)
            if guild_id is None:
                self.dmChannel.pop(message.channel.id, None)
                return
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                logger.warning(
                    "DM TTS guild missing",
                    extra={
                        "guild_id": guild_id,
                        "channel_id": message.channel.id,
                        "user_id": message.author.id,
                    },
                )
                return

            guild_queue = self._ensure_guild_state(guild)
            if guild_queue is None:
                logger.warning(
                    "DM TTS voice state unavailable",
                    extra={
                        "guild_id": guild_id,
                        "channel_id": message.channel.id,
                        "user_id": message.author.id,
                    },
                )
                return

            member = guild.get_member(message.author.id)
            if not member:
                try:
                    member = await guild.fetch_member(message.author.id)
                except Exception:
                    logger.exception(
                        "DM TTS member lookup failed",
                        extra={"guild_id": guild_id, "user_id": message.author.id},
                    )
                    return

            if member.voice is None:
                self.dmChannel.pop(message.channel.id, None)
                return

            if member.voice.channel.id != guild_queue["voice_channel_id"]:
                return

            tts_text = self._build_tts_text(message)
            queued = await self._enqueue_tts(
                guild_id=guild_id,
                voice_model=guild_queue,
                text=tts_text,
                user_id=message.author.id,
                channel_id=message.channel.id,
            )
            if not queued:
                return

            if not guild_queue["vc"].is_playing():
                await self.safe_play_tts(guild_id)
            return
        
        if message.author.voice is None:
            return

        if self.queue.get(message.guild.id, None) is None:
            guild_queue = self._ensure_guild_state(message.guild)
            if guild_queue is None:
                await self.clear_guild_queue(
                    message.guild.id,
                    reason="invalid_voice_state",
                )

                try:
                    if not message.guild.voice_client:
                        vc = await message.author.voice.channel.connect()
                        logger.info(
                            "Voice channel connection succeeded",
                            extra={
                                "guild_id": message.guild.id,
                                "voice_channel_id": message.author.voice.channel.id,
                                "user_id": message.author.id,
                            },
                        )
                    else:
                        vc = message.guild.voice_client
                        logger.info(
                            "Reusing existing voice client",
                            extra={
                                "guild_id": message.guild.id,
                                "voice_channel_id": vc.channel.id if vc.channel else None,
                                "user_id": message.author.id,
                            },
                        )
                except Exception:
                    logger.exception(
                        "Voice channel connection failed",
                        extra={
                            "guild_id": message.guild.id,
                            "voice_channel_id": message.author.voice.channel.id,
                            "user_id": message.author.id,
                        },
                    )
                    return

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
        queued = await self._enqueue_tts(
            guild_id=message.guild.id,
            voice_model=guild_queue,
            text=tts_text,
            user_id=message.author.id,
            channel_id=message.channel.id,
        )
        if not queued:
            return

        if not guild_queue["vc"].is_playing():
            await self.safe_play_tts(message.guild.id)

async def setup(bot: commands.Bot):
    await bot.add_cog(TTS(bot))
