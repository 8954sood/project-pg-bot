from discord import Interaction, app_commands
from discord.ext.commands import CommandError


def is_admin():
    async def predicate(ctx: Interaction):
        if not ctx.user.guild_permissions.administrator:
            raise CommandError("이 명령어는 관리자만 사용할 수 있습니다.")
        return True
    return app_commands.check(predicate)