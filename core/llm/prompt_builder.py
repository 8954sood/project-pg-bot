from core.llm.config import LLMSettings
from core.llm.models import BufferedConversation, ChatMessage, MemoryState, ToolResult

SYSTEM_PROMPT = (
    "너는 Discord 서버용 공용 대화 봇 MVP의 응답 생성기다. "
    "한국어로 짧고 자연스럽게 답하고, 현재 대화/기억/tool 결과를 구분해서 사용한다. "
    "사용자가 말투 변경을 요청하면 안전 범위 안에서 그 스타일을 따른다. "
    "최근 대화 메시지를 우선 참고해 바로 앞 맥락을 이어간다. "
    "혐오, 개인정보 노출, 직접적인 괴롭힘 표현은 따라 하지 않는다. "
    "모르는 내용은 아는 척하지 않는다. 실제 사람인 척하지 않는다. "
    "일반 대화는 1~3문장, 기획/구조 질문은 짧은 요약과 핵심만 답한다."
)


class LLMPromptBuilder:
    def __init__(self, settings: LLMSettings):
        self.settings = settings
        self.last_budget_report: dict[str, int] = {}

    def build_messages(
        self,
        *,
        conversation: BufferedConversation,
        memory_state: MemoryState,
        tool_results: list[ToolResult] | None = None,
    ) -> list[ChatMessage]:
        self.last_budget_report = {}
        tool_results = tool_results or []
        messages = [ChatMessage(role="system", content=SYSTEM_PROMPT)]
        recent_messages = self._build_recent_conversation_messages(memory_state)
        messages.extend(self._fit_recent_messages(recent_messages, self.settings.max_recent_context_chars))
        dynamic_context = self._build_dynamic_context_block(memory_state, conversation.participants)
        if dynamic_context:
            messages.append(ChatMessage(role="user", content=dynamic_context))
        if tool_results:
            messages.append(self._build_tool_result_message(tool_results))
        messages.append(self._build_current_buffer_message(conversation))
        return messages

    def _build_dynamic_context_block(self, memory_state: MemoryState, participant_ids: set[str]) -> str:
        user_memories, user_styles = self._participant_context(memory_state, participant_ids)
        server_sections = [
            "[현재 우선 말투]\n" + (memory_state.active_style_directive or "아직 없음"),
            f"[서버 말투]\n{memory_state.server_style.summary}",
            "[서버 기억]\n" + self._bullet(memory_state.server_memory.notes[-8:]),
        ]
        if memory_state.recent_summary.strip():
            server_sections.append(f"[최근 대화 요약]\n{memory_state.recent_summary}")
        server_context = self._clip(
            "server_context",
            "\n\n".join(server_sections),
            self.settings.max_global_context_chars,
        )
        participant_context = self._clip(
            "participant_context",
            "\n\n".join([
                "[유저 기억]\n" + self._bullet(user_memories),
                "[유저 말투]\n" + self._bullet(user_styles),
            ]),
            self.settings.max_participant_context_chars,
        )
        return server_context + "\n\n" + participant_context

    def _build_tool_result_message(self, tool_results: list[ToolResult]) -> ChatMessage:
        tool_text = self._clip("tool_context", "[Tool 결과]\n" + self._tool_text(tool_results), self.settings.max_tool_context_chars)
        return ChatMessage(role="user", content=tool_text)

    def _build_current_buffer_message(self, conversation: BufferedConversation) -> ChatMessage:
        header = "[현재 버퍼]\n"
        current_buffer = header + self._clip(
            "current_buffer",
            conversation.text,
            max(0, self.settings.max_current_buffer_chars - len(header)),
            keep_tail=True,
        )
        return ChatMessage(role="user", content=current_buffer)

    @staticmethod
    def _bullet(items: list[str]) -> str:
        return "\n".join(f"- {item}" for item in items) if items else "- 아직 없음"

    @staticmethod
    def _tool_text(results: list[ToolResult]) -> str:
        if not results:
            return "- 사용한 tool 없음"
        return "\n".join(f"- {result.name}: {result.content}" for result in results)

    @staticmethod
    def _build_recent_conversation_messages(memory_state: MemoryState, limit: int = 12) -> list[ChatMessage]:
        logs = memory_state.recent_logs[-limit:]
        messages: list[ChatMessage] = []
        for entry in logs:
            if entry.role == "assistant":
                messages.append(ChatMessage(role="assistant", content=entry.content))
            else:
                author = entry.author_name or entry.author_id or "user"
                messages.append(ChatMessage(role="user", content=f"{author}: {entry.content}"))
        return messages

    @staticmethod
    def _participant_context(memory_state: MemoryState, user_ids: set[str], limit: int = 8) -> tuple[list[str], list[str]]:
        user_memories: list[str] = []
        user_styles: list[str] = []
        for user_id in sorted(user_ids):
            user_memory = memory_state.user_memories.get(user_id)
            user_style = memory_state.user_styles.get(user_id)
            if user_memory is not None:
                user_memories.extend(f"{user_memory.user_name} ({user_id}): {note}" for note in user_memory.notes[-limit:])
            if user_style is not None:
                user_styles.extend(f"{user_style.user_name} ({user_id}): {note}" for note in user_style.notes[-limit:])
                if user_style.phrases:
                    user_styles.append(f"{user_style.user_name} ({user_id}) phrases: {', '.join(user_style.phrases[-limit:])}")
        return user_memories[-limit:], user_styles[-limit:]

    def _fit_recent_messages(self, messages: list[ChatMessage], max_chars: int) -> list[ChatMessage]:
        selected: list[ChatMessage] = []
        total = 0
        excluded = 0
        for message in reversed(messages):
            size = len(message.content)
            if selected and total + size > max_chars:
                excluded += 1
                continue
            if not selected and size > max_chars:
                selected.append(ChatMessage(role=message.role, content=self._clip_text(message.content, max_chars, keep_tail=True)))
                total = max_chars
                continue
            selected.append(message)
            total += size
        selected.reverse()
        self.last_budget_report["recent_original_chars"] = sum(len(message.content) for message in messages)
        self.last_budget_report["recent_final_chars"] = sum(len(message.content) for message in selected)
        self.last_budget_report["recent_excluded"] = excluded
        return selected

    def _clip(self, key: str, text: str, max_chars: int, keep_tail: bool = False) -> str:
        self.last_budget_report[f"{key}_original_chars"] = len(text)
        clipped = self._clip_text(text, max_chars, keep_tail=keep_tail)
        self.last_budget_report[f"{key}_final_chars"] = len(clipped)
        return clipped

    @staticmethod
    def _clip_text(text: str, max_chars: int, keep_tail: bool = False) -> str:
        if len(text) <= max_chars:
            return text
        marker = "\n..."
        if max_chars <= len(marker):
            return text[:max_chars]
        if keep_tail:
            return marker + text[-(max_chars - len(marker)):]
        return text[: max_chars - len(marker)] + marker
