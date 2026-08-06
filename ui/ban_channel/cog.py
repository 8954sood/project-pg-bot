import asyncio
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.ban_channel.models import BanChannelConfig
from core.ban_channel.service import BanChannelService
from core.utile import is_admin


logger = logging.getLogger(__name__)


class BanChannelCog(commands.Cog):
    ban_channel = app_commands.Group(
        name="ban_channel",
        description="자동 밴 채널을 관리합니다.",
        guild_only=True,
        default_permissions=discord.Permissions(administrator=True),
    )

    def __init__(self, bot: commands.Bot, service: BanChannelService) -> None:
        self.bot = bot
        self.service = service
        self._pending_bans: dict[tuple[int, int], asyncio.Task[bool]] = {}

    @staticmethod
    def _warning_embed() -> discord.Embed:
        return discord.Embed(
            title="🚫 이 채널에 메시지를 작성하지 마세요.",
            description=(
                "관리자가 아닌 사용자가 이 채널에 메시지를 작성하면 "
                "자동으로 서버에서 차단됩니다."
            ),
            colour=discord.Colour.red(),
        )

    @staticmethod
    def _missing_bot_permissions(
        guild: discord.Guild,
        channel: discord.TextChannel,
    ) -> list[str]:
        bot_member = guild.me
        if bot_member is None:
            return ["봇 멤버 정보 확인"]

        missing: list[str] = []
        if not bot_member.guild_permissions.ban_members:
            missing.append("멤버 차단")

        channel_permissions = channel.permissions_for(bot_member)
        required_channel_permissions = (
            ("view_channel", "채널 보기"),
            ("send_messages", "메시지 보내기"),
            ("embed_links", "링크 첨부"),
            ("manage_messages", "메시지 관리"),
        )
        for permission_name, display_name in required_channel_permissions:
            if not getattr(channel_permissions, permission_name):
                missing.append(display_name)
        return missing

    @ban_channel.command(name="set", description="자동 밴 채널을 설정합니다.")
    @is_admin()
    async def set_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "서버에서만 사용할 수 있는 명령어입니다.",
                ephemeral=True,
            )
            return

        missing_permissions = self._missing_bot_permissions(guild, channel)
        if missing_permissions:
            await interaction.response.send_message(
                "설정할 수 없습니다. 봇에 다음 권한이 필요합니다: "
                + ", ".join(missing_permissions),
                ephemeral=True,
            )
            return

        previous_config = self.service.get_config(guild.id)
        try:
            warning_message = await channel.send(embed=self._warning_embed())
        except discord.DiscordException:
            logger.exception(
                "Failed to post ban channel warning",
                extra={"guild_id": guild.id, "channel_id": channel.id},
            )
            await interaction.response.send_message(
                "경고 메시지를 게시하지 못해 설정을 적용하지 않았습니다.",
                ephemeral=True,
            )
            return

        try:
            await self.service.set_config(
                guild_id=guild.id,
                channel_id=channel.id,
                warning_message_id=warning_message.id,
            )
        except Exception:
            logger.exception(
                "Failed to persist ban channel config",
                extra={"guild_id": guild.id, "channel_id": channel.id},
            )
            await self._delete_message_best_effort(
                warning_message,
                guild_id=guild.id,
                channel_id=channel.id,
            )
            await interaction.response.send_message(
                "설정을 저장하지 못했습니다. 잠시 후 다시 시도해주세요.",
                ephemeral=True,
            )
            return

        if previous_config is not None:
            await self._delete_warning_best_effort(guild, previous_config)

        await interaction.response.send_message(
            f"자동 밴 채널을 {channel.mention}로 설정했습니다.",
            ephemeral=True,
        )

    @ban_channel.command(name="clear", description="자동 밴 채널 설정을 해제합니다.")
    @is_admin()
    async def clear_channel(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "서버에서만 사용할 수 있는 명령어입니다.",
                ephemeral=True,
            )
            return

        config = self.service.get_config(guild.id)
        if config is None:
            await interaction.response.send_message(
                "설정된 자동 밴 채널이 없습니다.",
                ephemeral=True,
            )
            return

        try:
            await self.service.clear_config(guild.id)
        except Exception:
            logger.exception(
                "Failed to clear ban channel config",
                extra={"guild_id": guild.id, "channel_id": config.channel_id},
            )
            await interaction.response.send_message(
                "설정을 해제하지 못했습니다. 잠시 후 다시 시도해주세요.",
                ephemeral=True,
            )
            return

        await self._delete_warning_best_effort(guild, config)
        await interaction.response.send_message(
            "자동 밴 채널 설정을 해제했습니다.",
            ephemeral=True,
        )

    @ban_channel.command(name="status", description="현재 자동 밴 채널을 확인합니다.")
    @is_admin()
    async def status(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "서버에서만 사용할 수 있는 명령어입니다.",
                ephemeral=True,
            )
            return

        config = self.service.get_config(guild.id)
        if config is None:
            message = "설정된 자동 밴 채널이 없습니다."
        else:
            channel = guild.get_channel(config.channel_id)
            channel_text = channel.mention if channel is not None else f"삭제된 채널 (`{config.channel_id}`)"
            message = f"현재 자동 밴 채널: {channel_text}"
        await interaction.response.send_message(message, ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if (
            message.guild is None
            or not isinstance(message.channel, discord.TextChannel)
            or message.type not in (discord.MessageType.default, discord.MessageType.reply)
            or message.webhook_id is not None
            or message.author.bot
            or not hasattr(message.author, "guild_permissions")
        ):
            return

        if (
            message.author.id == message.guild.owner_id
            or message.author.guild_permissions.administrator
        ):
            return

        if not self.service.is_configured_channel(message.guild.id, message.channel.id):
            return

        key = (message.guild.id, message.author.id)
        ban_task = self._pending_bans.get(key)
        if ban_task is None:
            ban_task = asyncio.create_task(
                self._ban_member(message.author, message.channel.id)
            )
            self._pending_bans[key] = ban_task
            ban_task.add_done_callback(
                lambda completed, pending_key=key: self._clear_pending_ban(
                    pending_key,
                    completed,
                )
            )

        banned = await ban_task
        if banned:
            await self._delete_message_best_effort(
                message,
                guild_id=message.guild.id,
                channel_id=message.channel.id,
                user_id=message.author.id,
            )

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        try:
            cleared = await self.service.clear_channel(
                guild_id=channel.guild.id,
                channel_id=channel.id,
            )
        except Exception:
            logger.exception(
                "Failed to clear deleted ban channel",
                extra={"guild_id": channel.guild.id, "channel_id": channel.id},
            )
            return

        if cleared is not None:
            logger.info(
                "Cleared deleted ban channel",
                extra={"guild_id": channel.guild.id, "channel_id": channel.id},
            )

    def _clear_pending_ban(
        self,
        key: tuple[int, int],
        completed: asyncio.Task[bool],
    ) -> None:
        if self._pending_bans.get(key) is completed:
            self._pending_bans.pop(key, None)

    async def _ban_member(self, member: discord.Member, channel_id: int) -> bool:
        guild = member.guild
        bot_member = guild.me
        if bot_member is None or member.top_role >= bot_member.top_role:
            logger.warning(
                "Automatic ban blocked by role hierarchy",
                extra={
                    "guild_id": guild.id,
                    "channel_id": channel_id,
                    "user_id": member.id,
                },
            )
            return False

        try:
            await guild.ban(
                member,
                delete_message_seconds=0,
                reason=f"자동 밴 채널 메시지 작성 (channel_id={channel_id})",
            )
        except discord.DiscordException:
            logger.exception(
                "Automatic ban failed",
                extra={
                    "guild_id": guild.id,
                    "channel_id": channel_id,
                    "user_id": member.id,
                },
            )
            return False
        except Exception:
            logger.exception(
                "Unexpected automatic ban failure",
                extra={
                    "guild_id": guild.id,
                    "channel_id": channel_id,
                    "user_id": member.id,
                },
            )
            return False

        logger.info(
            "Automatic ban succeeded",
            extra={
                "guild_id": guild.id,
                "channel_id": channel_id,
                "user_id": member.id,
            },
        )
        return True

    async def _delete_warning_best_effort(
        self,
        guild: discord.Guild,
        config: BanChannelConfig,
    ) -> None:
        if config.warning_message_id is None:
            return
        channel = guild.get_channel(config.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        message = channel.get_partial_message(config.warning_message_id)
        await self._delete_message_best_effort(
            message,
            guild_id=guild.id,
            channel_id=config.channel_id,
        )

    @staticmethod
    async def _delete_message_best_effort(
        message,
        *,
        guild_id: int,
        channel_id: int,
        user_id: Optional[int] = None,
    ) -> None:
        try:
            await message.delete()
        except discord.NotFound:
            return
        except discord.DiscordException:
            logger.exception(
                "Failed to delete ban channel message",
                extra={
                    "guild_id": guild_id,
                    "channel_id": channel_id,
                    "user_id": user_id,
                },
            )


async def setup(bot: commands.Bot) -> None:
    service = BanChannelService()
    await service.load_configs()
    await bot.add_cog(BanChannelCog(bot, service))
