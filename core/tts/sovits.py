"""GPT-SoVITS style ownership and its single-operation HTTP interface."""
import asyncio
import logging
import os
import re

import aiohttp

from core.local.tts.voice_store import VoiceStore
from core.tts.engines.pcm_source import PCMStreamAudioSource
from core.tts.emoji_normalizer import normalize_tts_emojis
from core.tts.language import validate_language
from core.tts.pronunciation import PronunciationPreprocessor
from core.tts.reference_voice import parse_reference_voice
from core.tts.text_normalizer import EmptyTTSText

logger = logging.getLogger(__name__)
MAX_AUDIO_BYTES = 8 * 1024 * 1024


class SovitsService:
    def __init__(self, url, store=VoiceStore, *, pronunciation=None):
        self.url = url.rstrip('/')
        self.store = store
        self.lock = asyncio.Lock()
        self.management_lock = asyncio.Lock()
        dictionary = os.getenv("TTS_PRONUNCIATION_DICTIONARY", "").strip()
        self.pronunciation = pronunciation if pronunciation is not None else (
            PronunciationPreprocessor.from_json(dictionary)
            if dictionary else PronunciationPreprocessor()
        )

    def configured(self):
        return bool(self.url)

    async def _request(self, session, method, path, **kwargs):
        # A different client may be using the server, even while our lock is held.
        for attempt in range(60):
            response = await session.request(method, self.url + path, **kwargs)
            if response.status != 429:
                if response.status >= 400:
                    detail = await response.text()
                    response.release()
                    raise RuntimeError(f'음성 서버 오류 ({response.status}): {detail[:300]}')
                return response
            response.release()
            await asyncio.sleep(1)
        raise RuntimeError('음성 서버가 계속 사용 중입니다. 잠시 후 다시 시도해주세요.')

    async def visible(self, user_id):
        return [v for v in await self.store.all() if v['public'] or v['owner_id'] == user_id]

    async def deletable(self, user_id, *, owner=False):
        return [voice for voice in await self.store.all() if (
            (voice['is_sample'] and owner)
            or (not voice['is_sample'] and voice['owner_id'] == user_id)
        )]

    async def register_package(self, user_id, name, data, *, filename, sample=False):
        """Only validated Voice Studio packages enter the Discord upload path.

        Reference transcripts describe the recording and must never pass through
        emoji normalization, pronunciation conversion or text truncation.
        """
        reference = await asyncio.to_thread(parse_reference_voice, data, filename=filename)
        return await self.register(
            user_id, name, reference.audio, reference.prompt_text,
            reference.prompt_lang, sample=sample,
        )

    async def register(self, user_id, name, audio, prompt_text, lang, *, sample=False):
        if not self.configured():
            raise ValueError('GPT-SoVITS 서버가 설정되지 않았습니다.')
        if not audio or len(audio) > MAX_AUDIO_BYTES:
            raise ValueError('WAV 파일은 8 MiB 이하여야 합니다.')
        if not name.strip() or not prompt_text.strip():
            raise ValueError('이름과 실제 녹음한 문장을 입력해주세요.')
        if len(name.strip()) > 80:
            raise ValueError('음성 이름은 80자 이하여야 합니다.')
        if len(prompt_text) > 1000:
            raise ValueError('현재 음성 서버는 참조 대사를 1,000자까지 지원합니다. Voice Studio에서 줄여주세요.')
        validate_language(lang)
        async with self.management_lock:
            voices = await self.store.all()
            if not sample and any(v['owner_id'] == user_id and not v['is_sample'] for v in voices):
                raise ValueError('개인 음성은 한 개만 등록할 수 있습니다. 먼저 기존 음성을 삭제해주세요.')
            async with self.lock, aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300)) as session:
                # Multipart bodies cannot be reused after a retry; build a fresh body each time.
                for attempt in range(60):
                    form = aiohttp.FormData()
                    for key, value in [('name', name), ('prompt_text', prompt_text), ('prompt_lang', lang)]:
                        form.add_field(key, value)
                    form.add_field('audio', audio, filename='reference.wav', content_type='audio/wav')
                    response = await session.post(self.url + '/v1/styles', data=form)
                    if response.status != 429:
                        break
                    response.release()
                    await asyncio.sleep(1)
                else:
                    raise RuntimeError('음성 서버가 계속 사용 중입니다.')
                async with response:
                    if response.status >= 400:
                        raise RuntimeError(f'음성 등록 실패 ({response.status}): {(await response.text())[:300]}')
                    style_id = (await response.json())['style_id']
                try:
                    await self.store.add(style_id, user_id, name.strip(), sample, sample, lang)
                except Exception:
                    try:
                        response = await self._request(session, 'DELETE', '/v1/styles/' + style_id)
                        response.release()
                    except Exception:
                        logger.exception('Failed to clean up untracked style %s', style_id)
                    raise
            return style_id

    async def add_existing_sample(self, user_id, style_id):
        if not re.fullmatch(r"[a-f0-9]{32}", style_id):
            raise ValueError("올바른 스타일 ID를 입력해주세요.")
        if not self.configured():
            raise ValueError('GPT-SoVITS 서버가 설정되지 않았습니다.')
        async with self.management_lock:
            if any(v['style_id'] == style_id for v in await self.store.all()):
                raise ValueError('이미 봇에 등록된 음성입니다.')
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                response = await self._request(session, 'GET', '/v1/styles/' + style_id)
                async with response:
                    voice = await response.json()
            await self.store.add(style_id, user_id, voice['name'], True, True, voice['prompt_lang'])

    async def delete(self, user_id, style_id, *, owner=False):
        async with self.management_lock:
            voice = next((v for v in await self.store.all() if v['style_id'] == style_id), None)
            if not voice or (voice['is_sample'] and not owner) or (not voice['is_sample'] and voice['owner_id'] != user_id):
                raise ValueError('삭제할 수 있는 음성이 아닙니다.')
            async with self.lock, aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
                response = await self._request(session, 'DELETE', '/v1/styles/' + style_id)
                response.release()
                await self.store.delete(style_id)

    async def set_public(self, user_id, public):
        async with self.management_lock:
            voice = next((v for v in await self.store.all() if v['owner_id'] == user_id and not v['is_sample']), None)
            if not voice:
                raise ValueError('먼저 개인 음성을 등록해주세요.')
            await self.store.set_public(voice['style_id'], public)

    async def create_discord_source(self, *, text, model_name, user_id):
        await self.lock.acquire()
        source = None
        session = None
        response = None
        task = None
        try:
            voice = next((v for v in await self.visible(user_id) if v['style_id'] == model_name), None)
            if not voice:
                raise ValueError('선택한 음성이 삭제되었거나 비공개로 전환되었습니다.')
            text = await self.prepare_text(text, voice['lang'])
            session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300, sock_read=120))
            response = await self._request(session, 'POST', '/v1/tts/stream', json={
                'style_id': model_name, 'text': text, 'lang': voice['lang']})
            expected = {
                'X-Audio-Format': 's16le',
                'X-Audio-Sample-Rate': '48000',
                'X-Audio-Channels': '1',
            }
            if any(response.headers.get(key) != value for key, value in expected.items()):
                raise RuntimeError('음성 서버의 PCM 오디오 형식이 올바르지 않습니다.')
            source = PCMStreamAudioSource(asyncio.get_running_loop())

            async def feed():
                error = None
                try:
                    async for chunk in response.content.iter_chunked(8192):
                        if not await asyncio.to_thread(source.write, chunk):
                            break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    error = exc
                    logger.exception('GPT-SoVITS stream interrupted')
                finally:
                    source.finish(error)
                    response.close()
                    try:
                        await session.close()
                    finally:
                        self.lock.release()

            task = asyncio.create_task(feed())
            source.feed_task = task
            # Do not mark Discord as speaking until a playable frame exists.
            await source.wait_ready()
            logger.info('GPT-SoVITS PCM playback ready', extra={
                'user_id': user_id, 'style_id': model_name,
                'audio_format': 's16le', 'sample_rate': 48000,
            })
            return source
        except BaseException:
            if source:
                source.cleanup()
            if task:
                await asyncio.gather(task, return_exceptions=True)
            else:
                if response is not None:
                    response.close()
                if session:
                    await session.close()
                self.lock.release()
            raise

    async def prepare_text(self, text, language):
        """Prepare synthesis text in the selected style's language off-loop."""
        def prepare():
            normalized = normalize_tts_emojis(text, language)
            if not normalized.strip():
                raise EmptyTTSText('이모지 처리 후 읽을 내용이 없습니다.')
            result = self.pronunciation.prepare(normalized, language)
            if not result.complete:
                raise ValueError('선택한 음성 언어로 변환할 수 없는 문자가 있습니다.')
            if len(result.text) > 2000:
                raise ValueError('발음 변환 후 음성 서버의 2,000자 한도를 초과했습니다.')
            return result.text

        return await asyncio.to_thread(prepare)
