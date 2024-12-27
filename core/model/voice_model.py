from discord import VoiceClient
from typing_extensions import TypedDict
from typing import List


class VoiceModel(TypedDict):
    guild_id: int
    voice_channel_id: int
    tts_queue: List[str]
    vc: VoiceClient
    is_playing: bool