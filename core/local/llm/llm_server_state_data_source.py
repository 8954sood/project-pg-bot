import json

import aiosqlite

from core.llm.models import ServerStyleProfile
from core.local.llm._shared import connect, utc_now
from core.local.llm.dto import LLMServerState


class LLMServerStateDataSource:
    @staticmethod
    async def init_table() -> None:
        async with connect() as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_server_states (
                    guild_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    server_style_summary TEXT NOT NULL,
                    server_style_phrases TEXT NOT NULL,
                    active_style_directive TEXT NOT NULL,
                    relationship_notes TEXT NOT NULL,
                    recent_summary TEXT NOT NULL,
                    updated_at TEXT,
                    PRIMARY KEY (guild_id, channel_id)
                )
                """
            )
            await db.commit()

    @staticmethod
    async def get(guild_id: str, channel_id: str) -> LLMServerState:
        async with connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM llm_server_states WHERE guild_id = ? AND channel_id = ?",
                (guild_id, channel_id),
            )
            row = await cursor.fetchone()
            if row:
                return LLMServerState(**row)
        return LLMServerState(
            guild_id=guild_id,
            channel_id=channel_id,
            server_style_summary=ServerStyleProfile().summary,
            server_style_phrases="[]",
            active_style_directive="",
            relationship_notes="[]",
            recent_summary="",
            updated_at=utc_now(),
        )

    @staticmethod
    async def upsert(
        guild_id: str,
        channel_id: str,
        *,
        server_style_summary: str | None = None,
        server_style_phrases: list[str] | None = None,
        active_style_directive: str | None = None,
        relationship_notes: list[str] | None = None,
        recent_summary: str | None = None,
    ) -> None:
        current = await LLMServerStateDataSource.get(guild_id, channel_id)
        now = utc_now()
        values = (
            guild_id,
            channel_id,
            server_style_summary if server_style_summary is not None else current.server_style_summary,
            json.dumps(server_style_phrases, ensure_ascii=False) if server_style_phrases is not None else current.server_style_phrases,
            active_style_directive if active_style_directive is not None else current.active_style_directive,
            json.dumps(relationship_notes, ensure_ascii=False) if relationship_notes is not None else current.relationship_notes,
            recent_summary if recent_summary is not None else current.recent_summary,
            now,
        )
        async with connect() as db:
            await db.execute(
                """
                INSERT INTO llm_server_states (
                    guild_id, channel_id, server_style_summary, server_style_phrases,
                    active_style_directive, relationship_notes, recent_summary, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, channel_id)
                DO UPDATE SET
                    server_style_summary = excluded.server_style_summary,
                    server_style_phrases = excluded.server_style_phrases,
                    active_style_directive = excluded.active_style_directive,
                    relationship_notes = excluded.relationship_notes,
                    recent_summary = excluded.recent_summary,
                    updated_at = excluded.updated_at
                """,
                values,
            )
            await db.commit()

    @staticmethod
    async def reset_style_and_notes(guild_id: str, channel_id: str) -> None:
        await LLMServerStateDataSource.upsert(
            guild_id,
            channel_id,
            server_style_summary=ServerStyleProfile().summary,
            server_style_phrases=[],
            active_style_directive="",
            relationship_notes=[],
            recent_summary="",
        )
