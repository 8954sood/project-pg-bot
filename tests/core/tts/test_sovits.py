import asyncio

import pytest
import pytest_asyncio
from aiohttp import web

from core.local.tts.voice_store import VoiceStore
from core.tts.sovits import SovitsService


@pytest.mark.asyncio
async def test_voice_ownership_and_existing_samples(tmp_path, monkeypatch):
    monkeypatch.setattr('core.local.tts.voice_store.db_path', str(tmp_path / 'test.sqlite'))
    await VoiceStore.init_table()
    app = web.Application()
    calls = []

    async def register(request):
        form = await request.post()
        calls.append(form['prompt_text'])
        return web.json_response({'style_id': f'id{len(calls)}'}, status=201)

    async def get_style(request):
        return web.json_response({'name': '기본', 'prompt_lang': 'ko'})

    async def delete(request):
        return web.Response(status=204)

    app.router.add_post('/v1/styles', register)
    app.router.add_get('/v1/styles/{style_id}', get_style)
    app.router.add_delete('/v1/styles/{style_id}', delete)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    service = SovitsService(f'http://127.0.0.1:{port}')
    try:
        assert await service.register(1, '내 음성', b'wav', '안녕하세요', 'ko') == 'id1'
        with pytest.raises(ValueError, match='한 개'):
            await service.register(1, '다른 음성', b'wav', '안녕하세요', 'ko')
        assert await service.visible(2) == []
        await service.set_public(1, True)
        assert len(await service.visible(2)) == 1
        await service.set_public(1, False)
        assert await service.visible(2) == []
        await service.add_existing_sample(99, 'a' * 32)
        assert [v['style_id'] for v in await service.visible(2)] == ['a' * 32]
        with pytest.raises(ValueError):
            await service.delete(2, 'id1')
        with pytest.raises(ValueError):
            await service.delete(99, 'a' * 32)
        await service.delete(99, 'a' * 32, owner=True)
        await service.delete(1, 'id1')
        assert await service.visible(1) == []
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_registration_is_serialized_and_retries_busy(tmp_path, monkeypatch):
    monkeypatch.setattr('core.local.tts.voice_store.db_path', str(tmp_path / 'test.sqlite'))
    await VoiceStore.init_table()
    app = web.Application()
    calls = 0

    async def register(request):
        nonlocal calls
        calls += 1
        form = await request.post()
        assert form['prompt_text'] == '실제 발화'
        if calls == 1:
            return web.Response(status=429)
        return web.json_response({'style_id': f'id{calls}'}, status=201)

    app.router.add_post('/v1/styles', register)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    service = SovitsService(f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}")
    try:
        results = await asyncio.gather(
            service.register(1, 'a', b'wav', '실제 발화', 'ko'),
            service.register(1, 'b', b'wav', '실제 발화', 'ko'),
            return_exceptions=True,
        )
        assert results[0] == 'id2'
        assert isinstance(results[1], ValueError)
        assert calls == 2
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_korean_engine_command_checks_voice_access():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from ui.tts.cog import TTS

    service = SimpleNamespace(
        sovits=SimpleNamespace(configured=lambda: True, visible=AsyncMock(return_value=[{'style_id': 'voice'}])),
        set_tts_engine_option=AsyncMock(),
    )
    cog = SimpleNamespace(service=service)
    ctx = SimpleNamespace(user=SimpleNamespace(id=1), response=SimpleNamespace(send_message=AsyncMock()))
    choice = SimpleNamespace(value='sovits')
    await TTS.change_engine.callback(cog, ctx, choice, 'private')
    service.set_tts_engine_option.assert_not_awaited()
    await TTS.change_engine.callback(cog, ctx, choice, 'voice')
    service.set_tts_engine_option.assert_awaited_once_with(user_id=1, engine='sovits', model_name='voice')
    await TTS.change_engine.callback(cog, ctx, SimpleNamespace(value='gtts'))
    service.set_tts_engine_option.assert_awaited_with(user_id=1, engine='gtts', model_name=None)


@pytest_asyncio.fixture
async def voice_server(tmp_path, monkeypatch):
    monkeypatch.setattr('core.local.tts.voice_store.db_path', str(tmp_path / 'voices.sqlite'))
    await VoiceStore.init_table()
    runners = []

    async def start(app):
        runner = web.AppRunner(app)
        runners.append(runner)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        return f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'

    yield start
    for runner in reversed(runners):
        await runner.cleanup()


