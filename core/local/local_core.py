from core.local.tts import TTSDataSource
from core.local.user import UserDataSource


class LocalCore:

    userDataSource: UserDataSource = UserDataSource
    ttsDataSource: TTSDataSource = TTSDataSource