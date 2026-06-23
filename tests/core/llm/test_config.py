from core.llm.config import load_llm_settings, parse_guild_channel_map


def test_parse_guild_channel_map_uses_guild_and_channel_pairs(caplog):
    parsed = parse_guild_channel_map("111:10,11;bad;222:20")

    assert parsed == {"111": {"10", "11"}, "222": {"20"}}
    assert "Invalid LLM_GUILD_CHANNEL_MAP entry ignored" in caplog.text


def test_llm_test_compatible_env_names():
    settings = load_llm_settings(
        {
            "OPENAI_API_KEY": "openai-key",
            "BASE_URL": "https://example.test",
            "MODEL": "main-model",
            "OPENAI_TIMEOUT_SECONDS": "11",
            "OPENAI_TEMPERATURE": "0.2",
            "OPENAI_MAX_TOKENS": "77",
            "LLM_GUILD_CHANNEL_MAP": "1:2",
            "LLM_MAX_RECENT_CONVERSATION_LINES": "5",
        }
    )

    assert settings.main.api_key == "openai-key"
    assert settings.main.base_url == "https://example.test"
    assert settings.main.model == "main-model"
    assert settings.main.timeout_seconds == 11
    assert settings.main.temperature == 0.2
    assert settings.main.max_tokens == 77
    assert settings.max_recent_conversation_lines == 5
    assert settings.is_allowed("1", "2")


def test_llm_default_timeout_allows_slow_cloud_responses():
    settings = load_llm_settings({})

    assert settings.main.timeout_seconds == 180
