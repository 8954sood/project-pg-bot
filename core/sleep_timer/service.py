import asyncio
import uuid
from datetime import datetime, timezone
from typing import Optional, Tuple

from core.local import LocalCore
from core.local.sleep_timer import SleepTimerReservation
from core.sleep_timer.models import PendingReservation, ReservationKey


class SleepTimerServiceMixin:
    locks: dict[ReservationKey, asyncio.Lock]
    pending: dict[ReservationKey, PendingReservation]

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

