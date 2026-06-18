import os
from dataclasses import replace

import pytest
from dotenv import load_dotenv

from core.llm.config import LLMPayloadLoggingConfig, load_llm_settings
from core.llm.engine import LLMEngine
from core.llm.llm_client import OpenAICompatibleClient
from core.llm.models import LLMInputMessage
from core.llm.service import LLMService
from core.local import LocalCore
from core.local import path as path_module


async def _noop_sleep(_):
    return None


async def _noop_complete():
    return None


def live_settings():
    load_dotenv(".env", override=False)
    if os.environ.get("RUN_LIVE_LLM_TESTS", "").strip().lower() not in {"1", "true", "yes", "on"}:
        pytest.skip("set RUN_LIVE_LLM_TESTS=1 to call the real LLM provider from .env")
    settings = load_llm_settings()
    if not settings.main.api_key or not settings.main.model:
        pytest.skip(".env must define LLM_API_KEY and LLM_MODEL for live LLM integration tests")
    main = replace(settings.main, temperature=0.0, max_tokens=min(settings.main.max_tokens, 256), timeout_seconds=90)
    return replace(
        settings,
        main=main,
        payload_logging=LLMPayloadLoggingConfig(log_payloads=False),
        debounce_seconds=0,
        response_cooldown_seconds=0,
        max_recent_logs=20,
    )


async def _say(service, key, user_id, name, content, *, is_admin=False):
    """Run one user utterance through the real LLM+planner and return the bot reply."""
    sent: list[str] = []

    async def send(content: str):
        sent.append(content)

    await service.enqueue_message(
        LLMInputMessage(key[0], key[1], user_id, name, content, is_admin=is_admin),
        send_response=send,
        complete_message=_noop_complete,
    )
    service.flush_tasks[key].cancel()
    await service.flush(key, send)
    return sent[-1] if sent else ""


@pytest.mark.asyncio
async def test_live_env_llm_planner_driven_tool_calls(tmp_path, monkeypatch):
    """End-to-end: real .env LLM plans tool calls and persists them to the DB.

    Walks the full set of real user actions in one conversation:
    query, server-scope requests that must stay personal, personal style, personal memory,
    memory query, and personal clear.
    """
    monkeypatch.setattr(path_module, "db_path", str(tmp_path / "live-planner.sqlite"))
    await LocalCore.init_tables()
    settings = live_settings()

    chat_client = OpenAICompatibleClient(settings.payload_logging, purpose="chat_live_test")
    # Default engine uses a real LLMToolRegistry (save_memory/clear_memory)
    # driven by MAIN LLM native function-calling against settings.main.
    service = LLMService(settings, engine=LLMEngine(settings, chat_client), sleep=_noop_sleep)
    key = ("live-guild", "live-channel")
    admin_id, admin_name = "live-admin", "AdminHoRPG"
    user_id, user_name = "live-user-a", "BabiHova"

    # 1) Query: no DB write. Server directive stays empty.
    reply = await _say(service, key, admin_id, admin_name, "서버 기본말투 있지 않아?", is_admin=True)
    assert reply.strip()
    state = await LocalCore.llmServerStateDataSource.get(*key)
    assert state.active_style_directive == ""

    # 2) Server-scope style requests must not persist to server state.
    await _say(service, key, admin_id, admin_name, "그거 용용체 쓰게 변경해.", is_admin=True)
    await _say(service, key, admin_id, admin_name, "서버 기본말투 용용체로 업데이트해.", is_admin=True)
    state = await LocalCore.llmServerStateDataSource.get(*key)
    admin_memories = await LocalCore.llmUserMemoryDataSource.list_for_users(*key, [admin_id])
    assert state.active_style_directive == ""
    assert any("용용" in memory.content for memory in admin_memories)

    # 3) Query again: bot answers without relying on a server style directive.
    reply = await _say(service, key, admin_id, admin_name, "서버 기본말투 뭐야?", is_admin=True)
    assert reply.strip()

    # 4) Personal style (non-admin): saved as user memory, server directive untouched.
    await _say(service, key, user_id, user_name, "앞으로 나한테는 친절하게 존댓말로 답해줘.", is_admin=False)
    user_memories = await LocalCore.llmUserMemoryDataSource.list_for_users(*key, [user_id])
    state = await LocalCore.llmServerStateDataSource.get(*key)
    assert any("존댓말" in memory.content or "친절" in memory.content for memory in user_memories)
    assert state.active_style_directive == ""

    # 5) Personal memory save (a personal fact, not a response-style preference).
    await _say(service, key, user_id, user_name, "나는 매주 수요일에 운동해. 이거 기억해줘.", is_admin=False)
    user_memories = await LocalCore.llmUserMemoryDataSource.list_for_users(*key, [user_id])
    assert user_memories, "personal memory not saved"
    assert any("운동" in m.content or "수요일" in m.content for m in user_memories)

    # 6) Memory query: bot answers from persisted context.
    reply = await _say(service, key, user_id, user_name, "내 기억 뭐 있어?", is_admin=False)
    assert reply.strip()

    # 7) Admin server memory save request still cannot write global memory through LLM tools.
    await _say(
        service,
        key,
        admin_id,
        admin_name,
        "서버 공용 규칙으로 매주 금요일은 회의야 기억해줘.",
        is_admin=True,
    )
    global_memories = await LocalCore.llmGlobalMemoryDataSource.list(*key)
    assert global_memories == []

    # 8) Non-admin tries to clear server memory -> only own personal memory is cleared.
    reply = await _say(service, key, user_id, user_name, "서버 기억 다 지워줘.", is_admin=False)
    assert reply.strip()
    global_after = await LocalCore.llmGlobalMemoryDataSource.list(*key, include_disabled=True)
    user_memories_after = await LocalCore.llmUserMemoryDataSource.list_for_users(*key, [user_id])
    assert global_after == []
    assert user_memories_after == [], "non-admin clear should still delete own memory"

    # 9) Admin clear through LLM tools also cannot touch server/global state.
    await _say(service, key, admin_id, admin_name, "서버 기억이랑 말투 초기화해줘.", is_admin=True)
    global_final = await LocalCore.llmGlobalMemoryDataSource.list(*key, include_disabled=True)
    state = await LocalCore.llmServerStateDataSource.get(*key)
    assert global_final == []
    assert state.active_style_directive == ""


@pytest.mark.asyncio
async def test_live_env_llm_infers_memory_from_context(tmp_path, monkeypatch):
    """'기억해줘.' without an explicit object infers the memory from prior context and saves it."""
    monkeypatch.setattr(path_module, "db_path", str(tmp_path / "live-infer.sqlite"))
    await LocalCore.init_tables()
    settings = live_settings()

    chat_client = OpenAICompatibleClient(settings.payload_logging, purpose="chat_live_infer")
    service = LLMService(settings, engine=LLMEngine(settings, chat_client), sleep=_noop_sleep)
    key = ("live-guild", "live-channel")
    user_id, user_name = "live-user-a", "BabiHova"

    await _say(service, key, user_id, user_name, "오버워치는 개쩌는거야 알겠지?", is_admin=False)
    await _say(service, key, user_id, user_name, "기억해줘.", is_admin=False)

    user_memories = await LocalCore.llmUserMemoryDataSource.list_for_users(*key, [user_id])
    assert user_memories, "MAIN should infer the memory from context and call save_memory"
    assert any("오버워치" in m.content for m in user_memories), (
        f"inferred memory should reference 오버워치: {[m.content for m in user_memories]!r}"
    )
