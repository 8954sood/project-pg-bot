import asyncio

import pytest

from core.llm.config import LLMSettings, LLMMemoryConfig, LLMProviderConfig
from core.llm.models import LLMBufferedMessage, LLMInputMessage, MemoryExtractionResult
from core.llm.service import LLMService
from core.llm.tool_planner import ToolPlan
from core.llm.tool_registry import LLMToolRegistry
from core.local import LocalCore
from core.local import path as path_module


class FakeEngine:
    def __init__(self):
        self.calls = 0
        self.last_tool_results = []

    async def respond(self, **kwargs):
        self.calls += 1
        self.last_tool_results = kwargs.get("tool_results", [])
        return "응답"


class SlowExtractor:
    def __init__(self):
        self.release = asyncio.Event()

    async def extract(self, messages):
        await self.release.wait()
        return MemoryExtractionResult("", "", ["서버 기억"], [], [], [], [], True)


class StaticExtractor:
    def __init__(self, result):
        self.result = result

    async def extract(self, messages):
        return self.result


class FakePlanner:
    """Injectable tool planner for unit tests (no LLM calls)."""

    def __init__(self, plans):
        self.plans = plans
        self.last_text = ""
        self.last_is_admin = None

    async def plan(self, text, is_admin, memory_state):
        self.last_text = text
        self.last_is_admin = is_admin
        return self.plans


async def no_sleep(_):
    return None


def settings():
    return LLMSettings(
        guild_channel_map={"1": {"2", "3"}},
        main=LLMProviderConfig(api_key="k", model="m"),
        memory=LLMMemoryConfig(
            enabled=True,
            min_user_chars=1,
            min_total_chars=1,
            every_n_turns=1,
            job_timeout_seconds=5,
        ),
        debounce_seconds=0,
    )


def registry(plans):
    return LLMToolRegistry(planner=FakePlanner(plans))


@pytest.mark.asyncio
async def test_memory_job_blocks_same_channel_but_not_other_channel(tmp_path, monkeypatch):
    monkeypatch.setattr(path_module, "db_path", str(tmp_path / "db.sqlite"))
    await LocalCore.init_tables()
    engine = FakeEngine()
    extractor = SlowExtractor()
    service = LLMService(settings(), engine=engine, extractor=extractor, tools=registry([]), sleep=no_sleep)
    sent = []
    completed = 0

    async def send(content):
        sent.append(content)

    async def complete():
        nonlocal completed
        completed += 1

    await service.enqueue_message(LLMInputMessage("1", "2", "u1", "User", "기억해 abc"), send_response=send, complete_message=complete)
    service.flush_tasks[("1", "2")].cancel()
    await service.flush(("1", "2"), send)
    assert engine.calls == 1
    assert (await LocalCore.llmMemoryJobDataSource.get("1", "2")).running == 1

    await service.enqueue_message(LLMInputMessage("1", "2", "u1", "User", "다음 말"), send_response=send, complete_message=complete)
    service.flush_tasks[("1", "2")].cancel()
    await service.flush(("1", "2"), send)
    assert engine.calls == 1

    await service.enqueue_message(LLMInputMessage("1", "3", "u1", "User", "다른 채널"), send_response=send, complete_message=complete)
    service.flush_tasks[("1", "3")].cancel()
    await service.flush(("1", "3"), send)
    assert engine.calls == 2

    extractor.release.set()
    await asyncio.gather(*service.memory_tasks)
    await service.flush_tasks[("1", "2")]
    await asyncio.gather(*service.memory_tasks)
    assert (await LocalCore.llmMemoryJobDataSource.get("1", "2")).running == 0
    assert engine.calls == 3


@pytest.mark.asyncio
async def test_personal_scope_style_is_not_saved_as_server_style(tmp_path, monkeypatch):
    monkeypatch.setattr(path_module, "db_path", str(tmp_path / "db.sqlite"))
    await LocalCore.init_tables()
    result = MemoryExtractionResult(
        "짧고 부드러운 말투",
        "모든 응답에 짧고 부드러운 말투를 적용한다.",
        [],
        [],
        [],
        [],
        [],
        True,
    )
    service = LLMService(settings(), engine=FakeEngine(), extractor=StaticExtractor(result), tools=registry([]), sleep=no_sleep)

    await service._run_memory_job(
        ("1", "2"),
        [
            LLMBufferedMessage(
                guild_id="1",
                channel_id="2",
                user_id="3",
                author_name="TestUser",
                content="이 말투는 나한테만 사용해. 길드에는 적용하지마",
                created_at="2026-06-18T00:00:00+00:00",
            )
        ],
    )

    server_state = await LocalCore.llmServerStateDataSource.get("1", "2")
    user_styles = await LocalCore.llmSpeechStyleDataSource.list_for_users("1", "2", ["3"])
    assert server_state.active_style_directive == ""
    assert server_state.server_style_summary.startswith("기본적으로")
    assert user_styles[0].user_id == "3"
    assert "서버/길드 전역에는 적용하지 않는다" in user_styles[0].notes


