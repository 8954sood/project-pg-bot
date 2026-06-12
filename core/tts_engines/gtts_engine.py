import asyncio
import io

from gtts import gTTS


class GTTSEngine:
    def __init__(self, get_lang_fn):
        self.get_lang_fn = get_lang_fn  # user_id -> "ko"/"en"...

    async def synth(
        self,
        *,
        text: str,
        user_id: int,
        timeout: float = None,
    ) -> io.BytesIO:
        return await asyncio.to_thread(self._synth_sync, text, user_id, timeout)

    def _synth_sync(
        self,
        text: str,
        user_id: int,
        timeout: float = None,
    ) -> io.BytesIO:
        lang = self.get_lang_fn(user_id) or "ko"
        tts = gTTS(text=text, lang=lang, timeout=timeout)
        fp = io.BytesIO()
        tts.write_to_fp(fp)
        fp.seek(0)
        return fp  # mp3 bytes
