import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from cogs.sleep_timer import (
    ConfirmationView,
    KST,
    SleepTimer,
    SleepTimerModal,
    WarningCancelView,
    format_target,
    format_remaining,
    parse_next_kst_time,
)
from core.local import LocalCore
from core.local.sleep_timer import SleepTimerReservation
from core.local.sleep_timer import sleep_timer_data_source as data_source_module
from core.local.sleep_timer.sleep_timer_data_source import SleepTimerDataSource


def make_bot(*, guild=None, user=None):
    return SimpleNamespace(
        get_guild=Mock(return_value=guild),
        get_user=Mock(return_value=user),
        fetch_user=AsyncMock(return_value=user),
        add_view=Mock(),
    )


def schedulable_guild(user_id=2):
    member = SimpleNamespace(
        id=user_id,
        voice=SimpleNamespace(channel=SimpleNamespace(id=99)),
    )
    return SimpleNamespace(
        me=SimpleNamespace(
            guild_permissions=SimpleNamespace(move_members=True),
        ),
        get_member=Mock(return_value=member),
    )


def make_cog(*, now=None, guild=None, user=None):
    return SleepTimer(
        make_bot(guild=guild, user=user),
        now=(lambda: now) if now else None,
    )


def reservation(
    *,
    guild_id=1,
    user_id=2,
    reservation_id="reservation-a",
    execute_at=2_000_000_000,
    warning_message_id=None,
):
    return SleepTimerReservation(
        guild_id=guild_id,
        user_id=user_id,
        reservation_id=reservation_id,
        execute_at=execute_at,
        created_at=1_900_000_000,
        warning_message_id=warning_message_id,
    )


def text_displays(view):
    return [
        item.content
        for item in view.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    ]


def buttons(view):
    return [
        item
        for item in view.walk_children()
        if isinstance(item, discord.ui.Button)
    ]


def test_parse_next_kst_time_uses_today_when_time_is_ahead():
    now = datetime(2026, 6, 15, 12, 0, tzinfo=KST)

    target = parse_next_kst_time("23:30", now)

    assert target == datetime(2026, 6, 15, 23, 30, tzinfo=KST)


def test_parse_next_kst_time_rolls_past_time_to_next_day():
    now = datetime(2026, 6, 15, 23, 31, tzinfo=KST)

    target = parse_next_kst_time("23:30", now)

    assert target == datetime(2026, 6, 16, 23, 30, tzinfo=KST)


@pytest.mark.parametrize("value", ["9:30", "24:00", "12:60", "text", ""])
def test_parse_next_kst_time_rejects_invalid_input(value):
    with pytest.raises(ValueError):
        parse_next_kst_time(value, datetime(2026, 6, 15, tzinfo=KST))


def test_modal_uses_components_v2_label_and_text_input():
    cog = make_cog(now=datetime(2026, 6, 15, tzinfo=timezone.utc))

    modal = SleepTimerModal(cog, guild_id=1, user_id=2)

    assert len(modal.children) == 1
    label = modal.children[0]
    assert isinstance(label, discord.ui.Label)
    assert isinstance(label.component, discord.ui.TextInput)
    assert label.component.custom_id == "sleep_timer_time"


def test_confirmation_view_displays_exact_date_remaining_time_and_actions():
    now = datetime(2026, 6, 15, 12, 0, tzinfo=KST)
    cog = make_cog(now=now)
    pending = cog.create_pending(
        guild_id=1,
        user_id=2,
        execute_at=datetime(2026, 6, 15, 13, 30, tzinfo=KST),
    )

    view = ConfirmationView(cog, pending)

    text = "\n".join(text_displays(view))
    assert "2026년 06월 15일 13:30 KST" in text
    assert "1시간 30분 후" in text
    assert [button.label for button in buttons(view)] == [
        "이 시간으로 확정",
        "다시 입력",
        "취소",
    ]


@pytest.mark.asyncio
async def test_pending_is_not_saved_until_confirmed(monkeypatch):
    now = datetime(2026, 6, 15, 3, 0, tzinfo=timezone.utc)
    cog = make_cog(now=now, guild=schedulable_guild())
    pending = cog.create_pending(
        1,
        2,
        datetime(2026, 6, 15, 13, 30, tzinfo=KST),
    )
    data_source = SimpleNamespace(
        get=AsyncMock(return_value=None),
        upsert=AsyncMock(),
    )
    monkeypatch.setattr(LocalCore, "sleepTimerDataSource", data_source)
    cog._replace_task = Mock()

    assert data_source.upsert.await_count == 0
    result = await cog.confirm_pending(pending)

    saved, replaced = result
    assert replaced is False
    assert saved.guild_id == 1
    assert saved.user_id == 2
    data_source.upsert.assert_awaited_once_with(saved)
    cog._replace_task.assert_called_once_with(saved)


