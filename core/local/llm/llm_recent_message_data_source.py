import aiosqlite

from core.local.llm._shared import connect, utc_now
from core.local.llm.dto import LLMRecentMessage


class LLMRecentMessageDataSource:
    @staticmethod
    async def init_table() -> None:
        async with connect() as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_recent_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    user_id TEXT,
                    author_name TEXT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            await db.commit()

    @staticmethod
    async def add(guild_id: str, channel_id: str, user_id: str | None, author_name: str | None, role: str, content: str) -> int:
        async with connect() as db:
            cursor = await db.execute(
                """
                INSERT INTO llm_recent_messages (
                    guild_id, channel_id, user_id, author_name, role, content, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (guild_id, channel_id, user_id, author_name, role, content, utc_now()),
            )
            await db.commit()
            return int(cursor.lastrowid)

    @staticmethod
    async def list_recent(guild_id: str, channel_id: str, limit: int) -> list[LLMRecentMessage]:
        async with connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM llm_recent_messages
                WHERE guild_id = ? AND channel_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (guild_id, channel_id, limit),
            )
            rows = await cursor.fetchall()
            return [LLMRecentMessage(**row) for row in reversed(rows)]

    @staticmethod
    async def prune(guild_id: str, channel_id: str, max_count: int) -> None:
        async with connect() as db:
            await db.execute(
                """
                DELETE FROM llm_recent_messages
                WHERE guild_id = ? AND channel_id = ?
                  AND id NOT IN (
                    SELECT id FROM llm_recent_messages
                    WHERE guild_id = ? AND channel_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                  )
                """,
                (guild_id, channel_id, guild_id, channel_id, max_count),
            )
            await db.commit()
