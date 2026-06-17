import uuid
from datetime import datetime, timezone
from typing import Optional

import discord

from core.local.sleep_timer import SleepTimerReservation
from core.sleep_timer.formatter import format_remaining, format_target
from core.sleep_timer.models import PendingReservation
from core.sleep_timer.parser import KST, parse_next_kst_time
from ui.common.views import OwnedLayoutView, status_view


CONFIRMATION_TIMEOUT = 300.0


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

