import json
import logging
import uuid
from typing import Any

from core.llm.config import LLMSettings
from core.llm.llm_client import OpenAICompatibleClient
from core.llm.models import BufferedConversation, LLMBufferedMessage, MemoryState
from core.llm.prompt_builder import LLMPromptBuilder
from core.llm.tools import LLMToolRegistry, ToolContext

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 4


class LLMEngine:
    """MAIN LLM orchestrator. Calls the LLM with tool definitions and loops until a final answer.

    Flow: MAIN LLM sees the user message + memory context + available tools, then either
    answers directly or emits tool calls (save_memory / save_style / clear_memory). Tool
    results are fed back to MAIN, which produces the final answer.
    """

    def __init__(
        self,
        settings: LLMSettings,
        client: OpenAICompatibleClient | None = None,
        tools: LLMToolRegistry | None = None,
    ):
        self.settings = settings
        self.client = client or OpenAICompatibleClient(settings.payload_logging, purpose="chat")
        self.tools = tools or LLMToolRegistry()
        self.prompt_builder = LLMPromptBuilder(settings)

    async def respond(
        self,
        *,
        conversation: BufferedConversation,
        memory_state: MemoryState,
        actor: LLMBufferedMessage,
        guild_id: str,
        channel_id: str,
    ) -> str:
        base_messages = [
            message.to_dict()
            for message in self.prompt_builder.build_messages(conversation=conversation, memory_state=memory_state)
        ]
        tools = self.tools.tool_definitions()
        ctx = ToolContext(guild_id=guild_id, channel_id=channel_id, actor=actor)
        loop_messages: list[dict[str, Any]] = []
        last_content = ""

        for _ in range(MAX_TOOL_ROUNDS):
            response = await self.client.chat(
                self.settings.main,
                base_messages + loop_messages,
                tools=tools,
            )
            last_content = response.content
            if not response.tool_calls:
                return response.content.strip()

            call_ids = [self._tool_call_id() for _ in response.tool_calls]
            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": response.content or "",
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments, ensure_ascii=False),
                        },
                    }
                    for call_id, call in zip(call_ids, response.tool_calls)
                ],
            }
            loop_messages.append(assistant_message)

            for call_id, call in zip(call_ids, response.tool_calls):
                try:
                    result = await self.tools.dispatch(call.name, call.arguments, ctx=ctx)
                except Exception as exc:
                    logger.exception("Tool dispatch failed", extra={"tool": call.name})
                    result = f"툴 실행 중 오류가 발생했습니다: {exc}"
                loop_messages.append({"role": "tool", "tool_call_id": call_id, "content": result})

        logger.warning("LLM orchestrator hit max tool rounds", extra={"rounds": MAX_TOOL_ROUNDS})
        return (last_content or "응답을 생성하지 못했습니다.").strip()

    @staticmethod
    def _tool_call_id() -> str:
        return f"call_{uuid.uuid4().hex[:12]}"