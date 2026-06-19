import asyncio

import pytest

from core.llm.config import LLMSettings, LLMProviderConfig
from core.llm.engine import LLMEngine
from core.llm.llm_client import LLMClientResponse
from core.llm.models import LLMInputMessage, ToolCall
from core.llm.service import LLMService
from core.llm.tools import LLMTool, LLMToolRegistry, ToolContext
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


class FakeWebSearchTool(LLMTool):
    name = "web_search"
    description = "Fake web search tool for service reproduction tests."
    parameters = {"type": "object", "properties": {"query": {"type": "string"}}}

    async def run(self, arguments: dict, ctx: ToolContext) -> str:
        return (
            '웹 검색 결과 query="메이플스토리 신규 캐릭터 최신 정보"\n'
            "1. 메이플스토리 신규 직업 레테\n"
            "   url: https://example.test/maple-lete\n"
            "   engine: fake\n"
            "   description: 신규 직업 레테가 공개되었습니다."
        )


class ContextAwareFakeClient:
    def __init__(self):
        self.calls: list[list[dict]] = []

    async def chat(self, config, messages, *, tools=None, tool_choice="auto"):
        self.calls.append(messages)
        has_tool_result = any(message.get("role") == "tool" for message in messages)
        joined = "\n".join(str(message.get("content", "")) for message in messages)
        current = str(messages[-1].get("content", "")) if messages else ""
        if "던파" in current:
            return LLMClientResponse(
                content="어떤 내용인지 기억이 잘 안 나시나요? 생각나시는 키워드를 말씀해 주시면 같이 찾아볼게요.",
                provider_path="fake",
            )
        if "메이플 신규 캐릭터" in joined and not has_tool_result:
            return LLMClientResponse(
                content="",
                provider_path="fake",
                tool_calls=[ToolCall("web_search", {"query": "메이플스토리 신규 캐릭터 최신 정보"})],
            )
        if has_tool_result:
            return LLMClientResponse(content="메이플 신규 캐릭터는 레테로 보여요.", provider_path="fake")
        return LLMClientResponse(content="응답", provider_path="fake")


class BlockingWebSearchTool(LLMTool):
    name = "web_search"
    description = "Fake blocking web search tool for concurrency reproduction tests."
    parameters = {"type": "object", "properties": {"query": {"type": "string"}}}
    started: asyncio.Event
    release: asyncio.Event

    async def run(self, arguments: dict, ctx: ToolContext) -> str:
        self.started.set()
        await self.release.wait()
        return (
            '웹 검색 결과 query="메이플스토리 신규 캐릭터 최신 정보"\n'
            "1. 메이플스토리 신규 직업 레테\n"
            "   url: https://example.test/maple-lete"
        )


async def no_sleep(_):
    return None


def settings():
    return LLMSettings(
        guild_channel_map={"1": {"2", "3"}},
        main=LLMProviderConfig(api_key="k", model="m"),
        debounce_seconds=0,
    )


def make_service(client):
    registry = LLMToolRegistry()
    engine = LLMEngine(settings(), client=client, tools=registry)
    return LLMService(settings(), engine=engine, tools=registry, sleep=no_sleep)


def make_service_with_tools(client, tool_classes):
    registry = LLMToolRegistry(tools=tool_classes)
    engine = LLMEngine(settings(), client=client, tools=registry)
    return LLMService(settings(), engine=engine, tools=registry, sleep=no_sleep)


async def enqueue_and_flush(service, message, *, send, complete):
    key = (message.guild_id, message.channel_id)
    await service.enqueue_message(message, send_response=send, complete_message=complete)
    service.flush_tasks[key].cancel()
    await service.flush(key, send)


async def _noop_complete():
    return None


