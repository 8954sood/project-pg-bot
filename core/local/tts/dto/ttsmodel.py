from dataclasses import dataclass


@dataclass
class TTSModel:
    guild_id: int
    channel_id: int