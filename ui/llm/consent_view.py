import discord

from core.local import LocalCore


CONSENT_TITLE = "LLM 메모리 봇 사용 안내"
CONSENT_DESCRIPTION = (
    "이 채널에서는 LLM 봇이 대화 흐름을 이해하고 더 자연스럽게 응답하기 위해 사용자의 메시지를 사용할 수 있습니다.\n\n"
    "사용된 메시지는 외부 분석, 광고, 별도 서비스 개선 목적이 아니라 이 Discord 서버/채널 안에서 LLM 봇의 메모리 기능을 제공하기 위한 용도로만 사용됩니다.\n\n"
    "동의하면 이후 이 채널에서 작성한 메시지가 LLM 응답 문맥과 메모리 추출에 사용될 수 있습니다.\n\n"
    "동의하지 않으면 LLM 봇은 사용자의 메시지를 장기 메모리에 저장하지 않으며, 해당 사용자의 메시지를 기반으로 개인 메모리를 만들지 않습니다."
)
CONSENT_TIMEOUT_SECONDS = 10
CONSENT_TIMEOUT_DESCRIPTION = (
    "10초 동안 응답이 없어 동의 버튼이 만료되었습니다.\n\n"
    "약관 동의를 다시 받으려면 이 채널에 메시지를 한 번 더 보내 주세요. "
    "새 동의 안내와 버튼을 다시 보내드립니다."
)
CONSENT_ACCEPTED_DESCRIPTION = (
    "동의가 저장되었습니다.\n\n"
    "다음 메시지부터 이 채널에서 작성한 메시지가 LLM 응답 문맥과 메모리 추출에 사용될 수 있습니다."
)
CONSENT_DECLINED_DESCRIPTION = (
    "비동의 상태가 저장되었습니다.\n\n"
    "LLM 봇은 사용자의 메시지를 장기 메모리에 저장하지 않으며, 해당 사용자의 메시지를 기반으로 개인 메모리를 만들지 않습니다."
)


def consent_embed() -> discord.Embed:
    return discord.Embed(title=CONSENT_TITLE, description=CONSENT_DESCRIPTION, colour=0x3498DB)


def consent_timeout_embed() -> discord.Embed:
    return discord.Embed(title="LLM 메모리 봇 사용 안내 만료", description=CONSENT_TIMEOUT_DESCRIPTION, colour=0x95A5A6)


def consent_accepted_embed() -> discord.Embed:
    return discord.Embed(title="LLM 메모리 봇 동의 완료", description=CONSENT_ACCEPTED_DESCRIPTION, colour=0x2ECC71)


def consent_declined_embed() -> discord.Embed:
    return discord.Embed(title="LLM 메모리 봇 비동의 완료", description=CONSENT_DECLINED_DESCRIPTION, colour=0x95A5A6)


class LLMConsentView(discord.ui.View):
    def __init__(self, *, guild_id: str, channel_id: str, user_id: str, consent_version: str):
        super().__init__(timeout=CONSENT_TIMEOUT_SECONDS)
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.user_id = user_id
        self.consent_version = consent_version
        self.message: discord.Message | None = None
        self.completed = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("본인의 동의 버튼만 사용할 수 있습니다.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="동의합니다", style=discord.ButtonStyle.success, custom_id="llm_consent_accept")
    async def accept(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.accept_consent()
        await interaction.response.send_message("동의가 저장되었습니다. 다음 메시지부터 LLM 봇이 응답할 수 있습니다.", ephemeral=True)

    @discord.ui.button(label="동의하지 않습니다", style=discord.ButtonStyle.secondary, custom_id="llm_consent_decline")
    async def decline(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.decline_consent()
        await interaction.response.send_message("비동의 상태가 저장되었습니다. 메시지는 LLM 처리와 장기 메모리에 사용되지 않습니다.", ephemeral=True)

    async def accept_consent(self) -> None:
        self.completed = True
        await LocalCore.llmConsentDataSource.set(
            self.guild_id,
            self.channel_id,
            self.user_id,
            self.consent_version,
            True,
        )
        await self._edit_source_message(consent_accepted_embed())
        self.stop()

    async def decline_consent(self) -> None:
        self.completed = True
        await LocalCore.llmConsentDataSource.set(
            self.guild_id,
            self.channel_id,
            self.user_id,
            self.consent_version,
            False,
        )
        await self._edit_source_message(consent_declined_embed())
        self.stop()

    async def on_timeout(self) -> None:
        if self.completed or self.message is None:
            return
        await self._edit_source_message(consent_timeout_embed())

    async def _edit_source_message(self, embed: discord.Embed) -> None:
        if self.message is None:
            return
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True
        await self.message.edit(embed=embed, view=self)
