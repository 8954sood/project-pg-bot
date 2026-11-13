import asyncio
import io

from gtts import gTTS

from core.tts.emoji_normalizer import normalize_tts_emojis
from core.tts.text_normalizer import EmptyTTSText, normalize_tts_text


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
        if lang in ("ko", "en", "ja"):
            text = normalize_tts_emojis(text, lang)
        text = normalize_tts_text(text)
        if not text.strip():
            raise EmptyTTSText("이모지 처리 후 읽을 내용이 없습니다.")
        tts = gTTS(text=text, lang=lang, timeout=timeout)
        fp = io.BytesIO()
        tts.write_to_fp(fp)
        fp.seek(0)
        return fp  # mp3 bytes
