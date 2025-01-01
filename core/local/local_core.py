from core.local.tts import TTSDataSource
from core.local.user import UserDataSource
from core.local.voiceoption import VoiceOptionDataSource


class LocalCore:

    userDataSource: UserDataSource = UserDataSource
    ttsDataSource: TTSDataSource = TTSDataSource
    voiceOptionDataSource: VoiceOptionDataSource = VoiceOptionDataSource


    @staticmethod
    async def init_tables():
        await LocalCore.userDataSource.init_table()
        await LocalCore.ttsDataSource.init_table()
        await LocalCore.voiceOptionDataSource.init_table()