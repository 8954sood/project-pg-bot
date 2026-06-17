import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")
TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


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

