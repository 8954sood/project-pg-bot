import asyncio
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import websockets

from ui.tts.cog import TTS
from core.tts.engines.ai_stream_engine import AIStreamEngine
from core.tts.engines.gtts_engine import GTTSEngine
from core.tts.engines.stream_source import FFmpegStdoutAudioSource


class FakeSource:
    def __init__(self):
        self.cleaned_up = False

    def cleanup(self):
        self.cleaned_up = True


class FakeVoiceClient:
    def __init__(self, *, connected=True):
        self.connected = connected
        self.playing = False
        self.channel = SimpleNamespace(id=123)
        self.play_calls = []
        self.stop_calls = 0
        self.disconnect_calls = 0
        self.cleanup_calls = 0

    def is_connected(self):
        return self.connected

    def is_playing(self):
        return self.playing

    def play(self, source, after=None):
        self.play_calls.append((source, after))
        self.playing = True

    def stop(self):
        self.stop_calls += 1
        self.playing = False

    async def disconnect(self):
        self.disconnect_calls += 1
        self.connected = False

    def cleanup(self):
        self.cleanup_calls += 1


def make_cog(loop, *, voice_client=None):
    cog = TTS.__new__(TTS)
    cog.bot = SimpleNamespace(loop=loop, user=SimpleNamespace(id=999))
    cog.queue = {}
    cog.play_locks = {}
    cog.messageChannel = {}
    cog.defaultChannel = {}
    cog.dmChannel = {}
    cog.voice_option = {}
    cog.tts_engine_option = {}
    cog.tts_engine_allow = set()
    cog.ai_engine = None
    cog.gtts_engine = SimpleNamespace(synth=AsyncMock(return_value=BytesIO(b"mp3")))
    cog.max_queue_size = 50
    cog.max_text_length = 300
    cog.gtts_timeout = 0.05
    if voice_client is not None:
        cog.queue[1] = {
            "guild_id": 1,
            "voice_channel_id": 123,
            "tts_queue": [],
            "vc": voice_client,
        }
    return cog


@pytest.mark.asyncio
async def test_source_item_remains_queued_until_creation_finishes():
    loop = asyncio.get_running_loop()
    vc = FakeVoiceClient()
    cog = make_cog(loop, voice_client=vc)
    item = {"text": "hello", "user_id": 10}
    cog.queue[1]["tts_queue"].append(item)
    started = asyncio.Event()
    release = asyncio.Event()

    async def fail_after_release(**kwargs):
        started.set()
        await release.wait()
        raise RuntimeError("generation failed")

    cog._create_tts_source = fail_after_release
    task = asyncio.create_task(cog.play_tts(1))
    await started.wait()

    assert cog.queue[1]["tts_queue"] == [item]

    release.set()
    await task
    assert cog.queue[1]["tts_queue"] == []
    assert vc.play_calls == []


@pytest.mark.asyncio
async def test_ai_failure_falls_back_to_gtts(monkeypatch):
    loop = asyncio.get_running_loop()
    cog = make_cog(loop)
    cog.tts_engine_option[10] = SimpleNamespace(engine="ai", model_name="model-a")
    cog.ai_engine = SimpleNamespace(
        create_discord_source=AsyncMock(side_effect=RuntimeError("AI down"))
    )
    fake_source = FakeSource()
    monkeypatch.setattr("core.tts.service.discord.FFmpegPCMAudio", lambda *args, **kwargs: fake_source)

    source, engine = await cog._create_tts_source(
        guild_id=1,
        queue_item={"text": "hello", "user_id": 10},
    )

    assert source is fake_source
    assert engine == "gtts"
    cog.gtts_engine.synth.assert_awaited_once_with(
        text="hello",
        user_id=10,
        timeout=cog.gtts_timeout,
    )


@pytest.mark.asyncio
async def test_all_engines_fail_skips_only_failed_item(monkeypatch):
    loop = asyncio.get_running_loop()
    vc = FakeVoiceClient()
    cog = make_cog(loop, voice_client=vc)
    first = {"text": "first", "user_id": 10}
    second = {"text": "second", "user_id": 11}
    cog.queue[1]["tts_queue"].extend([first, second])
    source = FakeSource()

    async def create_source(**kwargs):
        if kwargs["queue_item"] is first:
            raise RuntimeError("all engines failed")
        return source, "gtts"

    cog._create_tts_source = create_source
    await cog.play_tts(1)

    assert len(vc.play_calls) == 1
    assert vc.play_calls[0][0] is source
    assert cog.queue[1]["tts_queue"] == []


@pytest.mark.asyncio
async def test_concurrent_play_tts_is_single_flight():
    loop = asyncio.get_running_loop()
    vc = FakeVoiceClient()
    cog = make_cog(loop, voice_client=vc)
    cog.queue[1]["tts_queue"].append({"text": "hello", "user_id": 10})
    started = asyncio.Event()
    release = asyncio.Event()

    async def create_source(**kwargs):
        started.set()
        await release.wait()
        return FakeSource(), "gtts"

    cog._create_tts_source = create_source
    first = asyncio.create_task(cog.play_tts(1))
    await started.wait()
    second = asyncio.create_task(cog.play_tts(1))
    release.set()
    await asyncio.gather(first, second)

    assert len(vc.play_calls) == 1


