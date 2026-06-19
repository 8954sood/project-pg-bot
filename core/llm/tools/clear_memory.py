from core.llm.tools.base import LLMTool, ToolContext, register_tool
from core.local.llm import LLMUserMemoryDataSource


@register_tool
class ClearMemoryTool(LLMTool):
    name = "clear_memory"
    description = (
        "Call when the user explicitly asks to delete, clear, reset, or remove their own personal memory, tone, "
        "nickname, or response-format preferences. "
        "If memory_id is provided, delete only that actor-owned personal memory. "
        "If memory_id is omitted, delete all personal memories owned by the actor. "
        "This is a personal-memory tool for actor-owned personal memories only."
    )
    parameters = {
        "type": "object",
        "properties": {
            "memory_id": {
                "type": "integer",
                "description": "Actor-owned personal memory id to delete. Omit to delete all actor-owned personal memories.",
            },
        },
        "required": [],
    }

    async def run(self, arguments: dict, ctx: ToolContext) -> str:
        actor = ctx.actor
        memory_id = self.parse_memory_id(arguments)

        if memory_id is not None:
            deleted = await LLMUserMemoryDataSource.delete_user_memory(
                memory_id,
                ctx.guild_id,
                ctx.channel_id,
                actor.user_id,
            )
            return "본인 개인 메모리를 삭제했습니다." if deleted else "삭제할 수 있는 본인 개인 메모리를 찾지 못했습니다."

        deleted_user_memories = await LLMUserMemoryDataSource.delete_user(ctx.guild_id, ctx.channel_id, actor.user_id)
        return f"본인 개인 메모리 {deleted_user_memories}개를 삭제했습니다."
