from typing import Optional

import aiosqlite

from core.ban_channel.models import BanChannelConfig
from core.local import db_path


class BanChannelDataSource:
    @staticmethod
    async def init_table() -> None:
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS tbl_ban_channel (
                    guild_id INTEGER PRIMARY KEY,
                    channel_id INTEGER NOT NULL,
                    warning_message_id INTEGER
                )
                """
            )
            await db.commit()

    @staticmethod
    async def get(guild_id: int) -> Optional[BanChannelConfig]:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT guild_id, channel_id, warning_message_id
                FROM tbl_ban_channel
                WHERE guild_id = ?
                """,
                (guild_id,),
            )
            row = await cursor.fetchone()
            return BanChannelConfig(**row) if row else None

    @staticmethod
    async def get_all() -> list[BanChannelConfig]:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT guild_id, channel_id, warning_message_id
                FROM tbl_ban_channel
                """
            )
            rows = await cursor.fetchall()
            return [BanChannelConfig(**row) for row in rows]

    @staticmethod
    async def upsert(config: BanChannelConfig) -> None:
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                """
                INSERT INTO tbl_ban_channel (
                    guild_id, channel_id, warning_message_id
                )
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id)
                DO UPDATE SET
                    channel_id = excluded.channel_id,
                    warning_message_id = excluded.warning_message_id
                """,
                (
                    config.guild_id,
                    config.channel_id,
                    config.warning_message_id,
                ),
            )
            await db.commit()

    @staticmethod
    async def delete(guild_id: int) -> bool:
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                "DELETE FROM tbl_ban_channel WHERE guild_id = ?",
                (guild_id,),
            )
            await db.commit()
            return cursor.rowcount > 0