@pytest.mark.asyncio
async def test_voice_state_change_during_source_creation_cancels_playback():
    loop = asyncio.get_running_loop()
    vc = FakeVoiceClient()
    cog = make_cog(loop, voice_client=vc)
    cog.queue[1]["tts_queue"].append({"text": "hello", "user_id": 10})
    started = asyncio.Event()
    release = asyncio.Event()
    source = FakeSource()

    async def create_source(**kwargs):
        started.set()
        await release.wait()
        return source, "gtts"

    cog._create_tts_source = create_source
    task = asyncio.create_task(cog.play_tts(1))
    await started.wait()
    cog.queue.pop(1)
    release.set()
    await task

    assert source.cleaned_up
    assert vc.play_calls == []


@pytest.mark.asyncio
async def test_playback_callback_logs_error_and_schedules_next(caplog):
    loop = asyncio.get_running_loop()
    cog = make_cog(loop)
    cog.safe_play_tts = AsyncMock()
    callback = cog._make_after_callback(1)

    with caplog.at_level("ERROR"):
        callback(RuntimeError("playback failed"))
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert "TTS playback callback error" in caplog.text
    cog.safe_play_tts.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_clear_queue_without_connected_voice_client_is_safe():
    loop = asyncio.get_running_loop()
    cog = make_cog(loop)
    await cog.clear_guild_queue(1, reason="test_no_client")

    vc = FakeVoiceClient(connected=False)
    cog.queue[1] = {
        "guild_id": 1,
        "voice_channel_id": 123,
        "tts_queue": [{"text": "hello", "user_id": 10}],
        "vc": vc,
    }
    await cog.clear_guild_queue(1, reason="test_disconnected")

    assert vc.disconnect_calls == 0
    assert 1 not in cog.queue


@pytest.mark.asyncio
async def test_clear_queue_removes_all_dm_routes_without_mutation_error():
    loop = asyncio.get_running_loop()
    cog = make_cog(loop)
    cog.dmChannel = {10: 1, 11: 2, 12: 1}

    await cog.clear_guild_queue(1, reason="test_dm_cleanup")

    assert cog.dmChannel == {11: 2}


@pytest.mark.asyncio
async def test_clear_queue_discards_unused_play_lock():
    loop = asyncio.get_running_loop()
    cog = make_cog(loop)
    cog._get_play_lock(1)

    await cog.clear_guild_queue(1, reason="test_lock_cleanup")

    assert 1 not in cog.play_locks


@pytest.mark.asyncio
async def test_clear_queue_discards_locked_play_lock_after_release():
    loop = asyncio.get_running_loop()
    cog = make_cog(loop)
    lock = cog._get_play_lock(1)

    await lock.acquire()
    await cog.clear_guild_queue(1, reason="test_locked_lock_cleanup")
    assert cog.play_locks[1] is lock

    lock.release()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert 1 not in cog.play_locks


@pytest.mark.asyncio
async def test_stale_voice_client_is_cleaned_up_after_release_timeout():
    loop = asyncio.get_running_loop()
    cog = make_cog(loop)
    vc = FakeVoiceClient(connected=False)
    guild = SimpleNamespace(id=1, voice_client=vc)

    await cog._wait_for_voice_client_release(guild, vc, timeout=0)

    assert vc.cleanup_calls == 1


@pytest.mark.asyncio
async def test_full_queue_drops_new_message_without_disconnect():
    loop = asyncio.get_running_loop()
    vc = FakeVoiceClient()
    cog = make_cog(loop, voice_client=vc)
    cog.max_queue_size = 1
    cog.queue[1]["tts_queue"].append({"text": "existing", "user_id": 10})

    queued = await cog._enqueue_tts(
        guild_id=1,
        voice_model=cog.queue[1],
        text="new",
        user_id=11,
        channel_id=20,
    )

    assert queued is False
    assert len(cog.queue[1]["tts_queue"]) == 1
    assert vc.disconnect_calls == 0
    assert vc.is_connected()


@pytest.mark.asyncio
async def test_enqueue_truncates_text_to_configured_limit():
    loop = asyncio.get_running_loop()
    vc = FakeVoiceClient()
    cog = make_cog(loop, voice_client=vc)
    cog.max_text_length = 5

    queued = await cog._enqueue_tts(
        guild_id=1,
        voice_model=cog.queue[1],
        text="123456789",
        user_id=11,
        channel_id=20,
    )

    assert queued is True
    assert cog.queue[1]["tts_queue"][0]["text"] == "1234…"


