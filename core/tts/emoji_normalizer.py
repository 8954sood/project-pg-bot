"""Offline emoji names for TTS; independent of Discord runtime and engines."""

import re
from functools import lru_cache
from threading import Lock

from core.tts.language import validate_language
from core.tts.models import TTSLanguage


_LANGUAGE_LOCK = Lock()
_CUSTOM_EMOJI = re.compile(r"<a?:[^:<>\s]+:[0-9]+>")
_SKIN_TONES = dict.fromkeys(range(0x1F3FB, 0x1F400))
_HORIZONTAL_SPACE = re.compile(r"[^\S\r\n]+")
_GENERIC_NAMES = {"ko": "이모지", "en": "emoji", "ja": "絵文字"}
_FAMILY_NAMES = {"ko": "가족", "en": "family", "ja": "家族"}
_SHORT_NAMES = {
    "ko": {
        "❤": "하트", "♥": "하트", "👍": "엄지 척", "👋": "손 흔들기",
        "😊": "미소", "😀": "웃는 얼굴", "😂": "웃음 눈물", "🎉": "축하 폭죽",
    },
    "en": {
        "❤": "heart", "♥": "heart", "👍": "thumbs up", "👋": "waving hand",
        "😊": "smile", "😀": "grinning face", "😂": "tears of joy", "🎉": "party popper",
    },
    "ja": {
        "❤": "ハート", "♥": "ハート", "👍": "サムズアップ", "👋": "手を振る",
        "😊": "にこにこ", "😀": "笑顔", "😂": "嬉し泣き", "🎉": "クラッカー",
    },
}


@lru_cache(maxsize=3)
def _emoji_language(language: TTSLanguage):
    # Import/load only when this optional utility is used. Serialize the
    # library's shared language-data initialization, including first trie use.
    import emoji

    with _LANGUAGE_LOCK:
        emoji.config.load_language(language)
        list(emoji.analyze(""))
    return emoji


def _spoken_name(sequence: str, language: TTSLanguage, emoji) -> str:
    base = sequence.translate(_SKIN_TONES)
    if not base:
        return ""
    plain = base.replace("\ufe0f", "")
    if plain.endswith("\u20e3") and plain[0] in "0123456789#*":
        return plain[0]
    short_name = _SHORT_NAMES[language].get(plain)
    if short_name is not None:
        return short_name
    data = emoji.EMOJI_DATA.get(base) or emoji.EMOJI_DATA.get(plain) or {}
    if data.get("en", "").startswith(":family_"):
        return _FAMILY_NAMES[language]
    name = data.get(language)
    if name is None:
        # New emoji may not yet have a localized label. Never inject an
        # English fallback into a model configured for another language.
        return _GENERIC_NAMES[language]
    return name.strip(":").replace("_", " ")


def normalize_tts_emojis(content: str, target_language: TTSLanguage) -> str:
    """Remove custom emoji and read Unicode emoji in ko/en/ja.

    Bare text symbols and explicit text presentation (VS15) are preserved.
    Emoji presentation (VS16), keycaps, flags and known ZWJ sequences are read
    as complete units. Skin-tone modifiers are omitted. Non-RGI ZWJ sequences
    without a known base name use a localized generic label.

    Existing punctuation is never rewritten. When replacements occur,
    horizontal whitespace is collapsed; CR/LF line breaks remain intact.
    This utility does not call the bot, mutate reference transcripts or
    transliterate the rest of the message. Compose it before pronunciation.
    """
    validate_language(target_language)
    # Keep an explicit gap so removing adjacent custom emoji cannot join words.
    text, removed = _CUSTOM_EMOJI.subn(" ", content)
    if text.isascii():
        result = _HORIZONTAL_SPACE.sub(" ", text).strip(" ") if removed else content
        return "" if removed and not result.strip() else result

    emoji = _emoji_language(target_language)
    parts = []
    cursor = 0
    changed = bool(removed)
    pending_boundary = False

    def append_text(value: str) -> None:
        nonlocal pending_boundary
        if not value:
            return
        if pending_boundary and parts and parts[-1][-1:].isalnum() and value[0].isalnum():
            parts.append(" ")
        parts.append(value)
        pending_boundary = False

    for token in emoji.analyze(text, join_emoji=True):
        match = token.value
        # The tokenizer excludes VS15 from the emoji match. Respect that
        # explicit request for text presentation, including default emoji.
        if "\ufe0e" in text[match.start:match.end + 1]:
            continue
        data = match.data
        if (
            len(token.chars) == 1 and data is not None
            and data["status"] == emoji.STATUS["unqualified"]
        ):
            continue
        append_text(text[cursor:match.start])
        name = _spoken_name(token.chars, target_language, emoji)
        if name:
            if parts and parts[-1][-1:].isalnum() and name[0].isalnum():
                parts.append(" ")
            parts.append(name)
        pending_boundary = True
        cursor = match.end
        changed = True
    if not changed:
        return content
    append_text(text[cursor:])
    result = _HORIZONTAL_SPACE.sub(" ", "".join(parts)).strip(" ")
    return result if result.strip() else ""
