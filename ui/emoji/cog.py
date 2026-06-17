import os
import re

import discord
from discord.ext import commands

EMOJI_CHANNEL_ID = 1380134082469429390
EMOJI_LOG_CHANNEL_ID = 1454384548438868109


class EmojiRegister(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        await self.bot.process_commands(message)
        if (
            message.author.bot or
            isinstance(message.channel, discord.channel.DMChannel) or
            message.guild is None or
            EMOJI_CHANNEL_ID == 0 or
            message.channel.id != EMOJI_CHANNEL_ID
        ):
            return

        attachments = [a for a in message.attachments if self._is_image_attachment(a)]
        if not attachments:
            return

        available_slots = message.guild.emoji_limit - len(message.guild.emojis)
        if available_slots <= 0:
            await message.channel.send("이 서버의 이모지 슬롯이 가득 찼습니다.")
            return
        if len(attachments) > available_slots:
            await message.channel.send(f"이모지 슬롯이 부족합니다. {available_slots}개만 등록합니다.")

        next_num = self._next_emoji_number(message.guild)
        for attachment in attachments[:available_slots]:
            if attachment.width is None or attachment.height is None:
                await message.channel.send(
                    f"{attachment.filename}: 이미지 파일을 확인할 수 없습니다. 1:1 이미지 파일만 업로드해주세요."
                )
                continue

            if attachment.width != attachment.height:
                await message.channel.send(
                    f"{attachment.filename}: 이미지 비율이 1:1이 아닙니다. 1:1로 수정해서 다시 올려주세요."
                )
                continue

            emoji_name = f"emoji_{next_num}"
            next_num += 1
            try:
                image_bytes = await attachment.read()
                new_emoji = await message.guild.create_custom_emoji(
                    name=emoji_name,
                    image=image_bytes,
                    reason=f"Uploaded by {message.author} via emoji channel",
                )
            except discord.HTTPException as e:
                await message.channel.send(f"{attachment.filename}: 이모지 등록 중 오류가 발생했습니다: {e}")
                continue

            await self._log_emoji_create(message, attachment, new_emoji)
            await message.channel.send(f"등록 완료: {new_emoji} (`:{emoji_name}:`)")

    @staticmethod
    def _next_emoji_number(guild: discord.Guild) -> int:
        max_num = 0
        for emoji in guild.emojis:
            match = re.fullmatch(r"emoji_(\d+)", emoji.name)
            if match:
                max_num = max(max_num, int(match.group(1)))
        return max_num + 1

    @staticmethod
    def _is_image_attachment(attachment: discord.Attachment) -> bool:
        if attachment.content_type and attachment.content_type.startswith("image/"):
            return True
        filename = attachment.filename.lower()
        return filename.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))

    async def _log_emoji_create(
        self,
        message: discord.Message,
        attachment: discord.Attachment,
        new_emoji: discord.Emoji,
    ) -> None:
        if EMOJI_LOG_CHANNEL_ID == 0:
            return
        channel = message.guild.get_channel(EMOJI_LOG_CHANNEL_ID)
        if channel is None:
            return
        embed = discord.Embed(
            title="이모지 등록 로그",
            color=0x2ecc71,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="사용자",
            value=f"{message.author.mention} (`{message.author.id}`)",
            inline=False,
        )
        embed.add_field(
            name="이모지",
            value=f"{new_emoji} (`:{new_emoji.name}:`)",
            inline=False,
        )
        embed.add_field(
            name="이미지",
            value=f"[{attachment.filename}]({attachment.url})",
            inline=False,
        )
        embed.set_thumbnail(url=new_emoji.url)
        await channel.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(EmojiRegister(bot))
