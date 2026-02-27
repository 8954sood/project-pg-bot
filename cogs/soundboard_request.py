import base64
import json
import os
import subprocess
import tempfile
import traceback
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
FORWARDED_MESSAGES_PATH = "./soundboard_forwarded_messages.json"
MAX_FORWARDED_MESSAGES = 1000
MAX_TRACEBACK_LENGTH = 1500
DISCORD_MESSAGE_LIMIT = 2000


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
        await self.cog.prompt_reject_reason(interaction, self.source_channel_id, self.source_message_id)


class RejectReasonModal(discord.ui.Modal, title="거절 사유 입력"):
    reason = discord.ui.TextInput(
        label="거절 사유",
        style=discord.TextStyle.paragraph,
        max_length=500,
        required=True,
    )

    def __init__(self, cog: "SoundboardRequest", source_channel_id: int, source_message_id: int):
        super().__init__()
        self.cog = cog
        self.source_channel_id = source_channel_id
        self.source_message_id = source_message_id

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.handle_reject_with_reason(
            interaction,
            self.source_channel_id,
            self.source_message_id,
            self.reason.value,
        )


class SoundboardRequest(commands.Cog):
    def __init__(self, bot: commands.Bot, forwarded_messages_path: str = FORWARDED_MESSAGES_PATH):
        self.bot = bot
        self.forwarded_messages_path = forwarded_messages_path
        self.forwarded_messages: OrderedDict[int, None] = OrderedDict()
        self._load_forwarded_messages()

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

    def _load_forwarded_messages(self):
        if not os.path.exists(self.forwarded_messages_path):
            return
        try:
            with open(self.forwarded_messages_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                return
            for message_id in data[-MAX_FORWARDED_MESSAGES:]:
                if isinstance(message_id, int):
                    self.forwarded_messages[message_id] = None
                    continue
                if isinstance(message_id, str) and message_id.isdigit():
                    self.forwarded_messages[int(message_id)] = None
        except (OSError, ValueError, TypeError):
            pass

    def _save_forwarded_messages(self):
        try:
            with open(self.forwarded_messages_path, "w", encoding="utf-8") as f:
                json.dump(list(self.forwarded_messages.keys()), f)
        except OSError:
            pass

    def _remember_forwarded_message(self, message_id: int):
        self.forwarded_messages[message_id] = None
        if len(self.forwarded_messages) > MAX_FORWARDED_MESSAGES:
            self.forwarded_messages.popitem(last=False)
        self._save_forwarded_messages()

    async def _notify_owner_error(self, context: str, exc: Exception):
        owner = self.bot.get_user(self.bot.owner_id) if self.bot.owner_id else None
        if owner is None:
            try:
                app_info = await self.bot.application_info()
                owner = app_info.owner
            except Exception:
                return
        if owner is None:
            return
        trace = traceback.format_exc()
        if len(trace) > MAX_TRACEBACK_LENGTH:
            separator_length = 5
            half = max(1, (MAX_TRACEBACK_LENGTH - separator_length) // 2)
            trace = f"{trace[:half]}\n...\n{trace[-half:]}"
        summary = f"{type(exc).__name__}: {exc}"
        prefix = f"[SoundboardRequest] {context}\n{summary}\n```"
        suffix = "```"
        max_trace_len = DISCORD_MESSAGE_LIMIT - len(prefix) - len(suffix)
        if max_trace_len < 0:
            max_trace_len = 0
        trace = trace[:max_trace_len]
        message = f"{prefix}{trace}{suffix}"
        try:
            await owner.send(message)
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
        self._remember_forwarded_message(message.id)

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
        try:
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
                return await interaction.response.send_message(
                    f"음성 파일은 {MAX_SOUND_DURATION_SECONDS:g}초 이하여야 합니다.",
                    ephemeral=True,
                )

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
        except Exception as exc:
            await self._notify_owner_error("handle_accept", exc)
            if not interaction.response.is_done():
                await interaction.response.send_message("처리 중 오류가 발생했습니다.", ephemeral=True)

    async def prompt_reject_reason(self, interaction: discord.Interaction, source_channel_id: int, source_message_id: int):
        try:
            if not self._is_admin(interaction.user):
                return await interaction.response.send_message("관리자만 처리할 수 있습니다.", ephemeral=True)
            await interaction.response.send_modal(RejectReasonModal(self, source_channel_id, source_message_id))
        except Exception as exc:
            await self._notify_owner_error("prompt_reject_reason", exc)
            if not interaction.response.is_done():
                await interaction.response.send_message("처리 중 오류가 발생했습니다.", ephemeral=True)

    async def handle_reject_with_reason(
        self,
        interaction: discord.Interaction,
        source_channel_id: int,
        source_message_id: int,
        reason: str,
    ):
        try:
            if not self._is_admin(interaction.user):
                return await interaction.response.send_message("관리자만 처리할 수 있습니다.", ephemeral=True)

            source_message = await self._fetch_source_message(source_channel_id, source_message_id)
            if source_message is not None:
                try:
                    dm_message = (
                        f"사운드보드 요청이 거절되었습니다.\n사유: {reason}\n요청: {source_message.content.strip()}"
                    )
                    if len(dm_message) > DISCORD_MESSAGE_LIMIT:
                        dm_message = dm_message[:DISCORD_MESSAGE_LIMIT - 3] + "..."
                    await source_message.author.send(dm_message)
                except discord.HTTPException:
                    pass

            embed = interaction.message.embeds[0] if interaction.message and interaction.message.embeds else None
            await interaction.response.edit_message(content=f"거절 처리되었습니다.\n사유: {reason}", embed=embed, view=None)
        except Exception as exc:
            await self._notify_owner_error("handle_reject_with_reason", exc)
            if not interaction.response.is_done():
                await interaction.response.send_message("처리 중 오류가 발생했습니다.", ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        try:
            if (
                message.author.bot or
                not isinstance(message.channel, discord.TextChannel) or
                message.guild is None or
                message.channel.id != REQUEST_CHANNEL_ID
            ):
                return

            if self._is_admin(message.author):
                return

            attachment = self._get_audio_attachment(message)
            if attachment is not None:
                duration = await self._get_audio_duration_seconds(attachment)
                if duration is not None and duration > MAX_SOUND_DURATION_SECONDS:
                    try:
                        await message.author.send(
                            f"요청 음성 파일은 {MAX_SOUND_DURATION_SECONDS:g}초 이하여야 하므로 메시지가 삭제되었습니다."
                        )
                    except discord.HTTPException:
                        pass
                    await self._delete_invalid_message(message)
                    return

            if not self._is_valid_request_message(message):
                return

            try:
                await message.add_reaction(THUMBS_UP_EMOJI)
            except discord.HTTPException:
                pass
        except Exception as exc:
            await self._notify_owner_error("on_message", exc)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        try:
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
        except Exception as exc:
            await self._notify_owner_error("on_raw_reaction_add", exc)


async def setup(bot: commands.Bot):
    await bot.add_cog(SoundboardRequest(bot))
