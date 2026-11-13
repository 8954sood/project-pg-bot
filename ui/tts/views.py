"""Voice registration and selection screens. Ownership stays in Core services."""
import logging

import discord

from core.tts.reference_voice import MAX_PACKAGE_BYTES
from ui.common.views import OwnedLayoutView, status_view

logger = logging.getLogger(__name__)
_LANGUAGE_NAMES = {'ko': '한국어', 'en': '영어', 'ja': '일본어'}


def registration_guide(studio_url: str, *, sample=False) -> str:
    visibility = '모든 사용자가 쓸 수 있는 공개 샘플로 등록됩니다.' if sample else '개인 음성은 한 개이며 처음에는 비공개로 등록됩니다.'
    return (
        f'[Voice Studio 열기]({studio_url})\n'
        '1. 사이트에서 언어와 대사를 선택하고 녹음하거나 기존 음성을 가져오세요.\n'
        '2. 잡음 없는 **3~10초** 구간을 고르고 실제 녹음과 대사가 일치하는지 확인하세요.\n'
        '3. **.pgvoice로 저장**한 뒤 아래에 파일을 첨부하고 이름을 입력하세요.\n'
        f'대사와 언어는 파일에서 자동으로 읽습니다. {visibility}\n'
        '본인에게 사용 권한이 있는 음성만 등록해주세요.'
    )


