from dataclasses import dataclass
from typing import Optional


@dataclass
class SleepTimerReservation:
    guild_id: int
    user_id: int
    reservation_id: str
    execute_at: int
    created_at: int
    warning_message_id: Optional[int]
