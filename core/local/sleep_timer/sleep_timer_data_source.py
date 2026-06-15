from typing import List, Optional

import aiosqlite

from core.local import db_path
from core.local.sleep_timer.dto import SleepTimerReservation


class SleepTimerDataSource:
    @staticmethod
    async def init_table() -> None:
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS tbl_sleep_timer (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    reservation_id TEXT NOT NULL,
                    execute_at INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    warning_message_id INTEGER,
                    PRIMARY KEY (guild_id, user_id)
                )
                """
            )
            await db.commit()

    @staticmethod
    async def get(guild_id: int, user_id: int) -> Optional[SleepTimerReservation]:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT guild_id, user_id, reservation_id, execute_at, created_at,
                       warning_message_id
                FROM tbl_sleep_timer
                WHERE guild_id = ? AND user_id = ?
                """,
                (guild_id, user_id),
            )
            row = await cursor.fetchone()
            return SleepTimerReservation(**row) if row else None

    @staticmethod
    async def get_all() -> List[SleepTimerReservation]:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT guild_id, user_id, reservation_id, execute_at, created_at,
                       warning_message_id
                FROM tbl_sleep_timer
                """
            )
            rows = await cursor.fetchall()
            return [SleepTimerReservation(**row) for row in rows]

    @staticmethod
    async def upsert(reservation: SleepTimerReservation) -> None:
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                """
                INSERT INTO tbl_sleep_timer (
                    guild_id, user_id, reservation_id, execute_at, created_at,
                    warning_message_id
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id)
                DO UPDATE SET
                    reservation_id = excluded.reservation_id,
                    execute_at = excluded.execute_at,
                    created_at = excluded.created_at,
                    warning_message_id = excluded.warning_message_id
                """,
                (
                    reservation.guild_id,
                    reservation.user_id,
                    reservation.reservation_id,
                    reservation.execute_at,
                    reservation.created_at,
                    reservation.warning_message_id,
                ),
            )
            await db.commit()

    @staticmethod
    async def set_warning_message(
        guild_id: int,
        user_id: int,
        reservation_id: str,
        warning_message_id: int,
    ) -> None:
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                """
                UPDATE tbl_sleep_timer
                SET warning_message_id = ?
                WHERE guild_id = ? AND user_id = ? AND reservation_id = ?
                """,
                (warning_message_id, guild_id, user_id, reservation_id),
            )
            await db.commit()

    @staticmethod
    async def delete(
        guild_id: int,
        user_id: int,
        reservation_id: Optional[str] = None,
    ) -> bool:
        async with aiosqlite.connect(db_path) as db:
            if reservation_id is None:
                cursor = await db.execute(
                    "DELETE FROM tbl_sleep_timer WHERE guild_id = ? AND user_id = ?",
                    (guild_id, user_id),
                )
            else:
                cursor = await db.execute(
                    """
                    DELETE FROM tbl_sleep_timer
                    WHERE guild_id = ? AND user_id = ? AND reservation_id = ?
                    """,
                    (guild_id, user_id, reservation_id),
                )
            await db.commit()
            return cursor.rowcount > 0
