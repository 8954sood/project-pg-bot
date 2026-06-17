from collections import deque
from dataclasses import dataclass, field
from typing import Any, TypedDict


@dataclass(slots=True)
class TTSQueueItem:
    text: str
    user_id: int
    channel_id: int | None = None

    def as_queue_model(self) -> "TTSQueueModel":
        return {"text": self.text, "user_id": self.user_id}


@dataclass
class GuildTTSState:
    guild_id: int
    voice_channel_id: int
    vc: Any
    queue: deque[TTSQueueItem] = field(default_factory=deque)


class TTSQueueModel(TypedDict):
    text: str
    user_id: int


class VoiceModel(TypedDict):
    guild_id: int
    voice_channel_id: int
    tts_queue: list[TTSQueueModel]
    vc: Any


__all__ = ["GuildTTSState", "TTSQueueItem", "TTSQueueModel", "VoiceModel"]
