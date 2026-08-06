import pytest

from core.ban_channel.models import BanChannelConfig
from core.local.ban_channel import ban_channel_data_source as data_source_module
from core.local.ban_channel import BanChannelDataSource


@pytest.mark.asyncio
async def test_ban_channel_data_source_crud_uses_one_channel_per_guild(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        data_source_module,
        "db_path",
        str(tmp_path / "ban-channel.db"),
    )
    await BanChannelDataSource.init_table()

    first = BanChannelConfig(
        guild_id=1,
        channel_id=10,
        warning_message_id=100,
    )
    second = BanChannelConfig(
        guild_id=1,
        channel_id=20,
        warning_message_id=200,
    )

    await BanChannelDataSource.upsert(first)
    assert await BanChannelDataSource.get(1) == first

    await BanChannelDataSource.upsert(second)
    assert await BanChannelDataSource.get(1) == second
    assert await BanChannelDataSource.get_all() == [second]

    assert await BanChannelDataSource.delete(1) is True
    assert await BanChannelDataSource.delete(1) is False
    assert await BanChannelDataSource.get(1) is None
