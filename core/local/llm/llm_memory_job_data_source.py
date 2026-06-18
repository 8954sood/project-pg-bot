import aiosqlite

from core.local.llm._shared import connect, utc_now
from core.local.llm.dto import LLMMemoryJobState


class LLMMemoryJobDataSource:
    @staticmethod
    async def init_table() -> None:
        async with connect() as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_memory_jobs (
                    guild_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    running INTEGER NOT NULL,
                    pending_job_id TEXT,
                    started_at TEXT,
                    turns_since_last_memory_extraction INTEGER NOT NULL,
                    memory_extraction_cooldown_turns INTEGER NOT NULL,
                    last_memory_extraction_had_changes INTEGER NOT NULL,
                    updated_at TEXT,
                    PRIMARY KEY (guild_id, channel_id)
                )
                """
            )
            await db.commit()

    @staticmethod
    async def get(guild_id: str, channel_id: str) -> LLMMemoryJobState:
        async with connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM llm_memory_jobs WHERE guild_id = ? AND channel_id = ?",
                (guild_id, channel_id),
            )
            row = await cursor.fetchone()
            if row:
                return LLMMemoryJobState(**row)
        return LLMMemoryJobState(guild_id, channel_id, 0, None, None, 0, 0, 0, utc_now())

    @staticmethod
    async def set_running(guild_id: str, channel_id: str, running: bool, pending_job_id: str | None = None) -> None:
        now = utc_now()
        async with connect() as db:
            await db.execute(
                """
                INSERT INTO llm_memory_jobs (
                    guild_id, channel_id, running, pending_job_id, started_at,
                    turns_since_last_memory_extraction, memory_extraction_cooldown_turns,
                    last_memory_extraction_had_changes, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 0, 0, 0, ?)
                ON CONFLICT(guild_id, channel_id)
                DO UPDATE SET
                    running = excluded.running,
                    pending_job_id = excluded.pending_job_id,
                    started_at = excluded.started_at,
                    updated_at = excluded.updated_at
                """,
                (guild_id, channel_id, 1 if running else 0, pending_job_id, now if running else None, now),
            )
            await db.commit()

    @staticmethod
    async def complete(guild_id: str, channel_id: str, had_changes: bool, cooldown_turns: int) -> None:
        async with connect() as db:
            await db.execute(
                """
                INSERT INTO llm_memory_jobs (
                    guild_id, channel_id, running, pending_job_id, started_at,
                    turns_since_last_memory_extraction, memory_extraction_cooldown_turns,
                    last_memory_extraction_had_changes, updated_at
                )
                VALUES (?, ?, 0, NULL, NULL, 0, ?, ?, ?)
                ON CONFLICT(guild_id, channel_id)
                DO UPDATE SET
                    running = 0,
                    pending_job_id = NULL,
                    started_at = NULL,
                    turns_since_last_memory_extraction = 0,
                    memory_extraction_cooldown_turns = excluded.memory_extraction_cooldown_turns,
                    last_memory_extraction_had_changes = excluded.last_memory_extraction_had_changes,
                    updated_at = excluded.updated_at
                """,
                (guild_id, channel_id, cooldown_turns, 1 if had_changes else 0, utc_now()),
            )
            await db.commit()

    @staticmethod
    async def increment_turns(guild_id: str, channel_id: str) -> LLMMemoryJobState:
        state = await LLMMemoryJobDataSource.get(guild_id, channel_id)
        now = utc_now()
        cooldown = max(0, state.memory_extraction_cooldown_turns - 1)
        turns = state.turns_since_last_memory_extraction + 1
        async with connect() as db:
            await db.execute(
                """
                INSERT INTO llm_memory_jobs (
                    guild_id, channel_id, running, pending_job_id, started_at,
                    turns_since_last_memory_extraction, memory_extraction_cooldown_turns,
                    last_memory_extraction_had_changes, updated_at
                )
                VALUES (?, ?, 0, NULL, NULL, ?, ?, ?, ?)
                ON CONFLICT(guild_id, channel_id)
                DO UPDATE SET
                    turns_since_last_memory_extraction = excluded.turns_since_last_memory_extraction,
                    memory_extraction_cooldown_turns = excluded.memory_extraction_cooldown_turns,
                    updated_at = excluded.updated_at
                """,
                (guild_id, channel_id, turns, cooldown, state.last_memory_extraction_had_changes, now),
            )
            await db.commit()
        return await LLMMemoryJobDataSource.get(guild_id, channel_id)
