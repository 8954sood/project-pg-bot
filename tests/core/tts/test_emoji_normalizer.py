from concurrent.futures import ThreadPoolExecutor

import emoji
import pytest

from core.tts.emoji_normalizer import normalize_tts_emojis
from core.tts.pronunciation import PronunciationPreprocessor


@pytest.mark.parametrize(("language", "expected"), [
    ("ko", "고마워 하트! 미소 엄지 척 축하 폭죽"),
    ("en", "고마워 heart! smile thumbs up party popper"),
    ("ja", "고마워 ハート! にこにこ サムズアップ クラッカー"),
])
def test_unicode_emoji_use_short_names_in_the_target_language(language, expected):
    assert normalize_tts_emojis("고마워 ❤️! 😊👍🎉", language) == expected


@pytest.mark.parametrize("language", ["ko", "en", "ja"])
@pytest.mark.parametrize(("text", "expected"), [
    ("좋아요 <:pepe:123456789>", "좋아요"),
    ("hello<a:dance:123><:wave:456>world", "hello world"),
    ("<:pepe:123> <a:dance:456>", ""),
    ("<:wave:456>\r\n<a:dance:123>", ""),
    ("A <:wave:123> B\nC<a:dance:456>D", "A B\nC D"),
])
def test_custom_emoji_are_removed_without_reading_their_name_or_id(language, text, expected):
    assert normalize_tts_emojis(text, language) == expected


@pytest.mark.parametrize("text", [
    "20% 할인! A+B=3, -2.5 / 10 * 2 #tag @name (test)?",
    "© ™ ® ↔ ♥ ❤ ☀ ☎ ✂ ✳ ℹ 〰 ∑ × ÷ ≠ ≤ ≥ ± € $ ₩ ¥",
    "1 2 3 # * :heart: :smile: <not-an-emoji>",
    "<:name:not-an-id> <a:name:12x> <:name:> <:a<b:123> <:a:１２３>",
    "😀\ufe0e ❤\ufe0e ☀\ufe0e ©\ufe0e ↔\ufe0e",
    "👩\ufe0e\u200d💻 😀\ufe0e\u200d🔥",
    "  일반\t\t문장\r\n  English\n日本語  ",
    "", "  \t\n", "𐀀 Русский Ελληνικά", "\u200d\ufe0f",
])
@pytest.mark.parametrize("language", ["ko", "en", "ja"])
def test_plain_symbols_text_presentation_and_nonemoji_input_are_preserved(text, language):
    assert normalize_tts_emojis(text, language) == text


@pytest.mark.parametrize(("text", "expected"), [
    ("안녕👋반가워", "안녕 손 흔들기 반가워"),
    ("😊😂!", "미소 웃음 눈물!"),
    ("(❤️),20%+3=5?", "(하트),20%+3=5?"),
    ("안녕\n😊\r\n반가워", "안녕\n미소\r\n반가워"),
    ("  안녕\t 😊  친구  ", "안녕 미소 친구"),
    ("👩🏽‍💻", "여자 기술 전문가"),
    ("👨‍👩‍👧‍👦", "가족"),
    ("👨‍👩🏿‍👧🏻‍👦🏾", "가족"),
    ("🏽", ""),
    ("앞🏽뒤", "앞 뒤"),
    ("🇰🇷", "깃발 대한민국"),
    ("🏴\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f", "깃발 잉글랜드"),
    ("1️⃣2⃣#️⃣*️⃣", "1 2#*"),
])
def test_complete_sequences_and_word_boundaries_are_handled(text, expected):
    assert normalize_tts_emojis(text, "ko") == expected


@pytest.mark.parametrize("tone", ["🏻", "🏼", "🏽", "🏾", "🏿"])
@pytest.mark.parametrize(("language", "expected"), [
    ("ko", "엄지 척"), ("en", "thumbs up"), ("ja", "サムズアップ"),
])
def test_skin_tone_does_not_add_a_long_description(tone, language, expected):
    assert normalize_tts_emojis("👍" + tone, language) == expected


@pytest.mark.parametrize(("language", "expected"), [
    ("ko", "수달"), ("en", "otter"), ("ja", "カワウソ"),
])
def test_emoji_without_a_short_override_use_bundled_localized_names(language, expected):
    assert normalize_tts_emojis("🦦", language) == expected


def test_missing_localized_name_uses_localized_fallback_not_english(monkeypatch):
    normalize_tts_emojis("🦦", "ko")
    monkeypatch.delitem(emoji.EMOJI_DATA["🦦"], "ko")

    assert normalize_tts_emojis("🦦", "ko") == "이모지"


@pytest.mark.parametrize(("language", "expected"), [
    ("ko", "이모지"), ("en", "emoji"), ("ja", "絵文字"),
])
def test_nonstandard_zwj_sequence_without_a_base_name_has_a_single_fallback(language, expected):
    assert normalize_tts_emojis("😀\u200d🔥", language) == expected


def test_emoji_names_are_resolved_offline(monkeypatch):
    import socket

    def fail(*args, **kwargs):
        raise AssertionError("Emoji preprocessing must not access the network")

    monkeypatch.setattr(socket, "socket", fail)

    assert normalize_tts_emojis("🦦🇰🇷❤️", "ko") == "수달 깃발 대한민국 하트"


def test_languages_are_independent_across_concurrent_calls():
    languages = ["ko", "en", "ja"] * 12
    with ThreadPoolExecutor(max_workers=6) as executor:
        actual = list(executor.map(lambda lang: normalize_tts_emojis("❤️👍🏿", lang), languages))

    assert actual == ["하트 엄지 척", "heart thumbs up", "ハート サムズアップ"] * 12


@pytest.mark.parametrize(("text", "language", "expected"), [
    ("test ❤️ <:pepe:123>", "ko", "테스트 하트"),
    ("국물 ❤️ <:pepe:123>", "en", "gungmul heart"),
    ("test ❤️ <:pepe:123>", "ja", "テスト ハート"),
])
def test_can_be_composed_before_existing_pronunciation_preprocessor(text, language, expected):
    prepared = normalize_tts_emojis(text, language)
    result = PronunciationPreprocessor().prepare(prepared, language)

    assert result.text == expected
    assert result.complete


@pytest.mark.parametrize("language", ["zh", "", None])
def test_invalid_target_is_rejected_even_for_empty_or_ascii_input(language):
    with pytest.raises(ValueError, match="Unsupported TTS language"):
        normalize_tts_emojis("", language)