@pytest.mark.asyncio
async def test_confirm_replaces_existing_reservation(monkeypatch):
    now = datetime(2026, 6, 15, 3, 0, tzinfo=timezone.utc)
    cog = make_cog(now=now, guild=schedulable_guild())
    pending = cog.create_pending(
        1,
        2,
        datetime(2026, 6, 15, 13, 30, tzinfo=KST),
    )
    data_source = SimpleNamespace(
        get=AsyncMock(return_value=reservation()),
        upsert=AsyncMock(),
    )
    monkeypatch.setattr(LocalCore, "sleepTimerDataSource", data_source)
    cog._replace_task = Mock()

    _, replaced = await cog.confirm_pending(pending)

    assert replaced is True


@pytest.mark.asyncio
async def test_stale_confirmation_cannot_overwrite_newer_input(monkeypatch):
    cog = make_cog(
        now=datetime(2026, 6, 15, tzinfo=timezone.utc),
        guild=schedulable_guild(),
    )
    old_pending = cog.create_pending(
        1,
        2,
        datetime(2026, 6, 15, 13, 30, tzinfo=KST),
    )
    cog.create_pending(
        1,
        2,
        datetime(2026, 6, 15, 14, 30, tzinfo=KST),
    )
    data_source = SimpleNamespace(get=AsyncMock(), upsert=AsyncMock())
    monkeypatch.setattr(LocalCore, "sleepTimerDataSource", data_source)

    result = await cog.confirm_pending(old_pending)

    assert result is None
    data_source.upsert.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirmation_rechecks_voice_state_before_saving(monkeypatch):
    guild = schedulable_guild()
    guild.get_member.return_value.voice = None
    cog = make_cog(
        now=datetime(2026, 6, 15, tzinfo=timezone.utc),
        guild=guild,
    )
    pending = cog.create_pending(
        1,
        2,
        datetime(2026, 6, 15, 13, 30, tzinfo=KST),
    )
    data_source = SimpleNamespace(get=AsyncMock(), upsert=AsyncMock())
    monkeypatch.setattr(LocalCore, "sleepTimerDataSource", data_source)

    result = await cog.confirm_pending(pending)

    assert result is None
    data_source.upsert.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_deletes_only_matching_reservation_and_stops_task(monkeypatch):
    cog = make_cog(now=datetime(2026, 6, 15, tzinfo=timezone.utc))
    task = asyncio.create_task(asyncio.sleep(60))
    cog.tasks[(1, 2)] = task
    data_source = SimpleNamespace(delete=AsyncMock(return_value=True))
    monkeypatch.setattr(LocalCore, "sleepTimerDataSource", data_source)

    cancelled = await cog.cancel_reservation(1, 2, "reservation-a")
    await asyncio.sleep(0)

    assert cancelled is True
    assert task.cancelled()
    data_source.delete.assert_awaited_once_with(1, 2, "reservation-a")


