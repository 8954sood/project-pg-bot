from types import SimpleNamespace

from core.tts.engine_selector import TTSEngineSelector


def test_missing_user_engine_defaults_to_gtts():
    selector = TTSEngineSelector({}, set())

    selection = selector.get_user_engine(1)

    assert selection.engine == "gtts"
    assert selection.model_name is None


def test_user_engine_option_returns_configured_ai_model():
    selector = TTSEngineSelector(
        {1: SimpleNamespace(engine="ai", model_name="model-a")},
        set(),
    )

    selection = selector.get_user_engine(1)

    assert selection.engine == "ai"
    assert selection.model_name == "model-a"
    assert selection.uses_ai


def test_engine_change_permission_uses_allow_set():
    selector = TTSEngineSelector({}, {1})

    assert selector.is_engine_change_allowed(1) is True
    assert selector.is_engine_change_allowed(2) is False


def test_ai_requires_model_and_available_engine():
    selector = TTSEngineSelector({}, set())

    assert selector.should_try_ai(SimpleNamespace(engine="ai", model_name="m"), True)
    assert not selector.should_try_ai(SimpleNamespace(engine="ai", model_name=None), True)
    assert not selector.should_try_ai(SimpleNamespace(engine="ai", model_name="m"), False)
