import io
from gtts import gTTS

class GTTSEngine:
    def __init__(self, get_lang_fn):
        self.get_lang_fn = get_lang_fn  # user_id -> "ko"/"en"...

    async def synth(self, *, text: str, user_id: int) -> io.BytesIO:
        lang = self.get_lang_fn(user_id) or "ko"
        tts = gTTS(text=text, lang=lang)
        fp = io.BytesIO()
        tts.write_to_fp(fp)
        fp.seek(0)
        return fp  # mp3 bytes
