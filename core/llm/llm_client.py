import asyncio
import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.llm.config import LLMProviderConfig, LLMPayloadLoggingConfig
from core.llm.models import ToolCall

logger = logging.getLogger(__name__)
REQUEST_JSONL_PATH = Path("logs/llm_requests.jsonl")


@dataclass(slots=True)
class LLMClientResponse:
    content: str
    provider_path: str
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMClientError(RuntimeError):
    pass


class OpenAICompatibleClient:
    def __init__(self, payload_logging: LLMPayloadLoggingConfig, *, purpose: str = "chat"):
        self.payload_logging = payload_logging
        self.purpose = purpose

    async def chat(
        self,
        config: LLMProviderConfig,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
    ) -> LLMClientResponse:
        if not config.api_key:
            raise LLMClientError("LLM API key is not configured")
        if not config.model:
            raise LLMClientError("LLM model is not configured")
        paths = self._candidate_paths(config.base_url)
        last_error: Exception | None = None
        for path in paths:
            try:
                return await asyncio.to_thread(self._post_chat, config, path, messages, tools, tool_choice)
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
        if base in {"https://ollama.com", "http://ollama.com"}:
            return [base + "/v1/chat/completions"]
        return [base + "/v1/chat/completions", base + "/api/chat"]

    def _post_chat(
        self,
        config: LLMProviderConfig,
        url: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        tool_choice: str,
    ) -> LLMClientResponse:
        payload: dict[str, Any]
        if url.endswith("/api/chat"):
            payload = {
                "model": config.model,
                "messages": [self._to_ollama_chat_message(message) for message in messages],
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
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        encoded = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        start = time.perf_counter()
        request = urllib.request.Request(url, data=encoded, headers=headers, method="POST")
        self._write_jsonl_request(url, payload, tool_choice)
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
            message = data["choices"][0]["message"]
            return LLMClientResponse(
                content=message.get("content") or "",
                provider_path=url,
                tool_calls=self._parse_tool_calls(message.get("tool_calls")),
            )
        if "message" in data:
            message = data["message"]
            return LLMClientResponse(
                content=message.get("content") or "",
                provider_path=url,
                tool_calls=self._parse_tool_calls(message.get("tool_calls")),
            )
        if isinstance(data.get("response"), str):
            return LLMClientResponse(content=data["response"], provider_path=url)
        raise LLMClientError("Unsupported LLM response shape")

    @staticmethod
    def _parse_tool_calls(raw: object) -> list[ToolCall]:
        if not isinstance(raw, list):
            return []
        calls: list[ToolCall] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            function = item.get("function") if isinstance(item.get("function"), dict) else item
            if not isinstance(function, dict):
                continue
            name = str(function.get("name", "")).strip()
            if not name:
                continue
            arguments = function.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments) if arguments.strip() else {}
                except json.JSONDecodeError:
                    arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            calls.append(ToolCall(name=name, arguments=arguments))
        return calls

    def _log_request(self, config: LLMProviderConfig, messages: list[dict[str, Any]]) -> None:
        approx = sum(self._content_length(message.get("content", "")) for message in messages)
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

    def _write_jsonl_request(self, url: str, payload: dict[str, Any], tool_choice: str) -> None:
        redacted_payload = self._redact_images(payload)
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "purpose": self.purpose,
            "url": url,
            "model": payload.get("model"),
            "message_count": len(payload.get("messages", [])),
            "tool_choice": tool_choice if payload.get("tools") else None,
            "tools": payload.get("tools", []),
            "payload": redacted_payload,
        }
        try:
            REQUEST_JSONL_PATH.parent.mkdir(parents=True, exist_ok=True)
            with REQUEST_JSONL_PATH.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            logger.exception("Failed to write LLM request JSONL", extra={"purpose": self.purpose})

    @classmethod
    def _to_ollama_chat_message(cls, message: dict[str, Any]) -> dict[str, Any]:
        content = message.get("content", "")
        if not isinstance(content, list):
            return dict(message)

        converted = dict(message)
        text_parts: list[str] = []
        images: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                text_parts.append(str(part.get("text", "")))
            elif part.get("type") == "image_url":
                image_base64 = cls._extract_image_base64(part.get("image_url"))
                if image_base64:
                    images.append(image_base64)
        converted["content"] = "\n".join(text for text in text_parts if text)
        if images:
            converted["images"] = images
        return converted

    @staticmethod
    def _extract_image_base64(image_url: object) -> str:
        if isinstance(image_url, dict):
            url = str(image_url.get("url", ""))
        else:
            url = str(image_url or "")
        if url.startswith("data:") and "," in url:
            return url.split(",", 1)[1]
        return url

    @classmethod
    def _redact_images(cls, value: Any) -> Any:
        if isinstance(value, dict):
            if value.get("type") == "image_url":
                return {"type": "image_url", "image_url": cls._redacted_image_url(value.get("image_url"))}
            return {
                key: [f"<redacted image: base64_chars={len(str(image))}>" for image in item]
                if key == "images" and isinstance(item, list)
                else cls._redact_images(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [cls._redact_images(item) for item in value]
        return value

    @classmethod
    def _redacted_image_url(cls, image_url: object) -> object:
        if isinstance(image_url, dict):
            url = str(image_url.get("url", ""))
            return {"url": cls._redacted_image_value(url)}
        return cls._redacted_image_value(str(image_url or ""))

    @staticmethod
    def _redacted_image_value(value: str) -> str:
        if value.startswith("data:image/"):
            media_type = value.split(";", 1)[0].removeprefix("data:")
            data = value.split(",", 1)[1] if "," in value else ""
            return f"<redacted image: media_type={media_type}, base64_chars={len(data)}>"
        return "<redacted image>"

    @staticmethod
    def _content_length(content: object) -> int:
        if isinstance(content, str):
            return len(content)
        if isinstance(content, list):
            total = 0
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    total += len(str(part.get("text", "")))
                elif isinstance(part, dict) and part.get("type") == "image_url":
                    total += len("[image]")
            return total
        return len(str(content))
