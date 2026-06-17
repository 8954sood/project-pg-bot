import asyncio
from datetime import datetime, timezone
from typing import Callable, Dict, Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.local import LocalCore
from core.sleep_timer.models import PendingReservation, ReservationKey
from core.sleep_timer.parser import KST, parse_next_kst_time
from core.sleep_timer.formatter import format_remaining, format_target
from core.sleep_timer.scheduler import SleepTimerSchedulerMixin
from core.sleep_timer.service import SleepTimerServiceMixin
from ui.common.views import OwnedLayoutView, status_view
from ui.sleep_timer.views import (
    CONFIRMATION_TIMEOUT,
    ConfirmationView,
    ManagementView,
    SleepTimerModal,
    TimeInputErrorView,
    WarningCancelView,
)


class SleepTimer(
    SleepTimerServiceMixin,
    SleepTimerSchedulerMixin,
    commands.Cog,
):
    def __init__(
        self,
        bot: commands.Bot,
        *,
        now: Optional[Callable[[], datetime]] = None,
        sleeper: Optional[Callable[[float], object]] = None,
    ):
        self.bot = bot
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.sleep = sleeper or asyncio.sleep
        self.tasks: Dict[ReservationKey, asyncio.Task] = {}
        self.locks: Dict[ReservationKey, asyncio.Lock] = {}
        self.pending: Dict[ReservationKey, PendingReservation] = {}

    @app_commands.command(
        name=app_commands.locale_str("sleep_timer", key="sleep_timer.name"),
        description=app_commands.locale_str(
            "Leave your voice channel automatically at a scheduled time.",
            key="sleep_timer.description",
        ),
    )
    @app_commands.guild_only()
    async def sleep_timer(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            await interaction.response.send_message(
                view=status_view("사용할 수 없습니다", "서버에서만 사용할 수 있습니다."),
                ephemeral=True,
            )
            return

        if member.voice is None or member.voice.channel is None:
            await interaction.response.send_message(
                view=status_view(
                    "음성 채널 입장 필요",
                    "음성 채널에 입장한 뒤 다시 실행해 주세요.",
                ),
                ephemeral=True,
            )
            return

        bot_member = guild.me
        if bot_member is None or not bot_member.guild_permissions.move_members:
            await interaction.response.send_message(
                view=status_view(
                    "권한 부족",
                    "봇에 `멤버 이동` 권한이 필요합니다.",
                ),
                ephemeral=True,
            )
            return

        reservation = await LocalCore.sleepTimerDataSource.get(guild.id, member.id)
        await interaction.response.send_message(
            view=ManagementView(
                self,
                guild.id,
                member.id,
                reservation,
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    cog = SleepTimer(bot)
    await bot.add_cog(cog)
    await cog.restore_reservations()


__all__ = [
    "CONFIRMATION_TIMEOUT",
    "ConfirmationView",
    "KST",
    "ManagementView",
    "OwnedLayoutView",
    "PendingReservation",
    "SleepTimer",
    "SleepTimerModal",
    "TimeInputErrorView",
    "WarningCancelView",
    "format_remaining",
    "format_target",
    "parse_next_kst_time",
    "setup",
    "status_view",
]

