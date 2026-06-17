import asyncio
import logging
import subprocess
from typing import Optional

from discord import AudioSource

logger = logging.getLogger(__name__)


class FFmpegStdoutAudioSource(AudioSource):
    """discord.py가 읽어갈 PCM을 ffmpeg stdout에서 공급"""

    def __init__(
        self,
        proc: subprocess.Popen,
        *,
        feed_task: Optional[asyncio.Task] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ):
        self.proc = proc
        self.feed_task = feed_task
        self.loop = loop
        self._cleaned_up = False

    def read(self) -> bytes:
        try:
            if self.proc.poll() is not None or not self.proc.stdout:
                return b""
            return self.proc.stdout.read(3840) or b""
        except Exception:
            logger.exception("ffmpeg stdout read failed")
            return b""

    def is_opus(self) -> bool:
        return False

    def cleanup(self):
        if self._cleaned_up:
            return
        self._cleaned_up = True

        if self.feed_task and not self.feed_task.done():
            try:
                if self.loop and self.loop.is_running():
                    self.loop.call_soon_threadsafe(self.feed_task.cancel)
                else:
                    self.feed_task.cancel()
            except Exception:
                logger.exception("AI TTS websocket feed task cancellation failed")

        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except Exception:
            logger.exception("ffmpeg stdin cleanup failed")
        try:
            if self.proc.stdout:
                self.proc.stdout.close()
        except Exception:
            logger.exception("ffmpeg stdout cleanup failed")
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=2)
        except Exception:
            logger.exception("ffmpeg process termination failed")
