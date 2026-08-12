from unittest.mock import AsyncMock

import pytest

from core.ban_channel.models import BanChannelConfig
from core.ban_channel.service import (
    MAX_BAN_CHANNELS_PER_GUILD,
    BanChannelLimitReachedError,
    BanChannelService,
)


@pytest.mark.asyncio
async def test_service_loads_and_manages_multiple_cached_configs():
    initial = [
        BanChannelConfig(guild_id=1, channel_id=10, warning_message_id=100),
        BanChannelConfig(guild_id=1, channel_id=20, warning_message_id=200),
    ]
    data_source = AsyncMock()
    data_source.get_all.return_value = initial
    service = BanChannelService(data_source=data_source)

    await service.load_configs()

    assert service.get_configs(1) == initial
    assert service.get_config(1, 10) == initial[0]
    assert service.is_configured_channel(1, 10) is True
    assert service.is_configured_channel(1, 11) is False

    added = await service.set_config(
        guild_id=1,
        channel_id=30,
        warning_message_id=300,
    )
    assert service.get_configs(1) == [*initial, added]
    data_source.upsert.assert_awaited_once_with(added)

    cleared = await service.clear_config(1, 20)
    assert cleared == initial[1]
    assert service.get_configs(1) == [initial[0], added]
    data_source.delete.assert_awaited_once_with(1, 20)


@pytest.mark.asyncio
async def test_service_rejects_a_fourth_channel_but_allows_existing_channel_update():
    data_source = AsyncMock()
    service = BanChannelService(data_source=data_source)

    for channel_id in range(MAX_BAN_CHANNELS_PER_GUILD):
        await service.set_config(
            guild_id=1,
            channel_id=channel_id,
            warning_message_id=channel_id,
        )

    with pytest.raises(BanChannelLimitReachedError):
        await service.set_config(
            guild_id=1,
            channel_id=99,
            warning_message_id=999,
        )

    updated = await service.set_config(
        guild_id=1,
        channel_id=0,
        warning_message_id=100,
    )
    assert len(service.get_configs(1)) == MAX_BAN_CHANNELS_PER_GUILD
    assert service.get_config(1, 0) == updated


@pytest.mark.asyncio
async def test_clear_channel_only_removes_matching_channel_and_can_clear_all():
    first = BanChannelConfig(guild_id=1, channel_id=10, warning_message_id=100)
    second = BanChannelConfig(guild_id=1, channel_id=20, warning_message_id=200)
    data_source = AsyncMock()
    service = BanChannelService(data_source=data_source)
    service.configs[1] = {first.channel_id: first, second.channel_id: second}

    assert await service.clear_channel(guild_id=1, channel_id=11) is None
    data_source.delete.assert_not_awaited()

    assert await service.clear_channel(guild_id=1, channel_id=10) == first
    data_source.delete.assert_awaited_once_with(1, 10)

    assert await service.clear_all_configs(1) == [second]
    assert service.get_configs(1) == []
    data_source.delete_all.assert_awaited_once_with(1)
