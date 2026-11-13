"""Reusable pronunciation preprocessing for single-language TTS models.

This utility is deliberately not connected to the bot or any engine. A caller
chooses the model's target language and decides how to handle unresolved input.
"""

import json
import re
import unicodedata
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

from core.tts.language import (
    analyze_language, iter_language_runs, letter_language, validate_language,
)
from core.tts.models import PronunciationResult, TTSLanguage
from core.tts.transliteration import PronunciationConverters


_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


class PronunciationPreprocessor:
    def __init__(
        self,
        overrides: Mapping[str, Mapping[str, str]] | None = None,
        *,
        cache_size: int = 2048,
        max_cached_run_length: int = 128,
    ) -> None:
        if not isinstance(cache_size, int) or cache_size < 0:
            raise ValueError("cache_size must be a nonnegative integer")
        if not isinstance(max_cached_run_length, int) or max_cached_run_length < 0:
            raise ValueError("max_cached_run_length must be a nonnegative integer")
        self._overrides = {}
        self._patterns = {}
        if overrides is not None and not isinstance(overrides, Mapping):
            raise ValueError("overrides must map target languages to dictionaries")
        for target, entries in (overrides or {}).items():
            validate_language(target)
            if not isinstance(entries, Mapping):
                raise ValueError(f"Overrides for {target} must be a dictionary")
            copied = {}
            for key, value in entries.items():
                if not isinstance(key, str) or not key.strip() or not isinstance(value, str) or not value.strip():
                    raise ValueError("Override keys and values must be nonempty strings")
                key = unicodedata.normalize("NFC", key)
                value = unicodedata.normalize("NFC", value)
                if any(language not in (None, target) for language, _ in iter_language_runs(value)):
                    raise ValueError(f"Override value {value!r} must use the {target} script")
                # Match ASCII names case-insensitively without changing kana/Hangul.
                folded = key.translate(_ASCII_LOWER)
                if folded in copied:
                    raise ValueError(f"Duplicate override key: {key!r}")
                copied[folded] = value
            self._overrides[target] = copied
            alternatives = []
            for key in sorted(copied, key=len, reverse=True):
                pattern = re.escape(key)
                if letter_language(key[0]) == "en":
                    pattern = r"(?<![a-zA-Z])" + pattern
                if letter_language(key[-1]) == "en":
                    pattern += r"(?![a-zA-Z])"
                alternatives.append(pattern)
            if alternatives:
                self._patterns[target] = re.compile("|".join(alternatives), re.IGNORECASE | re.ASCII)
        self._converters = PronunciationConverters()
        self._cached_convert = lru_cache(maxsize=cache_size)(self._converters.convert)
        self._max_cached_run_length = max_cached_run_length

    @classmethod
    def from_json(cls, path: str | Path, **kwargs) -> "PronunciationPreprocessor":
        """Load a target-language -> original -> pronunciation dictionary once."""
        with Path(path).open(encoding="utf-8") as file:
            overrides = json.load(file)
        if not isinstance(overrides, dict):
            raise ValueError("Pronunciation dictionary must be a JSON object")
        return cls(overrides, **kwargs)

    def warmup(self) -> None:
        """Load bundled dictionaries before handling time-sensitive requests."""
        self._converters.warmup()

    def cache_info(self):
        return self._cached_convert.cache_info()

    def clear_cache(self) -> None:
        self._cached_convert.cache_clear()

    def prepare(self, text: str, target_language: TTSLanguage) -> PronunciationResult:
        """Convert foreign-script runs and report remaining unsupported letters.

        Overrides are applied first, longest match first, and their output is
        never converted again. Native-script text and separators pass through;
        apostrophes inside English words are part of the word pronunciation.
        This does not validate the acoustic quality of the resulting speech.
        """
        validate_language(target_language)
        profile = analyze_language(text)
        normalized = unicodedata.normalize("NFC", text)
        parts = []
        pattern = self._patterns.get(target_language)
        cursor = 0
        if pattern is not None:
            for match in pattern.finditer(normalized):
                parts.append(self._convert_text(normalized[cursor:match.start()], target_language))
                key = match.group()
                key = key.translate(_ASCII_LOWER)
                parts.append(self._overrides[target_language][key])
                cursor = match.end()
        parts.append(self._convert_text(normalized[cursor:], target_language))
        result = "".join(parts)
        unresolved = tuple(dict.fromkeys(
            run for language, run in iter_language_runs(result)
            if language not in (None, target_language)
        ))
        return PronunciationResult(
            text=result,
            target_language=target_language,
            profile=profile,
            changed=result != text,
            unresolved=unresolved,
        )

    def _convert_text(self, text: str, target_language: TTSLanguage) -> str:
        parts = []
        for source, run in iter_language_runs(text):
            if source in (None, "other", target_language):
                parts.append(run)
            elif len(run) <= self._max_cached_run_length:
                parts.append(self._cached_convert(run, source, target_language))
            else:
                parts.append(self._converters.convert(run, source, target_language))
        return "".join(parts)
