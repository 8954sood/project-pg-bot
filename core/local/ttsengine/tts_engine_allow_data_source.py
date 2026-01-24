from typing import List

import aiosqlite

from core.local import db_path
from core.local.ttsengine.dto import TTSEngineAllow


class TTSEngineAllowDataSource:
    @staticmethod
    async def init_table():
        async with aiosqlite.connect(db_path) as db:
            await db.execute("""
                            CREATE TABLE IF NOT EXISTS tbl_tts_engine_allow (
                                user_id INTEGER PRIMARY KEY
                            )
                        """)
            await db.commit()

    @staticmethod
    async def add(user_id: int) -> None:
        async with aiosqlite.connect(db_path) as db:
            query = """
                INSERT INTO tbl_tts_engine_allow (user_id)
                VALUES (?)
                ON CONFLICT(user_id) DO NOTHING
            """
            await db.execute(query, (user_id,))
            await db.commit()

    @staticmethod
    async def remove(user_id: int) -> None:
        async with aiosqlite.connect(db_path) as db:
            query = "DELETE FROM tbl_tts_engine_allow WHERE user_id = ?"
            await db.execute(query, (user_id,))
            await db.commit()

    @staticmethod
    async def get_all() -> List[TTSEngineAllow]:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            query = "SELECT * FROM tbl_tts_engine_allow"
            cursor = await db.execute(query)
            rows = await cursor.fetchall()
            return [TTSEngineAllow(**row) for row in rows] if rows else []
