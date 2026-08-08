import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest
from discord.ext.commands import CommandError

from ui.ban_channel.cog import BanChannelCog


def make_message(*, administrator=False, bot=False, webhook_id=None):
    guild = SimpleNamespace(
        id=1,
        name="Test Guild",
        owner_id=999,
        me=SimpleNamespace(top_role=10),
        ban=AsyncMock(),
    )
    channel = Mock(spec=discord.TextChannel)
    channel.id = 20
    author = SimpleNamespace(
        id=2,
        bot=bot,
        guild_permissions=SimpleNamespace(administrator=administrator),
        guild=guild,
        top_role=1,
        send=AsyncMock(),
    )
    message = SimpleNamespace(
        guild=guild,
        channel=channel,
        type=discord.MessageType.default,
        webhook_id=webhook_id,
        author=author,
        delete=AsyncMock(),
    )
    return message


def make_cog(*, configured=True):
    service = SimpleNamespace(
        is_configured_channel=Mock(return_value=configured),
    )
    return BanChannelCog(SimpleNamespace(), service), service


def make_interaction_and_channel():
    bot_member = SimpleNamespace(
        guild_permissions=SimpleNamespace(ban_members=True),
    )
    guild = SimpleNamespace(
        id=1,
        me=bot_member,
        get_channel=Mock(return_value=None),
    )
    interaction = SimpleNamespace(
        guild=guild,
        response=SimpleNamespace(send_message=AsyncMock()),
    )
    permissions = SimpleNamespace(
        view_channel=True,
        send_messages=True,
        embed_links=True,
        manage_messages=True,
    )
    warning_message = SimpleNamespace(id=100, delete=AsyncMock())
    channel = Mock(spec=discord.TextChannel)
    channel.id = 20
    channel.mention = "#trap"
    channel.permissions_for = Mock(return_value=permissions)
    channel.send = AsyncMock(return_value=warning_message)
    return interaction, channel, warning_message


def test_command_group_is_hidden_from_non_admins_by_default():
    group = BanChannelCog.ban_channel

    assert group.guild_only is True
    assert group.default_permissions.administrator is True
    assert {command.name for command in group.commands} == {"set", "clear", "status"}


@pytest.mark.asyncio
async def test_every_subcommand_has_runtime_admin_check():
    non_admin = SimpleNamespace(
        user=SimpleNamespace(
            guild_permissions=SimpleNamespace(administrator=False),
        )
    )
    admin = SimpleNamespace(
        user=SimpleNamespace(
            guild_permissions=SimpleNamespace(administrator=True),
        )
    )

    for command in BanChannelCog.ban_channel.commands:
        assert command.checks
        with pytest.raises(CommandError):
            await command.checks[0](non_admin)
        assert await command.checks[0](admin) is True


@pytest.mark.asyncio
async def test_set_command_posts_warning_and_persists_config():
    service = SimpleNamespace(
        get_config=Mock(return_value=None),
        set_config=AsyncMock(),
    )
    cog = BanChannelCog(SimpleNamespace(), service)
    interaction, channel, warning_message = make_interaction_and_channel()
    command = BanChannelCog.ban_channel.get_command("set")

    await command.callback(cog, interaction, channel)

    channel.send.assert_awaited_once()
    assert isinstance(channel.send.await_args.kwargs["embed"], discord.Embed)
    service.set_config.assert_awaited_once_with(
        guild_id=1,
        channel_id=20,
        warning_message_id=warning_message.id,
    )
    interaction.response.send_message.assert_awaited_once_with(
        "자동 밴 채널을 #trap로 설정했습니다.",
        ephemeral=True,
    )


