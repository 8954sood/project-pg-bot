import unicodedata

import pytest

from core.tts.language import analyze_language, is_language_dominant, iter_language_runs
from core.tts.models import LanguageProfile


def test_counts_only_letters_including_unsupported_scripts():
    profile = analyze_language("한글 abc 日本語! 123 😀 Ж")

    assert profile == LanguageProfile(ko=2, en=3, ja=3, other=1)
    assert profile.total == 9
    assert profile.ratio("en") == pytest.approx(1 / 3)


def test_majority_is_strict_and_threshold_is_configurable():
    assert not is_language_dominant("안녕하세요 hello", "ko")
    assert is_language_dominant("안녕하세요 hi", "ko")
    assert is_language_dominant("Hello 안녕", "en")
    # Five kana + five Latin letters: exactly 50%, not a majority.
    assert not is_language_dominant("こんにちは hello", "ja")
    assert is_language_dominant("こんにちは hello", "ja", threshold=0.4)
    assert not is_language_dominant("가ЖЖ", "ko")


@pytest.mark.parametrize("text", ["", "123", "!? 😀", " \n\t"])
def test_empty_and_nonletter_text_has_no_dominant_language(text):
    assert analyze_language(text).total == 0
    assert not is_language_dominant(text, "en", threshold=0)


@pytest.mark.parametrize("threshold", [-0.1, 1.1, float("nan")])
def test_invalid_threshold_is_rejected(threshold):
    with pytest.raises(ValueError, match="threshold"):
        is_language_dominant("hello", "en", threshold)


def test_unsupported_language_is_rejected_even_for_empty_input():
    with pytest.raises(ValueError, match="Unsupported"):
        is_language_dominant("", "zh")
    with pytest.raises(ValueError, match="Unsupported"):
        LanguageProfile().ratio("zh")


def test_unicode_script_coverage_and_canonical_equivalence():
    assert analyze_language("ㄱᄀ가").ko == 3
    assert analyze_language("ｶﾞカナ漢𠮷").ja == 6
    assert analyze_language("café ＡＢＣ").en == 7
    assert analyze_language(unicodedata.normalize("NFD", "한글 café")) == analyze_language("한글 café")


def test_runs_preserve_separators_and_group_japanese_context():
    text = "Python코드 can't 日本語カナ 123😀!"
    assert list(iter_language_runs(text)) == [
        ("en", "Python"), ("ko", "코드"), (None, " "), ("en", "can't"),
        (None, " "), ("ja", "日本語カナ"), (None, " 123😀!"),
    ]
    assert "".join(run for _, run in iter_language_runs(text)) == text
