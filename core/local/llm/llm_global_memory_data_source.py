import aiosqlite

from core.local.llm._shared import connect, utc_now
from core.local.llm.dto import LLMGlobalMemory


class LLMGlobalMemoryDataSource:
    @staticmethod
    async def init_table() -> None:
        async with connect() as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_global_memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    channel_id TEXT,
                    key TEXT,
                    content TEXT NOT NULL,
                    importance INTEGER,
                    enabled INTEGER NOT NULL,
                    created_by TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
                """
            )
            await db.commit()

    @staticmethod
    async def add(guild_id: str, channel_id: str | None, key: str | None, content: str, importance: int, created_by: str) -> int:
        now = utc_now()
        async with connect() as db:
            cursor = await db.execute(
                """
                INSERT INTO llm_global_memories (
                    guild_id, channel_id, key, content, importance, enabled,
                    created_by, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (guild_id, channel_id, key, content, importance, created_by, now, now),
            )
            await db.commit()
            return int(cursor.lastrowid)

    @staticmethod
    async def list(guild_id: str, channel_id: str | None = None, include_disabled: bool = False) -> list[LLMGlobalMemory]:
        async with connect() as db:
            db.row_factory = aiosqlite.Row
            clauses = ["guild_id = ?"]
            params: list[object] = [guild_id]
            if channel_id is not None:
                clauses.append("(channel_id IS NULL OR channel_id = ?)")
                params.append(channel_id)
            if not include_disabled:
                clauses.append("enabled = 1")
            cursor = await db.execute(
                f"SELECT * FROM llm_global_memories WHERE {' AND '.join(clauses)} ORDER BY importance DESC, id DESC",
                tuple(params),
            )
            rows = await cursor.fetchall()
            return [LLMGlobalMemory(**row) for row in rows]

    @staticmethod
    async def update(memory_id: int, guild_id: str, *, content: str | None = None, key: str | None = None, importance: int | None = None) -> bool:
        fields: list[str] = []
        params: list[object] = []
        if content is not None:
            fields.append("content = ?")
            params.append(content)
        if key is not None:
            fields.append("key = ?")
            params.append(key)
        if importance is not None:
            fields.append("importance = ?")
            params.append(importance)
        if not fields:
            return False
        fields.append("updated_at = ?")
        params.append(utc_now())
        params.extend([memory_id, guild_id])
        async with connect() as db:
            cursor = await db.execute(
                f"UPDATE llm_global_memories SET {', '.join(fields)} WHERE id = ? AND guild_id = ?",
                tuple(params),
            )
            await db.commit()
            return cursor.rowcount > 0

    @staticmethod
    async def set_enabled(memory_id: int, guild_id: str, enabled: bool) -> bool:
        async with connect() as db:
            cursor = await db.execute(
                "UPDATE llm_global_memories SET enabled = ?, updated_at = ? WHERE id = ? AND guild_id = ?",
                (1 if enabled else 0, utc_now(), memory_id, guild_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    @staticmethod
    async def delete(memory_id: int, guild_id: str) -> bool:
        async with connect() as db:
            cursor = await db.execute(
                "DELETE FROM llm_global_memories WHERE id = ? AND guild_id = ?",
                (memory_id, guild_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    @staticmethod
    async def delete_scope(guild_id: str, channel_id: str | None = None) -> int:
        async with connect() as db:
            if channel_id is None:
                cursor = await db.execute(
                    "DELETE FROM llm_global_memories WHERE guild_id = ?",
                    (guild_id,),
                )
            else:
                cursor = await db.execute(
                    """
                    DELETE FROM llm_global_memories
                    WHERE guild_id = ? AND (channel_id IS NULL OR channel_id = ?)
                    """,
                    (guild_id, channel_id),
                )
            await db.commit()
            return cursor.rowcount
