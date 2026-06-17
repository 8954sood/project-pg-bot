import asyncio
from types import SimpleNamespace

import pytest

from core.tts.playback import TTSPlayback


class FakeSource:
    def __init__(self):
        self.cleaned_up = False

    def cleanup(self):
        self.cleaned_up = True


class FakeVoiceClient:
    def __init__(self, *, connected=True):
        self.connected = connected
        self.playing = False
        self.channel = SimpleNamespace(id=10)
        self.play_calls = []
        self.disconnect_calls = 0

    def is_connected(self):
        return self.connected

    def is_playing(self):
        return self.playing

    def play(self, source, after=None):
        self.playing = True
        self.play_calls.append((source, after))

    def stop(self):
        self.playing = False

    async def disconnect(self):
        self.disconnect_calls += 1
        self.connected = False


@pytest.mark.asyncio
async def test_playback_starts_first_queued_item_and_removes_it():
    vc = FakeVoiceClient()
    queue = {
        1: {
            "guild_id": 1,
            "voice_channel_id": 10,
            "tts_queue": [{"text": "hello", "user_id": 2}],
            "vc": vc,
        }
    }
    source = FakeSource()

    async def create_source(**kwargs):
        return source, "gtts"

    playback = TTSPlayback(
        queue=queue,
        play_locks={},
        bot_loop=asyncio.get_running_loop(),
        create_source=create_source,
    )

    await playback.play_tts(1)

    assert vc.play_calls[0][0] is source
    assert queue[1]["tts_queue"] == []


@pytest.mark.asyncio
async def test_clear_queue_disconnects_and_cleans_routes():
    vc = FakeVoiceClient()
    queue = {
        1: {
            "guild_id": 1,
            "voice_channel_id": 10,
            "tts_queue": [{"text": "hello", "user_id": 2}],
            "vc": vc,
        }
    }
    message_channels = {1: 100}
    dm_channels = {200: 1, 201: 2}
    playback = TTSPlayback(
        queue=queue,
        play_locks={},
        bot_loop=asyncio.get_running_loop(),
        create_source=None,
    )

    await playback.clear_guild_queue(
        1,
        reason="test",
        message_channels=message_channels,
        dm_channels=dm_channels,
    )

    assert queue == {}
    assert message_channels == {}
    assert dm_channels == {201: 2}
    assert vc.disconnect_calls == 1
