from core.local.tts import TTSDataSource
from core.local.user import UserDataSource
from core.local.voiceoption import VoiceOptionDataSource
from core.local.ttsengine import TTSEngineOptionDataSource, TTSEngineAllowDataSource
from core.local.sleep_timer import SleepTimerDataSource


class LocalCore:

    userDataSource: UserDataSource = UserDataSource
    ttsDataSource: TTSDataSource = TTSDataSource
    voiceOptionDataSource: VoiceOptionDataSource = VoiceOptionDataSource
    ttsEngineOptionDataSource: TTSEngineOptionDataSource = TTSEngineOptionDataSource
    ttsEngineAllowDataSource: TTSEngineAllowDataSource = TTSEngineAllowDataSource
    sleepTimerDataSource: SleepTimerDataSource = SleepTimerDataSource


    @staticmethod
    async def init_tables():
        await LocalCore.userDataSource.init_table()
        await LocalCore.ttsDataSource.init_table()
        await LocalCore.voiceOptionDataSource.init_table()
        await LocalCore.ttsEngineOptionDataSource.init_table()
        await LocalCore.ttsEngineAllowDataSource.init_table()
        await LocalCore.sleepTimerDataSource.init_table()
