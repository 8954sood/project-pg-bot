import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.llm.config import LLMSettings, load_llm_settings
from core.llm.models import LLMInputMessage
from core.llm.service import LLMService
from core.local import LocalCore
from core.utile import is_admin
from ui.llm.consent_view import LLMConsentView, consent_embed
from ui.llm.typing_manager import LLMTypingManager

logger = logging.getLogger(__name__)


class LLMCog(commands.Cog):
    llm_memory = app_commands.Group(name="llm-memory", description="LLM 전역 메모리 관리")

    def __init__(self, bot: commands.Bot, settings: Optional[LLMSettings] = None):
        self.bot = bot
        self.settings = settings or load_llm_settings()
        self.service = LLMService(self.settings)
        self.typing = LLMTypingManager(self.settings.typing_refresh_seconds)

    def _is_ignored_message(self, message: discord.Message) -> bool:
        if message.guild is None:
            return True
        if getattr(message, "webhook_id", None) is not None:
            return True
        if message.author.bot:
            return True
        if self.bot.user is not None and message.author.id == self.bot.user.id:
            return True
        return not self.settings.is_allowed(message.guild.id, message.channel.id)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if self._is_ignored_message(message):
            return
        guild_id = str(message.guild.id)
        channel_id = str(message.channel.id)
        user_id = str(message.author.id)

        try:
            consent = await LocalCore.llmConsentDataSource.get(
                guild_id,
                channel_id,
                user_id,
                self.settings.consent_version,
            )
            if consent is None or not consent.consented:
                view = LLMConsentView(
                    guild_id=guild_id,
                    channel_id=channel_id,
                    user_id=user_id,
                    consent_version=self.settings.consent_version,
                )
                consent_message = await message.channel.send(
                    content=message.author.mention,
                    embed=consent_embed(),
                    view=view,
                )
                view.message = consent_message
                return

            await self.typing.start(guild_id, channel_id, message.channel)

            async def send_response(content: str) -> None:
                await message.channel.send(content)

            async def complete_message() -> None:
                await self.typing.stop(guild_id, channel_id)

            await self.service.enqueue_message(
                LLMInputMessage(
                    guild_id=guild_id,
                    channel_id=channel_id,
                    user_id=user_id,
                    author_name=message.author.display_name,
                    content=message.clean_content or message.content,
                    is_admin=getattr(message.author.guild_permissions, "administrator", False),
                ),
                send_response=send_response,
                complete_message=complete_message,
            )
        except Exception:
            logger.exception("LLM message handling failed", extra={"guild_id": guild_id, "channel_id": channel_id})
            await self.typing.stop(guild_id, channel_id)

    def _guild_enabled(self, interaction: discord.Interaction) -> bool:
        return interaction.guild is not None and str(interaction.guild.id) in self.settings.guild_channel_map

    async def _reject_if_unavailable(self, interaction: discord.Interaction) -> bool:
        if not self._guild_enabled(interaction):
            await interaction.response.send_message("LLM 메모리 봇이 활성화된 서버에서만 사용할 수 있습니다.", ephemeral=True)
            return True
        return False

    @llm_memory.command(name="list", description="전역 메모리 목록을 확인합니다.")
    @app_commands.guild_only()
    @is_admin()
    async def list_memory(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None) -> None:
        if await self._reject_if_unavailable(interaction):
            return
        rows = await LocalCore.llmGlobalMemoryDataSource.list(
            str(interaction.guild.id),
            str(channel.id) if channel else None,
            include_disabled=True,
        )
        if not rows:
            await interaction.response.send_message("등록된 전역 메모리가 없습니다.", ephemeral=True)
            return
        lines = [
            f"`{row.id}` [{'on' if row.enabled else 'off'}] {row.key or '-'}: {row.content[:160]}"
            for row in rows[:20]
        ]
        await interaction.response.send_message("\n".join(lines)[:1900], ephemeral=True)

    @llm_memory.command(name="add", description="전역 메모리를 추가합니다.")
    @app_commands.guild_only()
    @is_admin()
    async def add_memory(
        self,
        interaction: discord.Interaction,
        content: str,
        key: Optional[str] = None,
        channel: Optional[discord.TextChannel] = None,
        importance: int = 1,
    ) -> None:
        if await self._reject_if_unavailable(interaction):
            return
        memory_id = await LocalCore.llmGlobalMemoryDataSource.add(
            str(interaction.guild.id),
            str(channel.id) if channel else None,
            key,
            content,
            importance,
            str(interaction.user.id),
        )
        await interaction.response.send_message(f"전역 메모리를 추가했습니다. id=`{memory_id}`", ephemeral=True)

    @llm_memory.command(name="edit", description="전역 메모리를 수정합니다.")
    @app_commands.guild_only()
    @is_admin()
    async def edit_memory(
        self,
        interaction: discord.Interaction,
        memory_id: int,
        content: str,
        key: Optional[str] = None,
        importance: Optional[int] = None,
    ) -> None:
        if await self._reject_if_unavailable(interaction):
            return
        updated = await LocalCore.llmGlobalMemoryDataSource.update(
            memory_id,
            str(interaction.guild.id),
            content=content,
            key=key,
            importance=importance,
        )
        await interaction.response.send_message("수정했습니다." if updated else "대상 전역 메모리를 찾지 못했습니다.", ephemeral=True)

    @llm_memory.command(name="delete", description="전역 메모리를 삭제합니다.")
    @app_commands.guild_only()
    @is_admin()
    async def delete_memory(self, interaction: discord.Interaction, memory_id: int) -> None:
        if await self._reject_if_unavailable(interaction):
            return
        deleted = await LocalCore.llmGlobalMemoryDataSource.delete(memory_id, str(interaction.guild.id))
        await interaction.response.send_message("삭제했습니다." if deleted else "대상 전역 메모리를 찾지 못했습니다.", ephemeral=True)

    @llm_memory.command(name="enable", description="전역 메모리를 활성화합니다.")
    @app_commands.guild_only()
    @is_admin()
    async def enable_memory(self, interaction: discord.Interaction, memory_id: int) -> None:
        if await self._reject_if_unavailable(interaction):
            return
        updated = await LocalCore.llmGlobalMemoryDataSource.set_enabled(memory_id, str(interaction.guild.id), True)
        await interaction.response.send_message("활성화했습니다." if updated else "대상 전역 메모리를 찾지 못했습니다.", ephemeral=True)

    @llm_memory.command(name="disable", description="전역 메모리를 비활성화합니다.")
    @app_commands.guild_only()
    @is_admin()
    async def disable_memory(self, interaction: discord.Interaction, memory_id: int) -> None:
        if await self._reject_if_unavailable(interaction):
            return
        updated = await LocalCore.llmGlobalMemoryDataSource.set_enabled(memory_id, str(interaction.guild.id), False)
        await interaction.response.send_message("비활성화했습니다." if updated else "대상 전역 메모리를 찾지 못했습니다.", ephemeral=True)

    @llm_memory.command(name="my-list", description="내 개인 메모리 목록을 확인합니다.")
    @app_commands.guild_only()
    async def list_my_memory(self, interaction: discord.Interaction) -> None:
        if await self._reject_if_unavailable(interaction):
            return
        rows = await LocalCore.llmUserMemoryDataSource.list_user(
            str(interaction.guild.id),
            str(interaction.channel.id),
            str(interaction.user.id),
            include_disabled=True,
        )
        if not rows:
            await interaction.response.send_message("등록된 개인 메모리가 없습니다.", ephemeral=True)
            return
        lines = [
            f"`{row.id}` [{'on' if row.enabled else 'off'}] {row.key or '-'}: {row.content[:160]}"
            for row in rows[:20]
        ]
        await interaction.response.send_message("\n".join(lines)[:1900], ephemeral=True)

    @llm_memory.command(name="my-add", description="내 개인 메모리를 추가합니다.")
    @app_commands.guild_only()
    async def add_my_memory(self, interaction: discord.Interaction, content: str, key: Optional[str] = None) -> None:
        if await self._reject_if_unavailable(interaction):
            return
        memory_id = await LocalCore.llmUserMemoryDataSource.add(
            str(interaction.guild.id),
            str(interaction.channel.id),
            str(interaction.user.id),
            content,
            key=key,
            user_name=getattr(interaction.user, "display_name", str(interaction.user.id)),
        )
        await interaction.response.send_message(f"개인 메모리를 추가했습니다. id=`{memory_id}`", ephemeral=True)

    @llm_memory.command(name="my-edit", description="내 개인 메모리를 수정합니다.")
    @app_commands.guild_only()
    async def edit_my_memory(self, interaction: discord.Interaction, memory_id: int, content: str, key: Optional[str] = None) -> None:
        if await self._reject_if_unavailable(interaction):
            return
        updated = await LocalCore.llmUserMemoryDataSource.update_user_memory(
            memory_id,
            str(interaction.guild.id),
            str(interaction.channel.id),
            str(interaction.user.id),
            content=content,
            key=key,
        )
        await interaction.response.send_message("수정했습니다." if updated else "대상 개인 메모리를 찾지 못했습니다.", ephemeral=True)

    @llm_memory.command(name="my-delete", description="내 개인 메모리를 삭제합니다.")
    @app_commands.guild_only()
    async def delete_my_memory(self, interaction: discord.Interaction, memory_id: int) -> None:
        if await self._reject_if_unavailable(interaction):
            return
        deleted = await LocalCore.llmUserMemoryDataSource.delete_user_memory(
            memory_id,
            str(interaction.guild.id),
            str(interaction.channel.id),
            str(interaction.user.id),
        )
        await interaction.response.send_message("삭제했습니다." if deleted else "대상 개인 메모리를 찾지 못했습니다.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LLMCog(bot))