@pytest.mark.asyncio
async def test_admin_can_clear_server_memory_and_style(tmp_path, monkeypatch):
    monkeypatch.setattr(path_module, "db_path", str(tmp_path / "db.sqlite"))
    await LocalCore.init_tables()
    await LocalCore.llmGlobalMemoryDataSource.add("1", "2", "server_memory", "공용 기억", 1, "admin")
    await LocalCore.llmServerStateDataSource.upsert("1", "2", active_style_directive="공용 말투")
    service = LLMService(
        settings(),
        engine=FakeEngine(),
        extractor=None,
        tools=registry([ToolPlan("clear_memory", "server")]),
        sleep=no_sleep,
    )
    sent = []

    async def send(content):
        sent.append(content)

    async def complete():
        return None

    await service.enqueue_message(
        LLMInputMessage("1", "2", "admin", "Admin", "서버에 기록된 말투, 기억들 다 지워줘.", is_admin=True),
        send_response=send,
        complete_message=complete,
    )
    service.flush_tasks[("1", "2")].cancel()
    await service.flush(("1", "2"), send)

    server_state = await LocalCore.llmServerStateDataSource.get("1", "2")
    global_memories = await LocalCore.llmGlobalMemoryDataSource.list("1", "2", include_disabled=True)
    assert sent == ["서버/채널 전역 기억 1개와 서버 말투 설정을 삭제했습니다."]
    assert global_memories == []
    assert server_state.active_style_directive == ""


@pytest.mark.asyncio
async def test_non_admin_clear_server_request_only_clears_own_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(path_module, "db_path", str(tmp_path / "db.sqlite"))
    await LocalCore.init_tables()
    await LocalCore.llmGlobalMemoryDataSource.add("1", "2", "server_memory", "공용 기억", 1, "admin")
    await LocalCore.llmUserMemoryDataSource.add("1", "2", "user", "개인 기억", user_name="User")
    await LocalCore.llmSpeechStyleDataSource.upsert("1", "2", "user", "개인 말투", user_name="User", notes="개인 말투")
    service = LLMService(
        settings(),
        engine=FakeEngine(),
        extractor=None,
        tools=registry([ToolPlan("clear_memory", "server")]),
        sleep=no_sleep,
    )
    sent = []

    async def send(content):
        sent.append(content)

    async def complete():
        return None

    await service.enqueue_message(
        LLMInputMessage("1", "2", "user", "User", "서버 기억이랑 말투 지워줘.", is_admin=False),
        send_response=send,
        complete_message=complete,
    )
    service.flush_tasks[("1", "2")].cancel()
    await service.flush(("1", "2"), send)

    global_memories = await LocalCore.llmGlobalMemoryDataSource.list("1", "2", include_disabled=True)
    user_memories = await LocalCore.llmUserMemoryDataSource.list_for_users("1", "2", ["user"])
    user_styles = await LocalCore.llmSpeechStyleDataSource.list_for_users("1", "2", ["user"])
    assert "Discord 관리자 권한이 필요합니다" in sent[0]
    assert len(global_memories) == 1
    assert user_memories == []
    assert user_styles == []


@pytest.mark.asyncio
async def test_memory_and_style_plans_are_executed(tmp_path, monkeypatch):
    monkeypatch.setattr(path_module, "db_path", str(tmp_path / "db.sqlite"))
    await LocalCore.init_tables()
    engine = FakeEngine()
    service = LLMService(
        settings(),
        engine=engine,
        extractor=None,
        tools=registry([
            ToolPlan("update_memory", "user", note="중요한 설정을 기억"),
            ToolPlan("update_style", "user", note="짧고 부드럽게 답한다"),
        ]),
        sleep=no_sleep,
    )
    sent = []

    async def send(content):
        sent.append(content)

    async def complete():
        return None

    await service.enqueue_message(
        LLMInputMessage("1", "2", "user", "User", "앞으로 중요한 설정을 기억해줘. 말투는 짧고 부드럽게 답해줘.", is_admin=False),
        send_response=send,
        complete_message=complete,
    )
    service.flush_tasks[("1", "2")].cancel()
    await service.flush(("1", "2"), send)

    user_memories = await LocalCore.llmUserMemoryDataSource.list_for_users("1", "2", ["user"])
    user_styles = await LocalCore.llmSpeechStyleDataSource.list_for_users("1", "2", ["user"])
    tool_names = [result.name for result in engine.last_tool_results]
    assert "update_user_memory" in tool_names
    assert "update_user_style" in tool_names
    assert user_memories
    assert user_memories[0].content == "중요한 설정을 기억"
    assert user_styles
    assert user_styles[0].notes.startswith("봇은 User에게 다음 말투/응답 지시를 적용한다: 짧고 부드럽게 답한다")