class VoiceRegistrationModal(discord.ui.Modal):
    def __init__(self, service, owner_id, bot, *, sample=False):
        super().__init__(title='공개 샘플 보이스 등록' if sample else '내 보이스 등록', timeout=900)
        self.service = service
        self.owner_id = owner_id
        self.bot = bot
        self.sample = sample
        self.name_input = discord.ui.TextInput(placeholder='목록에 표시할 이름', max_length=80)
        self.file_input = discord.ui.FileUpload(required=True, min_values=1, max_values=1)
        self.add_item(discord.ui.TextDisplay(registration_guide(service.voice_studio_url, sample=sample)))
        self.add_item(discord.ui.Label(text='보이스 이름', component=self.name_input))
        self.add_item(discord.ui.Label(
            text='Voice Studio에서 저장한 .pgvoice 파일',
            description='일반 WAV/ZIP는 지원하지 않습니다. 최대 700,000바이트.',
            component=self.file_input,
        ))

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('등록 창을 연 사용자만 제출할 수 있습니다.', ephemeral=True)
            return False
        if self.sample and not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message('봇 소유자만 공개 샘플을 등록할 수 있습니다.', ephemeral=True)
            return False
        return True

    async def on_submit(self, interaction):
        if not await self.interaction_check(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            files = self.file_input.values
            if len(files) != 1:
                raise ValueError('.pgvoice 파일을 한 개 첨부해주세요.')
            audio = files[0]
            if not audio.filename.lower().endswith('.pgvoice') or not 1 <= audio.size <= MAX_PACKAGE_BYTES:
                raise ValueError('Voice Studio에서 저장한 700,000바이트 이하 .pgvoice 파일을 첨부해주세요.')
            await self.service.sovits.register_package(
                interaction.user.id, self.name_input.value, await audio.read(),
                filename=audio.filename, sample=self.sample,
            )
            voices = await self.service.sovits.visible(interaction.user.id)
            await interaction.followup.send(view=VoicePickerView(
                self.service, interaction.user.id, voices, self.bot,
                notice='등록했습니다. 아래 목록에서 사용할 보이스를 선택하세요.',
            ), ephemeral=True)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
        except Exception:
            logger.exception('Voice package registration failed')
            await interaction.followup.send('음성 서버에 등록하지 못했습니다. 잠시 후 다시 시도해주세요.', ephemeral=True)

    async def on_error(self, interaction, error):
        logger.error('Voice registration modal failed', exc_info=(type(error), error, error.__traceback__))
        if interaction.response.is_done():
            await interaction.followup.send('음성 등록 중 오류가 발생했습니다.', ephemeral=True)
        else:
            await interaction.response.send_message('음성 등록 중 오류가 발생했습니다.', ephemeral=True)


async def show_voice_picker(interaction, service, bot, *, action='select'):
    await interaction.response.defer(ephemeral=True, thinking=True)
    if action == 'delete':
        voices = await service.sovits.deletable(interaction.user.id, owner=await bot.is_owner(interaction.user))
    else:
        voices = await service.sovits.visible(interaction.user.id)
    if not voices:
        message = '삭제할 수 있는 보이스가 없습니다.' if action == 'delete' else '사용할 수 있는 보이스가 없습니다. `/보이스등록`으로 등록해주세요.'
        return await interaction.followup.send(message, ephemeral=True)
    await interaction.followup.send(
        view=VoicePickerView(service, interaction.user.id, voices, bot, action=action), ephemeral=True,
    )


class VoicePickerView(OwnedLayoutView):
    PAGE_SIZE = 25

    def __init__(self, service, owner_id, voices, bot, *, action='select', notice=''):
        super().__init__(owner_id)
        self.service = service
        self.voices = voices
        self.bot = bot
        self.action = action
        self.notice = notice
        self.page = 0
        self.render()

    def render(self):
        self.clear_items()
        title = '삭제할 보이스 선택' if self.action == 'delete' else '사용할 보이스 선택'
        pages = max(1, (len(self.voices) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        options = []
        for index, voice in enumerate(self.voices[self.page * self.PAGE_SIZE:(self.page + 1) * self.PAGE_SIZE]):
            kind = '공개 샘플' if voice['is_sample'] else ('내 보이스' if voice['owner_id'] == self.owner_id else '공개 보이스')
            options.append(discord.SelectOption(
                label=f"{self.page * self.PAGE_SIZE + index + 1}. {voice['name']}"[:100],
                value=voice['style_id'],
                description=f"{_LANGUAGE_NAMES.get(voice['lang'], voice['lang'])} · {kind}",
            ))
        self.menu = discord.ui.Select(placeholder=title, options=options)
        self.menu.callback = self.choose
        items = [discord.ui.TextDisplay(f'## {title}\n{self.notice}\n총 {len(self.voices)}개 · {self.page + 1}/{pages} 페이지'),
                 discord.ui.ActionRow(self.menu)]
        if pages > 1:
            previous = discord.ui.Button(label='이전', disabled=self.page == 0)
            following = discord.ui.Button(label='다음', disabled=self.page + 1 == pages)
            previous.callback = self.previous_page
            following.callback = self.next_page
            items.append(discord.ui.ActionRow(previous, following))
        self.add_item(discord.ui.Container(*items, accent_color=discord.Color.blurple()))

    async def previous_page(self, interaction):
        self.page = max(0, self.page - 1)
        self.render()
        await interaction.response.edit_message(view=self)

    async def next_page(self, interaction):
        self.page = min((len(self.voices) - 1) // self.PAGE_SIZE, self.page + 1)
        self.render()
        await interaction.response.edit_message(view=self)

    async def choose(self, interaction):
        style_id = self.menu.values[0]
        voice = next((v for v in self.voices if v['style_id'] == style_id), None)
        if voice is None:
            return await interaction.response.send_message('목록을 다시 열어 보이스를 선택해주세요.', ephemeral=True)
        if self.action == 'delete':
            await interaction.response.edit_message(view=DeleteVoiceView(
                self.service, self.owner_id, voice, self.bot,
            ))
            self.stop()
            return
        await interaction.response.defer()
        try:
            name = await self.service.select_voice(interaction.user.id, style_id)
        except ValueError as exc:
            return await interaction.followup.send(str(exc), ephemeral=True)
        await interaction.edit_original_response(view=status_view('보이스 선택 완료', f'{discord.utils.escape_markdown(name)} 보이스로 설정했습니다.'))
        self.stop()


class DeleteVoiceView(OwnedLayoutView):
    def __init__(self, service, owner_id, voice, bot):
        super().__init__(owner_id)
        self.service = service
        self.voice = voice
        self.bot = bot
        confirm = discord.ui.Button(label='이 보이스 삭제', style=discord.ButtonStyle.danger)
        cancel = discord.ui.Button(label='취소')
        confirm.callback = self.confirm
        cancel.callback = self.cancel
        name = discord.utils.escape_markdown(voice['name'])
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(f'## 보이스 삭제 확인\n**{name}** 보이스를 삭제할까요? 등록한 음성 데이터도 삭제됩니다.'),
            discord.ui.ActionRow(confirm, cancel), accent_color=discord.Color.red(),
        ))

    async def confirm(self, interaction):
        await interaction.response.defer()
        try:
            await self.service.sovits.delete(
                interaction.user.id, self.voice['style_id'], owner=await self.bot.is_owner(interaction.user),
            )
        except ValueError as exc:
            return await interaction.followup.send(str(exc), ephemeral=True)
        except Exception:
            logger.exception('Voice deletion failed')
            return await interaction.followup.send('음성을 삭제하지 못했습니다. 잠시 후 다시 시도해주세요.', ephemeral=True)
        await interaction.edit_original_response(view=status_view('삭제 완료', '보이스를 삭제했습니다.'))
        self.stop()

    async def cancel(self, interaction):
        await interaction.response.edit_message(view=status_view('삭제 취소', '보이스를 유지합니다.'))
        self.stop()