def voice_package(text='실제 녹음한 대사입니다. ❤️ Python?', language='ko'):
    import hashlib
    import io
    import json
    import zipfile

    from core.tts.reference_voice import SAMPLE_RATE, VOICE_CREATOR, WAV_HEADER

    samples = SAMPLE_RATE * 3
    audio = WAV_HEADER.pack(
        b'RIFF', 36 + samples * 2, b'WAVE', b'fmt ', 16, 1, 1,
        SAMPLE_RATE, SAMPLE_RATE * 2, 2, 16, b'data', samples * 2,
    ) + b'\x01\0' * samples
    manifest = {
        'format': 'pgvoice', 'version': 1, 'created_by': VOICE_CREATOR,
        'reference': {'prompt_text': text, 'prompt_lang': language},
        'audio': {
            'file': 'reference.wav', 'encoding': 'pcm_s16le', 'sample_rate': SAMPLE_RATE,
            'channels': 1, 'sample_count': samples, 'sha256': hashlib.sha256(audio).hexdigest(),
        },
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as package:
        package.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False))
        package.writestr('reference.wav', audio)
    return output.getvalue(), audio


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['ko', 'en', 'ja'])
async def test_package_registration_forwards_original_audio_and_transcript(voice_server, language):
    transcript = '원문. Python? ❤️ 日本語!'
    data, audio = voice_package(transcript, language)
    app = web.Application()

    async def register(request):
        form = await request.post()
        assert form['prompt_text'] == transcript
        assert form['prompt_lang'] == language
        assert form['audio'].file.read() == audio
        assert form['name'] == 'Voice Studio'
        return web.json_response({'style_id': 'b' * 32}, status=201)

    app.router.add_post('/v1/styles', register)
    service = SovitsService(await voice_server(app))
    await service.register_package(1, 'Voice Studio', data, filename='voice.PGVOICE')
    rows = await service.visible(1)
    assert rows[0]['lang'] == language
    assert not rows[0]['public']


@pytest.mark.asyncio
async def test_package_rejection_never_uploads_or_saves(voice_server):
    from unittest.mock import AsyncMock

    service = SovitsService('http://unused')
    service.register = AsyncMock()
    data, _ = voice_package()
    for payload, filename in [(data, 'renamed.wav'), (b'broken', 'broken.pgvoice')]:
        with pytest.raises(ValueError):
            await service.register_package(1, 'test', payload, filename=filename)
    service.register.assert_not_awaited()
    assert await VoiceStore.all() == []


@pytest.mark.asyncio
async def test_valid_package_beyond_server_transcript_limit_is_not_truncated(voice_server):
    data, _ = voice_package('가' * 1001)
    service = SovitsService('http://unused')
    with pytest.raises(ValueError, match='1,000자'):
        await service.register_package(1, 'test', data, filename='voice.pgvoice')
    assert await VoiceStore.all() == []


@pytest.mark.asyncio
async def test_sovits_prepares_emoji_and_pronunciation_in_target_language():
    from core.tts.language import analyze_language
    from core.tts.text_normalizer import EmptyTTSText

    service = SovitsService('')
    assert await service.prepare_text('test ❤️ 日本語!', 'ko') == '테스트 하트 니혼고!'
    for language in ('ko', 'en', 'ja'):
        result = await service.prepare_text('안녕하세요 test 日本語 ❤️?', language)
        assert analyze_language(result).ratio(language) == 1
        assert result.endswith('?')
        assert '❤️' not in result
    with pytest.raises(EmptyTTSText):
        await service.prepare_text('<:custom:123>', 'ko')
    with pytest.raises(ValueError, match='변환할 수 없는'):
        await service.prepare_text('안녕 Русский', 'ko')
    with pytest.raises(ValueError, match='2,000자'):
        await service.prepare_text('가' * 2001, 'ko')


@pytest.mark.asyncio
async def test_streams_share_one_lock_and_send_prepared_text(voice_server, monkeypatch):
    import io
    from types import SimpleNamespace
    from unittest.mock import Mock

    app = web.Application()
    finish = asyncio.Event()
    requests = []

    async def stream(request):
        requests.append(await request.json())
        response = web.StreamResponse()
        await response.prepare(request)
        await response.write(b'\0' * 960)
        await finish.wait()
        await response.write_eof()
        return response

    app.router.add_post('/v1/tts/stream', stream)
    service = SovitsService(await voice_server(app))
    await VoiceStore.add('a' * 32, 1, '한국어', True, False, 'ko')
    processes = []

    def process(*args, **kwargs):
        proc = SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO(),
                               poll=lambda: 0, terminate=Mock(), wait=Mock())
        processes.append(proc)
        return proc

    monkeypatch.setattr('core.tts.sovits.subprocess.Popen', process)
    source = await service.create_discord_source(text='test ❤️!', model_name='a' * 32, user_id=1)
    second = asyncio.create_task(service.create_discord_source(text='안녕?', model_name='a' * 32, user_id=2))
    try:
        await asyncio.sleep(0.02)
        assert len(requests) == 1
        assert service.lock.locked()
        assert requests[0] == {'style_id': 'a' * 32, 'text': '테스트 하트!', 'lang': 'ko'}
        finish.set()
        other_source = await asyncio.wait_for(second, 2)
        await asyncio.wait_for(asyncio.gather(source.feed_task, other_source.feed_task), 2)
        assert len(requests) == 2
        assert not service.lock.locked()
        other_source.cleanup()
    finally:
        finish.set()
        second.cancel()
        await asyncio.gather(second, return_exceptions=True)
        source.cleanup()
