from core.llm.tools.base import LLMTool, ToolContext
from core.local.llm import LLMUserMemoryDataSource


class SaveStyleTool(LLMTool):
    name = "save_style"
    description = (
        "Deprecated compatibility tool. Save the speaking user's tone, style, or response-format preference "
        "as actor-owned personal memory."
    )
    parameters = {
        "type": "object",
        "properties": {
            "note": {"type": "string", "description": "Personal tone or response-format preference to store."},
        },
        "required": ["note"],
    }

    async def run(self, arguments: dict, ctx: ToolContext) -> str:
        note = self.parse_note(arguments) or "사용자가 요청한 말투/응답 방식"
        actor = ctx.actor

        memory_id = await LLMUserMemoryDataSource.add(
            ctx.guild_id, ctx.channel_id, actor.user_id, f"개인 말투/응답 설정: {note}",
            user_name=actor.author_name,
        )
        return f"{actor.author_name} 개인 메모리에 말투 설정을 저장했습니다. id={memory_id}"
