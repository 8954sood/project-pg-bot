from core.local.tts import TTSDataSource
from core.local.user import UserDataSource
from core.local.voiceoption import VoiceOptionDataSource
from core.local.ttsengine import TTSEngineOptionDataSource, TTSEngineAllowDataSource
from core.local.sleep_timer import SleepTimerDataSource
from core.local.llm import (
    LLMConsentDataSource,
    LLMGlobalMemoryDataSource,
    LLMRecentMessageDataSource,
    LLMServerStateDataSource,
    LLMUserMemoryDataSource,
)


class LocalCore:

    userDataSource: UserDataSource = UserDataSource
    ttsDataSource: TTSDataSource = TTSDataSource
    voiceOptionDataSource: VoiceOptionDataSource = VoiceOptionDataSource
    ttsEngineOptionDataSource: TTSEngineOptionDataSource = TTSEngineOptionDataSource
    ttsEngineAllowDataSource: TTSEngineAllowDataSource = TTSEngineAllowDataSource
    sleepTimerDataSource: SleepTimerDataSource = SleepTimerDataSource
    llmConsentDataSource: LLMConsentDataSource = LLMConsentDataSource
    llmGlobalMemoryDataSource: LLMGlobalMemoryDataSource = LLMGlobalMemoryDataSource
    llmUserMemoryDataSource: LLMUserMemoryDataSource = LLMUserMemoryDataSource
    llmRecentMessageDataSource: LLMRecentMessageDataSource = LLMRecentMessageDataSource
    llmServerStateDataSource: LLMServerStateDataSource = LLMServerStateDataSource


    @staticmethod
    async def init_tables():
        await LocalCore.userDataSource.init_table()
        await LocalCore.ttsDataSource.init_table()
        await LocalCore.voiceOptionDataSource.init_table()
        await LocalCore.ttsEngineOptionDataSource.init_table()
        await LocalCore.ttsEngineAllowDataSource.init_table()
        await LocalCore.sleepTimerDataSource.init_table()
        await LocalCore.llmConsentDataSource.init_table()
        await LocalCore.llmGlobalMemoryDataSource.init_table()
        await LocalCore.llmUserMemoryDataSource.init_table()
        await LocalCore.llmRecentMessageDataSource.init_table()
        await LocalCore.llmServerStateDataSource.init_table()
