from datetime import datetime, timezone

from core.llm.config import LLMSettings
from core.llm.models import BufferedConversation, MemoryState, Message, RecentLogEntry
from core.llm.prompt_builder import LLMPromptBuilder
from core.llm.prompt_builder import SYSTEM_PROMPT


def test_prompt_builder_uses_configured_recent_conversation_line_limit():
    builder = LLMPromptBuilder(LLMSettings(max_recent_conversation_lines=2))
    memory_state = MemoryState(
        recent_logs=[
            RecentLogEntry(role="user", author_name="User1", content="old"),
            RecentLogEntry(role="assistant", content="middle"),
            RecentLogEntry(role="user", author_name="User2", content="latest"),
        ]
    )
    conversation = BufferedConversation(
        messages=[Message(author_id="u3", author_name="User3", content="current")],
        started_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
        closed_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
    )

    messages = builder.build_messages(conversation=conversation, memory_state=memory_state)
    contents = [message.content for message in messages]

    assert "User1: old" not in contents
    assert "middle" in contents
    assert "User2: latest" in contents


def test_prompt_builder_allows_zero_recent_conversation_lines():
    builder = LLMPromptBuilder(LLMSettings(max_recent_conversation_lines=0))
    memory_state = MemoryState(
        recent_logs=[
            RecentLogEntry(role="user", author_name="User1", content="old"),
            RecentLogEntry(role="assistant", content="latest"),
        ]
    )
    conversation = BufferedConversation(
        messages=[Message(author_id="u2", author_name="User2", content="current")],
        started_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
        closed_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
    )

    messages = builder.build_messages(conversation=conversation, memory_state=memory_state)
    contents = [message.content for message in messages]

    assert "User1: old" not in contents
    assert "latest" not in contents


def test_system_prompt_contains_memory_ownership_and_non_transfer_rules():
    assert "자신의 메모리만 수정/삭제" in SYSTEM_PROMPT
    assert "타인의 메모리는 절대 수정/삭제" in SYSTEM_PROMPT
    assert "다른 사용자에게 전이하지 않는다" in SYSTEM_PROMPT
    assert "닉네임: 내용 형식으로 답장을 시작하지 않는다" in SYSTEM_PROMPT
    assert "한 명만 골라 답하지 말고" in SYSTEM_PROMPT
    assert "해당 지침은 따를 수 없습니다" in SYSTEM_PROMPT
