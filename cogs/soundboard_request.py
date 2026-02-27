import base64
import os
import subprocess
import tempfile
from collections import OrderedDict
from typing import Optional

import discord
from discord.ext import commands
from discord.http import Route

REQUEST_CHANNEL_ID = 1476845289674510541
ADMIN_CHANNEL_ID = 1074296127723151360
THUMBS_UP_EMOJI = "👍"
MAX_TITLE_LENGTH = 32
MAX_SOUND_DURATION_SECONDS = 5.0


class SoundboardReviewView(discord.ui.View):
    def __init__(self, cog: "SoundboardRequest", source_channel_id: int, source_message_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.source_channel_id = source_channel_id
        self.source_message_id = source_message_id

    @discord.ui.button(label="수락", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self.cog.handle_accept(interaction, self.source_channel_id, self.source_message_id)

    @discord.ui.button(label="거절", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self.cog.handle_reject(interaction)


class SoundboardRequest(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.forwarded_messages: OrderedDict[int, None] = OrderedDict()

    @staticmethod
    def _is_admin(member: discord.abc.User) -> bool:
        return isinstance(member, discord.Member) and member.guild_permissions.administrator

    @staticmethod
    def _is_audio_attachment(attachment: discord.Attachment) -> bool:
        content_type = attachment.content_type or ""
        if content_type.startswith("audio/"):
            return True
        name = (attachment.filename or "").lower()
        return name.endswith((".mp3", ".wav", ".ogg", ".m4a", ".webm"))

    def _get_audio_attachment(self, message: discord.Message) -> Optional[discord.Attachment]:
        for attachment in message.attachments:
            if self._is_audio_attachment(attachment):
                return attachment
        return None

    def _is_valid_request_message(self, message: discord.Message) -> bool:
        content = (message.content or "").strip()
        return 0 < len(content) <= MAX_TITLE_LENGTH and self._get_audio_attachment(message) is not None

    async def _delete_invalid_message(self, message: discord.Message):
        try:
            await message.delete()
        except discord.HTTPException:
            pass

    async def _count_human_thumbs_up(self, message: discord.Message) -> int:
        for reaction in message.reactions:
            if str(reaction.emoji) != THUMBS_UP_EMOJI:
                continue
            users = [user async for user in reaction.users()]
            return sum(1 for user in users if not user.bot)
        return 0

    async def _forward_for_review(self, message: discord.Message):
        if message.id in self.forwarded_messages:
            return
        admin_channel = message.guild.get_channel(ADMIN_CHANNEL_ID)
        if not isinstance(admin_channel, discord.TextChannel):
            return

        attachment = self._get_audio_attachment(message)
        if attachment is None:
            return

        embed = discord.Embed(
            title="사운드보드 요청",
            color=0x3498DB,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="요청자", value=f"{message.author.mention} (`{message.author.id}`)", inline=False)
        embed.add_field(name="요청 메시지", value=message.content.strip(), inline=False)
        embed.add_field(name="음성 파일", value=f"[{attachment.filename}]({attachment.url})", inline=False)
        embed.set_footer(text=f"source_message_id={message.id}")

        view = SoundboardReviewView(self, message.channel.id, message.id)
        await admin_channel.send(embed=embed, view=view)
        self.forwarded_messages[message.id] = None
        if len(self.forwarded_messages) > 1000:
            self.forwarded_messages.popitem(last=False)

    async def _fetch_source_message(self, channel_id: int, message_id: int) -> Optional[discord.Message]:
        channel = self.bot.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return None
        try:
            return await channel.fetch_message(message_id)
        except discord.HTTPException:
            return None

    async def _get_audio_duration_seconds(self, attachment: discord.Attachment) -> Optional[float]:
        suffix = os.path.splitext(attachment.filename or "")[1]
        fd, path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        try:
            data = await attachment.read()
            with open(path, "wb") as f:
                f.write(data)
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    path,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                return None
            return float(result.stdout.strip())
        except (ValueError, OSError):
            return None
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    async def _create_soundboard_sound(
        self,
        guild: discord.Guild,
        *,
        name: str,
        attachment: discord.Attachment,
        reason: str,
    ):
        audio_bytes = await attachment.read()
        content_type = attachment.content_type or "audio/mpeg"
        b64 = base64.b64encode(audio_bytes).decode("ascii")
        sound = f"data:{content_type};base64,{b64}"

        await self.bot.http.request(
            Route("POST", "/guilds/{guild_id}/soundboard-sounds", guild_id=guild.id),
            json={"name": name, "sound": sound},
            reason=reason,
        )

    async def handle_accept(self, interaction: discord.Interaction, source_channel_id: int, source_message_id: int):
        if not self._is_admin(interaction.user):
            return await interaction.response.send_message("관리자만 처리할 수 있습니다.", ephemeral=True)
        if interaction.guild is None:
            return await interaction.response.send_message("길드에서만 처리할 수 있습니다.", ephemeral=True)

        source_message = await self._fetch_source_message(source_channel_id, source_message_id)
        if source_message is None:
            return await interaction.response.send_message("원본 메시지를 찾을 수 없습니다.", ephemeral=True)

        if not self._is_valid_request_message(source_message):
            return await interaction.response.send_message("요청 형식이 올바르지 않습니다.", ephemeral=True)

        attachment = self._get_audio_attachment(source_message)
        if attachment is None:
            return await interaction.response.send_message("음성 파일을 찾을 수 없습니다.", ephemeral=True)

        duration = await self._get_audio_duration_seconds(attachment)
        if duration is None:
            return await interaction.response.send_message("음성 길이를 확인할 수 없습니다.", ephemeral=True)
        if duration > MAX_SOUND_DURATION_SECONDS:
            return await interaction.response.send_message("음성 파일은 5초 이하여야 합니다.", ephemeral=True)

        try:
            await self._create_soundboard_sound(
                interaction.guild,
                name=source_message.content.strip(),
                attachment=attachment,
                reason=f"Approved by {interaction.user}",
            )
        except discord.HTTPException as exc:
            return await interaction.response.send_message(f"사운드보드 업로드 실패: {exc}", ephemeral=True)

        embed = interaction.message.embeds[0] if interaction.message and interaction.message.embeds else None
        await interaction.response.edit_message(content="수락 처리되었습니다.", embed=embed, view=None)

    async def handle_reject(self, interaction: discord.Interaction):
        if not self._is_admin(interaction.user):
            return await interaction.response.send_message("관리자만 처리할 수 있습니다.", ephemeral=True)
        embed = interaction.message.embeds[0] if interaction.message and interaction.message.embeds else None
        await interaction.response.edit_message(content="거절 처리되었습니다.", embed=embed, view=None)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if (
            message.author.bot or
            not isinstance(message.channel, discord.TextChannel) or
            message.guild is None or
            message.channel.id != REQUEST_CHANNEL_ID
        ):
            return

        if self._is_admin(message.author):
            return

        if not self._is_valid_request_message(message):
            await self._delete_invalid_message(message)
            return

        try:
            await message.add_reaction(THUMBS_UP_EMOJI)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.channel_id != REQUEST_CHANNEL_ID:
            return

        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        if guild is None:
            return

        member = payload.member or guild.get_member(payload.user_id)
        if member is None:
            try:
                member = await guild.fetch_member(payload.user_id)
            except discord.HTTPException:
                return

        if member.bot:
            return

        channel = guild.get_channel(payload.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.HTTPException:
            return

        emoji = str(payload.emoji)
        if emoji != THUMBS_UP_EMOJI and not self._is_admin(member):
            try:
                await message.remove_reaction(payload.emoji, member)
            except discord.HTTPException:
                pass
            return

        if emoji != THUMBS_UP_EMOJI:
            return

        if not self._is_valid_request_message(message):
            return

        if await self._count_human_thumbs_up(message) >= 6:
            await self._forward_for_review(message)


async def setup(bot: commands.Bot):
    await bot.add_cog(SoundboardRequest(bot))
