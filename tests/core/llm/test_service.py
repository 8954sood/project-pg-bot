import asyncio

import pytest

from core.llm.config import LLMSettings, LLMMemoryConfig, LLMProviderConfig
from core.llm.engine import LLMEngine
from core.llm.llm_client import LLMClientResponse
from core.llm.models import LLMBufferedMessage, LLMInputMessage, MemoryExtractionResult, ToolCall
from core.llm.service import LLMService
from core.llm.tool_registry import LLMToolRegistry
from core.local import LocalCore
from core.local import path as path_module


class FakeClient:
    """Scripted LLM client for unit tests. Returns canned (content, tool_calls) per call."""

    def __init__(self, responses=None):
        # responses: list of (content, list[ToolCall]). If None, always a plain final answer.
        self.responses = responses
        self.calls = 0

    async def chat(self, config, messages, *, tools=None, tool_choice="auto"):
        self.calls += 1
        if self.responses is not None:
            content, tool_calls = self.responses[min(self.calls - 1, len(self.responses) - 1)]
        else:
            content, tool_calls = "응답", []
        return LLMClientResponse(content=content, provider_path="fake", tool_calls=list(tool_calls))


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


def make_service(client, *, extractor=None):
    registry = LLMToolRegistry()
    engine = LLMEngine(settings(), client=client, tools=registry)
    return LLMService(settings(), engine=engine, extractor=extractor, tools=registry, sleep=no_sleep)


async def enqueue_and_flush(service, message, *, send, complete):
    key = (message.guild_id, message.channel_id)
    await service.enqueue_message(message, send_response=send, complete_message=complete)
    service.flush_tasks[key].cancel()
    await service.flush(key, send)


async def _noop_complete():
    return None


@pytest.mark.asyncio
async def test_memory_job_blocks_same_channel_but_not_other_channel(tmp_path, monkeypatch):
    monkeypatch.setattr(path_module, "db_path", str(tmp_path / "db.sqlite"))
    await LocalCore.init_tables()
    client = FakeClient()
    extractor = SlowExtractor()
    service = make_service(client, extractor=extractor)
    sent = []

    async def send(content):
        sent.append(content)

    await enqueue_and_flush(service, LLMInputMessage("1", "2", "u1", "User", "기억해 abc"), send=send, complete=_noop_complete)
    assert client.calls == 1
    assert (await LocalCore.llmMemoryJobDataSource.get("1", "2")).running == 1

    await enqueue_and_flush(service, LLMInputMessage("1", "2", "u1", "User", "다음 말"), send=send, complete=_noop_complete)
    assert client.calls == 1  # blocked by running memory job on same channel

    await enqueue_and_flush(service, LLMInputMessage("1", "3", "u1", "User", "다른 채널"), send=send, complete=_noop_complete)
    assert client.calls == 2  # other channel not blocked

    extractor.release.set()
    await asyncio.gather(*service.memory_tasks)
    await service.flush_tasks[("1", "2")]
    await asyncio.gather(*service.memory_tasks)
    assert (await LocalCore.llmMemoryJobDataSource.get("1", "2")).running == 0
    assert client.calls == 3  # buffered "다음 말" flushed after job completed


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
    service = make_service(FakeClient(), extractor=StaticExtractor(result))

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
async def test_admin_clear_memory_tool_removes_server_memory_and_style(tmp_path, monkeypatch):
    monkeypatch.setattr(path_module, "db_path", str(tmp_path / "db.sqlite"))
    await LocalCore.init_tables()
    await LocalCore.llmGlobalMemoryDataSource.add("1", "2", "server_memory", "공용 기억", 1, "admin")
    await LocalCore.llmServerStateDataSource.upsert("1", "2", active_style_directive="공용 말투")
    client = FakeClient(
        responses=[
            ("", [ToolCall("clear_memory", {"scope": "server"})]),
            ("서버 기억과 말투를 초기화했어.", []),
        ]
    )
    service = make_service(client)
    sent = []

    async def send(content):
        sent.append(content)

    await enqueue_and_flush(
        service,
        LLMInputMessage("1", "2", "admin", "Admin", "서버에 기록된 말투, 기억들 다 지워줘.", is_admin=True),
        send=send,
        complete=_noop_complete,
    )

    server_state = await LocalCore.llmServerStateDataSource.get("1", "2")
    global_memories = await LocalCore.llmGlobalMemoryDataSource.list("1", "2", include_disabled=True)
    assert client.calls == 2  # tool round + final answer round
    assert sent[-1] == "서버 기억과 말투를 초기화했어."
    assert global_memories == []
    assert server_state.active_style_directive == ""


@pytest.mark.asyncio
async def test_non_admin_clear_server_tool_only_clears_own_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(path_module, "db_path", str(tmp_path / "db.sqlite"))
    await LocalCore.init_tables()
    await LocalCore.llmGlobalMemoryDataSource.add("1", "2", "server_memory", "공용 기억", 1, "admin")
    await LocalCore.llmUserMemoryDataSource.add("1", "2", "user", "개인 기억", user_name="User")
    await LocalCore.llmSpeechStyleDataSource.upsert("1", "2", "user", "개인 말투", user_name="User", notes="개인 말투")
    client = FakeClient(
        responses=[
            ("", [ToolCall("clear_memory", {"scope": "server"})]),
            ("내 기억은 지웠어.", []),
        ]
    )
    service = make_service(client)
    sent = []

    async def send(content):
        sent.append(content)

    await enqueue_and_flush(
        service,
        LLMInputMessage("1", "2", "user", "User", "서버 기억이랑 말투 지워줘.", is_admin=False),
        send=send,
        complete=_noop_complete,
    )

    global_memories = await LocalCore.llmGlobalMemoryDataSource.list("1", "2", include_disabled=True)
    user_memories = await LocalCore.llmUserMemoryDataSource.list_for_users("1", "2", ["user"])
    user_styles = await LocalCore.llmSpeechStyleDataSource.list_for_users("1", "2", ["user"])
    assert len(global_memories) == 1  # server memory untouched
    assert user_memories == []
    assert user_styles == []


