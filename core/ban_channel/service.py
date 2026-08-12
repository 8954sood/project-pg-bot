from core.ban_channel.models import BanChannelConfig
from core.local import LocalCore


MAX_BAN_CHANNELS_PER_GUILD = 3


class BanChannelLimitReachedError(Exception):
    pass


class BanChannelService:
    def __init__(self, data_source=None) -> None:
        self.data_source = data_source or LocalCore.banChannelDataSource
        self.configs: dict[int, dict[int, BanChannelConfig]] = {}

    async def load_configs(self) -> None:
        configs = await self.data_source.get_all()
        self.configs = {}
        for config in configs:
            self.configs.setdefault(config.guild_id, {})[config.channel_id] = config

    def get_configs(self, guild_id: int) -> list[BanChannelConfig]:
        return list(self.configs.get(guild_id, {}).values())

    def get_config(
        self,
        guild_id: int,
        channel_id: int,
    ) -> BanChannelConfig | None:
        return self.configs.get(guild_id, {}).get(channel_id)

    def is_configured_channel(self, guild_id: int, channel_id: int) -> bool:
        return self.get_config(guild_id, channel_id) is not None

    async def set_config(
        self,
        *,
        guild_id: int,
        channel_id: int,
        warning_message_id: int | None,
    ) -> BanChannelConfig:
        guild_configs = self.configs.setdefault(guild_id, {})
        if (
            channel_id not in guild_configs
            and len(guild_configs) >= MAX_BAN_CHANNELS_PER_GUILD
        ):
            raise BanChannelLimitReachedError

        config = BanChannelConfig(
            guild_id=guild_id,
            channel_id=channel_id,
            warning_message_id=warning_message_id,
        )
        await self.data_source.upsert(config)
        guild_configs[channel_id] = config
        return config

    async def clear_config(
        self,
        guild_id: int,
        channel_id: int,
    ) -> BanChannelConfig | None:
        config = self.get_config(guild_id, channel_id)
        if config is None:
            return None

        await self.data_source.delete(guild_id, channel_id)
        guild_configs = self.configs[guild_id]
        guild_configs.pop(channel_id)
        if not guild_configs:
            self.configs.pop(guild_id)
        return config

    async def clear_all_configs(self, guild_id: int) -> list[BanChannelConfig]:
        configs = self.get_configs(guild_id)
        if not configs:
            return []

        await self.data_source.delete_all(guild_id)
        self.configs.pop(guild_id)
        return configs

    async def clear_channel(
        self,
        *,
        guild_id: int,
        channel_id: int,
    ) -> BanChannelConfig | None:
        return await self.clear_config(guild_id, channel_id)
