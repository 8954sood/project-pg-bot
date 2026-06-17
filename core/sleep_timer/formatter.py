import math
from datetime import datetime

from core.sleep_timer.parser import KST


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