@pytest.mark.asyncio
async def test_multiple_tool_calls_in_one_round_save_memory_and_style(tmp_path, monkeypatch):
    monkeypatch.setattr(path_module, "db_path", str(tmp_path / "db.sqlite"))
    await LocalCore.init_tables()
    client = FakeClient(
        responses=[
            (
                "",
                [
                    ToolCall("save_memory", {"note": "중요한 설정을 기억", "scope": "user"}),
                    ToolCall("save_style", {"note": "짧고 부드럽게 답한다", "scope": "user"}),
                ],
            ),
            ("저장했어.", []),
        ]
    )
    service = make_service(client)
    sent = []

    async def send(content):
        sent.append(content)

    await enqueue_and_flush(
        service,
        LLMInputMessage("1", "2", "user", "User", "앞으로 중요한 설정을 기억해줘. 말투는 짧고 부드럽게 답해줘.", is_admin=False),
        send=send,
        complete=_noop_complete,
    )

    user_memories = await LocalCore.llmUserMemoryDataSource.list_for_users("1", "2", ["user"])
    user_styles = await LocalCore.llmSpeechStyleDataSource.list_for_users("1", "2", ["user"])
    assert user_memories and user_memories[0].content == "중요한 설정을 기억"
    assert user_styles and user_styles[0].notes.endswith("짧고 부드럽게 답한다")
    assert client.calls == 2


@pytest.mark.asyncio
async def test_admin_server_memory_and_style_tools_persist(tmp_path, monkeypatch):
    monkeypatch.setattr(path_module, "db_path", str(tmp_path / "db.sqlite"))
    await LocalCore.init_tables()
    client = FakeClient(
        responses=[
            (
                "",
                [
                    ToolCall("save_memory", {"note": "회의 내용은 요약해서 기억", "scope": "server"}),
                    ToolCall("save_style", {"note": "차분하게 답한다", "scope": "server"}),
                ],
            ),
            ("서버에 저장했어.", []),
        ]
    )
    service = make_service(client)
    sent = []

    async def send(content):
        sent.append(content)

    await enqueue_and_flush(
        service,
        LLMInputMessage(
            "1", "2", "admin", "Admin",
            "서버 공용 규칙으로 회의 내용은 요약해서 기억해줘. 서버 말투는 차분하게 답해줘.",
            is_admin=True,
        ),
        send=send,
        complete=_noop_complete,
    )

    global_memories = await LocalCore.llmGlobalMemoryDataSource.list("1", "2")
    server_state = await LocalCore.llmServerStateDataSource.get("1", "2")
    assert global_memories and global_memories[0].content == "회의 내용은 요약해서 기억"
    assert server_state.active_style_directive == "차분하게 답한다"


@pytest.mark.asyncio
async def test_plain_chat_makes_no_tool_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(path_module, "db_path", str(tmp_path / "db.sqlite"))
    await LocalCore.init_tables()
    await LocalCore.llmGlobalMemoryDataSource.add("1", "2", "server_memory", "공용 회의는 매주 수요일", 1, "admin")
    client = FakeClient()  # always returns a final answer, no tool_calls
    service = make_service(client)
    sent = []

    async def send(content):
        sent.append(content)

    await enqueue_and_flush(
        service,
        LLMInputMessage("1", "2", "user", "User", "지금 저장된 기억이랑 말투 뭐 있어?", is_admin=False),
        send=send,
        complete=_noop_complete,
    )

    assert client.calls == 1
    assert sent[-1] == "응답"
    global_memories = await LocalCore.llmGlobalMemoryDataSource.list("1", "2")
    assert len(global_memories) == 1  # nothing written


@pytest.mark.asyncio
async def test_non_admin_server_style_tool_downgrades_to_user(tmp_path, monkeypatch):
    monkeypatch.setattr(path_module, "db_path", str(tmp_path / "db.sqlite"))
    await LocalCore.init_tables()
    client = FakeClient(
        responses=[
            ("", [ToolCall("save_style", {"note": "용용체로 답한다", "scope": "server"})]),
            ("개인 말투로 적용했어.", []),
        ]
    )
    service = make_service(client)
    sent = []

    async def send(content):
        sent.append(content)

    await enqueue_and_flush(
        service,
        LLMInputMessage("1", "2", "user", "User", "서버 말투 용용체로 바꿔줘.", is_admin=False),
        send=send,
        complete=_noop_complete,
    )

    server_state = await LocalCore.llmServerStateDataSource.get("1", "2")
    user_styles = await LocalCore.llmSpeechStyleDataSource.list_for_users("1", "2", ["user"])
    assert server_state.active_style_directive == ""
    assert user_styles and user_styles[0].user_id == "user"
    assert "용용체로 답한다" in user_styles[0].notes