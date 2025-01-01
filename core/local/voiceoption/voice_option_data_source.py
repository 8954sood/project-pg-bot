import aiosqlite
from typing import Optional, List

import gtts

from core.local import db_path
from core.local.voiceoption import VoiceOption


class VoiceOptionDataSource:

    @staticmethod
    async def init_table():
        async with aiosqlite.connect(db_path) as db:
            # 테이블 존재 확인 및 생성
            await db.execute("""
                            CREATE TABLE IF NOT EXISTS tbl_voice_option (
                                user_id INTEGER PRIMARY KEY,
                                lang TEXT -- 추가 필드 정의
                            )
                        """)
            await db.commit()


    @staticmethod
    async def get_voice_option(user_id: int) -> Optional[VoiceOption]:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            query = f"SELECT * FROM tbl_voice_option WHERE user_id = ?"
            tu = (user_id,)
            cursor = await db.execute(query, tu)
            row = await cursor.fetchone()
            if row:
                return VoiceOption(**row)
            else:
                return None

    @staticmethod
    async def update(user_id: int, lang: str) -> None:
        if lang not in gtts.lang.tts_langs():
            raise ValueError("Invalid language code")

        async with aiosqlite.connect(db_path) as db:
            query = f"UPDATE tbl_voice_option SET lang = ? WHERE user_id = ?"
            tu = (lang, user_id)
            await db.execute(query, tu)
            await db.commit()

    @staticmethod
    async def insert(user_id: int, lang: str) -> None:
        if lang not in gtts.lang.tts_langs():
            raise ValueError("Invalid language code")

        async with aiosqlite.connect(db_path) as db:
            query = f"INSERT INTO tbl_voice_option VALUES (?, ?)"
            tu = (user_id, lang)
            await db.execute(query, tu)
            await db.commit()

    @staticmethod
    async def get_all() -> List[VoiceOption]:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            query = f"SELECT * FROM tbl_voice_option"
            cursor = await db.execute(query)
            rows = await cursor.fetchall()
            return [VoiceOption(**row) for row in rows] if rows else []
