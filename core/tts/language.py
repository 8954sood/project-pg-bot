"""Fast, offline script detection shared by the pronunciation utilities."""

import unicodedata
from collections.abc import Iterator
from functools import lru_cache

from core.tts.models import LanguageProfile, TTSLanguage


def validate_language(language: str) -> None:
    if language not in ("ko", "en", "ja"):
        raise ValueError(f"Unsupported TTS language: {language!r}; use ko, en or ja")


@lru_cache(maxsize=4096)
def letter_language(char: str) -> str | None:
    """Classify one letter. Latin is treated as en and Han as ja."""
    if not char.isalpha():
        return None
    code = ord(char)
    if 65 <= code <= 90 or 97 <= code <= 122:
        return "en"
    if (
        0xAC00 <= code <= 0xD7A3
        or 0x1100 <= code <= 0x11FF
        or 0x3130 <= code <= 0x318F
        or 0xA960 <= code <= 0xA97F
        or 0xD7B0 <= code <= 0xD7FF
    ):
        return "ko"
    if (
        0x3040 <= code <= 0x30FF
        or 0x31F0 <= code <= 0x31FF
        or 0xFF66 <= code <= 0xFF9F
        or 0x1B000 <= code <= 0x1B16F
        or 0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0x20000 <= code <= 0x323AF
        or char in "々〆"
    ):
        return "ja"
    if unicodedata.name(char, "").startswith(("LATIN ", "FULLWIDTH LATIN ")):
        return "en"
    return "other"


def analyze_language(text: str) -> LanguageProfile:
    """Count NFC-normalized letters; ignore whitespace, numbers and symbols."""
    counts = {"ko": 0, "en": 0, "ja": 0, "other": 0}
    for char in unicodedata.normalize("NFC", text):
        language = letter_language(char)
        if language is not None:
            counts[language] += 1
    return LanguageProfile(**counts)


def is_language_dominant(
    text: str, language: TTSLanguage, threshold: float = 0.5,
) -> bool:
    """Use a strict > threshold comparison. Empty/non-letter input is False."""
    validate_language(language)
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0 and 1")
    return analyze_language(text).ratio(language) > threshold


def iter_language_runs(text: str) -> Iterator[tuple[str | None, str]]:
    """Split at script changes, keeping punctuation and spacing verbatim."""
    start = 0
    current = None
    for index, char in enumerate(text):
        language = letter_language(char)
        # Keep decomposed accents/dakuten and English contractions together.
        if unicodedata.category(char).startswith("M"):
            language = current
        elif (
            char in "'’" and current == "en" and index + 1 < len(text)
            and letter_language(text[index + 1]) == "en"
        ):
            language = "en"
        if index and language != current:
            yield current, text[start:index]
            start = index
        current = language
    if text:
        yield current, text[start:]
