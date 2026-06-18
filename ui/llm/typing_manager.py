import asyncio
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


class LLMTypingManager:
    def __init__(self, refresh_seconds: float = 8.0):
        self.refresh_seconds = refresh_seconds
        self.pending: dict[tuple[str, str], int] = defaultdict(int)
        self.tasks: dict[tuple[str, str], asyncio.Task] = {}
        self.channels: dict[tuple[str, str], object] = {}

    async def start(self, guild_id: str, channel_id: str, channel: object) -> None:
        key = (guild_id, channel_id)
        self.pending[key] += 1
        self.channels[key] = channel
        await self._send_typing(key, channel)
        task = self.tasks.get(key)
        if task is None or task.done():
            self.tasks[key] = asyncio.create_task(self._run(key))

    async def stop(self, guild_id: str, channel_id: str) -> None:
        key = (guild_id, channel_id)
        self.pending[key] = max(0, self.pending.get(key, 0) - 1)
        if self.pending[key] == 0:
            task = self.tasks.pop(key, None)
            self.channels.pop(key, None)
            if task is not None:
                task.cancel()

    async def _run(self, key: tuple[str, str]) -> None:
        try:
            while self.pending.get(key, 0) > 0:
                channel = self.channels.get(key)
                if channel is None:
                    return
                await self._send_typing(key, channel)
                await asyncio.sleep(self.refresh_seconds)
        except asyncio.CancelledError:
            return

    @staticmethod
    async def _send_typing(key: tuple[str, str], channel: object) -> None:
        try:
            typing = getattr(channel, "typing", None)
            if callable(typing):
                await typing()
                return
            trigger_typing = getattr(channel, "trigger_typing", None)
            if callable(trigger_typing):
                await trigger_typing()
                return
            logger.warning("LLM channel has no typing API", extra={"guild_id": key[0], "channel_id": key[1]})
        except Exception:
            logger.exception("LLM typing indicator failed", extra={"guild_id": key[0], "channel_id": key[1]})
