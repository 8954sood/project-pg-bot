from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.tts.service import TTSService
from ui.tts.cog import TTS
from ui.tts.views import DeleteVoiceView, VoicePickerView


def voices(count=1):
    return [dict(style_id=f'{i:032x}', name=f'보이스 {i}', owner_id=1, public=False, is_sample=False, lang='ko') for i in range(count)]


def context(user_id=1):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        response=SimpleNamespace(defer=AsyncMock(), edit_message=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()), edit_original_response=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_select_paginates_more_than_25_voices_without_visible_style_ids():
    ctx = context()
    view = VoicePickerView(SimpleNamespace(), 1, voices(32), SimpleNamespace())
    assert len(view.menu.options) == 25
    assert view.menu.options[0].label == '1. 보이스 0'
    assert view.menu.options[0].value not in view.menu.options[0].label
    await view.next_page(ctx)
    assert len(view.menu.options) == 7
    assert view.menu.options[0].value == f'{25:032x}'
    await view.previous_page(ctx)
    assert len(view.menu.options) == 25
    assert len(view.to_components()) == 1


@pytest.mark.asyncio
async def test_voice_selection_saves_only_after_choosing_an_option():
    ctx = context()
    service = SimpleNamespace(select_voice=AsyncMock(return_value='내 음성'))
    view = VoicePickerView(service, 1, voices(), SimpleNamespace())
    service.select_voice.assert_not_awaited()
    view.menu._values = [voices()[0]['style_id']]
    await view.choose(ctx)
    service.select_voice.assert_awaited_once_with(1, voices()[0]['style_id'])
    ctx.edit_original_response.assert_awaited_once()


@pytest.mark.asyncio
async def test_selection_rechecks_public_access_in_core():
    service = TTSService.__new__(TTSService)
    service.sovits = SimpleNamespace(configured=lambda: True, visible=AsyncMock(return_value=[]))
    service.set_tts_engine_option = AsyncMock()
    with pytest.raises(ValueError, match='삭제|비공개'):
        await service.select_voice(1, 'stale-id')
    service.set_tts_engine_option.assert_not_awaited()


@pytest.mark.asyncio
async def test_picker_can_only_be_operated_by_its_owner():
    ctx = context(2)
    view = VoicePickerView(SimpleNamespace(), 1, voices(), SimpleNamespace())
    assert not await view.interaction_check(ctx)
    ctx.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_command_uses_filtered_list_and_requires_confirmation():
    ctx = context()
    service = SimpleNamespace(sovits=SimpleNamespace(deletable=AsyncMock(return_value=voices()), delete=AsyncMock()))
    bot = SimpleNamespace(is_owner=AsyncMock(return_value=False))
    cog = SimpleNamespace(service=service, bot=bot)
    await TTS.voice_delete.callback(cog, ctx)
    service.sovits.deletable.assert_awaited_once_with(1, owner=False)
    picker = ctx.followup.send.call_args.kwargs['view']
    picker.menu._values = [voices()[0]['style_id']]
    await picker.choose(ctx)
    service.sovits.delete.assert_not_awaited()
    confirmation = ctx.response.edit_message.call_args.kwargs['view']
    assert isinstance(confirmation, DeleteVoiceView)
    await confirmation.confirm(ctx)
    service.sovits.delete.assert_awaited_once_with(1, voices()[0]['style_id'], owner=False)


@pytest.mark.asyncio
async def test_delete_cancel_keeps_voice():
    ctx = context()
    service = SimpleNamespace(sovits=SimpleNamespace(delete=AsyncMock()))
    confirmation = DeleteVoiceView(service, 1, voices()[0], SimpleNamespace())
    await confirmation.cancel(ctx)
    service.sovits.delete.assert_not_awaited()
