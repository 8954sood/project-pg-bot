from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.tts.engine_selector import TTSEngineSelector
from core.tts.engines.gtts_engine import GTTSEngine
from core.tts.service import create_tts_source_with_dependencies
from core.tts.sovits import SovitsService
from core.tts.text_normalizer import EmptyTTSText, build_tts_input


def test_queue_input_keeps_sentences_and_removes_only_custom_emoji():
    assert build_tts_input('test ❤️. 안녕? <:wave:123>', False) == 'test ❤️. 안녕?'
    assert build_tts_input('<a:dance:123>', False) == ''
    assert build_tts_input('<:wave:123>', True) == '(이미지)'


@pytest.mark.parametrize('language,name', [('ko', '하트'), ('en', 'heart'), ('ja', 'ハート')])
def test_gtts_reads_emoji_in_user_language_without_transliterating_text(monkeypatch, language, name):
    calls = []

    def synth(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(write_to_fp=lambda fp: fp.write(b'mp3'))

    monkeypatch.setattr('core.tts.engines.gtts_engine.gTTS', synth)
    engine = GTTSEngine(lambda user_id: language)
    assert engine._synth_sync('Python ❤️ <:wave:123>', 1).read() == b'mp3'
    assert calls == [{'text': f'Python {name}', 'lang': language, 'timeout': None}]


def test_gtts_preserves_existing_other_languages_and_punctuation_rules(monkeypatch):
    calls = []
    monkeypatch.setattr('core.tts.engines.gtts_engine.gTTS', lambda **kwargs: (
        calls.append(kwargs) or SimpleNamespace(write_to_fp=lambda fp: fp.write(b'mp3'))
    ))
    GTTSEngine(lambda user_id: 'fr')._synth_sync('Bonjour?', 1)
    assert calls[0]['lang'] == 'fr'
    assert calls[0]['text'] == 'Bonjour물음표'


@pytest.mark.asyncio
async def test_sovits_fallback_uses_original_text_not_converted_text(monkeypatch):
    options = {1: SimpleNamespace(engine='sovits', model_name='voice')}
    raw = 'test ❤️ Русский?'
    engine = SovitsService('')
    converted = await engine.prepare_text('test ❤️?', 'ko')
    assert converted != 'test ❤️?'
    # The actual preprocessor rejects this unresolved script before an HTTP call.
    sovits = SimpleNamespace(create_discord_source=AsyncMock(side_effect=ValueError('unresolved')))
    gtts = SimpleNamespace(synth=AsyncMock(return_value=BytesIO(b'mp3')))
    source = object()
    monkeypatch.setattr('core.tts.service.discord.FFmpegPCMAudio', lambda *args, **kwargs: source)
    result = await create_tts_source_with_dependencies(
        guild_id=1, queue_item={'text': raw, 'user_id': 1},
        engine_selector=TTSEngineSelector(options, set()), ai_engine=None,
        sovits_engine=sovits, gtts_engine=gtts, gtts_timeout=10,
    )
    assert result == (source, 'gtts')
    gtts.synth.assert_awaited_once_with(text=raw, user_id=1, timeout=10)


@pytest.mark.asyncio
async def test_empty_sovits_input_is_skipped_without_gtts_fallback():
    options = {1: SimpleNamespace(engine='sovits', model_name='voice')}
    sovits = SimpleNamespace(create_discord_source=AsyncMock(side_effect=EmptyTTSText()))
    gtts = SimpleNamespace(synth=AsyncMock())
    with pytest.raises(EmptyTTSText):
        await create_tts_source_with_dependencies(
            guild_id=1, queue_item={'text': '<:wave:123>', 'user_id': 1},
            engine_selector=TTSEngineSelector(options, set()), ai_engine=None,
            sovits_engine=sovits, gtts_engine=gtts, gtts_timeout=10,
        )
    gtts.synth.assert_not_awaited()


@pytest.mark.asyncio
async def test_configured_pronunciation_dictionary_is_used(tmp_path, monkeypatch):
    path = tmp_path / 'pronunciation.json'
    path.write_text('{"ko": {"Galaxy": "갤럭시"}}')
    monkeypatch.setenv('TTS_PRONUNCIATION_DICTIONARY', str(path))
    engine = SovitsService('')
    assert await engine.prepare_text('Galaxy ❤️!', 'ko') == '갤럭시 하트!'
