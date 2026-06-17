from core.tts.text_normalizer import build_tts_text, normalize_tts_text


def test_question_only_text_is_read_as_repeated_question_marks():
    assert normalize_tts_text("????") == "물음표물음표물음표"


def test_period_only_text_is_read_as_repeated_dots():
    assert normalize_tts_text("....") == "점점점"


def test_mixed_text_replaces_question_and_period():
    assert normalize_tts_text("a?b.") == "a물음표b(점)"


def test_image_text_prefixes_existing_text():
    assert build_tts_text("hello", True) == "(이미지)hello"


def test_image_text_uses_image_only_for_empty_content():
    assert build_tts_text("", True) == "(이미지)"
