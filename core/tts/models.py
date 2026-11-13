from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict


TTSLanguage = Literal["ko", "en", "ja"]


@dataclass(frozen=True, slots=True)
class LanguageProfile:
    """Letter counts by script, not statistical language identification."""

    ko: int = 0
    en: int = 0
    ja: int = 0
    other: int = 0

    @property
    def total(self) -> int:
        return self.ko + self.en + self.ja + self.other

    def ratio(self, language: TTSLanguage) -> float:
        if language not in ("ko", "en", "ja"):
            raise ValueError(f"Unsupported TTS language: {language!r}")
        return getattr(self, language) / self.total if self.total else 0.0


@dataclass(frozen=True, slots=True)
class PronunciationResult:
    text: str
    target_language: TTSLanguage
    profile: LanguageProfile
    changed: bool
    unresolved: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        """Whether every remaining letter uses the target script."""
        return not self.unresolved


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


__all__ = [
    "GuildTTSState", "TTSQueueItem", "TTSQueueModel", "VoiceModel",
    "TTSLanguage", "LanguageProfile", "PronunciationResult",
]
