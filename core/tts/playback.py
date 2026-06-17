import asyncio
import logging
from collections.abc import Callable, MutableMapping
from typing import Optional

from core.tts.models import TTSQueueModel, VoiceModel

logger = logging.getLogger(__name__)

CreateSource = Callable[..., object]


class TTSPlayback:
    def __init__(
        self,
        *,
        queue: MutableMapping[int, VoiceModel],
        play_locks: MutableMapping[int, asyncio.Lock],
        bot_loop: asyncio.AbstractEventLoop,
        create_source,
    ) -> None:
        self.queue = queue
        self.play_locks = play_locks
        self.bot_loop = bot_loop
        self.create_source = create_source

    def discard_play_lock(self, guild_id: int) -> None:
        lock = self.play_locks.get(guild_id)
        if lock is None:
            return
        if not lock.locked():
            self.play_locks.pop(guild_id, None)
            return

        async def discard_after_release(expected_lock: asyncio.Lock) -> None:
            async with expected_lock:
                pass
            if (
                self.play_locks.get(guild_id) is expected_lock
                and guild_id not in self.queue
            ):
                self.play_locks.pop(guild_id, None)

        asyncio.create_task(discard_after_release(lock))

    def get_play_lock(self, guild_id: int) -> asyncio.Lock:
        lock = self.play_locks.get(guild_id)
        if lock is None:
            lock = asyncio.Lock()
            self.play_locks[guild_id] = lock
        return lock

    async def clear_guild_queue(
        self,
        guild_id: int,
        *,
        reason: str = "unknown",
        message_channels: MutableMapping[int, int] | None = None,
        dm_channels: MutableMapping[int, int] | None = None,
    ) -> None:
        voice_model = self.queue.get(guild_id)
        vc = voice_model.get("vc") if voice_model else None
        queue_size = len(voice_model["tts_queue"]) if voice_model else 0
        logger.info(
            "Clearing guild TTS queue",
            extra={"guild_id": guild_id, "queue_size": queue_size, "reason": reason},
        )

        self.queue.pop(guild_id, None)
        if message_channels is not None:
            message_channels.pop(guild_id, None)
        if dm_channels is not None:
            for key in [key for key, value in dm_channels.items() if value == guild_id]:
                dm_channels.pop(key, None)

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

        self.discard_play_lock(guild_id)

    async def wait_for_voice_client_release(
        self,
        guild,
        previous_vc,
        *,
        timeout: float = 2.0,
    ) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while guild.voice_client is previous_vc:
            if asyncio.get_running_loop().time() >= deadline:
                logger.warning(
                    "Timed out waiting for stale voice client release",
                    extra={"guild_id": guild.id},
                )
                if not previous_vc.is_connected():
                    try:
                        previous_vc.cleanup()
                    except Exception:
                        logger.exception(
                            "Stale voice client cleanup failed",
                            extra={"guild_id": guild.id},
                        )
                return
            await asyncio.sleep(0.05)

    def make_after_callback(self, guild_id: int):
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
                    self.bot_loop,
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

    async def play_tts(self, guild_id: int) -> None:
        async with self.get_play_lock(guild_id):
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

                queue_item: TTSQueueModel = voice_model["tts_queue"][0]
                try:
                    source, used_engine = await self.create_source(
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
                    vc.play(source, after=self.make_after_callback(guild_id))
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

    async def safe_play_tts(self, guild_id: int) -> None:
        try:
            await self.play_tts(guild_id)
        except Exception:
            logger.exception("Unexpected TTS playback failure", extra={"guild_id": guild_id})