@pytest.mark.asyncio
async def test_non_admin_message_bans_without_deleting_history_then_deletes_trigger():
    cog, service = make_cog()
    message = make_message()

    await cog.on_message(message)

    service.is_configured_channel.assert_called_once_with(1, 20)
    message.guild.ban.assert_awaited_once_with(
        message.author,
        delete_message_seconds=0,
        reason="자동 밴 채널 메시지 작성 (channel_id=20)",
    )
    message.author.send.assert_awaited_once()
    embed = message.author.send.await_args.kwargs["embed"]
    assert isinstance(embed, discord.Embed)
    assert "자동으로 차단" in embed.title
    assert "babihoba" in embed.fields[1].value
    assert "lindendong" in embed.fields[1].value
    message.delete.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_dm_failure_does_not_cancel_successful_ban():
    cog, _ = make_cog()
    message = make_message()
    response = SimpleNamespace(status=403, reason="Forbidden")
    message.author.send.side_effect = discord.Forbidden(response, "blocked")

    await cog.on_message(message)

    message.guild.ban.assert_awaited_once()
    message.author.send.assert_awaited_once()
    message.delete.assert_awaited_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("administrator", "bot", "webhook_id"),
    [
        (True, False, None),
        (False, True, None),
        (False, False, 123),
    ],
)
async def test_admin_bot_and_webhook_messages_are_ignored(
    administrator,
    bot,
    webhook_id,
):
    cog, service = make_cog()
    message = make_message(
        administrator=administrator,
        bot=bot,
        webhook_id=webhook_id,
    )

    await cog.on_message(message)

    service.is_configured_channel.assert_not_called()
    message.guild.ban.assert_not_awaited()
    message.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_message_outside_configured_channel_is_ignored():
    cog, service = make_cog(configured=False)
    message = make_message()

    await cog.on_message(message)

    service.is_configured_channel.assert_called_once_with(1, 20)
    message.guild.ban.assert_not_awaited()
    message.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_dm_thread_and_system_messages_are_ignored():
    cog, service = make_cog()

    dm_message = make_message()
    dm_message.guild = None

    thread_message = make_message()
    thread_message.channel = Mock(spec=discord.Thread)
    thread_message.channel.id = 20

    system_message = make_message()
    system_message.type = discord.MessageType.pins_add

    for message in (dm_message, thread_message, system_message):
        await cog.on_message(message)

    service.is_configured_channel.assert_not_called()
    for message in (dm_message, thread_message, system_message):
        if message.guild is not None:
            message.guild.ban.assert_not_awaited()
        message.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_messages_share_one_pending_ban():
    cog, _ = make_cog()
    first = make_message()
    second = make_message()
    second.guild = first.guild
    second.author = first.author
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed_ban(member, channel_id):
        started.set()
        await release.wait()
        return True

    cog._ban_member = AsyncMock(side_effect=delayed_ban)

    first_task = asyncio.create_task(cog.on_message(first))
    await started.wait()
    second_task = asyncio.create_task(cog.on_message(second))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(first_task, second_task)

    cog._ban_member.assert_awaited_once_with(first.author, 20)
    first.delete.assert_awaited_once_with()
    second.delete.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_role_hierarchy_failure_does_not_delete_message():
    cog, _ = make_cog()
    message = make_message()
    message.author.top_role = 10

    await cog.on_message(message)

    message.guild.ban.assert_not_awaited()
    message.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_discord_api_failure_does_not_delete_message():
    cog, _ = make_cog()
    message = make_message()
    response = SimpleNamespace(status=500, reason="Server Error")
    message.guild.ban.side_effect = discord.HTTPException(response, "boom")

    await cog.on_message(message)

    message.guild.ban.assert_awaited_once()
    message.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_deleted_configured_channel_is_cleared():
    service = SimpleNamespace(
        clear_channel=AsyncMock(return_value=object()),
    )
    cog = BanChannelCog(SimpleNamespace(), service)
    channel = SimpleNamespace(
        id=20,
        guild=SimpleNamespace(id=1),
    )

    await cog.on_guild_channel_delete(channel)

    service.clear_channel.assert_awaited_once_with(guild_id=1, channel_id=20)
