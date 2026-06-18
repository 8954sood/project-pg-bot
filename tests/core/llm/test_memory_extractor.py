from types import SimpleNamespace

import pytest

from core.llm.config import LLMProviderConfig
from core.llm.memory_extractor import LLMMemoryExtractor
from core.llm.models import LLMBufferedMessage


class FakeClient:
    def __init__(self):
        self.messages = None

    async def chat(self, config, messages):
        self.messages = messages
        return SimpleNamespace(content="{}")


@pytest.mark.asyncio
async def test_memory_extractor_prompt_defaults_to_personal_memory():
    client = FakeClient()
    extractor = LLMMemoryExtractor(client, LLMProviderConfig(api_key="k", model="m"))

    await extractor.extract(
        [
            LLMBufferedMessage(
                guild_id="1",
                channel_id="2",
                user_id="3",
                author_name="User",
                content="앞으로 나에게는 짧고 부드러운 말투로 답해줘",
                created_at="2026-06-18T00:00:00+00:00",
            )
        ]
    )

    prompt = client.messages[0]["content"]
    assert "기본값은 개인 저장" in prompt
    assert "서버/길드/모두/전체/채널 전체가 명시되지 않으면 해당 발화자 개인 말투" in prompt
    assert "active_style_directive/server_style_summary는 비워 둔다" in prompt
