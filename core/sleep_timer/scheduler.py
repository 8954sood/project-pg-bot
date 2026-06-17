import asyncio
import logging
from datetime import datetime, timedelta, timezone

import discord

from core.local import LocalCore
from core.local.sleep_timer import SleepTimerReservation
from core.sleep_timer.models import ReservationKey


logger = logging.getLogger(__name__)
WARNING_OFFSET = timedelta(minutes=5)


class SleepTimerSchedulerMixin:
    tasks: dict[ReservationKey, asyncio.Task]

    def cog_unload(self) -> None:
        for task in self.tasks.values():
            task.cancel()
        self.tasks.clear()
        self.pending.clear()

    def _replace_task(self, reservation: SleepTimerReservation) -> None:
        key = (reservation.guild_id, reservation.user_id)
        old_task = self.tasks.pop(key, None)
        if old_task and old_task is not asyncio.current_task():
            old_task.cancel()
        self.tasks[key] = asyncio.create_task(
            self._run_reservation(reservation),
            name=f"sleep-timer-{reservation.guild_id}-{reservation.user_id}",
        )

    async def restore_reservations(self) -> None:
        from ui.sleep_timer.views import WarningCancelView

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
        from ui.sleep_timer.views import WarningCancelView

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
        from ui.common.views import status_view

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

