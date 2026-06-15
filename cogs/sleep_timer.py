import asyncio
import logging
import math
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

from core.local import LocalCore
from core.local.sleep_timer import SleepTimerReservation


logger = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")
WARNING_OFFSET = timedelta(minutes=5)
CONFIRMATION_TIMEOUT = 300.0
TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
ReservationKey = Tuple[int, int]


@dataclass
class PendingReservation:
    token: str
    guild_id: int
    user_id: int
    execute_at: datetime


def parse_next_kst_time(value: str, now: datetime) -> datetime:
    value = value.strip()
    if not TIME_PATTERN.fullmatch(value):
        raise ValueError("시간은 24시간제 HH:MM 형식이어야 합니다.")

    if now.tzinfo is None:
        raise ValueError("현재 시각에는 시간대 정보가 필요합니다.")

    hour, minute = (int(part) for part in value.split(":"))
    now_kst = now.astimezone(KST)
    target = now_kst.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now_kst:
        target += timedelta(days=1)
    return target


def format_remaining(execute_at: datetime, now: datetime) -> str:
    seconds = max(0, math.ceil((execute_at - now).total_seconds()))
    if seconds < 60:
        return "1분 이내"

    hours, remainder = divmod(seconds, 3600)
    minutes = math.ceil(remainder / 60) if remainder else 0
    if minutes == 60:
        hours += 1
        minutes = 0

    if hours:
        return f"{hours}시간 {minutes}분 후" if minutes else f"{hours}시간 후"
    return f"{minutes}분 후"


def format_target(execute_at: datetime, now: datetime) -> str:
    execute_kst = execute_at.astimezone(KST)
    remaining = format_remaining(execute_at, now)

    return (
        f"## 입력한 시간을 확인해 주세요\n"
        f"**실행 시각:** {execute_kst:%Y년 %m월 %d일 %H:%M} KST\n"
        f"**남은 시간:** 약 {remaining}\n\n"
        "아래 버튼을 눌러야 예약이 최종 등록됩니다."
    )


def status_view(title: str, description: str) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(f"## {title}\n{description}"),
            accent_color=discord.Color.blurple(),
        )
    )
    return view


class OwnedLayoutView(discord.ui.LayoutView):
    def __init__(self, owner_id: int, *, timeout: Optional[float] = 180.0):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            view=status_view("사용할 수 없습니다", "이 화면을 연 사용자만 조작할 수 있습니다."),
            ephemeral=True,
        )
        return False

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item,
    ) -> None:
        logger.error(
            "Sleep timer UI failed",
            exc_info=(type(error), error, error.__traceback__),
        )
        if interaction.response.is_done():
            await interaction.followup.send(
                view=status_view("오류", "요청 처리 중 오류가 발생했습니다."),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                view=status_view("오류", "요청 처리 중 오류가 발생했습니다."),
                ephemeral=True,
            )


