import re

from core.llm.models import LLMBufferedMessage, MemoryState, ToolResult
from core.local.llm import (
    LLMGlobalMemoryDataSource,
    LLMRecentMessageDataSource,
    LLMServerStateDataSource,
    LLMSpeechStyleDataSource,
    LLMUserMemoryDataSource,
)


class LLMToolRegistry:
    async def run_planned_tools(
        self,
        *,
        guild_id: str,
        channel_id: str,
        current_buffer: list[LLMBufferedMessage],
        memory_state: MemoryState,
    ) -> list[ToolResult]:
        if not current_buffer:
            return []
        text = "\n".join(message.content for message in current_buffer)
        actor = current_buffer[-1]
        results: list[ToolResult] = []

        if self._is_clear_memory_or_style_request(text):
            results.append(await self._clear_memory_or_style(guild_id, channel_id, actor, text))
            return results

        if self._is_explicit_memory_update(text):
            results.append(await self._update_memory(guild_id, channel_id, actor, text))
        if self._is_explicit_style_update(text):
            results.append(await self._update_style(guild_id, channel_id, actor, text))
        if self._is_memory_query(text):
            results.extend(await self._search_memory(guild_id, channel_id, actor, text, memory_state))
        return results[:6]

    async def collect_context(self, **kwargs: object) -> str:
        results = await self.run_planned_tools(**kwargs)
        return "\n".join(f"- {result.name}: {result.content}" for result in results)

    async def _clear_memory_or_style(
        self,
        guild_id: str,
        channel_id: str,
        actor: LLMBufferedMessage,
        text: str,
    ) -> ToolResult:
        server_scope = self._is_server_scope_request(text)
        if actor.is_admin and server_scope:
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
        if server_scope:
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
        text: str,
    ) -> ToolResult:
        note = self._clean_note(text)
        if actor.is_admin and self._is_server_scope_request(text):
            memory_id = await LLMGlobalMemoryDataSource.add(guild_id, channel_id, "server_memory", note, 1, actor.user_id)
            return ToolResult(
                name="update_server_memory",
                ok=True,
                content=f"서버/채널 전역 기억을 저장했습니다. id={memory_id}",
                data={"scope": "server", "memory_id": memory_id},
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
            data={"scope": "user", "memory_id": memory_id, "user_id": actor.user_id},
        )

    async def _update_style(
        self,
        guild_id: str,
        channel_id: str,
        actor: LLMBufferedMessage,
        text: str,
    ) -> ToolResult:
        note = self._clean_note(text)
        if actor.is_admin and self._is_server_scope_request(text):
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
                data={"scope": "server"},
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
            data={"scope": "user", "user_id": actor.user_id},
        )

    async def _search_memory(
        self,
        guild_id: str,
        channel_id: str,
        actor: LLMBufferedMessage,
        text: str,
        memory_state: MemoryState,
    ) -> list[ToolResult]:
        recent = await LLMRecentMessageDataSource.list_recent(guild_id, channel_id, 8)
        user_memories = memory_state.user_memories.get(actor.user_id)
        user_style = memory_state.user_styles.get(actor.user_id)
        server_memory = memory_state.server_memory.notes[-8:]
        results = [
            ToolResult(
                name="search_server_memory",
                ok=True,
                content=self._bullet(server_memory, "서버 공용 기억이 아직 없습니다."),
                data={"count": len(server_memory)},
            ),
            ToolResult(
                name="search_user_memory",
                ok=True,
                content=self._bullet(user_memories.notes[-8:] if user_memories else [], "해당 유저 기억이 아직 없습니다."),
                data={"count": len(user_memories.notes) if user_memories else 0},
            ),
            ToolResult(
                name="extract_speech_style",
                ok=True,
                content=self._style_content(user_style),
            ),
        ]
        if any(keyword in text for keyword in ("처음", "저번", "전에", "뭐라", "말했")):
            lines = [f"{row.author_name or row.role}: {row.content}" for row in recent]
            results.append(
                ToolResult(
                    name="search_recent_messages",
                    ok=True,
                    content="\n".join(lines) if lines else "관련 최근 대화를 찾지 못했습니다.",
                    data={"count": len(lines)},
                )
            )
        return results

    @staticmethod
    def _style_content(user_style) -> str:
        if user_style is None:
            return "아직 말투 메모가 없습니다."
        lines = list(user_style.notes[-5:])
        if user_style.phrases:
            lines.append("자주 보인 표현: " + ", ".join(user_style.phrases[-8:]))
        return "\n".join(f"- {line}" for line in lines) if lines else "아직 말투 메모가 없습니다."

    @staticmethod
    def _bullet(items: list[str], empty: str) -> str:
        return "\n".join(f"- {item}" for item in items) if items else empty

    @staticmethod
    def _is_clear_memory_or_style_request(text: str) -> bool:
        compact = re.sub(r"\s+", "", text)
        has_delete = any(keyword in compact for keyword in ("지워", "삭제", "초기화", "리셋", "비워"))
        has_memory_or_style = any(keyword in compact for keyword in ("기억", "메모리", "말투", "스타일"))
        return has_delete and has_memory_or_style

    @staticmethod
    def _is_explicit_memory_update(text: str) -> bool:
        compact = re.sub(r"\s+", "", text)
        return any(keyword in compact for keyword in ("기억해", "기억해줘", "잊지마", "저장해", "메모해"))

    @staticmethod
    def _is_explicit_style_update(text: str) -> bool:
        compact = re.sub(r"\s+", "", text)
        has_style = any(keyword in compact for keyword in ("말투", "스타일", "어조", "응답방식"))
        has_request = any(keyword in compact for keyword in ("적용", "써줘", "해줘", "바꿔", "말해", "답해"))
        return has_style and has_request

    @staticmethod
    def _is_memory_query(text: str) -> bool:
        compact = re.sub(r"\s+", "", text)
        return any(keyword in compact for keyword in ("기억", "메모리", "말투", "스타일", "처음", "저번", "전에")) and any(
            keyword in compact for keyword in ("뭐", "알려", "보여", "조사", "저장", "있어")
        )

    @staticmethod
    def _is_server_scope_request(text: str) -> bool:
        compact = re.sub(r"\s+", "", text)
        return any(keyword in compact for keyword in ("서버", "길드", "채널", "전역", "공용", "모두"))

    @staticmethod
    def _clean_note(text: str) -> str:
        cleaned = re.sub(r"\s+", " ", text).strip()
        cleaned = re.sub(r"(앞으로|기억해줘|기억해|잊지마|저장해|메모해)", "", cleaned).strip()
        return cleaned or text.strip()