@pytest.mark.asyncio
async def test_admin_server_memory_and_style_plans_are_executed(tmp_path, monkeypatch):
    monkeypatch.setattr(path_module, "db_path", str(tmp_path / "db.sqlite"))
    await LocalCore.init_tables()
    engine = FakeEngine()
    service = LLMService(
        settings(),
        engine=engine,
        extractor=None,
        tools=registry([
            ToolPlan("update_memory", "server", note="회의 내용은 요약해서 기억"),
            ToolPlan("update_style", "server", note="차분하게 답한다"),
        ]),
        sleep=no_sleep,
    )
    sent = []

    async def send(content):
        sent.append(content)

    async def complete():
        return None

    await service.enqueue_message(
        LLMInputMessage(
            "1",
            "2",
            "admin",
            "Admin",
            "서버 공용 규칙으로 회의 내용은 요약해서 기억해줘. 서버 말투는 차분하게 답해줘.",
            is_admin=True,
        ),
        send_response=send,
        complete_message=complete,
    )
    service.flush_tasks[("1", "2")].cancel()
    await service.flush(("1", "2"), send)

    global_memories = await LocalCore.llmGlobalMemoryDataSource.list("1", "2")
    server_state = await LocalCore.llmServerStateDataSource.get("1", "2")
    tool_names = [result.name for result in engine.last_tool_results]
    assert "update_server_memory" in tool_names
    assert "update_server_style" in tool_names
    assert global_memories
    assert global_memories[0].content == "회의 내용은 요약해서 기억"
    assert server_state.active_style_directive == "차분하게 답한다"


@pytest.mark.asyncio
async def test_query_plan_runs_no_db_tools(tmp_path, monkeypatch):
    """Query/intent-free messages produce no tool results and no DB writes."""
    monkeypatch.setattr(path_module, "db_path", str(tmp_path / "db.sqlite"))
    await LocalCore.init_tables()
    await LocalCore.llmGlobalMemoryDataSource.add("1", "2", "server_memory", "공용 회의는 매주 수요일", 1, "admin")
    engine = FakeEngine()
    service = LLMService(
        settings(),
        engine=engine,
        extractor=None,
        tools=registry([]),  # planner returns no actionable plans for a query
        sleep=no_sleep,
    )
    sent = []

    async def send(content):
        sent.append(content)

    async def complete():
        return None

    await service.enqueue_message(
        LLMInputMessage("1", "2", "user", "User", "지금 저장된 기억이랑 말투 뭐 있어?", is_admin=False),
        send_response=send,
        complete_message=complete,
    )
    service.flush_tasks[("1", "2")].cancel()
    await service.flush(("1", "2"), send)

    assert engine.last_tool_results == []
    # Nothing new written; pre-existing server memory untouched.
    global_memories = await LocalCore.llmGlobalMemoryDataSource.list("1", "2")
    assert len(global_memories) == 1
    assert sent and sent[-1] == "응답"


@pytest.mark.asyncio
async def test_non_admin_server_plan_is_downgraded_to_user_scope(tmp_path, monkeypatch):
    """The registry enforces admin-only server scope regardless of planner output."""
    monkeypatch.setattr(path_module, "db_path", str(tmp_path / "db.sqlite"))
    await LocalCore.init_tables()
    engine = FakeEngine()
    service = LLMService(
        settings(),
        engine=engine,
        extractor=None,
        tools=registry([ToolPlan("update_style", "server", note="용용체로 답한다")]),
        sleep=no_sleep,
    )
    sent = []

    async def send(content):
        sent.append(content)

    async def complete():
        return None

    await service.enqueue_message(
        LLMInputMessage("1", "2", "user", "User", "서버 말투 용용체로 바꿔줘.", is_admin=False),
        send_response=send,
        complete_message=complete,
    )
    service.flush_tasks[("1", "2")].cancel()
    await service.flush(("1", "2"), send)

    server_state = await LocalCore.llmServerStateDataSource.get("1", "2")
    user_styles = await LocalCore.llmSpeechStyleDataSource.list_for_users("1", "2", ["user"])
    tool_names = [result.name for result in engine.last_tool_results]
    assert tool_names == ["update_user_style"]
    assert server_state.active_style_directive == ""
    assert user_styles and user_styles[0].user_id == "user"