class SleepTimerModal(discord.ui.Modal):
    def __init__(self, cog: "SleepTimer", guild_id: int, user_id: int):
        super().__init__(title="수면 타이머 시간 입력", timeout=CONFIRMATION_TIMEOUT)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.time_input = discord.ui.TextInput(
            custom_id="sleep_timer_time",
            placeholder="예: 23:30",
            required=True,
            min_length=5,
            max_length=5,
        )
        self.add_item(
            discord.ui.Label(
                text="퇴출할 시간",
                description="한국 시간(KST), 24시간제 HH:MM 형식",
                component=self.time_input,
            )
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            target = parse_next_kst_time(self.time_input.value, self.cog.now())
        except ValueError as error:
            await interaction.response.send_message(
                view=TimeInputErrorView(
                    self.cog,
                    self.guild_id,
                    self.user_id,
                    str(error),
                ),
                ephemeral=True,
            )
            return

        pending = self.cog.create_pending(self.guild_id, self.user_id, target)
        await interaction.response.send_message(
            view=ConfirmationView(self.cog, pending),
            ephemeral=True,
        )


class TimeInputErrorView(OwnedLayoutView):
    def __init__(
        self,
        cog: "SleepTimer",
        guild_id: int,
        user_id: int,
        error_message: str,
    ):
        super().__init__(user_id, timeout=CONFIRMATION_TIMEOUT)
        self.cog = cog
        self.guild_id = guild_id

        retry = discord.ui.Button(
            label="다시 입력",
            style=discord.ButtonStyle.primary,
            custom_id=f"sleep_timer:retry:{uuid.uuid4()}",
        )
        cancel = discord.ui.Button(
            label="취소",
            style=discord.ButtonStyle.secondary,
            custom_id=f"sleep_timer:error_cancel:{uuid.uuid4()}",
        )
        retry.callback = self.retry
        cancel.callback = self.cancel
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(f"## 시간 형식 오류\n{error_message}"),
                discord.ui.ActionRow(retry, cancel),
                accent_color=discord.Color.red(),
            )
        )

    async def retry(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            SleepTimerModal(self.cog, self.guild_id, self.owner_id)
        )

    async def cancel(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(
            view=status_view("입력 취소", "기존 예약은 변경되지 않았습니다.")
        )


class ConfirmationView(OwnedLayoutView):
    def __init__(self, cog: "SleepTimer", pending: PendingReservation):
        super().__init__(pending.user_id, timeout=CONFIRMATION_TIMEOUT)
        self.cog = cog
        self.pending = pending

        confirm = discord.ui.Button(
            label="이 시간으로 확정",
            style=discord.ButtonStyle.success,
            custom_id=f"sleep_timer:confirm:{pending.token}",
        )
        retry = discord.ui.Button(
            label="다시 입력",
            style=discord.ButtonStyle.primary,
            custom_id=f"sleep_timer:confirm_retry:{pending.token}",
        )
        cancel = discord.ui.Button(
            label="취소",
            style=discord.ButtonStyle.secondary,
            custom_id=f"sleep_timer:confirm_cancel:{pending.token}",
        )
        confirm.callback = self.confirm
        retry.callback = self.retry
        cancel.callback = self.cancel
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(format_target(pending.execute_at, cog.now())),
                discord.ui.ActionRow(confirm, retry, cancel),
                accent_color=discord.Color.gold(),
            )
        )

    async def confirm(self, interaction: discord.Interaction) -> None:
        result = await self.cog.confirm_pending(self.pending)
        self.stop()
        if result is None:
            await interaction.response.edit_message(
                view=status_view(
                    "확정할 수 없습니다",
                    "요청이 만료·변경되었거나 현재 음성 채널 상태를 확인할 수 없습니다.",
                )
            )
            return

        reservation, replaced = result
        replacement = "\n기존 예약은 취소하고 새 예약으로 교체했습니다." if replaced else ""
        execute_kst = datetime.fromtimestamp(
            reservation.execute_at, timezone.utc
        ).astimezone(KST)
        await interaction.response.edit_message(
            view=status_view(
                "수면 타이머 등록 완료",
                f"**{execute_kst:%Y년 %m월 %d일 %H:%M} KST**에 음성 채널에서 나갑니다."
                f"{replacement}",
            )
        )

    async def retry(self, interaction: discord.Interaction) -> None:
        self.cog.discard_pending(self.pending)
        self.stop()
        await interaction.response.send_modal(
            SleepTimerModal(
                self.cog,
                self.pending.guild_id,
                self.pending.user_id,
            )
        )

    async def cancel(self, interaction: discord.Interaction) -> None:
        self.cog.discard_pending(self.pending)
        self.stop()
        await interaction.response.edit_message(
            view=status_view("등록 취소", "기존 예약은 변경되지 않았습니다.")
        )

    async def on_timeout(self) -> None:
        self.cog.discard_pending(self.pending)


class ManagementView(OwnedLayoutView):
    def __init__(
        self,
        cog: "SleepTimer",
        guild_id: int,
        user_id: int,
        reservation: Optional[SleepTimerReservation],
    ):
        super().__init__(user_id, timeout=CONFIRMATION_TIMEOUT)
        self.cog = cog
        self.guild_id = guild_id
        self.reservation = reservation

        register = discord.ui.Button(
            label="새로 등록" if reservation else "예약 등록",
            style=discord.ButtonStyle.primary,
            custom_id=f"sleep_timer:register:{uuid.uuid4()}",
        )
        register.callback = self.register
        buttons = [register]

        if reservation:
            cancel = discord.ui.Button(
                label="예약 취소",
                style=discord.ButtonStyle.danger,
                custom_id=f"sleep_timer:manage_cancel:{uuid.uuid4()}",
            )
            cancel.callback = self.cancel_reservation
            buttons.append(cancel)
            execute_kst = datetime.fromtimestamp(
                reservation.execute_at, timezone.utc
            ).astimezone(KST)
            text = (
                "## 수면 타이머 관리\n"
                f"현재 예약: **{execute_kst:%Y년 %m월 %d일 %H:%M} KST**\n"
                "새 시간을 등록하거나 기존 예약을 취소할 수 있습니다."
            )
        else:
            text = (
                "## 수면 타이머\n"
                "한국 시간 기준으로 음성 채널에서 나갈 시간을 등록합니다."
            )

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(text),
                discord.ui.ActionRow(*buttons),
                accent_color=discord.Color.blurple(),
            )
        )

    async def register(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            SleepTimerModal(self.cog, self.guild_id, self.owner_id)
        )

    async def cancel_reservation(self, interaction: discord.Interaction) -> None:
        cancelled = await self.cog.cancel_reservation(
            self.guild_id,
            self.owner_id,
            self.reservation.reservation_id if self.reservation else None,
        )
        self.stop()
        message = "예약을 취소했습니다." if cancelled else "이미 취소되거나 변경된 예약입니다."
        await interaction.response.edit_message(
            view=status_view("수면 타이머", message)
        )