@pytest.mark.asyncio
async def test_gtts_timeout_skips_item_without_disconnect():
    loop = asyncio.get_running_loop()
    vc = FakeVoiceClient()
    cog = make_cog(loop, voice_client=vc)
    cog.gtts_timeout = 0.01
    cog.queue[1]["tts_queue"].append({"text": "hello", "user_id": 10})

    async def slow_synth(**kwargs):
        await asyncio.sleep(1)

    cog.gtts_engine.synth = slow_synth
    await cog.play_tts(1)

    assert cog.queue[1]["tts_queue"] == []
    assert vc.play_calls == []
    assert vc.disconnect_calls == 0
    assert vc.is_connected()


@pytest.mark.parametrize("stdout", [None, Mock(read=Mock(side_effect=OSError("read failed")))])
def test_ffmpeg_source_read_returns_empty_bytes_on_errors(stdout):
    proc = Mock()
    proc.poll.return_value = None
    proc.stdout = stdout
    source = FFmpegStdoutAudioSource(proc)

    assert source.read() == b""


def test_ffmpeg_source_cleanup_is_idempotent():
    proc = Mock()
    proc.stdin = Mock()
    proc.stdout = Mock()
    proc.poll.return_value = 0
    source = FFmpegStdoutAudioSource(proc)

    source.cleanup()
    source.cleanup()

    proc.stdin.close.assert_called_once()
    proc.stdout.close.assert_called_once()


def test_gtts_engine_passes_network_timeout(monkeypatch):
    created = {}
    fake_tts = SimpleNamespace(write_to_fp=lambda fp: fp.write(b"mp3"))

    def create_gtts(**kwargs):
        created.update(kwargs)
        return fake_tts

    monkeypatch.setattr("core.tts.engines.gtts_engine.gTTS", create_gtts)
    engine = GTTSEngine(lambda user_id: "ko")

    result = engine._synth_sync("hello", 10, 3.5)

    assert result.read() == b"mp3"
    assert created["timeout"] == 3.5


class FakePipe:
    def __init__(self):
        self.closed = False
        self.writes = []

    def close(self):
        self.closed = True

    def write(self, data):
        self.writes.append(data)


class FakeProcess:
    def __init__(self):
        self.stdin = FakePipe()
        self.stdout = FakePipe()
        self.terminated = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.terminated = True


class HangingWebsocket:
    async def send(self, payload):
        return None

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(60)


class WebsocketContext:
    async def __aenter__(self):
        return HangingWebsocket()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class EndingWebsocket(HangingWebsocket):
    async def __anext__(self):
        return '{"type": "end"}'


class EndingWebsocketContext(WebsocketContext):
    async def __aenter__(self):
        return EndingWebsocket()


@pytest.mark.asyncio
async def test_ai_first_chunk_timeout_cleans_up_ffmpeg(monkeypatch):
    proc = FakeProcess()
    monkeypatch.setattr(
        "core.tts.engines.ai_stream_engine.subprocess.Popen",
        lambda *args, **kwargs: proc,
    )
    monkeypatch.setattr(
        "core.tts.engines.ai_stream_engine.websockets.connect",
        lambda *args, **kwargs: WebsocketContext(),
    )
    engine = AIStreamEngine(ai_ws_url="ws://example.invalid", first_chunk_timeout=0.01)

    with pytest.raises(RuntimeError, match="first chunk timeout"):
        await engine.create_discord_source(text="hello", model_name="model-a")

    assert proc.stdin.closed
    assert proc.stdout.closed
    assert proc.terminated


@pytest.mark.asyncio
async def test_ai_end_before_first_chunk_cleans_up_ffmpeg(monkeypatch):
    proc = FakeProcess()
    monkeypatch.setattr(
        "core.tts.engines.ai_stream_engine.subprocess.Popen",
        lambda *args, **kwargs: proc,
    )
    monkeypatch.setattr(
        "core.tts.engines.ai_stream_engine.websockets.connect",
        lambda *args, **kwargs: EndingWebsocketContext(),
    )
    engine = AIStreamEngine(ai_ws_url="ws://example.invalid", first_chunk_timeout=1)

    with pytest.raises(RuntimeError, match="AI TTS unavailable"):
        await engine.create_discord_source(text="hello", model_name="model-a")

    assert proc.stdin.closed
    assert proc.stdout.closed
    assert proc.terminated


@pytest.mark.asyncio
async def test_websockets_15_connects_to_local_server(monkeypatch):
    received_requests = []

    async def handler(websocket, *args):
        received_requests.append(await websocket.recv())
        await websocket.send(b"audio")
        await websocket.send('{"type": "end"}')

    proc = FakeProcess()
    monkeypatch.setattr(
        "core.tts.engines.ai_stream_engine.subprocess.Popen",
        lambda *args, **kwargs: proc,
    )

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        engine = AIStreamEngine(
            ai_ws_url=f"ws://127.0.0.1:{port}",
            first_chunk_timeout=1,
        )
        source = await engine.create_discord_source(
            text="hello",
            model_name="model-a",
        )
        await asyncio.sleep(0)
        source.cleanup()

    assert received_requests
    assert proc.stdin.writes == [b"audio"]
