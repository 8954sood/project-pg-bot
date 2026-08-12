import aiosqlite
import pytest

from core.ban_channel.models import BanChannelConfig
from core.local.ban_channel import ban_channel_data_source as data_source_module
from core.local.ban_channel import BanChannelDataSource


@pytest.mark.asyncio
async def test_ban_channel_data_source_crud_supports_three_channels_per_guild(
    tmp_path,
    monkeypatch,
):
    database_path = str(tmp_path / "ban-channel.db")
    monkeypatch.setattr(data_source_module, "db_path", database_path)
    await BanChannelDataSource.init_table()

    first = BanChannelConfig(guild_id=1, channel_id=10, warning_message_id=100)
    second = BanChannelConfig(guild_id=1, channel_id=20, warning_message_id=200)
    third = BanChannelConfig(guild_id=1, channel_id=30, warning_message_id=300)

    for config in (first, second, third):
        await BanChannelDataSource.upsert(config)

    assert await BanChannelDataSource.get(1, 10) == first
    assert await BanChannelDataSource.get(1, 20) == second
    assert await BanChannelDataSource.get_all() == [first, second, third]

    updated_first = BanChannelConfig(guild_id=1, channel_id=10, warning_message_id=101)
    await BanChannelDataSource.upsert(updated_first)
    assert await BanChannelDataSource.get(1, 10) == updated_first

    assert await BanChannelDataSource.delete(1, 20) is True
    assert await BanChannelDataSource.delete(1, 20) is False
    assert await BanChannelDataSource.get_all() == [updated_first, third]

    assert await BanChannelDataSource.delete_all(1) is True
    assert await BanChannelDataSource.delete_all(1) is False
    assert await BanChannelDataSource.get_all() == []


@pytest.mark.asyncio
async def test_init_table_migrates_existing_single_channel_configuration(
    tmp_path,
    monkeypatch,
):
    database_path = str(tmp_path / "ban-channel.db")
    monkeypatch.setattr(data_source_module, "db_path", database_path)
    async with aiosqlite.connect(database_path) as db:
        await db.execute(
            """
            CREATE TABLE tbl_ban_channel (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                warning_message_id INTEGER
            )
            """
        )
        await db.execute(
            """
            INSERT INTO tbl_ban_channel (guild_id, channel_id, warning_message_id)
            VALUES (1, 10, 100)
            """
        )
        await db.commit()

    await BanChannelDataSource.init_table()

    existing = BanChannelConfig(guild_id=1, channel_id=10, warning_message_id=100)
    added = BanChannelConfig(guild_id=1, channel_id=20, warning_message_id=200)
    assert await BanChannelDataSource.get(1, 10) == existing

    await BanChannelDataSource.upsert(added)
    assert await BanChannelDataSource.get_all() == [existing, added]