class WarningCancelView(discord.ui.LayoutView):
    def __init__(
        self,
        cog: "SleepTimer",
        guild_id: int,
        user_id: int,
        reservation_id: str,
        execute_at: datetime,
    ):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.reservation_id = reservation_id
        execute_kst = execute_at.astimezone(KST)
        remaining = format_remaining(execute_at, cog.now())

        cancel = discord.ui.Button(
            label="퇴출 취소",
            style=discord.ButtonStyle.danger,
            custom_id=(
                f"sleep_timer:warning_cancel:{guild_id}:{user_id}:{reservation_id}"
            ),
        )
        cancel.callback = self.cancel
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "## 수면 타이머 알림\n"
                    f"**실행 시각:** {execute_kst:%Y년 %m월 %d일 %H:%M} KST\n"
                    f"**남은 시간:** 약 {remaining}\n"
                    "계속 머무르려면 아래 버튼으로 예약을 취소하세요."
                ),
                discord.ui.ActionRow(cancel),
                accent_color=discord.Color.orange(),
            )
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            view=status_view("사용할 수 없습니다", "예약한 사용자만 취소할 수 있습니다."),
            ephemeral=True,
        )
        return False

    async def cancel(self, interaction: discord.Interaction) -> None:
        cancelled = await self.cog.cancel_reservation(
            self.guild_id,
            self.user_id,
            self.reservation_id,
        )
        self.stop()
        message = "음성 퇴출 예약을 취소했습니다." if cancelled else "이미 종료되거나 변경된 예약입니다."
        await interaction.response.edit_message(
            view=status_view("수면 타이머", message)
        )


