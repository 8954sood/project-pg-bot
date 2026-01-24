from core.local.tts import TTSDataSource
from core.local.user import UserDataSource
from core.local.voiceoption import VoiceOptionDataSource
from core.local.ttsengine import TTSEngineOptionDataSource, TTSEngineAllowDataSource


class LocalCore:

    userDataSource: UserDataSource = UserDataSource
    ttsDataSource: TTSDataSource = TTSDataSource
    voiceOptionDataSource: VoiceOptionDataSource = VoiceOptionDataSource
    ttsEngineOptionDataSource: TTSEngineOptionDataSource = TTSEngineOptionDataSource
    ttsEngineAllowDataSource: TTSEngineAllowDataSource = TTSEngineAllowDataSource


    @staticmethod
    async def init_tables():
        await LocalCore.userDataSource.init_table()
        await LocalCore.ttsDataSource.init_table()
        await LocalCore.voiceOptionDataSource.init_table()
        await LocalCore.ttsEngineOptionDataSource.init_table()
        await LocalCore.ttsEngineAllowDataSource.init_table()
