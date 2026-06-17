import logging
from typing import Optional

import discord


logger = logging.getLogger(__name__)


def status_view(title: str, description: str) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(f"## {title}\n{description}"),
            accent_color=discord.Color.blurple(),
        )
    )
    return view


class OwnedLayoutView(discord.ui.LayoutView):
    def __init__(self, owner_id: int, *, timeout: Optional[float] = 180.0):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            view=status_view("사용할 수 없습니다", "이 화면을 연 사용자만 조작할 수 있습니다."),
            ephemeral=True,
        )
        return False

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item,
    ) -> None:
        logger.error(
            "Discord UI failed",
            exc_info=(type(error), error, error.__traceback__),
        )
        if interaction.response.is_done():
            await interaction.followup.send(
                view=status_view("오류", "요청 처리 중 오류가 발생했습니다."),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                view=status_view("오류", "요청 처리 중 오류가 발생했습니다."),
                ephemeral=True,
            )

