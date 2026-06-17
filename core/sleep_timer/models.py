from dataclasses import dataclass
from datetime import datetime
from typing import Tuple


ReservationKey = Tuple[int, int]


@dataclass
class PendingReservation:
    token: str
    guild_id: int
    user_id: int
    execute_at: datetime

