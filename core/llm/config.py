import logging
import os
from dataclasses import dataclass, field
from typing import Mapping

logger = logging.getLogger(__name__)


def _first_env(environ: Mapping[str, str], *names: str, default: str = "") -> str:
    for name in names:
        value = environ.get(name)
        if value is not None and value != "":
            return value
    return default


def _bool(value: str, default: bool = False) -> bool:
    if value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: str, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_guild_channel_map(raw: str) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for entry in raw.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        if ":" not in entry:
            logger.warning("Invalid LLM_GUILD_CHANNEL_MAP entry ignored: %s", entry)
            continue
        guild_id, channel_ids = entry.split(":", 1)
        guild_id = guild_id.strip()
        channels = {channel.strip() for channel in channel_ids.split(",") if channel.strip()}
        if not guild_id or not channels:
            logger.warning("Invalid LLM_GUILD_CHANNEL_MAP entry ignored: %s", entry)
            continue
        result[guild_id] = channels
    return result


@dataclass(frozen=True, slots=True)
class LLMProviderConfig:
    api_key: str = field(default="", repr=False)
    base_url: str = "https://api.openai.com/v1"
    model: str = ""
    timeout_seconds: float = 180.0
    temperature: float = 0.7
    max_tokens: int = 1024

    def redacted(self) -> dict[str, object]:
        return {
            "api_key": "***" if self.api_key else "",
            "base_url": self.base_url,
            "model": self.model,
            "timeout_seconds": self.timeout_seconds,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }


@dataclass(frozen=True, slots=True)
class LLMPayloadLoggingConfig:
    log_payloads: bool = False
    max_chars: int = 2000


@dataclass(frozen=True, slots=True)
class LLMSettings:
    guild_channel_map: dict[str, set[str]] = field(default_factory=dict)
    consent_version: str = "2026-06-llm-memory-v1"
    main: LLMProviderConfig = field(default_factory=LLMProviderConfig)
    payload_logging: LLMPayloadLoggingConfig = field(default_factory=LLMPayloadLoggingConfig)
    debounce_seconds: float = 2.0
    response_cooldown_seconds: float = 3.0
    max_recent_logs: int = 80
    max_recent_conversation_lines: int = 12
    max_global_context_chars: int = 3000
    max_participant_context_chars: int = 3000
    max_recent_context_chars: int = 5000
    max_tool_context_chars: int = 3000
    max_current_buffer_chars: int = 3000
    typing_refresh_seconds: float = 8.0

    def is_allowed(self, guild_id: object, channel_id: object) -> bool:
        channels = self.guild_channel_map.get(str(guild_id))
        return channels is not None and str(channel_id) in channels


def load_llm_settings(environ: Mapping[str, str] | None = None) -> LLMSettings:
    env = environ or os.environ
    main = LLMProviderConfig(
        api_key=_first_env(env, "LLM_API_KEY", "OPENAI_API_KEY", "API_KEY"),
        base_url=_first_env(env, "LLM_BASE_URL", "OPENAI_BASE_URL", "BASE_URL", default="https://api.openai.com/v1"),
        model=_first_env(env, "LLM_MODEL", "OPENAI_MODEL", "MODEL"),
        timeout_seconds=_float(_first_env(env, "LLM_TIMEOUT_SECONDS", "OPENAI_TIMEOUT_SECONDS", default="180"), 180.0),
        temperature=_float(_first_env(env, "LLM_TEMPERATURE", "OPENAI_TEMPERATURE", default="0.7"), 0.7),
        max_tokens=_int(_first_env(env, "LLM_MAX_TOKENS", "OPENAI_MAX_TOKENS", default="1024"), 1024),
    )
    return LLMSettings(
        guild_channel_map=parse_guild_channel_map(env.get("LLM_GUILD_CHANNEL_MAP", "")),
        consent_version=env.get("LLM_CONSENT_VERSION", "2026-06-llm-memory-v1"),
        main=main,
        payload_logging=LLMPayloadLoggingConfig(
            log_payloads=_bool(_first_env(env, "LLM_LOG_PAYLOADS", "OPENAI_LOG_PAYLOADS", default="false")),
            max_chars=_int(_first_env(env, "LLM_LOG_PAYLOAD_MAX_CHARS", "OPENAI_LOG_PAYLOAD_MAX_CHARS", default="2000"), 2000),
        ),
        debounce_seconds=_float(env.get("LLM_DEBOUNCE_SECONDS", "2"), 2.0),
        response_cooldown_seconds=_float(env.get("LLM_RESPONSE_COOLDOWN_SECONDS", "3"), 3.0),
        max_recent_logs=_int(env.get("LLM_MAX_RECENT_LOGS", "80"), 80),
        max_recent_conversation_lines=_int(env.get("LLM_MAX_RECENT_CONVERSATION_LINES", "12"), 12),
        max_global_context_chars=_int(env.get("LLM_MAX_GLOBAL_CONTEXT_CHARS", "3000"), 3000),
        max_participant_context_chars=_int(env.get("LLM_MAX_PARTICIPANT_CONTEXT_CHARS", "3000"), 3000),
        max_recent_context_chars=_int(env.get("LLM_MAX_RECENT_CONTEXT_CHARS", "5000"), 5000),
        max_tool_context_chars=_int(env.get("LLM_MAX_TOOL_CONTEXT_CHARS", "3000"), 3000),
        max_current_buffer_chars=_int(env.get("LLM_MAX_CURRENT_BUFFER_CHARS", "3000"), 3000),
        typing_refresh_seconds=_float(env.get("LLM_TYPING_REFRESH_SECONDS", "8"), 8.0),
    )
