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
