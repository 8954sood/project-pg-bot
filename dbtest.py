import asyncio

from core.local.local_core import LocalCore


async def main():

    user = await LocalCore.userDataSource.get_user_by_user_id(464712715487805442)
    print(user)
    if user is not None:
        print(user.author)


asyncio.run(main())