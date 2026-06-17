import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class BotSettings:
    token: str | None
    opus_path: str | None
    guild_id: int


def load_settings() -> BotSettings:
    load_dotenv()
    return BotSettings(
        token=os.environ.get("BOT_TOKEN"),
        opus_path=os.environ.get("OPUS_PATH"),
        guild_id=int(os.environ.get("DEV_GUILD_ID", "1074259285825032213")),
    )

