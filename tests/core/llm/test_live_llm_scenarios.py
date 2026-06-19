import asyncio
import importlib
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

web_search_module = importlib.import_module("core.llm.tools.web_search")


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


def live_runtime_settings():
    load_dotenv(".env", override=False)
    if os.environ.get("RUN_LIVE_LLM_TESTS", "").strip().lower() not in {"1", "true", "yes", "on"}:
        pytest.skip("set RUN_LIVE_LLM_TESTS=1 to call the real LLM provider from .env")
    settings = load_llm_settings()
    if not settings.main.api_key or not settings.main.model:
        pytest.skip(".env must define LLM_API_KEY and LLM_MODEL for live LLM integration tests")
    return replace(
        settings,
        payload_logging=LLMPayloadLoggingConfig(log_payloads=False),
        debounce_seconds=0,
        response_cooldown_seconds=0,
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


async def _say_all(service, key, user_id, name, content, *, is_admin=False):
    """Run one user utterance and return every message the service tried to send."""
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
    return sent


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
    # Default engine uses a real LLMToolRegistry (save_memory/edit_memory/clear_memory)
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

    # 8) Non-admin tries to clear server memory. This must not mutate server/global memory.
    reply = await _say(service, key, user_id, user_name, "서버 기억 다 지워줘.", is_admin=False)
    assert reply.strip()
    global_after = await LocalCore.llmGlobalMemoryDataSource.list(*key, include_disabled=True)
    assert global_after == []

    # 9) Admin server-scope clear request also cannot touch server/global state.
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


@pytest.mark.asyncio
async def test_live_env_llm_edits_existing_personal_memory(tmp_path, monkeypatch):
    """Real LLM should use edit_memory for a general personal memory update, not duplicate save_memory."""
    monkeypatch.setattr(path_module, "db_path", str(tmp_path / "live-edit-memory.sqlite"))
    await LocalCore.init_tables()
    settings = live_settings()

    chat_client = OpenAICompatibleClient(settings.payload_logging, purpose="chat_live_edit_memory")
    service = LLMService(settings, engine=LLMEngine(settings, chat_client), sleep=_noop_sleep)
    key = ("live-guild", "live-channel")
    user_id, user_name = "live-user-a", "BabiHova"
    memory_id = await LocalCore.llmUserMemoryDataSource.add(
        key[0],
        key[1],
        user_id,
        "사용자는 오버워치를 좋아한다.",
        user_name=user_name,
    )

    reply = await _say(
        service,
        key,
        user_id,
        user_name,
        "내 개인 메모리 중 오버워치를 좋아한다는 내용을 마비노기를 좋아한다로 수정해줘.",
        is_admin=False,
    )

    user_memories = await LocalCore.llmUserMemoryDataSource.list_for_users(*key, [user_id])
    assert reply.strip()
    assert len(user_memories) == 1, f"edit should update existing memory instead of adding duplicate: {user_memories!r}"
    assert user_memories[0].id == memory_id
    assert "마비노기" in user_memories[0].content
    assert "오버워치" not in user_memories[0].content


@pytest.mark.asyncio
async def test_live_env_maple_search_then_dnftopic_does_not_drop_first_reply(tmp_path, monkeypatch):
    """Reproduce the reported Discord sequence with the real provider and real web_search tool."""
    monkeypatch.setattr(path_module, "db_path", str(tmp_path / "live-dropped-reply.sqlite"))
    await LocalCore.init_tables()
    settings = live_runtime_settings()

    chat_client = OpenAICompatibleClient(settings.payload_logging, purpose="chat_live_dropped_reply")
    service = LLMService(settings, engine=LLMEngine(settings, chat_client), sleep=_noop_sleep)
    key = ("live-guild", "live-channel")
    user_id, user_name = "464712715487805442", "바비호바"
    await LocalCore.llmUserMemoryDataSource.add(
        key[0],
        key[1],
        user_id,
        "사용자는 프갤봇의 창조주라고 주장한다.",
        user_name=user_name,
    )
    await LocalCore.llmUserMemoryDataSource.add(
        key[0],
        key[1],
        user_id,
        "사용자는 섹현쿤을 엘소드의 신이라고 생각한다.",
        user_name=user_name,
    )

    first_sent = await _say_all(
        service,
        key,
        user_id,
        user_name,
        "웹에서 메이플 신규 캐릭터 나왔다는데 조사해줘",
        is_admin=False,
    )
    second_sent = await _say_all(
        service,
        key,
        user_id,
        user_name,
        "아 던파에서 뭐더라",
        is_admin=False,
    )

    assert first_sent, "first utterance produced no Discord send attempt"
    assert first_sent[-1].strip(), f"first reply was blank after live web_search tool round: {first_sent!r}"
    assert second_sent, "second utterance produced no Discord send attempt"
    assert second_sent[-1].strip(), f"second reply was blank: {second_sent!r}"


@pytest.mark.asyncio
async def test_live_env_second_message_during_web_search_replies_to_both_messages(tmp_path, monkeypatch):
    """Use the real provider for tool planning, then block web_search to reproduce the race."""
    monkeypatch.setattr(path_module, "db_path", str(tmp_path / "live-tool-race.sqlite"))
    await LocalCore.init_tables()
    settings = live_runtime_settings()
    tool_started = asyncio.Event()
    release_tool = asyncio.Event()

    async def blocking_search(query, engines, limit):
        tool_started.set()
        await release_tool.wait()
        return (
            '웹 검색 결과 query="메이플스토리 신규 캐릭터 최신 정보"\n'
            "1. 메이플스토리 신규 직업 레테\n"
            "   url: https://example.test/maple-lete\n"
            "   engine: fake\n"
            "   description: 신규 직업 레테가 공개되었습니다."
        )

    monkeypatch.setattr(web_search_module, "_run_search", blocking_search)
    chat_client = OpenAICompatibleClient(settings.payload_logging, purpose="chat_live_tool_race")
    service = LLMService(settings, engine=LLMEngine(settings, chat_client), sleep=_noop_sleep)
    key = ("live-guild", "live-channel")
    user_id, user_name = "464712715487805442", "바비호바"
    sent: list[str] = []

    async def send(content: str):
        sent.append(content)

    await service.enqueue_message(
        LLMInputMessage(
            key[0],
            key[1],
            user_id,
            user_name,
            "웹에서 메이플 신규 캐릭터 나왔다는데 조사해줘",
            is_admin=False,
        ),
        send_response=send,
        complete_message=_noop_complete,
    )
    await asyncio.wait_for(tool_started.wait(), timeout=30)
    first_task = service.flush_tasks[key]

    await service.enqueue_message(
        LLMInputMessage(key[0], key[1], user_id, user_name, "아 던파에서 뭐더라", is_admin=False),
        send_response=send,
        complete_message=_noop_complete,
    )
    release_tool.set()
    await first_task
    next_task = service.flush_tasks.get(key)
    if next_task is not None and next_task is not first_task:
        await next_task

    assert len(sent) == 2
    assert all(reply.strip() for reply in sent)
