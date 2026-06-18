import asyncio

import pytest

from core.llm.config import LLMSettings, LLMMemoryConfig, LLMProviderConfig
from core.llm.models import LLMBufferedMessage, LLMInputMessage, MemoryExtractionResult
from core.llm.service import LLMService
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


@pytest.mark.asyncio
async def test_memory_job_blocks_same_channel_but_not_other_channel(tmp_path, monkeypatch):
    monkeypatch.setattr(path_module, "db_path", str(tmp_path / "db.sqlite"))
    await LocalCore.init_tables()
    engine = FakeEngine()
    extractor = SlowExtractor()
    service = LLMService(settings(), engine=engine, extractor=extractor, sleep=no_sleep)
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
    service = LLMService(settings(), engine=FakeEngine(), extractor=StaticExtractor(result), sleep=no_sleep)

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
    service = LLMService(settings(), engine=FakeEngine(), extractor=None, sleep=no_sleep)
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
    service = LLMService(settings(), engine=FakeEngine(), extractor=None, sleep=no_sleep)
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
async def test_explicit_memory_and_style_requests_are_saved_by_tools(tmp_path, monkeypatch):
    monkeypatch.setattr(path_module, "db_path", str(tmp_path / "db.sqlite"))
    await LocalCore.init_tables()
    engine = FakeEngine()
    service = LLMService(settings(), engine=engine, extractor=None, sleep=no_sleep)
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
    assert user_styles
