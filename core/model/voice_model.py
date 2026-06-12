from typing import List, TypedDict

from discord import VoiceClient

from .tts_queue_model import TTSQueueModel


class VoiceModel(TypedDict):
    guild_id: int
    voice_channel_id: int
    tts_queue: List[TTSQueueModel]
    vc: VoiceClient
