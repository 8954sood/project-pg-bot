from unittest.mock import AsyncMock

import pytest

from core.ban_channel.models import BanChannelConfig
from core.ban_channel.service import BanChannelService


@pytest.mark.asyncio
async def test_service_loads_sets_and_clears_cached_config():
    initial = BanChannelConfig(guild_id=1, channel_id=10, warning_message_id=100)
    data_source = AsyncMock()
    data_source.get_all.return_value = [initial]
    service = BanChannelService(data_source=data_source)

    await service.load_configs()

    assert service.get_config(1) == initial
    assert service.is_configured_channel(1, 10) is True
    assert service.is_configured_channel(1, 11) is False

    updated = await service.set_config(
        guild_id=1,
        channel_id=20,
        warning_message_id=200,
    )
    assert service.get_config(1) == updated
    data_source.upsert.assert_awaited_once_with(updated)

    cleared = await service.clear_config(1)
    assert cleared == updated
    assert service.get_config(1) is None
    data_source.delete.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_clear_channel_only_removes_matching_channel():
    config = BanChannelConfig(guild_id=1, channel_id=10, warning_message_id=100)
    data_source = AsyncMock()
    service = BanChannelService(data_source=data_source)
    service.configs[1] = config

    assert await service.clear_channel(guild_id=1, channel_id=11) is None
    data_source.delete.assert_not_awaited()

    assert await service.clear_channel(guild_id=1, channel_id=10) == config
    data_source.delete.assert_awaited_once_with(1)
