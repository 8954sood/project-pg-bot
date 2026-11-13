from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.tts.reference_voice import MAX_PACKAGE_BYTES
from ui.tts.cog import TTS
from ui.tts.views import VoiceRegistrationModal, VoicePickerView


def make_cog():
    cog = TTS.__new__(TTS)
    voice = dict(style_id='voice-id', name='내 음성', owner_id=1, public=False, is_sample=False, lang='ko')
    cog.service = SimpleNamespace(
        voice_studio_url='https://studio.example/',
        sovits=SimpleNamespace(register_package=AsyncMock(return_value='voice-id'), visible=AsyncMock(return_value=[voice])),
    )
    cog.bot = SimpleNamespace(is_owner=AsyncMock(return_value=False))
    return cog


def make_context(user_id=1):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock(), send_modal=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


@pytest.mark.asyncio
async def test_registration_command_opens_one_modal_with_guide_name_and_upload():
    cog, ctx = make_cog(), make_context()
    await TTS.voice_register.callback(cog, ctx)
    modal = ctx.response.send_modal.call_args.args[0]
    assert isinstance(modal, VoiceRegistrationModal)
    payload = modal.to_dict()
    components = payload['components']
    assert len(components) == 3
    assert 'https://studio.example/' in components[0]['content']
    assert '.pgvoice' in components[0]['content']
    assert components[1]['component']['type'] == 4  # Text input
    assert components[2]['component']['type'] == 19  # File upload
    assert components[2]['component']['max_values'] == 1


@pytest.mark.asyncio
async def test_modal_registration_uses_package_metadata_and_shows_picker():
    cog, ctx = make_cog(), make_context()
    modal = VoiceRegistrationModal(cog.service, 1, cog.bot)
    modal.name_input._value = '내 음성'
    audio = SimpleNamespace(filename='voice.pgvoice', size=100, read=AsyncMock(return_value=b'package'))
    modal.file_input._values = [audio]
    await modal.on_submit(ctx)
    cog.service.sovits.register_package.assert_awaited_once_with(
        1, '내 음성', b'package', filename='voice.pgvoice', sample=False,
    )
    assert isinstance(ctx.followup.send.call_args.kwargs['view'], VoicePickerView)


@pytest.mark.asyncio
@pytest.mark.parametrize('filename,size', [('voice.wav', 100), ('voice.pgvoice', MAX_PACKAGE_BYTES + 1)])
async def test_invalid_attachments_are_rejected_before_download(filename, size):
    cog, ctx = make_cog(), make_context()
    modal = VoiceRegistrationModal(cog.service, 1, cog.bot)
    audio = SimpleNamespace(filename=filename, size=size, read=AsyncMock())
    modal.file_input._values = [audio]
    await modal.on_submit(ctx)
    audio.read.assert_not_awaited()
    cog.service.sovits.register_package.assert_not_awaited()
    assert '.pgvoice' in ctx.followup.send.call_args.args[0]


@pytest.mark.asyncio
async def test_only_owner_can_open_and_submit_public_sample_modal():
    cog, ctx = make_cog(), make_context()
    await TTS.sample_register.callback(cog, ctx)
    ctx.response.send_modal.assert_not_awaited()
    cog.bot.is_owner.return_value = True
    await TTS.sample_register.callback(cog, ctx)
    modal = ctx.response.send_modal.call_args.args[0]
    assert modal.sample
    cog.bot.is_owner.return_value = False
    await modal.on_submit(ctx)
    cog.service.sovits.register_package.assert_not_awaited()


@pytest.mark.asyncio
async def test_registration_modal_rejects_another_user():
    cog, ctx = make_cog(), make_context(2)
    modal = VoiceRegistrationModal(cog.service, 1, cog.bot)
    await modal.on_submit(ctx)
    cog.service.sovits.register_package.assert_not_awaited()
    ctx.response.defer.assert_not_awaited()
