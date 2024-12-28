from typing import Optional, List

import aiosqlite
from core.local import db_path
from core.local.tts.dto import TTSModel


class TTSDataSource:

    @staticmethod
    async def init_table():
        async with aiosqlite.connect(db_path) as db:
            # 테이블 존재 확인 및 생성
            await db.execute("""
                            CREATE TABLE IF NOT EXISTS tbl_tts (
                                guild_id INTEGER PRIMARY KEY,
                                channel_id INTEGER -- 추가 필드 정의
                            )
                        """)
            await db.commit()

    @staticmethod
    async def get(guild_id: int) -> Optional[TTSModel]:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            query = f"SELECT * FROM tbl_tts WHERE guild_id = ?"
            tu = (guild_id,)
            cursor = await db.execute(query, tu)
            row = await cursor.fetchone()
            if row:
                return TTSModel(**row)
            else:
                return None

    @staticmethod
    async def update(guild_id: int, channel_id: int) -> None:
        async with aiosqlite.connect(db_path) as db:
            query = f"UPDATE tbl_tts SET channel_id = ? WHERE guild_id = ?"
            tu = (channel_id, guild_id)
            await db.execute(query, tu)
            await db.commit()

    @staticmethod
    async def insert(guild_id: int, channel_id: int) -> None:
        async with aiosqlite.connect(db_path) as db:
            query = f"INSERT INTO tbl_tts VALUES (?, ?)"
            tu = (guild_id, channel_id)
            await db.execute(query, tu)
            await db.commit()

    @staticmethod
    async def get_all() -> List[TTSModel]:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            query = f"SELECT * FROM tbl_tts"
            cursor = await db.execute(query)
            rows = await cursor.fetchall()
            return [TTSModel(**row) for row in rows] if rows else []
