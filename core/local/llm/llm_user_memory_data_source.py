import aiosqlite

from core.local.llm._shared import connect, utc_now
from core.local.llm.dto import LLMUserMemory


class LLMUserMemoryDataSource:
    @staticmethod
    async def init_table() -> None:
        async with connect() as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_user_memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    user_name TEXT NOT NULL,
                    key TEXT,
                    content TEXT NOT NULL,
                    importance INTEGER,
                    enabled INTEGER NOT NULL,
                    created_at TEXT,
                    updated_at TEXT
                )
                """
            )
            await db.commit()

    @staticmethod
    async def add(
        guild_id: str,
        channel_id: str,
        user_id: str,
        content: str,
        importance: int = 1,
        key: str | None = None,
        user_name: str | None = None,
    ) -> int:
        now = utc_now()
        async with connect() as db:
            cursor = await db.execute(
                """
                INSERT INTO llm_user_memories (
                    guild_id, channel_id, user_id, user_name, key, content, importance,
                    enabled, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (guild_id, channel_id, user_id, user_name or user_id, key, content, importance, now, now),
            )
            await db.commit()
            return int(cursor.lastrowid)

    @staticmethod
    async def list_for_users(guild_id: str, channel_id: str, user_ids: list[str]) -> list[LLMUserMemory]:
        if not user_ids:
            return []
        placeholders = ",".join("?" for _ in user_ids)
        async with connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                f"""
                SELECT * FROM llm_user_memories
                WHERE guild_id = ? AND channel_id = ? AND enabled = 1
                  AND user_id IN ({placeholders})
                ORDER BY importance DESC, id DESC
                """,
                (guild_id, channel_id, *user_ids),
            )
            rows = await cursor.fetchall()
            return [LLMUserMemory(**row) for row in rows]

    @staticmethod
    async def delete_user(guild_id: str, channel_id: str, user_id: str) -> int:
        async with connect() as db:
            cursor = await db.execute(
                """
                DELETE FROM llm_user_memories
                WHERE guild_id = ? AND channel_id = ? AND user_id = ?
                """,
                (guild_id, channel_id, user_id),
            )
            await db.commit()
            return cursor.rowcount