@pytest.mark.asyncio
async def test_clear_memory_tool_never_removes_server_memory_or_style(tmp_path, monkeypatch):
    monkeypatch.setattr(path_module, "db_path", str(tmp_path / "db.sqlite"))
    await LocalCore.init_tables()
    await LocalCore.llmGlobalMemoryDataSource.add("1", "2", "server_memory", "공용 기억", 1, "admin")
    await LocalCore.llmServerStateDataSource.upsert("1", "2", active_style_directive="공용 말투")
    await LocalCore.llmUserMemoryDataSource.add("1", "2", "admin", "개인 기억", user_name="Admin")
    client = FakeClient(
        responses=[
            ("", [ToolCall("clear_memory", {"scope": "server"})]),
            ("내 기억만 지웠어.", []),
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
    user_memories = await LocalCore.llmUserMemoryDataSource.list_for_users("1", "2", ["admin"])
    assert client.calls == 2  # tool round + final answer round
    assert sent[-1] == "내 기억만 지웠어."
    assert len(global_memories) == 1
    assert server_state.active_style_directive == "공용 말투"
    assert user_memories == []


@pytest.mark.asyncio
async def test_non_admin_clear_server_tool_only_clears_own_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(path_module, "db_path", str(tmp_path / "db.sqlite"))
    await LocalCore.init_tables()
    await LocalCore.llmGlobalMemoryDataSource.add("1", "2", "server_memory", "공용 기억", 1, "admin")
    await LocalCore.llmUserMemoryDataSource.add("1", "2", "user", "개인 기억", user_name="User")
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
    assert len(global_memories) == 1  # server memory untouched
    assert user_memories == []


@pytest.mark.asyncio
async def test_clear_memory_tool_deletes_only_actor_owned_memory_id(tmp_path, monkeypatch):
    monkeypatch.setattr(path_module, "db_path", str(tmp_path / "db.sqlite"))
    await LocalCore.init_tables()
    own_id = await LocalCore.llmUserMemoryDataSource.add("1", "2", "user", "내 기억", user_name="User")
    other_id = await LocalCore.llmUserMemoryDataSource.add("1", "2", "other", "타인 기억", user_name="Other")
    client = FakeClient(
        responses=[
            ("", [ToolCall("clear_memory", {"memory_id": other_id})]),
            ("타인 기억은 지울 수 없어.", []),
            ("", [ToolCall("clear_memory", {"memory_id": own_id})]),
            ("내 기억은 지웠어.", []),
        ]
    )
    service = make_service(client)
    sent = []

    async def send(content):
        sent.append(content)

    await enqueue_and_flush(
        service,
        LLMInputMessage("1", "2", "user", "User", "저 메모리 지워줘.", is_admin=False),
        send=send,
        complete=_noop_complete,
    )
    assert await LocalCore.llmUserMemoryDataSource.list_for_users("1", "2", ["other"])

    await enqueue_and_flush(
        service,
        LLMInputMessage("1", "2", "user", "User", "내 메모리 지워줘.", is_admin=False),
        send=send,
        complete=_noop_complete,
    )
    assert await LocalCore.llmUserMemoryDataSource.list_for_users("1", "2", ["other"])
    assert await LocalCore.llmUserMemoryDataSource.list_for_users("1", "2", ["user"]) == []


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
                    ToolCall("save_memory", {"note": "짧고 부드럽게 답한다", "scope": "user"}),
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
    assert {memory.content for memory in user_memories} == {"중요한 설정을 기억", "짧고 부드럽게 답한다"}
    assert client.calls == 2


@pytest.mark.asyncio
async def test_server_scope_save_memory_tool_saves_actor_user_memory_only(tmp_path, monkeypatch):
    monkeypatch.setattr(path_module, "db_path", str(tmp_path / "db.sqlite"))
    await LocalCore.init_tables()
    client = FakeClient(
        responses=[
            (
                "",
                [
                    ToolCall("save_memory", {"note": "회의 내용은 요약해서 기억", "scope": "server"}),
                ],
            ),
            ("개인 메모리로 저장했어.", []),
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
    user_memories = await LocalCore.llmUserMemoryDataSource.list_for_users("1", "2", ["admin"])
    server_state = await LocalCore.llmServerStateDataSource.get("1", "2")
    assert global_memories == []
    assert user_memories and user_memories[0].content == "회의 내용은 요약해서 기억"
    assert server_state.active_style_directive == ""


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
async def test_save_style_tool_is_not_registered(tmp_path, monkeypatch):
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
    user_memories = await LocalCore.llmUserMemoryDataSource.list_for_users("1", "2", ["user"])
    assert server_state.active_style_directive == ""
    assert user_memories == []


@pytest.mark.asyncio
async def test_tool_round_empty_final_response_sends_fallback_reply(tmp_path, monkeypatch):
    monkeypatch.setattr(path_module, "db_path", str(tmp_path / "db.sqlite"))
    await LocalCore.init_tables()
    client = FakeClient(
        responses=[
            ("", [ToolCall("web_search", {"query": "메이플스토리 신규 캐릭터 최신 정보"})]),
            ("", []),
            ("어떤 내용인지 기억이 잘 안 나시나요? 생각나시는 키워드를 말씀해 주시면 같이 찾아볼게요.", []),
        ]
    )
    service = make_service_with_tools(client, [FakeWebSearchTool])
    sent = []
    key = ("1", "2")

    async def send(content):
        sent.append(content)

    await enqueue_and_flush(
        service,
        LLMInputMessage("1", "2", "user", "바비호바", "웹에서 메이플 신규 캐릭터 나왔다는데 조사해줘", is_admin=False),
        send=send,
        complete=_noop_complete,
    )
    await enqueue_and_flush(
        service,
        LLMInputMessage("1", "2", "user", "바비호바", "아 던파에서 뭐더라", is_admin=False),
        send=send,
        complete=_noop_complete,
    )

    assert client.calls == 3
    assert service.buffers[key] == []
    assert sent == [
        "응답을 생성하지 못했습니다. 다시 한 번 말씀해 주세요.",
        "어떤 내용인지 기억이 잘 안 나시나요? 생각나시는 키워드를 말씀해 주시면 같이 찾아볼게요.",
    ]


@pytest.mark.asyncio
async def test_second_message_during_tool_call_waits_for_first_flush_and_replies_to_both(tmp_path, monkeypatch):
    monkeypatch.setattr(path_module, "db_path", str(tmp_path / "db.sqlite"))
    await LocalCore.init_tables()
    BlockingWebSearchTool.started = asyncio.Event()
    BlockingWebSearchTool.release = asyncio.Event()
    client = ContextAwareFakeClient()
    service = make_service_with_tools(client, [BlockingWebSearchTool])
    key = ("1", "2")
    sent = []
    completed = 0

    async def send(content):
        sent.append(content)

    async def complete():
        nonlocal completed
        completed += 1

    await service.enqueue_message(
        LLMInputMessage("1", "2", "user", "바비호바", "웹에서 메이플 신규 캐릭터 나왔다는데 조사해줘", is_admin=False),
        send_response=send,
        complete_message=complete,
    )
    await BlockingWebSearchTool.started.wait()
    first_task = service.flush_tasks[key]

    await service.enqueue_message(
        LLMInputMessage("1", "2", "user", "바비호바", "아 던파에서 뭐더라", is_admin=False),
        send_response=send,
        complete_message=complete,
    )
    BlockingWebSearchTool.release.set()
    await first_task
    next_task = service.flush_tasks.get(key)
    if next_task is not None and next_task is not first_task:
        await next_task

    assert sent == [
        "메이플 신규 캐릭터는 레테로 보여요.",
        "어떤 내용인지 기억이 잘 안 나시나요? 생각나시는 키워드를 말씀해 주시면 같이 찾아볼게요.",
    ]
    assert len(client.calls) == 3
    assert completed == 2
