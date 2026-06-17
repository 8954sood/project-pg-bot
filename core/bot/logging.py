import logging
import os


class ContextFormatter(logging.Formatter):
    context_fields = (
        "guild_id",
        "channel_id",
        "voice_channel_id",
        "before_voice_channel_id",
        "after_voice_channel_id",
        "user_id",
        "tts_engine",
        "ai_model",
        "queue_size",
        "fallback",
        "reason",
        "text_length",
        "text_preview",
    )

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        context = " ".join(
            f"{field}={getattr(record, field)!r}"
            for field in self.context_fields
            if hasattr(record, field)
        )
        return f"{message} {context}" if context else message


def setup_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    formatter = ContextFormatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    for handler in logging.getLogger().handlers:
        handler.setFormatter(formatter)

