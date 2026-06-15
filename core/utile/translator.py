import discord
from discord import app_commands


class KoreanTranslator(app_commands.Translator):
    translations = {
        "sleep_timer.name": "수면타이머",
        "sleep_timer.description": "지정한 시간에 음성 채널에서 자동으로 나갑니다.",
    }

    async def translate(
        self,
        string: app_commands.locale_str,
        locale: discord.Locale,
        context: app_commands.TranslationContext,
    ):
        if locale is not discord.Locale.korean:
            return None
        key = getattr(string, "extras", {}).get("key")
        return self.translations.get(key)
