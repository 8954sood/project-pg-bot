"""Bounded 48 kHz mono PCM stream -> Discord 20 ms stereo frames."""
import asyncio
import threading

from discord import AudioSource


async def write_pcm_stream(source, chunks, sample_rate):
    """Feed native PCM into the bounded 48kHz playback source."""
    if sample_rate == 48000:
        async for chunk in chunks:
            if not await asyncio.to_thread(source.write, chunk):
                break
        return

    # Keep the existing high-quality native32 conversion on the bot side.
    # The inference server continues to stream its unchanged model output.
    proc = await asyncio.create_subprocess_exec(
        'ffmpeg', '-hide_banner', '-loglevel', 'error',
        '-f', 's16le', '-ar', str(sample_rate), '-ac', '1',
        '-probesize', '32', '-analyzeduration', '0', '-i', 'pipe:0',
        '-f', 's16le', '-ar', '48000', '-ac', '1', 'pipe:1',
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, limit=16384,
    )

    async def write_input():
        try:
            async for chunk in chunks:
                proc.stdin.write(chunk)
                await proc.stdin.drain()
        finally:
            proc.stdin.close()

    writer = asyncio.create_task(write_input())
    try:
        while chunk := await proc.stdout.read(8192):
            if not await asyncio.to_thread(source.write, chunk):
                return
        status = await proc.wait()
        if status:
            raise RuntimeError(f'PCM resampling failed (ffmpeg exit {status})')
        await writer
    finally:
        writer.cancel()
        if proc.returncode is None:
            try:
                # Playback was cancelled: do not block trying to flush audio
                # into a pipe whose consumer has already stopped reading.
                proc.kill()
            except ProcessLookupError:
                pass
        await asyncio.gather(writer, return_exceptions=True)
        await proc.stdout.read()
        await proc.wait()


class PCMStreamAudioSource(AudioSource):
    MONO_FRAME_BYTES = 1920  # 960 signed PCM16 samples, 20 ms at 48 kHz
    BUFFER_BYTES = MONO_FRAME_BYTES * 8
    SILENCE_FRAME = b'\0' * (MONO_FRAME_BYTES * 2)

    def __init__(self, loop):
        self.loop = loop
        self.feed_task = None
        self._condition = threading.Condition()
        self._buffer = bytearray()
        self._finished = False
        self._closed = False
        self._ready = asyncio.Event()
        # discord.py's AudioPlayer checks this attribute when read() ends.
        self._current_error = None

    def write(self, data: bytes) -> bool:
        """Producer thread: stop accepting data when playback is cancelled.

        Waiting for space propagates backpressure to the HTTP stream instead
        of buffering a whole utterance or starting another server inference.
        Transport chunks may split individual PCM samples.
        """
        offset = 0
        with self._condition:
            while offset < len(data):
                while len(self._buffer) >= self.BUFFER_BYTES and not self._closed and not self._finished:
                    self._condition.wait()
                if self._closed or self._finished:
                    return False
                count = min(self.BUFFER_BYTES - len(self._buffer), len(data) - offset)
                self._buffer.extend(data[offset:offset + count])
                offset += count
                if len(self._buffer) >= self.MONO_FRAME_BYTES:
                    self.loop.call_soon_threadsafe(self._ready.set)
                self._condition.notify_all()
        return True

    def finish(self, error=None):
        with self._condition:
            self._current_error = error
            if len(self._buffer) % 2:
                self._buffer.pop()
                self._current_error = error or RuntimeError('PCM stream ended in a partial sample')
            self._finished = True
            self._condition.notify_all()
        self.loop.call_soon_threadsafe(self._ready.set)

    async def wait_ready(self, timeout=120):
        await asyncio.wait_for(self._ready.wait(), timeout)
        with self._condition:
            if self._current_error:
                raise self._current_error
            if not self._buffer:
                raise RuntimeError('음성 서버가 빈 오디오를 반환했습니다.')

    def read(self) -> bytes:
        """Keep Discord's audio clock moving while waiting for server segments.

        A transport underrun emits a silent frame without consuming partial
        samples. Blocking for seconds would make AudioPlayer send subsequent
        packets in a burst to catch up with its 20 ms clock.
        """
        with self._condition:
            ready = self._condition.wait_for(
                lambda: len(self._buffer) >= self.MONO_FRAME_BYTES or self._finished or self._closed,
                timeout=0.02,
            )
            if not ready:
                return self.SILENCE_FRAME
            if self._closed or not self._buffer:
                return b''
            mono = bytes(self._buffer[:self.MONO_FRAME_BYTES])
            del self._buffer[:self.MONO_FRAME_BYTES]
            self._condition.notify_all()
        mono = mono.ljust(self.MONO_FRAME_BYTES, b'\0')
        stereo = bytearray(self.MONO_FRAME_BYTES * 2)
        stereo[0::4] = stereo[2::4] = mono[0::2]
        stereo[1::4] = stereo[3::4] = mono[1::2]
        return bytes(stereo)

    def is_opus(self):
        return False

    def cleanup(self):
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._buffer.clear()
            self._condition.notify_all()
        if self.feed_task and not self.feed_task.done():
            self.loop.call_soon_threadsafe(self.feed_task.cancel)
