from typing import Optional

import aiosqlite

from core.ban_channel.models import BanChannelConfig
from core.local import db_path


class BanChannelDataSource:
    @staticmethod
    async def init_table() -> None:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS tbl_ban_channel (
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    warning_message_id INTEGER,
                    PRIMARY KEY (guild_id, channel_id)
                )
                """
            )

            cursor = await db.execute("PRAGMA table_info(tbl_ban_channel)")
            columns = await cursor.fetchall()
            primary_key_columns = [
                column["name"]
                for column in sorted(columns, key=lambda column: column["pk"])
                if column["pk"]
            ]
            if primary_key_columns == ["guild_id"]:
                await db.execute(
                    "ALTER TABLE tbl_ban_channel RENAME TO tbl_ban_channel_legacy"
                )
                await db.execute(
                    """
                    CREATE TABLE tbl_ban_channel (
                        guild_id INTEGER NOT NULL,
                        channel_id INTEGER NOT NULL,
                        warning_message_id INTEGER,
                        PRIMARY KEY (guild_id, channel_id)
                    )
                    """
                )
                await db.execute(
                    """
                    INSERT INTO tbl_ban_channel (
                        guild_id, channel_id, warning_message_id
                    )
                    SELECT guild_id, channel_id, warning_message_id
                    FROM tbl_ban_channel_legacy
                    """
                )
                await db.execute("DROP TABLE tbl_ban_channel_legacy")
            await db.commit()

    @staticmethod
    async def get(
        guild_id: int,
        channel_id: int,
    ) -> Optional[BanChannelConfig]:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT guild_id, channel_id, warning_message_id
                FROM tbl_ban_channel
                WHERE guild_id = ? AND channel_id = ?
                """,
                (guild_id, channel_id),
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
                ORDER BY guild_id, channel_id
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
                ON CONFLICT(guild_id, channel_id)
                DO UPDATE SET
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
    async def delete(guild_id: int, channel_id: int) -> bool:
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                """
                DELETE FROM tbl_ban_channel
                WHERE guild_id = ? AND channel_id = ?
                """,
                (guild_id, channel_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    @staticmethod
    async def delete_all(guild_id: int) -> bool:
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                "DELETE FROM tbl_ban_channel WHERE guild_id = ?",
                (guild_id,),
            )
            await db.commit()
            return cursor.rowcount > 0
