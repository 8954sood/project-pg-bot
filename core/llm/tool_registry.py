import re
from typing import Protocol

from core.llm.models import LLMBufferedMessage, MemoryState, ToolResult
from core.local.llm import (
    LLMGlobalMemoryDataSource,
    LLMServerStateDataSource,
    LLMSpeechStyleDataSource,
    LLMUserMemoryDataSource,
)


class ToolPlanner(Protocol):
    async def plan(self, text: str, is_admin: bool, memory_state: MemoryState): ...


class LLMToolRegistry:
    def __init__(self, planner: ToolPlanner | None = None):
        self.planner = planner

    async def run_planned_tools(
        self,
        *,
        guild_id: str,
        channel_id: str,
        current_buffer: list[LLMBufferedMessage],
        memory_state: MemoryState,
    ) -> list[ToolResult]:
        if not current_buffer or self.planner is None:
            return []
        text = "\n".join(message.content for message in current_buffer)
        actor = current_buffer[-1]
        plans = await self.planner.plan(text, actor.is_admin, memory_state)
        results: list[ToolResult] = []
        for plan in plans:
            if plan.is_noop:
                continue
            if plan.tool == "clear_memory":
                return [await self._clear_memory_or_style(guild_id, channel_id, actor, plan.scope)]
            if plan.tool == "update_memory":
                results.append(
                    await self._update_memory(guild_id, channel_id, actor, plan.scope, plan.note or self._clean_note(text))
                )
            elif plan.tool == "update_style":
                results.append(
                    await self._update_style(guild_id, channel_id, actor, plan.scope, plan.note or self._clean_note(text))
                )
        return results[:6]

    async def collect_context(self, **kwargs: object) -> str:
        results = await self.run_planned_tools(**kwargs)
        return "\n".join(f"- {result.name}: {result.content}" for result in results)

    async def _clear_memory_or_style(
        self,
        guild_id: str,
        channel_id: str,
        actor: LLMBufferedMessage,
        scope: str,
    ) -> ToolResult:
        if scope == "server" and actor.is_admin:
            deleted_memories = await LLMGlobalMemoryDataSource.delete_scope(guild_id, channel_id)
            await LLMServerStateDataSource.reset_style_and_notes(guild_id, channel_id)
            return ToolResult(
                name="clear_memory_or_style",
                ok=True,
                content=f"서버/채널 전역 기억 {deleted_memories}개와 서버 말투 설정을 삭제했습니다.",
                data={"terminal_response": True, "scope": "server", "deleted_memories": deleted_memories},
            )

        deleted_user_memories = await LLMUserMemoryDataSource.delete_user(guild_id, channel_id, actor.user_id)
        deleted_user_styles = await LLMSpeechStyleDataSource.delete_user(guild_id, channel_id, actor.user_id)
        if scope == "server":
            content = (
                "서버 전역 기억/말투를 삭제하려면 Discord 관리자 권한이 필요합니다. "
                f"대신 본인 개인 기억 {deleted_user_memories}개와 개인 말투 설정 {deleted_user_styles}개를 삭제했습니다."
            )
        else:
            content = f"본인 개인 기억 {deleted_user_memories}개와 개인 말투 설정 {deleted_user_styles}개를 삭제했습니다."
        return ToolResult(
            name="clear_memory_or_style",
            ok=True,
            content=content,
            data={
                "terminal_response": True,
                "scope": "user",
                "deleted_user_memories": deleted_user_memories,
                "deleted_user_styles": deleted_user_styles,
            },
        )

    async def _update_memory(
        self,
        guild_id: str,
        channel_id: str,
        actor: LLMBufferedMessage,
        scope: str,
        note: str,
    ) -> ToolResult:
        if not note.strip():
            note = "사용자가 저장을 요청한 정보"
        if scope == "server" and actor.is_admin:
            memory_id = await LLMGlobalMemoryDataSource.add(guild_id, channel_id, "server_memory", note, 1, actor.user_id)
            return ToolResult(
                name="update_server_memory",
                ok=True,
                content=f"서버/채널 전역 기억을 저장했습니다. id={memory_id}",
                data={"scope": "server", "memory_id": memory_id, "note": note},
            )
        memory_id = await LLMUserMemoryDataSource.add(
            guild_id,
            channel_id,
            actor.user_id,
            note,
            user_name=actor.author_name,
        )
        return ToolResult(
            name="update_user_memory",
            ok=True,
            content=f"{actor.author_name} 개인 기억을 저장했습니다. id={memory_id}",
            data={"scope": "user", "memory_id": memory_id, "user_id": actor.user_id, "note": note},
        )

    async def _update_style(
        self,
        guild_id: str,
        channel_id: str,
        actor: LLMBufferedMessage,
        scope: str,
        note: str,
    ) -> ToolResult:
        if not note.strip():
            note = "사용자가 요청한 말투/응답 방식"
        if scope == "server" and actor.is_admin:
            await LLMServerStateDataSource.upsert(
                guild_id,
                channel_id,
                active_style_directive=note,
                server_style_summary=f"현재 서버 응답 말투 지시를 우선한다: {note}",
            )
            return ToolResult(
                name="update_server_style",
                ok=True,
                content="서버/채널 전역 말투 설정을 저장했습니다.",
                data={"scope": "server", "note": note},
            )
        await LLMSpeechStyleDataSource.upsert(
            guild_id,
            channel_id,
            actor.user_id,
            note,
            user_name=actor.author_name,
            notes=f"봇은 {actor.author_name}에게 다음 말투/응답 지시를 적용한다: {note}",
        )
        return ToolResult(
            name="update_user_style",
            ok=True,
            content=f"{actor.author_name} 개인 말투 설정을 저장했습니다.",
            data={"scope": "user", "user_id": actor.user_id, "note": note},
        )

    @staticmethod
    def _clean_note(text: str) -> str:
        cleaned = re.sub(r"\s+", " ", text).strip()
        cleaned = re.sub(
            r"(앞으로|기억해줘|기억해|잊지마|저장해|메모해|업데이트해|변경해|수정해|적용해|바꿔줘|바꿔|쓰게\s*해줘|쓰게)",
            "",
            cleaned,
        ).strip()
        return cleaned or text.strip()