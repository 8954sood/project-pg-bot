from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.llm.config import LLMSettings
from core.local import LocalCore
from ui.llm.cog import LLMCog
from ui.llm.consent_view import LLMConsentView


class FakeSentMessage:
    def __init__(self):
        self.edits = []

    async def edit(self, **kwargs):
        self.edits.append(kwargs)


class FakeInteractionResponse:
    def __init__(self):
        self.sent = []

    async def send_message(self, *args, **kwargs):
        self.sent.append((args, kwargs))


class FakeInteraction:
    def __init__(self, user_id=3):
        self.guild = SimpleNamespace(id=1)
        self.channel = SimpleNamespace(id=2)
        self.user = SimpleNamespace(id=user_id, display_name="User")
        self.response = FakeInteractionResponse()


class FakeChannel:
    id = 2

    def __init__(self):
        self.typing_calls = 0
        self.sent = []
        self.sent_messages = []

    async def typing(self):
        self.typing_calls += 1

    async def send(self, *args, **kwargs):
        self.sent.append((args, kwargs))
        sent_message = FakeSentMessage()
        self.sent_messages.append(sent_message)
        return sent_message


def make_message(*, guild_id=1, channel=None, author_bot=False, webhook_id=None):
    channel = channel or FakeChannel()
    return SimpleNamespace(
        guild=SimpleNamespace(id=guild_id),
        channel=channel,
        author=SimpleNamespace(
            id=3,
            bot=author_bot,
            mention="<@3>",
            display_name="User",
            guild_permissions=SimpleNamespace(administrator=False),
        ),
        webhook_id=webhook_id,
        clean_content="hello",
        content="hello",
    )


def make_cog():
    bot = SimpleNamespace(user=SimpleNamespace(id=999))
    cog = LLMCog(bot, LLMSettings(guild_channel_map={"1": {"2"}}, debounce_seconds=0))
    cog.service = SimpleNamespace(enqueue_message=AsyncMock())
    return cog


@pytest.mark.asyncio
async def test_disallowed_message_does_not_start_typing():
    cog = make_cog()
    channel = FakeChannel()

    await cog.on_message(make_message(guild_id=9, channel=channel))

    assert channel.typing_calls == 0
    cog.service.enqueue_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_unconsented_message_sends_embed_and_not_llm(monkeypatch):
    cog = make_cog()
    channel = FakeChannel()
    monkeypatch.setattr(LocalCore, "llmConsentDataSource", SimpleNamespace(get=AsyncMock(return_value=None)))

    await cog.on_message(make_message(channel=channel))

    assert channel.typing_calls == 0
    assert channel.sent
    assert channel.sent[0][1]["embed"].title == "LLM 메모리 봇 사용 안내"
    assert channel.sent[0][1]["view"].message is channel.sent_messages[0]
    cog.service.enqueue_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_consent_view_timeout_edits_message():
    view = LLMConsentView(guild_id="1", channel_id="2", user_id="3", consent_version="v1")
    message = FakeSentMessage()
    view.message = message

    await view.on_timeout()

    assert message.edits
    assert message.edits[0]["embed"].title == "LLM 메모리 봇 사용 안내 만료"
    assert message.edits[0]["view"] is view


@pytest.mark.asyncio
async def test_consent_accept_edits_source_message(monkeypatch):
    data_source = SimpleNamespace(set=AsyncMock())
    monkeypatch.setattr(LocalCore, "llmConsentDataSource", data_source)
    view = LLMConsentView(guild_id="1", channel_id="2", user_id="3", consent_version="v1")
    message = FakeSentMessage()
    view.message = message

    await view.accept_consent()

    data_source.set.assert_awaited_once_with("1", "2", "3", "v1", True)
    assert message.edits[0]["embed"].title == "LLM 메모리 봇 동의 완료"
    assert message.edits[0]["view"] is view


@pytest.mark.asyncio
async def test_consent_decline_edits_source_message(monkeypatch):
    data_source = SimpleNamespace(set=AsyncMock())
    monkeypatch.setattr(LocalCore, "llmConsentDataSource", data_source)
    view = LLMConsentView(guild_id="1", channel_id="2", user_id="3", consent_version="v1")
    message = FakeSentMessage()
    view.message = message

    await view.decline_consent()

    data_source.set.assert_awaited_once_with("1", "2", "3", "v1", False)
    assert message.edits[0]["embed"].title == "LLM 메모리 봇 비동의 완료"
    assert message.edits[0]["view"] is view

@pytest.mark.asyncio
async def test_consented_message_is_queued(monkeypatch):
    cog = make_cog()
    channel = FakeChannel()
    consent = SimpleNamespace(consented=1)
    monkeypatch.setattr(LocalCore, "llmConsentDataSource", SimpleNamespace(get=AsyncMock(return_value=consent)))

    await cog.on_message(make_message(channel=channel))

    assert channel.typing_calls == 1
    cog.service.enqueue_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_my_memory_commands_use_actor_identity(monkeypatch):
    cog = make_cog()
    data_source = SimpleNamespace(
        list_user=AsyncMock(return_value=[]),
        add=AsyncMock(return_value=10),
        update_user_memory=AsyncMock(return_value=True),
        delete_user_memory=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(LocalCore, "llmUserMemoryDataSource", data_source)
    interaction = FakeInteraction(user_id=42)

    await cog.list_my_memory.callback(cog, interaction)
    data_source.list_user.assert_awaited_once_with("1", "2", "42", include_disabled=True)

    await cog.add_my_memory.callback(cog, interaction, "내 메모리", key="k")
    data_source.add.assert_awaited_once_with("1", "2", "42", "내 메모리", key="k", user_name="User")

    await cog.edit_my_memory.callback(cog, interaction, 10, "수정", key=None)
    data_source.update_user_memory.assert_awaited_once_with(10, "1", "2", "42", content="수정", key=None)

    await cog.delete_my_memory.callback(cog, interaction, 10)
    data_source.delete_user_memory.assert_awaited_once_with(10, "1", "2", "42")
