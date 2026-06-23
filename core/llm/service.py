import asyncio
import json
import logging
import tempfile
from collections import defaultdict
from pathlib import Path
from datetime import datetime, timezone
from typing import Awaitable, Callable

from core.llm.config import LLMSettings
from core.llm.engine import LLMEngine
from core.llm.images import CachedLLMImage, LLMImageInput
from core.llm.models import (
    BufferedConversation,
    LLMBufferedMessage,
    LLMInputMessage,
    LLMResponseResult,
    MemoryState,
    Message,
    RecentLogEntry,
    ServerMemory,
    ServerStyleProfile,
    UserMemory,
)
from core.llm.tools import LLMToolRegistry
from core.local.llm import (
    LLMGlobalMemoryDataSource,
    LLMRecentMessageDataSource,
    LLMServerStateDataSource,
    LLMUserMemoryDataSource,
)

logger = logging.getLogger(__name__)

SendResponse = Callable[[str], Awaitable[None]]
CompleteMessage = Callable[[], Awaitable[None]]


class LLMService:
    def __init__(
        self,
        settings: LLMSettings,
        *,
        engine: LLMEngine | None = None,
        tools: LLMToolRegistry | None = None,
        sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
        image_cache_dir: Path | None = None,
    ):
        self.settings = settings
        self.tools = tools or LLMToolRegistry()
        self.engine = engine or LLMEngine(settings, tools=self.tools)
        self.sleep = sleep
        self.buffers: dict[tuple[str, str], list[LLMBufferedMessage]] = defaultdict(list)
        self.completions: dict[tuple[str, str], list[CompleteMessage]] = defaultdict(list)
        self.flush_tasks: dict[tuple[str, str], asyncio.Task] = {}
        self.flushing: set[tuple[str, str]] = set()
        self.locks: dict[tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)
        self.senders: dict[tuple[str, str], SendResponse] = {}
        self.image_cache_dir = image_cache_dir or Path(tempfile.gettempdir()) / "project-pg-bot" / "llm-images"
        self.image_cache: dict[tuple[str, str], dict[int, list[CachedLLMImage]]] = defaultdict(dict)

    async def enqueue_message(
        self,
        message: LLMInputMessage,
        *,
        send_response: SendResponse,
        complete_message: CompleteMessage,
    ) -> LLMResponseResult:
        key = (message.guild_id, message.channel_id)
        self.buffers[key].append(
            LLMBufferedMessage(
                guild_id=message.guild_id,
                channel_id=message.channel_id,
                user_id=message.user_id,
                author_name=message.author_name,
                content=message.content,
                is_admin=message.is_admin,
                images=list(message.images),
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        self.completions[key].append(complete_message)
        self.senders[key] = send_response
        self._schedule_flush(key, send_response)
        return LLMResponseResult(True, "queued")

    def _schedule_flush(self, key: tuple[str, str], send_response: SendResponse) -> None:
        task = self.flush_tasks.get(key)
        if task and not task.done():
            if key in self.flushing or self.locks[key].locked():
                return
            task.cancel()
        self.flush_tasks[key] = asyncio.create_task(self._debounced_flush(key, send_response))

    async def _debounced_flush(self, key: tuple[str, str], send_response: SendResponse) -> None:
        try:
            await self.sleep(self.settings.debounce_seconds)
            await self.flush(key, send_response)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("LLM debounced flush failed", extra={"guild_id": key[0], "channel_id": key[1]})
            await self._complete_pending(key)

    async def flush(self, key: tuple[str, str], send_response: SendResponse) -> None:
        guild_id, channel_id = key
        async with self.locks[key]:
            if not self.buffers[key]:
                return
            self.flushing.add(key)
            current = list(self.buffers[key])
            self.buffers[key].clear()
            current_completions = self.completions[key][: len(current)]
            del self.completions[key][: len(current)]
            try:
                conversation = BufferedConversation(
                    messages=[
                        Message(
                            author_id=message.user_id,
                            author_name=message.author_name,
                            content=message.content or self._image_placeholder(message),
                            timestamp=datetime.fromisoformat(message.created_at),
                            images=list(message.images),
                        )
                        for message in current
                    ],
                    started_at=datetime.fromisoformat(current[0].created_at),
                    closed_at=datetime.now(timezone.utc),
                )
                memory_state = await self._load_memory_state(guild_id, channel_id, conversation.participants)
                response_text = await self.engine.respond(
                    conversation=conversation,
                    memory_state=memory_state,
                    actor=current[-1],
                    guild_id=guild_id,
                    channel_id=channel_id,
                )
                if not response_text.strip():
                    response_text = "응답을 생성하지 못했습니다. 다시 한 번 말씀해 주세요."
                await send_response(response_text)
                await self._record_recent(guild_id, channel_id, current, response_text)
            except Exception:
                logger.exception("LLM response generation failed", extra={"guild_id": guild_id, "channel_id": channel_id})
                await send_response("LLM 응답을 생성하는 중 오류가 발생했습니다.")
            finally:
                self.flushing.discard(key)
                await self._complete_callbacks(key, current_completions)
                if self.buffers[key]:
                    next_sender = self.senders.get(key, send_response)
                    self.flush_tasks[key] = asyncio.create_task(self._debounced_flush(key, next_sender))

    async def _load_memory_state(self, guild_id: str, channel_id: str, user_ids: set[str]) -> MemoryState:
        server_state = await LLMServerStateDataSource.get(guild_id, channel_id)
        global_memories = await LLMGlobalMemoryDataSource.list(guild_id, channel_id)
        user_memories = await LLMUserMemoryDataSource.list_for_users(guild_id, channel_id, sorted(user_ids))
        recent_messages = await LLMRecentMessageDataSource.list_recent(guild_id, channel_id, self.settings.max_recent_logs)
        state = MemoryState(
            server_memory=ServerMemory(notes=[row.content for row in global_memories]),
            server_style=ServerStyleProfile(
                summary=server_state.server_style_summary,
                phrases=self._json_list(server_state.server_style_phrases),
            ),
            active_style_directive=server_state.active_style_directive,
            relationship_notes=self._json_list(server_state.relationship_notes),
            recent_logs=[
                RecentLogEntry(
                    role=row.role,
                    content=row.content,
                    id=row.id,
                    author_id=row.user_id,
                    author_name=row.author_name,
                    images=self._cached_images_for_row((guild_id, channel_id), row.id),
                    timestamp=datetime.fromisoformat(row.created_at),
                )
                for row in recent_messages
                if row.role in {"user", "assistant"}
            ],
            recent_summary=server_state.recent_summary,
        )
        for row in user_memories:
            memory = state.user_memories.setdefault(row.user_id, UserMemory(row.user_id, row.user_name))
            memory.notes.append(row.content)
        return state

    async def _record_recent(
        self,
        guild_id: str,
        channel_id: str,
        messages: list[LLMBufferedMessage],
        response_text: str,
    ) -> None:
        for message in messages:
            recent_id = await LLMRecentMessageDataSource.add(
                guild_id,
                channel_id,
                message.user_id,
                message.author_name,
                "user",
                message.content or self._image_placeholder(message),
            )
            if message.images:
                self._cache_recent_images((guild_id, channel_id), recent_id, message.images)
        await LLMRecentMessageDataSource.add(guild_id, channel_id, None, "assistant", "assistant", response_text)
        await LLMRecentMessageDataSource.prune(guild_id, channel_id, self.settings.max_recent_logs)
        await self._prune_image_cache(guild_id, channel_id)

    @staticmethod
    def _json_list(value: str | None) -> list[str]:
        if not value:
            return []
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value] if value else []
        return [str(item) for item in parsed] if isinstance(parsed, list) else []

    @staticmethod
    def _dedupe_tail(items: list[str], limit: int = 20) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in items:
            if item and item not in seen:
                seen.add(item)
                result.append(item)
        return result[-limit:]

    @staticmethod
    def _image_placeholder(message: LLMBufferedMessage) -> str:
        return f"[이미지 첨부 {len(message.images)}장]" if message.images else ""

    def _cache_recent_images(self, key: tuple[str, str], recent_id: int, images: list[LLMImageInput]) -> None:
        cached: list[CachedLLMImage] = []
        self.image_cache_dir.mkdir(parents=True, exist_ok=True)
        for index, image in enumerate(images):
            suffix = self._image_suffix(image.media_type)
            file_path = self.image_cache_dir / f"{key[0]}_{key[1]}_{recent_id}_{index}{suffix}"
            try:
                data = image.raw_bytes()
                file_path.write_bytes(data)
            except Exception:
                logger.exception(
                    "Failed to cache LLM recent image",
                    extra={"guild_id": key[0], "channel_id": key[1], "recent_id": recent_id, "image_index": index},
                )
                continue
            cached.append(
                CachedLLMImage(
                    media_type=image.media_type,
                    file_path=file_path,
                    original_bytes=image.original_bytes,
                    processed_bytes=len(data),
                    filename=image.filename,
                )
            )
        if cached:
            self.image_cache[key][recent_id] = cached

    def _cached_images_for_row(self, key: tuple[str, str], recent_id: int) -> list[LLMImageInput]:
        result: list[LLMImageInput] = []
        for cached in self.image_cache.get(key, {}).get(recent_id, []):
            image = cached.to_input()
            if image is not None:
                result.append(image)
        return result

    async def _prune_image_cache(self, guild_id: str, channel_id: str) -> None:
        key = (guild_id, channel_id)
        cached = self.image_cache.get(key)
        if not cached:
            return
        recent_messages = await LLMRecentMessageDataSource.list_recent(guild_id, channel_id, self.settings.max_recent_logs)
        if self.settings.max_recent_conversation_lines <= 0:
            keep_ids: set[int] = set()
        else:
            keep_ids = {
                row.id
                for row in recent_messages[-self.settings.max_recent_conversation_lines :]
                if row.role == "user"
            }
        for recent_id in list(cached):
            if recent_id not in keep_ids:
                self._delete_cached_images(cached.pop(recent_id))
        if not cached:
            self.image_cache.pop(key, None)

    @staticmethod
    def _delete_cached_images(images: list[CachedLLMImage]) -> None:
        for image in images:
            try:
                image.file_path.unlink(missing_ok=True)
            except OSError:
                logger.exception("Failed to delete LLM cached image", extra={"image_path": str(image.file_path)})

    @staticmethod
    def _image_suffix(media_type: str) -> str:
        if media_type == "image/png":
            return ".png"
        if media_type == "image/webp":
            return ".webp"
        if media_type == "image/gif":
            return ".gif"
        return ".jpg"

    async def _complete_pending(self, key: tuple[str, str]) -> None:
        completions = self.completions.pop(key, [])
        await self._complete_callbacks(key, completions)

    async def _complete_callbacks(self, key: tuple[str, str], completions: list[CompleteMessage]) -> None:
        for complete in completions:
            try:
                await complete()
            except Exception:
                logger.exception("LLM message completion callback failed", extra={"guild_id": key[0], "channel_id": key[1]})
