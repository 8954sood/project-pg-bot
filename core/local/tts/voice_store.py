import aiosqlite

from core.local import db_path


class VoiceStore:
    @staticmethod
    async def init_table():
        async with aiosqlite.connect(db_path) as db:
            await db.execute('''CREATE TABLE IF NOT EXISTS sovits_voices (
                style_id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL,
                name TEXT NOT NULL, public INTEGER NOT NULL DEFAULT 0,
                is_sample INTEGER NOT NULL DEFAULT 0, lang TEXT NOT NULL)''')
            await db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS sovits_one_personal_voice
                ON sovits_voices(owner_id) WHERE is_sample = 0''')
            await db.commit()

    @staticmethod
    async def all():
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute('SELECT * FROM sovits_voices ORDER BY name, style_id')
            return [dict(row) for row in await cursor.fetchall()]

    @staticmethod
    async def add(style_id, owner_id, name, public, is_sample, lang):
        async with aiosqlite.connect(db_path) as db:
            await db.execute('INSERT INTO sovits_voices VALUES (?, ?, ?, ?, ?, ?)',
                             (style_id, owner_id, name, int(public), int(is_sample), lang))
            await db.commit()

    @staticmethod
    async def delete(style_id):
        async with aiosqlite.connect(db_path) as db:
            await db.execute('DELETE FROM sovits_voices WHERE style_id = ?', (style_id,))
            await db.commit()

    @staticmethod
    async def set_public(style_id, public):
        async with aiosqlite.connect(db_path) as db:
            await db.execute('UPDATE sovits_voices SET public = ? WHERE style_id = ?', (int(public), style_id))
            await db.commit()
