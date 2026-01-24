from typing import Protocol, Optional
import io

class TTSEngine(Protocol):
    async def synth(self, *, text: str, user_id: int) -> io.BytesIO:
        """Return an in-memory audio stream (mp3/wav/pcm) ready for FFmpeg pipe."""
        