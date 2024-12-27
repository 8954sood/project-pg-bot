from typing import Optional

import aiosqlite

from core.local.path import db_path
from core.local.user.dto.user import User


class UserDataSource:
    @staticmethod
    async def get_user_by_user_id(userId: int) -> Optional[User]:
        """
        유저 ID를 통해 유저의 정보, 역할, 역할 이름을 반환합니다.

        return User
        """
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            query = f"SELECT * FROM users WHERE author = ?"
            tu = (userId,)
            cursor = await db.execute(query, tu)
            row = await cursor.fetchone()
            if row:
                return User(**row)
            else:
                return None

    @staticmethod
    async def insert_user(userId: int, roleId: int, roleName: str) -> None:
        async with aiosqlite.connect(db_path) as db:
            sql = "INSERT INTO users (author, role, rolename) VALUES (?, ?, ?)"
            val = (userId, roleId, roleName)
            await db.execute(sql, val)
            await db.commit()

    @staticmethod
    async def update_user(userId: int, roleId: int, roleName: str) -> None:
        async with aiosqlite.connect(db_path) as db:
            sql = "UPDATE users SET role = ?, rolename = ? WHERE author = ?"
            val = (roleId, roleName, userId)
            await db.execute(sql, val)
            await db.commit()