import json

from core.llm.config import LLMPayloadLoggingConfig
from core.llm import llm_client as client_module
from core.llm.llm_client import OpenAICompatibleClient


def test_ollama_and_openai_candidate_urls_match_llm_test_behavior():
    client = OpenAICompatibleClient(LLMPayloadLoggingConfig())

    assert client._candidate_paths("https://api.openai.com/v1") == [
        "https://api.openai.com/v1/chat/completions"
    ]
    assert client._candidate_paths("https://ollama.example") == [
        "https://ollama.example/v1/chat/completions",
        "https://ollama.example/api/chat",
    ]
    assert client._candidate_paths("https://ollama.com") == [
        "https://ollama.com/v1/chat/completions",
    ]


def test_llm_request_jsonl_writes_payload_without_authorization(tmp_path, monkeypatch):
    path = tmp_path / "llm_requests.jsonl"
    monkeypatch.setattr(client_module, "REQUEST_JSONL_PATH", path)
    client = OpenAICompatibleClient(LLMPayloadLoggingConfig(), purpose="test")

    client._write_jsonl_request(
        "https://example.test/v1/chat/completions",
        {
            "model": "m",
            "messages": [{"role": "user", "content": "hello"}],
            "tools": [{"type": "function", "function": {"name": "save_memory"}}],
            "tool_choice": "auto",
        },
        "auto",
    )

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["purpose"] == "test"
    assert record["payload"]["messages"][0]["content"] == "hello"
    assert record["tools"][0]["function"]["name"] == "save_memory"
    assert "Authorization" not in json.dumps(record)


def test_ollama_chat_message_converts_multimodal_content_to_images():
    message = OpenAICompatibleClient._to_ollama_chat_message(
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "이미지 봐줘"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc123"}},
            ],
        }
    )

    assert message["role"] == "user"
    assert message["content"] == "이미지 봐줘"
    assert message["images"] == ["abc123"]


def test_llm_request_jsonl_redacts_openai_and_ollama_images(tmp_path, monkeypatch):
    path = tmp_path / "llm_requests.jsonl"
    monkeypatch.setattr(client_module, "REQUEST_JSONL_PATH", path)
    client = OpenAICompatibleClient(LLMPayloadLoggingConfig(), purpose="test")

    client._write_jsonl_request(
        "https://example.test/v1/chat/completions",
        {
            "model": "m",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "hello"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,secret-image"}},
                    ],
                },
                {"role": "user", "content": "native", "images": ["native-secret"]},
            ],
        },
        "auto",
    )

    raw = path.read_text(encoding="utf-8")
    record = json.loads(raw)
    assert "secret-image" not in raw
    assert "native-secret" not in raw
    assert record["payload"]["messages"][0]["content"][1]["image_url"]["url"].startswith("<redacted image:")
    assert record["payload"]["messages"][1]["images"][0].startswith("<redacted image:")
