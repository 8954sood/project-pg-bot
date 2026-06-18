import asyncio
import json
import logging
import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Awaitable, Callable

from core.llm.config import LLMSettings
from core.llm.engine import LLMEngine
from core.llm.memory_extractor import LLMMemoryExtractor
from core.llm.memory_policy import MemoryExtractionPolicy
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
    ToolResult,
    UserMemory,
    UserStyleProfile,
)
from core.llm.tool_registry import LLMToolRegistry
from core.local.llm import (
    LLMGlobalMemoryDataSource,
    LLMMemoryJobDataSource,
    LLMRecentMessageDataSource,
    LLMServerStateDataSource,
    LLMSpeechStyleDataSource,
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
        extractor: LLMMemoryExtractor | None = None,
        tools: LLMToolRegistry | None = None,
        sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
    ):
        self.settings = settings
        self.engine = engine or LLMEngine(settings)
        self.extractor = extractor
        self.tools = tools or LLMToolRegistry()
        self.sleep = sleep
        self.buffers: dict[tuple[str, str], list[LLMBufferedMessage]] = defaultdict(list)
        self.completions: dict[tuple[str, str], list[CompleteMessage]] = defaultdict(list)
        self.flush_tasks: dict[tuple[str, str], asyncio.Task] = {}
        self.locks: dict[tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)
        self.senders: dict[tuple[str, str], SendResponse] = {}
        self.memory_tasks: list[asyncio.Task] = []

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
            job_state = await LLMMemoryJobDataSource.get(guild_id, channel_id)
            if self.settings.memory.blocks_next_response and job_state.running:
                return
            current = list(self.buffers[key])
            self.buffers[key].clear()
            try:
                conversation = BufferedConversation(
                    messages=[
                        Message(
                            author_id=message.user_id,
                            author_name=message.author_name,
                            content=message.content,
                            timestamp=datetime.fromisoformat(message.created_at),
                        )
                        for message in current
                    ],
                    started_at=datetime.fromisoformat(current[0].created_at),
                    closed_at=datetime.now(timezone.utc),
                )
                memory_state = await self._load_memory_state(guild_id, channel_id, conversation.participants)
                tool_results = await self.tools.run_planned_tools(
                    guild_id=guild_id,
                    channel_id=channel_id,
                    current_buffer=current,
                    memory_state=memory_state,
                )
                terminal_result = next((result for result in tool_results if result.data.get("terminal_response")), None)
                if terminal_result is not None:
                    await send_response(terminal_result.content)
                    await self._record_recent(guild_id, channel_id, current, terminal_result.content)
                    return
                response_text = await self.engine.respond(
                    conversation=conversation,
                    memory_state=memory_state,
                    tool_results=tool_results,
                )
                await send_response(response_text)
                await self._record_recent(guild_id, channel_id, current, response_text)
                state = await LLMMemoryJobDataSource.increment_turns(guild_id, channel_id)
                should_extract = MemoryExtractionPolicy(self.settings.memory).should_extract(current, state.turns_since_last_memory_extraction)
                user_chars = sum(len(message.content) for message in current if message.user_id)
                total_chars = sum(len(message.content) for message in current)
                logger.info(
                    "LLM memory extraction decision",
                    extra={
                        "guild_id": guild_id,
                        "channel_id": channel_id,
                        "should_extract": should_extract,
                        "extractor_available": self.extractor is not None,
                        "user_chars": user_chars,
                        "total_chars": total_chars,
                        "turns_since_last_memory_extraction": state.turns_since_last_memory_extraction,
                        "min_user_chars": self.settings.memory.min_user_chars,
                        "min_total_chars": self.settings.memory.min_total_chars,
                        "every_n_turns": self.settings.memory.every_n_turns,
                    },
                )
                if should_extract:
                    await self._start_memory_job(key, current, response_text)
            except Exception:
                logger.exception("LLM response generation failed", extra={"guild_id": guild_id, "channel_id": channel_id})
                await send_response("LLM 응답을 생성하는 중 오류가 발생했습니다.")
            finally:
                await self._complete_pending(key)

    async def _start_memory_job(self, key: tuple[str, str], messages: list[LLMBufferedMessage], response_text: str = "") -> None:
        job_id = str(uuid.uuid4())
        await LLMMemoryJobDataSource.set_running(key[0], key[1], True, job_id)
        logger.info("LLM memory extraction job started", extra={"guild_id": key[0], "channel_id": key[1], "job_id": job_id})
        task = asyncio.create_task(self._run_memory_job(key, messages, response_text))
        self.memory_tasks.append(task)

        def _log_task_result(done: asyncio.Task) -> None:
            try:
                done.result()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("LLM memory job task failed", extra={"guild_id": key[0], "channel_id": key[1]})

        task.add_done_callback(_log_task_result)

    async def _run_memory_job(self, key: tuple[str, str], messages: list[LLMBufferedMessage], response_text: str = "") -> None:
        guild_id, channel_id = key
        had_changes = False
        try:
            if self.extractor is not None:
                result = await asyncio.wait_for(
                    self.extractor.extract(messages),
                    timeout=self.settings.memory.job_timeout_seconds,
                )
                self._move_personal_scope_style_to_user_memory(result, messages)
                if result.active_style_directive or result.server_style_summary or result.relationship_notes_add:
                    state = await LLMServerStateDataSource.get(guild_id, channel_id)
                    relationship_notes = self._json_list(state.relationship_notes)
                    relationship_notes.extend(result.relationship_notes_add)
                    await LLMServerStateDataSource.upsert(
                        guild_id,
                        channel_id,
                        active_style_directive=result.active_style_directive or None,
                        server_style_summary=result.server_style_summary or None,
                        relationship_notes=self._dedupe_tail(relationship_notes),
                    )
                for note in result.server_memory_add:
                    await LLMGlobalMemoryDataSource.add(guild_id, channel_id, "server_memory", note, 1, "memory_extraction")
                for user_id, user_name, note in result.user_memory_add:
                    await LLMUserMemoryDataSource.add(guild_id, channel_id, user_id, note, 1, user_name=user_name)
                for user_id, user_name, note in result.user_style_add:
                    await LLMSpeechStyleDataSource.upsert(guild_id, channel_id, user_id, note, user_name=user_name, notes=note)
                for user_id, user_name, phrases in result.user_style_phrases_add:
                    await LLMSpeechStyleDataSource.upsert(
                        guild_id,
                        channel_id,
                        user_id,
                        ", ".join(phrases),
                        user_name=user_name,
                        phrases=json.dumps(phrases, ensure_ascii=False),
                    )
                had_changes = result.had_changes
        except Exception:
            logger.exception("LLM memory extraction failed", extra={"guild_id": guild_id, "channel_id": channel_id})
        finally:
            cooldown = 0 if had_changes else self.settings.memory.cooldown_turns_after_empty
            await LLMMemoryJobDataSource.complete(guild_id, channel_id, had_changes, cooldown)
            logger.info(
                "LLM memory extraction job completed",
                extra={"guild_id": guild_id, "channel_id": channel_id, "had_changes": had_changes, "cooldown": cooldown},
            )
            if self.buffers[key]:
                send_response = self.senders.get(key)
                if send_response is not None:
                    self._schedule_flush(key, send_response)

    async def _load_memory_state(self, guild_id: str, channel_id: str, user_ids: set[str]) -> MemoryState:
        server_state = await LLMServerStateDataSource.get(guild_id, channel_id)
        global_memories = await LLMGlobalMemoryDataSource.list(guild_id, channel_id)
        user_memories = await LLMUserMemoryDataSource.list_for_users(guild_id, channel_id, sorted(user_ids))
        speech_styles = await LLMSpeechStyleDataSource.list_for_users(guild_id, channel_id, sorted(user_ids))
        recent_messages = await LLMRecentMessageDataSource.list_recent(guild_id, channel_id, self.settings.max_recent_logs)
        job_state = await LLMMemoryJobDataSource.get(guild_id, channel_id)
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
                    author_id=row.user_id,
                    author_name=row.author_name,
                    timestamp=datetime.fromisoformat(row.created_at),
                )
                for row in recent_messages
                if row.role in {"user", "assistant"}
            ],
            recent_summary=server_state.recent_summary,
            memory_job_running=bool(job_state.running),
            memory_job_started_at=datetime.fromisoformat(job_state.started_at) if job_state.started_at else None,
            pending_memory_job_id=job_state.pending_job_id,
            turns_since_last_memory_extraction=job_state.turns_since_last_memory_extraction,
            memory_extraction_cooldown_turns=job_state.memory_extraction_cooldown_turns,
            last_memory_extraction_had_changes=bool(job_state.last_memory_extraction_had_changes),
        )
        for row in user_memories:
            memory = state.user_memories.setdefault(row.user_id, UserMemory(row.user_id, row.user_name))
            memory.notes.append(row.content)
        for row in speech_styles:
            style = state.user_styles.setdefault(row.user_id, UserStyleProfile(row.user_id, row.user_name))
            style.notes.extend(self._json_list(row.notes) or ([row.notes] if row.notes else []))
            style.phrases.extend(self._json_list(row.phrases))
        return state

    async def _record_recent(
        self,
        guild_id: str,
        channel_id: str,
        messages: list[LLMBufferedMessage],
        response_text: str,
    ) -> None:
        for message in messages:
            await LLMRecentMessageDataSource.add(
                guild_id,
                channel_id,
                message.user_id,
                message.author_name,
                "user",
                message.content,
            )
        await LLMRecentMessageDataSource.add(guild_id, channel_id, None, "assistant", "assistant", response_text)
        await LLMRecentMessageDataSource.prune(guild_id, channel_id, self.settings.max_recent_logs)

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
    def _move_personal_scope_style_to_user_memory(
        result,
        messages: list[LLMBufferedMessage],
    ) -> None:
        if not messages:
            return
        text = "\n".join(message.content for message in messages)
        personal_scope = re.search(
            r"(나한테만|내게만|저한테만|개인적|길드에는\s*적용하지\s*마|서버에는\s*적용하지\s*마|길드에\s*적용하지\s*마|서버에\s*적용하지\s*마)",
            text,
        )
        directive = result.active_style_directive or result.server_style_summary
        if not personal_scope or not directive:
            return
        last_message = messages[-1]
        result.user_style_add.append(
            (
                last_message.user_id,
                last_message.author_name,
                f"봇은 {last_message.author_name}에게만 다음 말투/응답 지시를 적용한다: {directive}. 서버/길드 전역에는 적용하지 않는다.",
            )
        )
        result.active_style_directive = ""
        result.server_style_summary = ""

    async def _complete_pending(self, key: tuple[str, str]) -> None:
        completions = self.completions.pop(key, [])
        for complete in completions:
            try:
                await complete()
            except Exception:
                logger.exception("LLM message completion callback failed", extra={"guild_id": key[0], "channel_id": key[1]})
