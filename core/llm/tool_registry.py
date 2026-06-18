import re
from typing import Any

from core.llm.models import LLMBufferedMessage, ToolResult
from core.local.llm import (
    LLMGlobalMemoryDataSource,
    LLMServerStateDataSource,
    LLMSpeechStyleDataSource,
    LLMUserMemoryDataSource,
)


class LLMToolRegistry:
    """Exposes function-calling tool definitions and dispatches MAIN LLM tool calls to DB ops."""

    SAVE_SCOPE_DESCRIPTION = "user=해당 발화자 개인 저장, server=서버/채널 전역 저장(관리자만). 명시가 없으면 user."

    def tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "save_memory",
                    "description": (
                        "사용자가 장기 기억/선호/정보/규칙을 저장하라고 명시했을 때 호출한다. "
                        "사용자 원문 그대로보다 봇이 저장할 핵심을 짧은 문장으로 note에 정리한다. "
                        "사용자가 '기억해줘'처럼 대상을 명시하지 않아도 직전 대화 맥락에서 저장할 내용을 추론해 저장한다 "
                        "(예: 직전에 오버워치를 칭찬했으면 '사용자는 오버워치를 좋아한다' 식으로 note 정리). "
                        "맥락이 명확하면 확인 질문 없이 바로 이 툴을 호출하고, 정말 맥락이 전혀 없을 때만 툴 없이 되묻는다."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "note": {"type": "string", "description": "DB에 저장할 기억 내용"},
                            "scope": {"type": "string", "enum": ["user", "server"], "description": self.SAVE_SCOPE_DESCRIPTION},
                        },
                        "required": ["note", "scope"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "save_style",
                    "description": (
                        "사용자가 봇의 말투/어조/응답 방식을 변경/적용/업데이트하라고 명시했을 때 호출한다. "
                        "note에 봇이 따를 말투 지시를 짧게 정리한다(예: '용용체로 답한다')."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "note": {"type": "string", "description": "봇이 따를 말투/응답 지시"},
                            "scope": {"type": "string", "enum": ["user", "server"], "description": self.SAVE_SCOPE_DESCRIPTION},
                        },
                        "required": ["note", "scope"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "clear_memory",
                    "description": (
                        "사용자가 기억/말투를 삭제/초기화/비우/리셋하라고 명시했을 때 호출한다. "
                        "저장된 기억과 말투 설정을 함께 지운다."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "scope": {"type": "string", "enum": ["user", "server"], "description": self.SAVE_SCOPE_DESCRIPTION},
                        },
                        "required": ["scope"],
                    },
                },
            },
        ]

    async def dispatch(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        ctx: dict[str, Any],
    ) -> str:
        guild_id = str(ctx["guild_id"])
        channel_id = str(ctx["channel_id"])
        actor = ctx["actor"]
        scope = str(arguments.get("scope", "user")).lower()
        if scope not in {"user", "server"}:
            scope = "user"
        note = str(arguments.get("note", "") or "").strip()
        if name == "save_memory":
            return (await self._update_memory(guild_id, channel_id, actor, scope, note)).content
        if name == "save_style":
            return (await self._update_style(guild_id, channel_id, actor, scope, note)).content
        if name == "clear_memory":
            return (await self._clear_memory_or_style(guild_id, channel_id, actor, scope)).content
        return f"알 수 없는 툴: {name}"

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
                data={"scope": "server", "deleted_memories": deleted_memories},
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