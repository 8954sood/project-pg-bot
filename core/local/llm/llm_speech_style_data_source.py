import aiosqlite

from core.local.llm._shared import connect, utc_now
from core.local.llm.dto import LLMSpeechStyle


class LLMSpeechStyleDataSource:
    @staticmethod
    async def init_table() -> None:
        async with connect() as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_user_speech_styles (
                    guild_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    user_name TEXT NOT NULL,
                    notes TEXT NOT NULL,
                    phrases TEXT NOT NULL,
                    style_summary TEXT NOT NULL,
                    updated_at TEXT,
                    PRIMARY KEY (guild_id, channel_id, user_id)
                )
                """
            )
            await db.commit()

    @staticmethod
    async def upsert(
        guild_id: str,
        channel_id: str,
        user_id: str,
        style_summary: str = "",
        *,
        user_name: str | None = None,
        notes: str = "",
        phrases: str = "",
    ) -> None:
        now = utc_now()
        async with connect() as db:
            await db.execute(
                """
                INSERT INTO llm_user_speech_styles (
                    guild_id, channel_id, user_id, user_name, notes, phrases,
                    style_summary, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, channel_id, user_id)
                DO UPDATE SET
                    user_name = excluded.user_name,
                    notes = excluded.notes,
                    phrases = excluded.phrases,
                    style_summary = excluded.style_summary,
                    updated_at = excluded.updated_at
                """,
                (guild_id, channel_id, user_id, user_name or user_id, notes, phrases, style_summary or notes, now),
            )
            await db.commit()

    @staticmethod
    async def list_for_users(guild_id: str, channel_id: str, user_ids: list[str]) -> list[LLMSpeechStyle]:
        if not user_ids:
            return []
        placeholders = ",".join("?" for _ in user_ids)
        async with connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                f"""
                SELECT * FROM llm_user_speech_styles
                WHERE guild_id = ? AND channel_id = ? AND user_id IN ({placeholders})
                """,
                (guild_id, channel_id, *user_ids),
            )
            rows = await cursor.fetchall()
            return [LLMSpeechStyle(**row) for row in rows]

    @staticmethod
    async def delete_user(guild_id: str, channel_id: str, user_id: str) -> int:
        async with connect() as db:
            cursor = await db.execute(
                """
                DELETE FROM llm_user_speech_styles
                WHERE guild_id = ? AND channel_id = ? AND user_id = ?
                """,
                (guild_id, channel_id, user_id),
            )
            await db.commit()
            return cursor.rowcount
