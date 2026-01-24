from typing import Optional, List

import aiosqlite

from core.local import db_path
from core.local.ttsengine.dto import TTSEngineOption


class TTSEngineOptionDataSource:
    @staticmethod
    async def init_table():
        async with aiosqlite.connect(db_path) as db:
            await db.execute("""
                            CREATE TABLE IF NOT EXISTS tbl_tts_engine_option (
                                user_id INTEGER PRIMARY KEY,
                                engine TEXT,
                                model_name TEXT
                            )
                        """)
            await db.commit()

    @staticmethod
    async def get(user_id: int) -> Optional[TTSEngineOption]:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            query = "SELECT * FROM tbl_tts_engine_option WHERE user_id = ?"
            cursor = await db.execute(query, (user_id,))
            row = await cursor.fetchone()
            return TTSEngineOption(**row) if row else None

    @staticmethod
    async def upsert(user_id: int, engine: str, model_name: Optional[str]) -> None:
        if engine not in ("gtts", "ai"):
            raise ValueError("Invalid engine")

        async with aiosqlite.connect(db_path) as db:
            query = """
                INSERT INTO tbl_tts_engine_option (user_id, engine, model_name)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id)
                DO UPDATE SET engine = excluded.engine, model_name = excluded.model_name
            """
            await db.execute(query, (user_id, engine, model_name))
            await db.commit()

    @staticmethod
    async def get_all() -> List[TTSEngineOption]:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            query = "SELECT * FROM tbl_tts_engine_option"
            cursor = await db.execute(query)
            rows = await cursor.fetchall()
            return [TTSEngineOption(**row) for row in rows] if rows else []
