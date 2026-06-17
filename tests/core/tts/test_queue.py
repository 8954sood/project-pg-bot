from core.tts.queue import enqueue_tts, truncate_text


def make_voice_model():
    return {
        "guild_id": 1,
        "voice_channel_id": 10,
        "tts_queue": [],
        "vc": object(),
    }


def test_empty_text_is_not_queued():
    voice_model = make_voice_model()

    queued = enqueue_tts(
        guild_id=1,
        voice_model=voice_model,
        text="   ",
        user_id=2,
        channel_id=3,
        max_queue_size=10,
        max_text_length=20,
    )

    assert queued is False
    assert voice_model["tts_queue"] == []


def test_long_text_is_truncated_with_ellipsis():
    voice_model = make_voice_model()

    queued = enqueue_tts(
        guild_id=1,
        voice_model=voice_model,
        text="123456789",
        user_id=2,
        channel_id=3,
        max_queue_size=10,
        max_text_length=5,
    )

    assert queued is True
    assert voice_model["tts_queue"][0]["text"] == "1234…"


def test_queue_full_drops_new_message():
    voice_model = make_voice_model()
    voice_model["tts_queue"].append({"text": "existing", "user_id": 1})

    queued = enqueue_tts(
        guild_id=1,
        voice_model=voice_model,
        text="new",
        user_id=2,
        channel_id=3,
        max_queue_size=1,
        max_text_length=20,
    )

    assert queued is False
    assert voice_model["tts_queue"] == [{"text": "existing", "user_id": 1}]


def test_truncate_text_handles_limit_one():
    assert truncate_text("hello", 1) == "…"
