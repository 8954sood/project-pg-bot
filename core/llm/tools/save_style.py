from core.llm.tools.base import SAVE_SCOPE_DESCRIPTION, LLMTool, ToolContext, register_tool
from core.local.llm import LLMServerStateDataSource, LLMSpeechStyleDataSource


@register_tool
class SaveStyleTool(LLMTool):
    name = "save_style"
    description = (
        "사용자가 봇의 말투/어조/응답 방식을 변경/적용/업데이트하라고 명시했을 때 호출한다. "
        "note에 봇이 따를 말투 지시를 짧게 정리한다(예: '용용체로 답한다')."
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
        scope = self.parse_scope(arguments)
        note = self.parse_note(arguments) or "사용자가 요청한 말투/응답 방식"
        actor = ctx.actor

        if scope == "server" and actor.is_admin:
            await LLMServerStateDataSource.upsert(
                ctx.guild_id,
                ctx.channel_id,
                active_style_directive=note,
                server_style_summary=f"현재 서버 응답 말투 지시를 우선한다: {note}",
            )
            return "서버/채널 전역 말투 설정을 저장했습니다."

        await LLMSpeechStyleDataSource.upsert(
            ctx.guild_id,
            ctx.channel_id,
            actor.user_id,
            note,
            user_name=actor.author_name,
            notes=f"봇은 {actor.author_name}에게 다음 말투/응답 지시를 적용한다: {note}",
        )
        return f"{actor.author_name} 개인 말투 설정을 저장했습니다."