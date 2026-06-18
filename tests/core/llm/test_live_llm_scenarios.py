import os
from dataclasses import replace

import pytest
from dotenv import load_dotenv

from core.llm.config import LLMMemoryConfig, LLMPayloadLoggingConfig, load_llm_settings
from core.llm.engine import LLMEngine
from core.llm.llm_client import OpenAICompatibleClient
from core.llm.memory_extractor import LLMMemoryExtractor
from core.llm.models import LLMBufferedMessage, LLMInputMessage
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
    aux = replace(settings.aux, temperature=0.0, max_tokens=min(settings.aux.max_tokens, 256), timeout_seconds=90)
    return replace(
        settings,
        main=main,
        aux=aux,
        payload_logging=LLMPayloadLoggingConfig(log_payloads=False),
        memory=LLMMemoryConfig(enabled=False),
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
async def test_live_env_llm_memory_extractor_smoke(tmp_path, monkeypatch):
    """The aux LLM memory extractor returns structured JSON from a real provider call."""
    monkeypatch.setattr(path_module, "db_path", str(tmp_path / "live-extractor.sqlite"))
    await LocalCore.init_tables()
    settings = live_settings()

    memory_client = OpenAICompatibleClient(settings.payload_logging, purpose="memory_extraction_live_test")
    extractor = LLMMemoryExtractor(memory_client, settings.aux)
    extracted = await extractor.extract(
        [
            LLMBufferedMessage(
                guild_id="live-guild",
                channel_id="live-channel",
                user_id="live-user-a",
                author_name="IntegrationUserA",
                content=(
                    "내 장기 선호로 테스트용 별칭은 초록연필이라고 기억해줘. "
                    "앞으로 나한테만 답할 때는 짧고 차분한 말투를 적용하고 서버에는 적용하지마."
                ),
                created_at="2026-06-18T00:00:00+00:00",
            )
        ]
    )
    assert extracted.had_changes
    # Personal-scope markers must keep server style fields empty.
    assert extracted.active_style_directive == ""
    assert extracted.server_style_summary == ""
    assert extracted.server_memory_add == []
    assert extracted.user_memory_add or extracted.user_style_add or extracted.user_style_phrases_add


@pytest.mark.asyncio
async def test_live_env_llm_planner_driven_tool_calls(tmp_path, monkeypatch):
    """End-to-end: real .env LLM plans tool calls and persists them to the DB.

    Walks the full set of real user actions in one conversation:
    query, server style update (the dragon-speak flow), personal style, personal memory,
    memory query, server memory, non-admin server-delete downgrade, admin server clear.
    """
    monkeypatch.setattr(path_module, "db_path", str(tmp_path / "live-planner.sqlite"))
    await LocalCore.init_tables()
    settings = live_settings()

    chat_client = OpenAICompatibleClient(settings.payload_logging, purpose="chat_live_test")
    # Default tools use a real LLMToolPlanner against settings.aux.
    service = LLMService(settings, engine=LLMEngine(settings, chat_client), extractor=None, sleep=_noop_sleep)
    key = ("live-guild", "live-channel")
    admin_id, admin_name = "live-admin", "AdminHoRPG"
    user_id, user_name = "live-user-a", "BabiHova"

    # 1) Query: no DB write. Server directive stays empty.
    reply = await _say(service, key, admin_id, admin_name, "서버 기본말투 있지 않아?", is_admin=True)
    assert reply.strip()
    state = await LocalCore.llmServerStateDataSource.get(*key)
    assert state.active_style_directive == ""

    # 2) The dragon-speak flow: explicit server style update must persist to the DB.
    await _say(service, key, admin_id, admin_name, "그거 용용체 쓰게 변경해.", is_admin=True)
    await _say(service, key, admin_id, admin_name, "서버 기본말투 용용체로 업데이트해.", is_admin=True)
    state = await LocalCore.llmServerStateDataSource.get(*key)
    assert "용용" in state.active_style_directive, f"server style not persisted: {state.active_style_directive!r}"

    # 3) Query again: the bot reflects the persisted dragon-speak directive.
    reply = await _say(service, key, admin_id, admin_name, "서버 기본말투 뭐야?", is_admin=True)
    assert "용용" in reply, f"reply did not reflect persisted style: {reply!r}"

    # 4) Personal style (non-admin): saved per-user, server directive untouched.
    await _say(service, key, user_id, user_name, "앞으로 나한테는 친절하게 존댓말로 답해줘.", is_admin=False)
    user_styles = await LocalCore.llmSpeechStyleDataSource.list_for_users(*key, [user_id])
    state = await LocalCore.llmServerStateDataSource.get(*key)
    assert user_styles, "personal style not saved"
    assert any("존댓말" in s.notes or "친절" in s.notes for s in user_styles)
    assert "용용" in state.active_style_directive, "personal request must not overwrite server style"

    # 5) Personal memory save (a personal fact, not a response-style preference).
    await _say(service, key, user_id, user_name, "나는 매주 수요일에 운동해. 이거 기억해줘.", is_admin=False)
    user_memories = await LocalCore.llmUserMemoryDataSource.list_for_users(*key, [user_id])
    assert user_memories, "personal memory not saved"
    assert any("운동" in m.content or "수요일" in m.content for m in user_memories)

    # 6) Memory query: bot answers from persisted context.
    reply = await _say(service, key, user_id, user_name, "내 기억 뭐 있어?", is_admin=False)
    assert reply.strip()

    # 7) Admin server memory save.
    await _say(
        service,
        key,
        admin_id,
        admin_name,
        "서버 공용 규칙으로 매주 금요일은 회의야 기억해줘.",
        is_admin=True,
    )
    global_memories = await LocalCore.llmGlobalMemoryDataSource.list(*key)
    assert global_memories and any("회의" in m.content or "금요일" in m.content for m in global_memories)

    # 8) Non-admin tries to clear server memory -> downgraded to personal clear + permission notice.
    reply = await _say(service, key, user_id, user_name, "서버 기억 다 지워줘.", is_admin=False)
    assert "관리자" in reply, f"expected permission notice, got: {reply!r}"
    global_after = await LocalCore.llmGlobalMemoryDataSource.list(*key, include_disabled=True)
    user_memories_after = await LocalCore.llmUserMemoryDataSource.list_for_users(*key, [user_id])
    user_styles_after = await LocalCore.llmSpeechStyleDataSource.list_for_users(*key, [user_id])
    assert global_after, "non-admin must not delete server memory"
    assert user_memories_after == [], "non-admin clear should still delete own memory"
    assert user_styles_after == [], "non-admin clear should still delete own style"

    # 9) Admin clears server memory + style -> actually removed from DB.
    await _say(service, key, admin_id, admin_name, "서버 기억이랑 말투 초기화해줘.", is_admin=True)
    global_final = await LocalCore.llmGlobalMemoryDataSource.list(*key, include_disabled=True)
    state = await LocalCore.llmServerStateDataSource.get(*key)
    assert global_final == [], "admin clear must remove all server memory"
    assert state.active_style_directive == "", "admin clear must reset server style"