from core.llm.config import LLMSettings
from core.llm.llm_client import OpenAICompatibleClient
from core.llm.models import BufferedConversation, MemoryState, ToolResult
from core.llm.prompt_builder import LLMPromptBuilder


class LLMEngine:
    def __init__(self, settings: LLMSettings, client: OpenAICompatibleClient | None = None):
        self.settings = settings
        self.client = client or OpenAICompatibleClient(settings.payload_logging, purpose="chat")
        self.prompt_builder = LLMPromptBuilder(settings)

    async def respond(
        self,
        *,
        conversation: BufferedConversation,
        memory_state: MemoryState,
        tool_results: list[ToolResult] | None = None,
    ) -> str:
        messages = self.prompt_builder.build_messages(
            conversation=conversation,
            memory_state=memory_state,
            tool_results=tool_results,
        )
        response = await self.client.chat(self.settings.main, [message.to_dict() for message in messages])
        return response.content.strip()
