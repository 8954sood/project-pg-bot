from core.llm.config import LLMMemoryConfig
from core.llm.models import LLMBufferedMessage


class MemoryExtractionPolicy:
    def __init__(self, config: LLMMemoryConfig):
        self.config = config

    def should_extract(self, messages: list[LLMBufferedMessage], turns_since_last: int) -> bool:
        if not self.config.enabled:
            return False
        user_chars = sum(len(message.content) for message in messages if message.user_id)
        total_chars = sum(len(message.content) for message in messages)
        if user_chars < self.config.min_user_chars or total_chars < self.config.min_total_chars:
            return False
        if self.config.trigger_keywords_enabled:
            joined = "\n".join(message.content for message in messages)
            if any(keyword in joined for keyword in self.config.trigger_keywords):
                return True
        return turns_since_last + 1 >= self.config.every_n_turns
