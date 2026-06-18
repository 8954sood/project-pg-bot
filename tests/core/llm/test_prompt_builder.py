from datetime import datetime, timezone

from core.llm.config import LLMSettings
from core.llm.models import BufferedConversation, MemoryState, Message, RecentLogEntry
from core.llm.prompt_builder import LLMPromptBuilder


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
