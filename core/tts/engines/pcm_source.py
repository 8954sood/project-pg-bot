"""Bounded 48 kHz mono PCM stream -> Discord 20 ms stereo frames."""
import asyncio
import threading

from discord import AudioSource


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
