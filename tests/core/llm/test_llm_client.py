from core.llm.config import LLMPayloadLoggingConfig
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

