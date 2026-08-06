from typing import Optional

from core.ban_channel.models import BanChannelConfig
from core.local import LocalCore


class BanChannelService:
    def __init__(self, data_source=None) -> None:
        self.data_source = data_source or LocalCore.banChannelDataSource
        self.configs: dict[int, BanChannelConfig] = {}

    async def load_configs(self) -> None:
        configs = await self.data_source.get_all()
        self.configs = {config.guild_id: config for config in configs}

    def get_config(self, guild_id: int) -> Optional[BanChannelConfig]:
        return self.configs.get(guild_id)

    def is_configured_channel(self, guild_id: int, channel_id: int) -> bool:
        config = self.get_config(guild_id)
        return config is not None and config.channel_id == channel_id

    async def set_config(
        self,
        *,
        guild_id: int,
        channel_id: int,
        warning_message_id: Optional[int],
    ) -> BanChannelConfig:
        config = BanChannelConfig(
            guild_id=guild_id,
            channel_id=channel_id,
            warning_message_id=warning_message_id,
        )
        await self.data_source.upsert(config)
        self.configs[guild_id] = config
        return config

    async def clear_config(self, guild_id: int) -> Optional[BanChannelConfig]:
        config = self.configs.get(guild_id)
        await self.data_source.delete(guild_id)
        self.configs.pop(guild_id, None)
        return config

    async def clear_channel(
        self,
        *,
        guild_id: int,
        channel_id: int,
    ) -> Optional[BanChannelConfig]:
        config = self.get_config(guild_id)
        if config is None or config.channel_id != channel_id:
            return None
        return await self.clear_config(guild_id)
