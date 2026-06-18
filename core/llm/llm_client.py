import asyncio
import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from core.llm.config import LLMProviderConfig, LLMPayloadLoggingConfig

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LLMClientResponse:
    content: str
    provider_path: str


class LLMClientError(RuntimeError):
    pass


class OpenAICompatibleClient:
    def __init__(self, payload_logging: LLMPayloadLoggingConfig, *, purpose: str = "chat"):
        self.payload_logging = payload_logging
        self.purpose = purpose

    async def chat(self, config: LLMProviderConfig, messages: list[dict[str, str]]) -> LLMClientResponse:
        if not config.api_key:
            raise LLMClientError("LLM API key is not configured")
        if not config.model:
            raise LLMClientError("LLM model is not configured")
        paths = self._candidate_paths(config.base_url)
        last_error: Exception | None = None
        for path in paths:
            try:
                return await asyncio.to_thread(self._post_chat, config, path, messages)
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "LLM request failed",
                    extra={
                        "purpose": self.purpose,
                        "config": config.redacted(),
                        "path": path,
                        "error_type": type(exc).__name__,
                    },
                )
        raise LLMClientError(f"LLM request failed: {type(last_error).__name__ if last_error else 'unknown'}")

    def _candidate_paths(self, base_url: str) -> list[str]:
        base = (base_url or "https://api.openai.com").rstrip("/")
        if base.endswith("/v1/chat/completions") or base.endswith("/api/chat"):
            return [base]
        if base.endswith("/v1"):
            return [base + "/chat/completions"]
        return [base + "/v1/chat/completions", base + "/api/chat"]

    def _post_chat(self, config: LLMProviderConfig, url: str, messages: list[dict[str, str]]) -> LLMClientResponse:
        payload: dict[str, Any]
        if url.endswith("/api/chat"):
            payload = {
                "model": config.model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": config.temperature, "num_predict": config.max_tokens},
            }
        else:
            payload = {
                "model": config.model,
                "messages": messages,
                "temperature": config.temperature,
                "max_tokens": config.max_tokens,
            }
        encoded = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        start = time.perf_counter()
        request = urllib.request.Request(url, data=encoded, headers=headers, method="POST")
        self._log_request(config, messages)
        try:
            with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise LLMClientError(f"HTTP {exc.code}") from exc
        latency_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "LLM response received",
            extra={
                "purpose": self.purpose,
                "model": config.model,
                "message_count": len(messages),
                "latency_ms": latency_ms,
            },
        )
        data = json.loads(raw)
        if "choices" in data:
            return LLMClientResponse(content=data["choices"][0]["message"]["content"], provider_path=url)
        if "message" in data:
            return LLMClientResponse(content=data["message"]["content"], provider_path=url)
        if isinstance(data.get("response"), str):
            return LLMClientResponse(content=data["response"], provider_path=url)
        raise LLMClientError("Unsupported LLM response shape")

    def _log_request(self, config: LLMProviderConfig, messages: list[dict[str, str]]) -> None:
        approx = sum(len(message.get("content", "")) for message in messages)
        extra: dict[str, object] = {
            "model": config.model,
            "purpose": self.purpose,
            "message_count": len(messages),
            "approx_input_chars": approx,
            "config": config.redacted(),
        }
        if self.payload_logging.log_payloads:
            payload = json.dumps(messages, ensure_ascii=False)
            extra["payload"] = payload[: self.payload_logging.max_chars]
        logger.info("LLM request prepared", extra=extra)
