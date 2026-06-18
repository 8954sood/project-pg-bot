from typing import Optional

import aiosqlite

from core.local.llm._shared import connect, utc_now
from core.local.llm.dto import LLMConsent


class LLMConsentDataSource:
    @staticmethod
    async def init_table() -> None:
        async with connect() as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_consents (
                    guild_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    consented INTEGER NOT NULL,
                    consent_version TEXT NOT NULL,
                    consented_at TEXT,
                    declined_at TEXT,
                    updated_at TEXT,
                    PRIMARY KEY (guild_id, channel_id, user_id, consent_version)
                )
                """
            )
            await db.commit()

    @staticmethod
    async def get(guild_id: str, channel_id: str, user_id: str, consent_version: str) -> Optional[LLMConsent]:
        async with connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM llm_consents
                WHERE guild_id = ? AND channel_id = ? AND user_id = ? AND consent_version = ?
                """,
                (guild_id, channel_id, user_id, consent_version),
            )
            row = await cursor.fetchone()
            return LLMConsent(**row) if row else None

    @staticmethod
    async def set(guild_id: str, channel_id: str, user_id: str, consent_version: str, consented: bool) -> None:
        now = utc_now()
        async with connect() as db:
            await db.execute(
                """
                INSERT INTO llm_consents (
                    guild_id, channel_id, user_id, consented, consent_version,
                    consented_at, declined_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, channel_id, user_id, consent_version)
                DO UPDATE SET
                    consented = excluded.consented,
                    consented_at = excluded.consented_at,
                    declined_at = excluded.declined_at,
                    updated_at = excluded.updated_at
                """,
                (
                    guild_id,
                    channel_id,
                    user_id,
                    1 if consented else 0,
                    consent_version,
                    now if consented else None,
                    None if consented else now,
                    now,
                ),
            )
            await db.commit()
