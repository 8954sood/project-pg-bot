from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True, slots=True)
class BanChannelConfig:
    guild_id: int
    channel_id: int
    warning_message_id: Optional[int] = None
