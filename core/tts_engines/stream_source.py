import asyncio
import subprocess
from discord import AudioSource

class FFmpegStdoutAudioSource(AudioSource):
    """discord.py가 읽어갈 PCM을 ffmpeg stdout에서 공급"""
    def __init__(self, proc: subprocess.Popen):
        self.proc = proc

    def read(self) -> bytes:
        # discord.py는 20ms(3840 bytes@48k stereo s16le) 단위로 읽는 편
        return self.proc.stdout.read(3840)

    def is_opus(self) -> bool:
        return False

    def cleanup(self):
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except: pass
        try:
            if self.proc.stdout:
                self.proc.stdout.close()
        except: pass
        try:
            self.proc.terminate()
        except: pass
