import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Optional, Tuple

import discord

from core.local import LocalCore
from core.local.ttsengine.dto import TTSEngineOption
from core.tts.engine_selector import TTSEngineSelector
from core.tts.engines.ai_stream_engine import AIStreamEngine
from core.tts.engines.gtts_engine import GTTSEngine
from core.tts.models import TTSQueueModel, VoiceModel
from core.tts.playback import TTSPlayback
from core.tts.queue import enqueue_tts
from core.tts.text_normalizer import build_tts_text

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class JoinResult:
    ok: bool
    message: str
    ephemeral: bool = False


async def create_tts_source_with_dependencies(
    *,
    guild_id: int,
    queue_item: TTSQueueModel,
    engine_selector: TTSEngineSelector,
    ai_engine,
    gtts_engine,
    gtts_timeout: float,
) -> Tuple[discord.AudioSource, str]:
    user_id = queue_item["user_id"]
    text = queue_item["text"]
    selection = engine_selector.get_user_engine(user_id)
    logger.debug(
        "TTS source creation starting",
        extra={
            "guild_id": guild_id,
            "user_id": user_id,
            "tts_engine": selection.engine,
            "ai_model": selection.model_name,
            "text_length": len(text),
        },
    )

    if selection.engine == "ai":
        if ai_engine and selection.model_name:
            try:
                source = await ai_engine.create_discord_source(
                    text=text,
                    model_name=selection.model_name,
                )
                if source is None:
                    raise RuntimeError("AI TTS returned no audio source")
                logger.info(
                    "TTS source creation succeeded",
                    extra={
                        "guild_id": guild_id,
                        "user_id": user_id,
                        "tts_engine": "ai",
                        "ai_model": selection.model_name,
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
                        "ai_model": selection.model_name,
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
                    "ai_model": selection.model_name,
                    "fallback": True,
                },
            )

    try:
        tts_fp = await asyncio.wait_for(
            gtts_engine.synth(
                text=text,
                user_id=user_id,
                timeout=gtts_timeout,
            ),
            timeout=gtts_timeout,
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
                "ai_model": selection.model_name,
                "fallback": selection.engine == "ai",
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
                "fallback": selection.engine == "ai",
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
                "fallback": selection.engine == "ai",
            },
        )
        raise RuntimeError("All TTS engines failed") from exc


