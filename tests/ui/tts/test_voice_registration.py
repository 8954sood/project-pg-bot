from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.tts.reference_voice import MAX_PACKAGE_BYTES
from ui.tts.cog import TTS


def make_cog():
    cog = TTS.__new__(TTS)
    cog.service = SimpleNamespace(sovits=SimpleNamespace(register_package=AsyncMock(return_value='voice-id')))
    cog.bot = SimpleNamespace(is_owner=AsyncMock(return_value=False))
    return cog


def make_context():
    return SimpleNamespace(
        user=SimpleNamespace(id=1),
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


@pytest.mark.asyncio
async def test_personal_registration_uses_package_metadata_in_core():
    cog, ctx = make_cog(), make_context()
    audio = SimpleNamespace(filename='voice.pgvoice', size=100, read=AsyncMock(return_value=b'package'))
    await TTS.voice_register.callback(cog, ctx, '내 음성', audio)
    cog.service.sovits.register_package.assert_awaited_once_with(
        1, '내 음성', b'package', filename='voice.pgvoice', sample=False,
    )
    assert ctx.followup.send.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('filename,size', [('voice.wav', 100), ('voice.pgvoice', MAX_PACKAGE_BYTES + 1)])
async def test_invalid_attachments_are_rejected_before_download(filename, size):
    cog, ctx = make_cog(), make_context()
    audio = SimpleNamespace(filename=filename, size=size, read=AsyncMock())
    await TTS.voice_register.callback(cog, ctx, '내 음성', audio)
    audio.read.assert_not_awaited()
    cog.service.sovits.register_package.assert_not_awaited()
    assert '.pgvoice' in ctx.followup.send.call_args.args[0]


@pytest.mark.asyncio
async def test_only_owner_can_upload_public_samples():
    cog, ctx = make_cog(), make_context()
    audio = SimpleNamespace(filename='voice.pgvoice', size=100, read=AsyncMock(return_value=b'package'))
    await TTS.sample_register.callback(cog, ctx, '샘플', audio)
    audio.read.assert_not_awaited()
    cog.bot.is_owner.return_value = True
    await TTS.sample_register.callback(cog, ctx, '샘플', audio)
    cog.service.sovits.register_package.assert_awaited_once_with(
        1, '샘플', b'package', filename='voice.pgvoice', sample=True,
    )