class SleepTimer(commands.Cog):
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

    def cog_unload(self) -> None:
        for task in self.tasks.values():
            task.cancel()
        self.tasks.clear()
        self.pending.clear()

    def _lock_for(self, key: ReservationKey) -> asyncio.Lock:
        lock = self.locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self.locks[key] = lock
        return lock

    def create_pending(
        self,
        guild_id: int,
        user_id: int,
        execute_at: datetime,
    ) -> PendingReservation:
        pending = PendingReservation(
            token=uuid.uuid4().hex,
            guild_id=guild_id,
            user_id=user_id,
            execute_at=execute_at.astimezone(timezone.utc),
        )
        self.pending[(guild_id, user_id)] = pending
        return pending

    def discard_pending(self, pending: PendingReservation) -> None:
        key = (pending.guild_id, pending.user_id)
        if self.pending.get(key) is pending:
            self.pending.pop(key, None)

    async def confirm_pending(
        self,
        pending: PendingReservation,
    ) -> Optional[Tuple[SleepTimerReservation, bool]]:
        key = (pending.guild_id, pending.user_id)
        async with self._lock_for(key):
            if self.pending.get(key) is not pending:
                return None
            if pending.execute_at <= self.now():
                self.pending.pop(key, None)
                return None

            guild = self.bot.get_guild(pending.guild_id)
            member = guild.get_member(pending.user_id) if guild else None
            bot_member = guild.me if guild else None
            if (
                member is None
                or member.voice is None
                or member.voice.channel is None
                or bot_member is None
                or not bot_member.guild_permissions.move_members
            ):
                self.pending.pop(key, None)
                return None

            existing = await LocalCore.sleepTimerDataSource.get(*key)
            reservation = SleepTimerReservation(
                guild_id=pending.guild_id,
                user_id=pending.user_id,
                reservation_id=uuid.uuid4().hex,
                execute_at=int(pending.execute_at.timestamp()),
                created_at=int(self.now().timestamp()),
                warning_message_id=None,
            )
            await LocalCore.sleepTimerDataSource.upsert(reservation)
            self.pending.pop(key, None)
            self._replace_task(reservation)
            return reservation, existing is not None

    def _replace_task(self, reservation: SleepTimerReservation) -> None:
        key = (reservation.guild_id, reservation.user_id)
        old_task = self.tasks.pop(key, None)
        if old_task and old_task is not asyncio.current_task():
            old_task.cancel()
        self.tasks[key] = asyncio.create_task(
            self._run_reservation(reservation),
            name=f"sleep-timer-{reservation.guild_id}-{reservation.user_id}",
        )

    async def cancel_reservation(
        self,
        guild_id: int,
        user_id: int,
        reservation_id: Optional[str] = None,
    ) -> bool:
        key = (guild_id, user_id)
        async with self._lock_for(key):
            deleted = await LocalCore.sleepTimerDataSource.delete(
                guild_id,
                user_id,
                reservation_id,
            )
            if not deleted:
                return False
            task = self.tasks.pop(key, None)
            if task and task is not asyncio.current_task():
                task.cancel()
            self.pending.pop(key, None)
            return True

    async def restore_reservations(self) -> None:
        now_timestamp = int(self.now().timestamp())
        for reservation in await LocalCore.sleepTimerDataSource.get_all():
            if reservation.execute_at <= now_timestamp:
                await LocalCore.sleepTimerDataSource.delete(
                    reservation.guild_id,
                    reservation.user_id,
                    reservation.reservation_id,
                )
                await self._notify_expired(reservation)
                continue

            if reservation.warning_message_id is not None:
                self.bot.add_view(
                    WarningCancelView(
                        self,
                        reservation.guild_id,
                        reservation.user_id,
                        reservation.reservation_id,
                        datetime.fromtimestamp(
                            reservation.execute_at,
                            timezone.utc,
                        ),
                    ),
                    message_id=reservation.warning_message_id,
                )
            self._replace_task(reservation)

    async def _run_reservation(self, reservation: SleepTimerReservation) -> None:
        key = (reservation.guild_id, reservation.user_id)
        try:
            warning_at = reservation.execute_at - int(WARNING_OFFSET.total_seconds())
            warning_delay = warning_at - self.now().timestamp()
            if warning_delay > 0:
                await self.sleep(warning_delay)

            if reservation.warning_message_id is None:
                await self._send_warning(reservation)

            execute_delay = reservation.execute_at - self.now().timestamp()
            if execute_delay > 0:
                await self.sleep(execute_delay)
            await self._execute_reservation(reservation)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Sleep timer task failed",
                extra={
                    "guild_id": reservation.guild_id,
                    "user_id": reservation.user_id,
                },
            )
        finally:
            if self.tasks.get(key) is asyncio.current_task():
                self.tasks.pop(key, None)

    async def _get_user(self, user_id: int):
        user = self.bot.get_user(user_id)
        if user is not None:
            return user
        try:
            return await self.bot.fetch_user(user_id)
        except discord.DiscordException:
            return None

    async def _send_warning(self, reservation: SleepTimerReservation) -> None:
        current = await LocalCore.sleepTimerDataSource.get(
            reservation.guild_id,
            reservation.user_id,
        )
        if current is None or current.reservation_id != reservation.reservation_id:
            return

        user = await self._get_user(reservation.user_id)
        if user is None:
            return
        view = WarningCancelView(
            self,
            reservation.guild_id,
            reservation.user_id,
            reservation.reservation_id,
            datetime.fromtimestamp(reservation.execute_at, timezone.utc),
        )
        try:
            message = await user.send(view=view)
        except discord.DiscordException:
            logger.warning(
                "Sleep timer warning DM failed",
                extra={
                    "guild_id": reservation.guild_id,
                    "user_id": reservation.user_id,
                },
            )
            return

        await LocalCore.sleepTimerDataSource.set_warning_message(
            reservation.guild_id,
            reservation.user_id,
            reservation.reservation_id,
            message.id,
        )
        reservation.warning_message_id = message.id

    async def _execute_reservation(
        self,
        reservation: SleepTimerReservation,
    ) -> None:
        key = (reservation.guild_id, reservation.user_id)
        async with self._lock_for(key):
            current = await LocalCore.sleepTimerDataSource.get(*key)
            if current is None or current.reservation_id != reservation.reservation_id:
                return

            guild = self.bot.get_guild(reservation.guild_id)
            member = guild.get_member(reservation.user_id) if guild else None
            if guild and member is None:
                try:
                    member = await guild.fetch_member(reservation.user_id)
                except discord.DiscordException:
                    member = None

            if member is not None and member.voice is not None:
                try:
                    await member.move_to(None, reason="수면 타이머 예약 실행")
                except (discord.Forbidden, discord.HTTPException):
                    logger.exception(
                        "Sleep timer voice disconnect failed",
                        extra={
                            "guild_id": reservation.guild_id,
                            "user_id": reservation.user_id,
                        },
                    )

            await LocalCore.sleepTimerDataSource.delete(
                reservation.guild_id,
                reservation.user_id,
                reservation.reservation_id,
            )

    async def _notify_expired(self, reservation: SleepTimerReservation) -> None:
        user = await self._get_user(reservation.user_id)
        if user is None:
            return
        try:
            await user.send(
                view=status_view(
                    "수면 타이머 만료",
                    "봇이 꺼져 있는 동안 예약 시각이 지나 예약을 종료했습니다.",
                )
            )
        except discord.DiscordException:
            logger.warning(
                "Sleep timer expiry DM failed",
                extra={
                    "guild_id": reservation.guild_id,
                    "user_id": reservation.user_id,
                },
            )

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
