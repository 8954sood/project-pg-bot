from datetime import datetime, timezone

import aiosqlite

from core.local import path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> aiosqlite.Connection:
    return aiosqlite.connect(path.db_path)
