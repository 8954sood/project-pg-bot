from dataclasses import dataclass
from typing import Mapping, Protocol


class TTSEngineOptionLike(Protocol):
    engine: str
    model_name: str | None


@dataclass(frozen=True, slots=True)
class TTSEngineSelection:
    engine: str
    model_name: str | None = None

    @property
    def uses_ai(self) -> bool:
        return self.engine == "ai" and bool(self.model_name)


class TTSEngineSelector:
    def __init__(
        self,
        engine_options: Mapping[int, TTSEngineOptionLike],
        engine_allow: set[int],
    ) -> None:
        self.engine_options = engine_options
        self.engine_allow = engine_allow

    def get_user_engine(self, user_id: int) -> TTSEngineSelection:
        option = self.engine_options.get(user_id)
        if option is None:
            return TTSEngineSelection("gtts", None)
        return TTSEngineSelection(option.engine, option.model_name)

    def is_engine_change_allowed(self, user_id: int) -> bool:
        return user_id in self.engine_allow

    def should_try_ai(self, selection: TTSEngineSelection, ai_engine_available: bool) -> bool:
        return selection.engine == "ai" and bool(selection.model_name) and ai_engine_available

    def should_fallback_to_gtts(self, selection: TTSEngineSelection) -> bool:
        return selection.engine == "ai"
