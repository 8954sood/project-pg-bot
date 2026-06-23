import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.llm.config import LLMSettings, load_llm_settings
from core.llm.images import MAX_LLM_IMAGES, LLMImageInput, is_supported_image, prepare_llm_image
from core.llm.models import LLMInputMessage
from core.llm.service import LLMService
from core.local import LocalCore
from core.utile import is_admin
from ui.llm.consent_view import LLMConsentView, consent_embed
from ui.llm.typing_manager import LLMTypingManager

logger = logging.getLogger(__name__)
MAX_USER_INPUT_CHARS = 200
DISCORD_MESSAGE_LIMIT = 2000
MAX_LLM_RESPONSE_CHARS = 4000
TOO_LONG_RESPONSE_MESSAGE = "LLM 응답이 너무 길어 전송하지 않았습니다."


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

            content = message.clean_content or message.content
            content_length = len(content)
            if content_length > MAX_USER_INPUT_CHARS:
                failure = f"메시지는 최대 {MAX_USER_INPUT_CHARS}자까지 입력할 수 있습니다. 현재 {content_length}자입니다."
                try:
                    await message.reply(failure, mention_author=False)
                except Exception:
                    await message.channel.send(failure)
                return

            images, image_error = await self._collect_images(message)
            if image_error:
                try:
                    await message.reply(image_error, mention_author=False)
                except Exception:
                    await message.channel.send(image_error)
                return
            await self.typing.start(guild_id, channel_id, message.channel)

            async def send_response(content: str) -> None:
                reply_text = content.strip() or "응답을 생성하지 못했습니다. 다시 한 번 말씀해 주세요."
                chunks = split_discord_response(reply_text)
                if chunks is None:
                    chunks = [TOO_LONG_RESPONSE_MESSAGE]
                for index, chunk in enumerate(chunks):
                    try:
                        if index == 0:
                            await message.reply(chunk, mention_author=False)
                        else:
                            await message.channel.send(chunk)
                    except Exception:
                        if index == 0:
                            await message.channel.send(chunk)
                        else:
                            raise

            async def complete_message() -> None:
                await self.typing.stop(guild_id, channel_id)

            await self.service.enqueue_message(
                LLMInputMessage(
                    guild_id=guild_id,
                    channel_id=channel_id,
                    user_id=user_id,
                    author_name=message.author.display_name,
                    content=content,
                    is_admin=getattr(message.author.guild_permissions, "administrator", False),
                    images=images,
                ),
                send_response=send_response,
                complete_message=complete_message,
            )
        except Exception:
            logger.exception("LLM message handling failed", extra={"guild_id": guild_id, "channel_id": channel_id})
            await self.typing.stop(guild_id, channel_id)

    async def _collect_images(self, message: discord.Message) -> tuple[list[LLMImageInput], str | None]:
        images: list[LLMImageInput] = []
        for attachment in message.attachments:
            if len(images) >= MAX_LLM_IMAGES:
                break
            filename = attachment.filename or ""
            content_type = attachment.content_type
            if not is_supported_image(filename, content_type):
                continue
            try:
                prepared = prepare_llm_image(filename, content_type, await attachment.read())
            except Exception:
                logger.exception(
                    "LLM image attachment processing failed",
                    extra={"attachment_filename": filename, "attachment_content_type": content_type},
                )
                display_name = filename or "이미지"
                return images, f"{display_name} 이미지를 불러오지 못했습니다. 잠시 후 다시 업로드해서 시도해 주세요."
            if prepared is not None:
                images.append(prepared)
        return images, None

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


def split_discord_response(text: str) -> list[str] | None:
    if len(text) <= DISCORD_MESSAGE_LIMIT:
        return [text]
    if len(text) > MAX_LLM_RESPONSE_CHARS:
        return None
    first, second = _split_text_near_limit(text, DISCORD_MESSAGE_LIMIT)
    return _balance_code_fences(first, second)


def _split_text_near_limit(text: str, limit: int) -> tuple[str, str]:
    split_at = text.rfind("\n", 0, limit + 1)
    if split_at <= 0:
        split_at = text.rfind(" ", 0, limit + 1)
    if split_at <= 0:
        split_at = limit
    first = text[:split_at].rstrip()
    second = text[split_at:].lstrip()
    return first, second


def _balance_code_fences(first: str, second: str) -> list[str]:
    if not _has_unclosed_code_fence(first):
        return [first, second]

    language = _open_code_fence_language(first)
    close_fence = "\n```"
    reopen_fence = f"```{language}\n" if language else "```\n"
    if len(first) + len(close_fence) <= DISCORD_MESSAGE_LIMIT and len(reopen_fence) + len(second) <= DISCORD_MESSAGE_LIMIT:
        return [first + close_fence, reopen_fence + second]

    return [_wrap_plain_text(first), _wrap_plain_text(second)]


def _has_unclosed_code_fence(text: str) -> bool:
    return len(_code_fence_lines(text)) % 2 == 1


def _open_code_fence_language(text: str) -> str:
    fences = _code_fence_lines(text)
    if not fences:
        return ""
    return fences[-1][3:].strip()


def _code_fence_lines(text: str) -> list[str]:
    return [line.lstrip() for line in text.splitlines() if line.lstrip().startswith("```")]


def _wrap_plain_text(text: str) -> str:
    return "```\n" + text.replace("```", "'''")[: DISCORD_MESSAGE_LIMIT - 8] + "\n```"
