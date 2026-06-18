from core.llm.tools.base import SAVE_SCOPE_DESCRIPTION, LLMTool, ToolContext, register_tool
from core.local.llm import LLMUserMemoryDataSource


@register_tool
class ClearMemoryTool(LLMTool):
    name = "clear_memory"
    description = (
        "사용자가 본인 개인 메모리/말투/호칭/응답 포맷을 삭제/초기화/비우/리셋하라고 명시했을 때 호출한다. "
        "memory_id가 있으면 해당 id의 본인 개인 메모리만 삭제하고, 없으면 본인 개인 메모리를 모두 삭제한다. "
        "서버 메모리와 타인의 메모리는 절대 삭제하지 않는다."
    )
    parameters = {
        "type": "object",
        "properties": {
            "scope": {"type": "string", "enum": ["user", "server"], "description": SAVE_SCOPE_DESCRIPTION},
            "memory_id": {"type": "integer", "description": "삭제할 본인 개인 메모리 id. 생략하면 본인 개인 메모리 전체 삭제."},
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
