from core.llm.tools.base import SAVE_SCOPE_DESCRIPTION, LLMTool, ToolContext, register_tool
from core.local.llm import LLMGlobalMemoryDataSource, LLMUserMemoryDataSource


@register_tool
class SaveMemoryTool(LLMTool):
    name = "save_memory"
    description = (
        "사용자가 장기 기억/선호/정보/규칙을 저장하라고 명시했을 때 호출한다. "
        "사용자 원문 그대로보다 봇이 저장할 핵심을 짧은 문장으로 note에 정리한다. "
        "사용자가 '기억해줘'처럼 대상을 명시하지 않아도 직전 대화 맥락에서 저장할 내용을 추론해 저장한다 "
        "(예: 직전에 오버워치를 칭찬했으면 '사용자는 오버워치를 좋아한다' 식으로 note 정리). "
        "맥락이 명확하면 확인 질문 없이 바로 이 툴을 호출하고, 정말 맥락이 전혀 없을 때만 툴 없이 되묻는다."
    )
    parameters = {
        "type": "object",
        "properties": {
            "note": {"type": "string", "description": "DB에 저장할 기억 내용"},
            "scope": {"type": "string", "enum": ["user", "server"], "description": SAVE_SCOPE_DESCRIPTION},
        },
        "required": ["note", "scope"],
    }

    async def run(self, arguments: dict, ctx: ToolContext) -> str:
        scope = self.parse_scope(arguments)
        note = self.parse_note(arguments) or "사용자가 저장을 요청한 정보"
        actor = ctx.actor

        if scope == "server" and actor.is_admin:
            memory_id = await LLMGlobalMemoryDataSource.add(
                ctx.guild_id, ctx.channel_id, "server_memory", note, 1, actor.user_id
            )
            return f"서버/채널 전역 기억을 저장했습니다. id={memory_id}"

        memory_id = await LLMUserMemoryDataSource.add(
            ctx.guild_id, ctx.channel_id, actor.user_id, note, user_name=actor.author_name
        )
        return f"{actor.author_name} 개인 기억을 저장했습니다. id={memory_id}"