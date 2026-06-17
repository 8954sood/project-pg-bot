import logging
from typing import MutableSequence

from core.tts.models import TTSQueueModel, VoiceModel

logger = logging.getLogger(__name__)


def truncate_text(text: str, max_text_length: int) -> str:
    if len(text) <= max_text_length:
        return text
    if max_text_length <= 0:
        return ""
    if max_text_length == 1:
        return "…"
    return f"{text[:max_text_length - 1]}…"


def enqueue_tts(
    *,
    guild_id: int,
    voice_model: VoiceModel,
    text: str,
    user_id: int,
    channel_id: int,
    max_queue_size: int,
    max_text_length: int,
) -> bool:
    text = text.strip()
    if not text:
        return False

    if len(text) > max_text_length:
        logger.debug(
            "TTS text truncated",
            extra={
                "guild_id": guild_id,
                "channel_id": channel_id,
                "user_id": user_id,
                "text_length": len(text),
            },
        )
        text = truncate_text(text, max_text_length)

    queue = voice_model["tts_queue"]
    queue_size = len(queue)
    if queue_size >= max_queue_size:
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

    queue.append({"text": text, "user_id": user_id})
    logger.debug(
        "TTS message queued",
        extra={
            "guild_id": guild_id,
            "channel_id": channel_id,
            "user_id": user_id,
            "queue_size": len(queue),
            "text_length": len(text),
            "text_preview": text[:30],
        },
    )
    return True


def peek(queue: MutableSequence[TTSQueueModel]) -> TTSQueueModel | None:
    if not queue:
        return None
    return queue[0]


def popleft(queue: MutableSequence[TTSQueueModel]) -> TTSQueueModel | None:
    if not queue:
        return None
    return queue.pop(0)


def clear(queue: MutableSequence[TTSQueueModel]) -> None:
    queue.clear()
