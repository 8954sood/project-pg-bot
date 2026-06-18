from core.llm.tools.base import SAVE_SCOPE_DESCRIPTION, LLMTool, ToolContext, register_tool
from core.local.llm import (
    LLMGlobalMemoryDataSource,
    LLMServerStateDataSource,
    LLMSpeechStyleDataSource,
    LLMUserMemoryDataSource,
)


@register_tool
class ClearMemoryTool(LLMTool):
    name = "clear_memory"
    description = (
        "사용자가 기억/말투를 삭제/초기화/비우/리셋하라고 명시했을 때 호출한다. "
        "저장된 기억과 말투 설정을 함께 지운다."
    )
    parameters = {
        "type": "object",
        "properties": {
            "scope": {"type": "string", "enum": ["user", "server"], "description": SAVE_SCOPE_DESCRIPTION},
        },
        "required": ["scope"],
    }

    async def run(self, arguments: dict, ctx: ToolContext) -> str:
        scope = self.parse_scope(arguments)
        actor = ctx.actor

        if scope == "server" and actor.is_admin:
            deleted_memories = await LLMGlobalMemoryDataSource.delete_scope(ctx.guild_id, ctx.channel_id)
            await LLMServerStateDataSource.reset_style_and_notes(ctx.guild_id, ctx.channel_id)
            return f"서버/채널 전역 기억 {deleted_memories}개와 서버 말투 설정을 삭제했습니다."

        deleted_user_memories = await LLMUserMemoryDataSource.delete_user(
            ctx.guild_id, ctx.channel_id, actor.user_id
        )
        deleted_user_styles = await LLMSpeechStyleDataSource.delete_user(
            ctx.guild_id, ctx.channel_id, actor.user_id
        )
        if scope == "server":
            return (
                "서버 전역 기억/말투를 삭제하려면 Discord 관리자 권한이 필요합니다. "
                f"대신 본인 개인 기억 {deleted_user_memories}개와 개인 말투 설정 {deleted_user_styles}개를 삭제했습니다."
            )
        return f"본인 개인 기억 {deleted_user_memories}개와 개인 말투 설정 {deleted_user_styles}개를 삭제했습니다."