class TTSService:
    def __init__(self, bot) -> None:
        self.bot = bot
        self.queue: dict[int, VoiceModel] = {}
        self.play_locks: dict[int, asyncio.Lock] = {}
        self.message_channels: dict[int, int] = {}
        self.default_channels: dict[int, int] = {}
        self.dm_channels: dict[int, int] = {}
        self.voice_option: dict[int, str] = {}
        self.tts_engine_option: dict[int, TTSEngineOption] = {}
        self.tts_engine_allow: set[int] = set()

        self.gtts_engine = GTTSEngine(lambda user_id: self.voice_option.get(user_id, "ko"))
        self.ai_ws_url = os.environ.get("AI_TTS_WS_URL", "")
        self.ai_engine = AIStreamEngine(ai_ws_url=self.ai_ws_url) if self.ai_ws_url else None
        self.max_queue_size = int(os.getenv("TTS_MAX_QUEUE_SIZE", "50"))
        self.max_text_length = int(os.getenv("TTS_MAX_TEXT_LENGTH", "300"))
        self.gtts_timeout = float(os.getenv("TTS_GTTS_TIMEOUT_SECONDS", "10"))
        self.playback = TTSPlayback(
            queue=self.queue,
            play_locks=self.play_locks,
            bot_loop=self.bot.loop,
            create_source=self.create_tts_source,
        )

    @property
    def engine_selector(self) -> TTSEngineSelector:
        return TTSEngineSelector(self.tts_engine_option, self.tts_engine_allow)

    async def load_initial_state(self) -> None:
        await self.load_local_default_channel()
        await self.load_local_voice_option()
        await self.load_local_tts_engine_option()
        await self.load_local_tts_engine_allow()

    async def load_local_default_channel(self) -> None:
        local_default_channels = await LocalCore.ttsDataSource.get_all()
        for item in local_default_channels:
            self.default_channels[item.guild_id] = item.channel_id
        logger.info("Loaded local TTS default channels", extra={"channel_count": len(self.default_channels)})

    async def load_local_voice_option(self) -> None:
        local_voice_options = await LocalCore.voiceOptionDataSource.get_all()
        for item in local_voice_options:
            self.voice_option[item.user_id] = item.lang
        logger.info("Loaded local TTS voice options", extra={"user_count": len(self.voice_option)})

    async def load_local_tts_engine_option(self) -> None:
        local_engine_options = await LocalCore.ttsEngineOptionDataSource.get_all()
        for item in local_engine_options:
            self.tts_engine_option[item.user_id] = item

    async def load_local_tts_engine_allow(self) -> None:
        local_allow = await LocalCore.ttsEngineAllowDataSource.get_all()
        self.tts_engine_allow = set([item.user_id for item in local_allow])

    def ensure_guild_state(self, guild: discord.Guild) -> Optional[VoiceModel]:
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

    async def enqueue(
        self,
        *,
        guild_id: int,
        voice_model: VoiceModel,
        text: str,
        user_id: int,
        channel_id: int,
    ) -> bool:
        return enqueue_tts(
            guild_id=guild_id,
            voice_model=voice_model,
            text=text,
            user_id=user_id,
            channel_id=channel_id,
            max_queue_size=self.max_queue_size,
            max_text_length=self.max_text_length,
        )

    async def create_tts_source(
        self,
        *,
        guild_id: int,
        queue_item: TTSQueueModel,
    ) -> Tuple[discord.AudioSource, str]:
        return await create_tts_source_with_dependencies(
            guild_id=guild_id,
            queue_item=queue_item,
            engine_selector=self.engine_selector,
            ai_engine=self.ai_engine,
            gtts_engine=self.gtts_engine,
            gtts_timeout=self.gtts_timeout,
        )

    def build_tts_text(self, content: str, has_image: bool) -> str:
        return build_tts_text(content, has_image)

    async def clear_guild_queue(self, guild_id: int, *, reason: str = "unknown") -> None:
        await self.playback.clear_guild_queue(
            guild_id,
            reason=reason,
            message_channels=self.message_channels,
            dm_channels=self.dm_channels,
        )

    async def set_default_channel(self, guild_id: int, channel_id: int) -> None:
        self.default_channels[guild_id] = channel_id
        local_tts_info = await LocalCore.ttsDataSource.get(guild_id)
        if local_tts_info is None:
            await LocalCore.ttsDataSource.insert(guild_id, channel_id)
        else:
            await LocalCore.ttsDataSource.update(guild_id, channel_id)

    async def set_voice_option(self, user_id: int, lang: str) -> None:
        user_voice_option = await LocalCore.voiceOptionDataSource.get_voice_option(user_id)
        if user_voice_option is None:
            await LocalCore.voiceOptionDataSource.insert(user_id, lang)
        else:
            await LocalCore.voiceOptionDataSource.update(user_id, lang)
        self.voice_option[user_id] = lang

    async def set_tts_engine_option(
        self,
        *,
        user_id: int,
        engine: str,
        model_name: str | None,
    ) -> None:
        model_to_save = model_name if engine == "ai" else None
        await LocalCore.ttsEngineOptionDataSource.upsert(user_id, engine, model_to_save)
        self.tts_engine_option[user_id] = TTSEngineOption(
            user_id=user_id,
            engine=engine,
            model_name=model_to_save,
        )

    async def add_tts_engine_allow(self, user_id: int) -> None:
        await LocalCore.ttsEngineAllowDataSource.add(user_id)
        self.tts_engine_allow.add(user_id)

    async def remove_tts_engine_allow(self, user_id: int) -> None:
        await LocalCore.ttsEngineAllowDataSource.remove(user_id)
        self.tts_engine_allow.discard(user_id)

    async def join(
        self,
        *,
        guild: discord.Guild,
        user: discord.Member,
        text_channel_id: int,
    ) -> JoinResult:
        guild_id = guild.id
        user_id = user.id
        requested_channel_id = user.voice.channel.id if user.voice else None
        logger.info(
            "TTS join requested",
            extra={
                "guild_id": guild_id,
                "channel_id": text_channel_id,
                "user_id": user_id,
                "voice_channel_id": requested_channel_id,
            },
        )
        if user.voice is None:
            return JoinResult(False, "채널에 입장 후 사용해주세요.")

        previous_vc = guild.voice_client
        previous_channel_id = (
            previous_vc.channel.id
            if previous_vc is not None and previous_vc.channel is not None
            else None
        )
        if previous_channel_id != user.voice.channel.id:
            await self.clear_guild_queue(
                guild_id,
                reason="manual_join_other_channel",
            )
            if previous_vc is not None:
                await self.playback.wait_for_voice_client_release(guild, previous_vc)

        try:
            current_vc = guild.voice_client
            if (
                current_vc is None
                or not current_vc.is_connected()
                or current_vc.channel is None
                or current_vc.channel.id != user.voice.channel.id
            ):
                vc = await user.voice.channel.connect()
                logger.info(
                    "Voice channel connection succeeded",
                    extra={
                        "guild_id": guild_id,
                        "voice_channel_id": user.voice.channel.id,
                        "user_id": user_id,
                    },
                )
            else:
                vc = guild.voice_client
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
                    "voice_channel_id": user.voice.channel.id,
                    "user_id": user_id,
                },
            )
            return JoinResult(False, "음성 채널 연결에 실패했습니다.", ephemeral=True)

        existing = self.queue.get(guild_id)
        if existing and existing.get("vc") is vc:
            existing["voice_channel_id"] = user.voice.channel.id
        else:
            self.queue[guild_id] = {
                "guild_id": guild_id,
                "voice_channel_id": user.voice.channel.id,
                "tts_queue": [],
                "vc": vc,
            }
        self.message_channels[guild_id] = text_channel_id
        return JoinResult(True, "해당 채널에서도 TTS를 수신할게요!")

    async def configure_dm_channel(self, *, dm_channel_id: int, guild_id: int) -> None:
        self.dm_channels[dm_channel_id] = guild_id

    async def handle_voice_state_update(
        self,
        *,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
        bot_user_id: int,
    ) -> None:
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

        if before.channel is not None and after.channel is None and member.id == bot_user_id:
            logger.info(
                "Bot voice disconnect detected",
                extra={"guild_id": guild_id, "voice_channel_id": before_channel_id},
            )
            await self.clear_guild_queue(guild_id, reason="bot_disconnected")
            return

        if before.channel is None:
            return

        if (
            after.channel is not None and
            member.id == bot_user_id and
            sum(1 for channel_member in after.channel.members if not channel_member.bot) == 0
        ):
            logger.info(
                "Empty voice channel detected after bot move",
                extra={"guild_id": guild_id, "voice_channel_id": after_channel_id},
            )
            await self.clear_guild_queue(guild_id, reason="voice_channel_empty")
            return

        if member.id == bot_user_id and after.channel is not None:
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
            bot_user_id in list(map(lambda x: x.id, join_member_list)) and
            sum(1 for channel_member in join_member_list if not channel_member.bot) == 0
        ):
            logger.info(
                "Empty voice channel detected",
                extra={"guild_id": guild_id, "voice_channel_id": before_channel_id},
            )
            await self.clear_guild_queue(guild_id, reason="voice_channel_empty")

    def should_receive_channel(self, channel_id: int) -> bool:
        return (
            channel_id in self.message_channels.values()
            or channel_id in self.default_channels.values()
            or channel_id in self.dm_channels.keys()
        )

    async def handle_message(self, message: discord.Message) -> None:
        if message.author.bot or not self.should_receive_channel(message.channel.id):
            return

        if isinstance(message.channel, discord.channel.DMChannel):
            await self._handle_dm_message(message)
            return

        if message.author.voice is None:
            return

        if self.queue.get(message.guild.id, None) is None:
            guild_queue = self.ensure_guild_state(message.guild)
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

        tts_text = self.build_tts_text(
            message.clean_content or "",
            self._has_image_attachment(message),
        )
        queued = await self.enqueue(
            guild_id=message.guild.id,
            voice_model=guild_queue,
            text=tts_text,
            user_id=message.author.id,
            channel_id=message.channel.id,
        )
        if queued and not guild_queue["vc"].is_playing():
            await self.playback.safe_play_tts(message.guild.id)

    async def _handle_dm_message(self, message: discord.Message) -> None:
        logger.debug(
            "DM TTS message received",
            extra={"channel_id": message.channel.id, "user_id": message.author.id},
        )
        guild_id = self.dm_channels.get(message.channel.id, None)
        if guild_id is None:
            self.dm_channels.pop(message.channel.id, None)
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

        guild_queue = self.ensure_guild_state(guild)
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
            self.dm_channels.pop(message.channel.id, None)
            return

        if member.voice.channel.id != guild_queue["voice_channel_id"]:
            return

        tts_text = self.build_tts_text(
            message.clean_content or "",
            self._has_image_attachment(message),
        )
        queued = await self.enqueue(
            guild_id=guild_id,
            voice_model=guild_queue,
            text=tts_text,
            user_id=message.author.id,
            channel_id=message.channel.id,
        )
        if queued and not guild_queue["vc"].is_playing():
            await self.playback.safe_play_tts(guild_id)

    def _has_image_attachment(self, message: discord.Message) -> bool:
        for attachment in message.attachments:
            content_type = attachment.content_type or ""
            if content_type.startswith("image/"):
                return True
            filename = (attachment.filename or "").lower()
            if filename.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")):
                return True
        return False
