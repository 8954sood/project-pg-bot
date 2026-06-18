from core.llm.tools.base import SAVE_SCOPE_DESCRIPTION, LLMTool, ToolContext, register_tool
from core.local.llm import LLMUserMemoryDataSource


@register_tool
class SaveMemoryTool(LLMTool):
    name = "save_memory"
    description = (
        "사용자가 장기 기억/선호/정보/규칙/호칭/말투/응답 포맷을 본인 개인 메모리로 저장하라고 명시했을 때 호출한다. "
        "사용자 원문 그대로보다 봇이 저장할 핵심을 짧은 문장으로 note에 정리한다. "
        "사용자가 '기억해줘'처럼 대상을 명시하지 않아도 직전 대화 맥락에서 저장할 내용을 추론해 저장한다 "
        "(예: 직전에 오버워치를 칭찬했으면 '사용자는 오버워치를 좋아한다' 식으로 note 정리). "
        "맥락이 명확하면 확인 질문 없이 바로 이 툴을 호출하고, 정말 맥락이 전혀 없을 때만 툴 없이 되묻는다. "
        "이 툴은 서버 메모리나 타인의 메모리를 절대 수정하지 않는다."
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
        note = self.parse_note(arguments) or "사용자가 저장을 요청한 정보"
        actor = ctx.actor

        memory_id = await LLMUserMemoryDataSource.add(
            ctx.guild_id, ctx.channel_id, actor.user_id, note, user_name=actor.author_name
        )
        return f"{actor.author_name} 개인 기억을 저장했습니다. id={memory_id}"
