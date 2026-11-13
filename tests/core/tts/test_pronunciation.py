import json
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest

from core.tts.models import LanguageProfile
from core.tts.pronunciation import PronunciationPreprocessor
from core.tts.transliteration import PronunciationConverters


@pytest.fixture(scope="module")
def preprocessor():
    return PronunciationPreprocessor()


@pytest.mark.parametrize(("source", "target", "expected"), [
    ("국물 같이", "en", "gungmul gachi"),
    ("안녕하세요 한국어 사랑해요", "ja", "アンニョンハセヨ ハングゴ サランヘヨ"),
    ("日本語 カタカナ", "en", "nihongo katakana"),
    ("日本語 がっこう コーヒー", "ko", "니혼고 갓코우 코오히이"),
    ("test computer stream", "ko", "테스트 컴퓨터 스트림"),
    ("hello world test", "ja", "ハロー ワールド テスト"),
])
def test_all_six_directions_work_offline(preprocessor, source, target, expected):
    result = preprocessor.prepare(source, target)

    assert result.text == expected
    assert result.changed
    assert result.complete
    assert result.unresolved == ()


def test_mixed_input_converts_minority_runs_and_preserves_layout(preprocessor):
    result = preprocessor.prepare("한국어 test\n日本語! 123 😀", "ko")

    assert result.text == "한국어 테스트\n니혼고! 123 😀"
    assert result.complete
    assert result.profile == LanguageProfile(ko=3, en=4, ja=3)


@pytest.mark.parametrize(("text", "target"), [
    ("안녕하세요 123!?😀", "ko"), ("English café!?", "en"),
    ("こんにちは 日本語 カタカナ!", "ja"), ("", "ja"), (" \n123😀", "ko"),
])
def test_native_and_nonletter_input_does_not_load_backends(monkeypatch, text, target):
    convert = Mock(side_effect=AssertionError("Backend must not run"))
    monkeypatch.setattr(PronunciationConverters, "convert", convert)
    processor = PronunciationPreprocessor()

    result = processor.prepare(text, target)

    assert result.text == text
    assert not result.changed
    assert result.complete
    convert.assert_not_called()


def test_unsupported_script_is_preserved_and_reported(preprocessor):
    result = preprocessor.prepare("안녕 Русский Ελληνικά Русский", "ko")

    assert result.text == "안녕 Русский Ελληνικά Русский"
    assert not result.changed
    assert not result.complete
    assert result.unresolved == ("Русский", "Ελληνικά")


def test_unconverted_latin_letter_is_reported(preprocessor):
    result = preprocessor.prepare("ß", "ja")

    assert result.text == "ß"
    assert result.unresolved == ("ß",)


def test_readings_and_tts_specific_respelling_can_be_overridden():
    processor = PronunciationPreprocessor({
        "ko": {"Python": "파이썬", "Discord": "디스코드", "こんにちは": "곤니치와"},
        "en": {"안녕하세요": "ahn nyung hah seh yo", "こんにちは": "konnichiwa"},
        "ja": {"Python": "パイソン"},
    })

    assert processor.prepare("Python Discord こんにちは", "ko").text == "파이썬 디스코드 곤니치와"
    assert processor.prepare("안녕하세요 こんにちは", "en").text == "ahn nyung hah seh yo konnichiwa"
    assert processor.prepare("Python", "ja").text == "パイソン"


def test_dictionary_matches_longest_phrase_and_does_not_rewrite_subwords():
    processor = PronunciationPreprocessor({"ko": {
        "AI": "에이아이", "OpenAI": "오픈에이아이", "OpenAI API": "오픈에이아이 에이피아이",
        "said": "세드", "OpenAI한국": "오픈에이아이한국",
    }})

    result = processor.prepare("openai api, OPENAI, said AI에서 openai한국", "ko")

    assert result.text == "오픈에이아이 에이피아이, 오픈에이아이, 세드 에이아이에서 오픈에이아이한국"


def test_dictionary_is_copied_and_replacements_are_not_recursive():
    entries = {"test": "테스트", "테스트": "시험"}
    processor = PronunciationPreprocessor({"ko": entries})
    entries["test"] = "변경"

    assert processor.prepare("test 테스트", "ko").text == "테스트 시험"


def test_dictionary_loads_from_utf8_json(tmp_path):
    path = tmp_path / "pronunciation.json"
    path.write_text(json.dumps({"ko": {"TTS": "티티에스"}}, ensure_ascii=False), encoding="utf-8")

    assert PronunciationPreprocessor.from_json(path).prepare("TTS", "ko").text == "티티에스"


