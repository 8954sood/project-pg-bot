import asyncio

import gtts.lang

from core.local.local_core import LocalCore


async def main():

    # user = await LocalCore.userDataSource.get_user_by_user_id(464712715487805442)
    # print(user)
    # if user is not None:
    #     print(user.author)
    await LocalCore.voiceOptionDataSource.insert(123, "ko-kr")
    print(gtts.lang.tts_langs())


asyncio.run(main())