@pytest.mark.asyncio
async def test_execute_disconnects_member_and_deletes_reservation(monkeypatch):
    member = SimpleNamespace(
        voice=SimpleNamespace(channel=SimpleNamespace(id=99)),
        move_to=AsyncMock(),
    )
    guild = SimpleNamespace(
        get_member=Mock(return_value=member),
        fetch_member=AsyncMock(),
    )
    cog = make_cog(
        now=datetime(2026, 6, 15, tzinfo=timezone.utc),
        guild=guild,
    )
    item = reservation()
    data_source = SimpleNamespace(
        get=AsyncMock(return_value=item),
        delete=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(LocalCore, "sleepTimerDataSource", data_source)

    await cog._execute_reservation(item)

    member.move_to.assert_awaited_once_with(None, reason="수면 타이머 예약 실행")
    data_source.delete.assert_awaited_once_with(1, 2, "reservation-a")


@pytest.mark.asyncio
async def test_warning_dm_failure_does_not_delete_reservation(monkeypatch):
    response = SimpleNamespace(status=403, reason="Forbidden")
    user = SimpleNamespace(
        send=AsyncMock(side_effect=discord.Forbidden(response, "DM blocked"))
    )
    cog = make_cog(
        now=datetime(2026, 6, 15, tzinfo=timezone.utc),
        user=user,
    )
    item = reservation()
    data_source = SimpleNamespace(
        get=AsyncMock(return_value=item),
        set_warning_message=AsyncMock(),
        delete=AsyncMock(),
    )
    monkeypatch.setattr(LocalCore, "sleepTimerDataSource", data_source)

    await cog._send_warning(item)

    data_source.set_warning_message.assert_not_awaited()
    data_source.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_warning_dm_shows_actual_target_and_remaining_time(monkeypatch):
    now = datetime(2026, 6, 15, 9, 32, 10, tzinfo=timezone.utc)
    execute_at = datetime(2026, 6, 15, 9, 33, tzinfo=timezone.utc)
    sent_views = []

    async def send(*, view):
        sent_views.append(view)
        return SimpleNamespace(id=123)

    user = SimpleNamespace(send=AsyncMock(side_effect=send))
    cog = make_cog(now=now, user=user)
    item = reservation(execute_at=int(execute_at.timestamp()))
    data_source = SimpleNamespace(
        get=AsyncMock(return_value=item),
        set_warning_message=AsyncMock(),
    )
    monkeypatch.setattr(LocalCore, "sleepTimerDataSource", data_source)

    await cog._send_warning(item)

    text = "\n".join(text_displays(sent_views[0]))
    assert "2026년 06월 15일 18:33 KST" in text
    assert "약 1분 이내" in text
    assert "약 5분 뒤" not in text


@pytest.mark.asyncio
async def test_restore_expires_overdue_and_restores_persistent_warning(monkeypatch):
    now = datetime.fromtimestamp(2_000_000_000, timezone.utc)
    expired = reservation(reservation_id="expired", execute_at=1_999_999_999)
    future = reservation(
        reservation_id="future",
        execute_at=2_000_000_600,
        warning_message_id=555,
    )
    cog = make_cog(now=now)
    data_source = SimpleNamespace(
        get_all=AsyncMock(return_value=[expired, future]),
        delete=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(LocalCore, "sleepTimerDataSource", data_source)
    cog._notify_expired = AsyncMock()
    cog._replace_task = Mock()

    await cog.restore_reservations()

    data_source.delete.assert_awaited_once_with(1, 2, "expired")
    cog._notify_expired.assert_awaited_once_with(expired)
    cog.bot.add_view.assert_called_once()
    restored_view = cog.bot.add_view.call_args.args[0]
    assert isinstance(restored_view, WarningCancelView)
    assert cog.bot.add_view.call_args.kwargs["message_id"] == 555
    cog._replace_task.assert_called_once_with(future)


@pytest.mark.asyncio
async def test_sleep_timer_data_source_crud_uses_guild_and_user_key(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(data_source_module, "db_path", str(tmp_path / "sleep-timer.db"))
    await SleepTimerDataSource.init_table()
    first = reservation(execute_at=2_000_000_000)
    second = reservation(reservation_id="reservation-b", execute_at=2_000_000_100)

    await SleepTimerDataSource.upsert(first)
    assert await SleepTimerDataSource.get(1, 2) == first

    await SleepTimerDataSource.upsert(second)
    assert await SleepTimerDataSource.get(1, 2) == second
    assert await SleepTimerDataSource.get_all() == [second]

    assert await SleepTimerDataSource.delete(1, 2, "reservation-a") is False
    assert await SleepTimerDataSource.delete(1, 2, "reservation-b") is True
    assert await SleepTimerDataSource.get(1, 2) is None


def test_format_target_accepts_utc_and_renders_kst():
    now = datetime(2026, 6, 15, 3, 0, tzinfo=timezone.utc)
    execute_at = datetime(2026, 6, 15, 4, 0, tzinfo=timezone.utc)

    text = format_target(execute_at, now)

    assert "2026년 06월 15일 13:00 KST" in text
    assert "1시간 후" in text


def test_format_remaining_rounds_up_without_claiming_five_minutes():
    now = datetime(2026, 6, 15, 9, 32, tzinfo=timezone.utc)

    assert format_remaining(
        datetime(2026, 6, 15, 9, 32, 59, tzinfo=timezone.utc),
        now,
    ) == "1분 이내"
    assert format_remaining(
        datetime(2026, 6, 15, 9, 33, 1, tzinfo=timezone.utc),
        now,
    ) == "2분 후"