def test_shipped_dictionary_supports_documented_examples():
    path = Path(__file__).resolve().parents[3] / "examples" / "tts_pronunciation.json"
    processor = PronunciationPreprocessor.from_json(path)

    assert processor.prepare("Python으로 日本語 test!", "ko").text == "파이썬으로 니혼고 테스트!"
    assert processor.prepare("안녕하세요 Python!", "ja").text == "アンニョンハセヨ パイソン!"
    assert processor.prepare("안녕하세요 日本語", "en").text == "ahn nyung hah seh yo nihongo"


def test_english_contraction_is_read_as_a_word(preprocessor):
    result = preprocessor.prepare("can't can’t", "ko")

    assert result.text == "캔트 캔트"
    assert result.complete


@pytest.mark.parametrize("overrides", [
    [], {"zh": {}}, {"ko": []}, {"ko": {"": "빈값"}}, {"ko": {"test": ""}},
    {"ko": {"test": "test"}}, {"ko": {1: "하나"}}, {"ko": {"a": 1}},
    {"ko": {"AI": "에이아이", "ai": "인공지능"}},
])
def test_invalid_dictionary_fails_early(overrides):
    with pytest.raises(ValueError):
        PronunciationPreprocessor(overrides)


def test_non_object_json_is_rejected(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("null", encoding="utf-8")

    with pytest.raises(ValueError, match="JSON object"):
        PronunciationPreprocessor.from_json(path)


@pytest.mark.parametrize("options", [{"cache_size": -1}, {"cache_size": 1.5}, {"max_cached_run_length": -1}])
def test_invalid_cache_settings_fail_early(options):
    with pytest.raises(ValueError):
        PronunciationPreprocessor(**options)


def test_unknown_words_and_initialisms_use_letter_names(preprocessor):
    assert preprocessor.prepare("AI qzxw", "ko").text == "에이아이 큐지엑스더블유"
    assert preprocessor.prepare("AI", "ja").text == "エーアイ"


def test_width_and_canonical_variants(preprocessor):
    assert preprocessor.prepare("ｶﾞｯｺｳ", "ko").text == "갓코우"
    assert preprocessor.prepare("ＴＥＳＴ", "ko").text == "티이에스티"
    assert preprocessor.prepare(unicodedata.normalize("NFD", "같이"), "en").text == "gachi"


def test_cache_is_per_direction_bounded_and_skips_long_input(monkeypatch):
    convert = Mock(side_effect=lambda text, source, target: "테스트" if target == "ko" else "テスト")
    monkeypatch.setattr(PronunciationConverters, "convert", convert)
    processor = PronunciationPreprocessor(cache_size=2, max_cached_run_length=4)

    processor.prepare("test test", "ko")
    assert convert.call_count == 1
    assert processor.cache_info().hits == 1
    processor.prepare("test", "ja")
    assert convert.call_count == 2
    processor.prepare("more", "ko")
    assert processor.cache_info().currsize == 2
    processor.prepare("longword longword", "ko")
    assert convert.call_count == 5
    assert processor.cache_info().currsize == 2
    processor.clear_cache()
    assert processor.cache_info().currsize == 0


def test_zero_cache_size_disables_caching(monkeypatch):
    convert = Mock(return_value="테스트")
    monkeypatch.setattr(PronunciationConverters, "convert", convert)
    processor = PronunciationPreprocessor(cache_size=0)

    processor.prepare("test test", "ko")

    assert convert.call_count == 2
    assert processor.cache_info().currsize == 0


def test_backend_errors_propagate_and_are_not_cached(monkeypatch):
    convert = Mock(side_effect=[RuntimeError("reader failed"), "테스트"])
    monkeypatch.setattr(PronunciationConverters, "convert", convert)
    processor = PronunciationPreprocessor()

    with pytest.raises(RuntimeError, match="reader failed"):
        processor.prepare("test", "ko")
    assert processor.prepare("test", "ko").text == "테스트"


def test_japanese_reader_initialization_and_conversion_are_safe_across_threads():
    processor = PronunciationPreprocessor(cache_size=0)
    texts = ["日本語", "がっこう", "カタカナ", "コーヒー"] * 4

    with ThreadPoolExecutor(max_workers=4) as executor:
        actual = list(executor.map(lambda text: processor.prepare(text, "ko").text, texts))

    assert actual == ["니혼고", "갓코우", "카타카나", "코오히이"] * 4


def test_invalid_target_is_rejected(preprocessor):
    with pytest.raises(ValueError, match="Unsupported"):
        preprocessor.prepare("hello", "zh")
