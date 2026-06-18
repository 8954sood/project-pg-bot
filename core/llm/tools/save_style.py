from core.llm.tools.base import SAVE_SCOPE_DESCRIPTION, LLMTool, ToolContext
from core.local.llm import LLMUserMemoryDataSource


class SaveStyleTool(LLMTool):
    name = "save_style"
    description = (
        "Deprecated compatibility tool. 말투/어조/응답 방식 변경 요청을 해당 발화자 본인의 개인 메모리에 저장한다. "
        "서버 말투, 서버 메모리, 타인의 메모리는 절대 수정하지 않는다."
    )
    parameters = {
        "type": "object",
        "properties": {
            "note": {"type": "string", "description": "봇이 따를 말투/응답 지시"},
            "scope": {"type": "string", "enum": ["user", "server"], "description": SAVE_SCOPE_DESCRIPTION},
        },
        "required": ["note", "scope"],
    }

    async def run(self, arguments: dict, ctx: ToolContext) -> str:
        note = self.parse_note(arguments) or "사용자가 요청한 말투/응답 방식"
        actor = ctx.actor

        memory_id = await LLMUserMemoryDataSource.add(
            ctx.guild_id, ctx.channel_id, actor.user_id, f"개인 말투/응답 설정: {note}",
            user_name=actor.author_name,
        )
        return f"{actor.author_name} 개인 메모리에 말투 설정을 저장했습니다. id={memory_id}"
