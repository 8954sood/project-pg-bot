import asyncio
import logging
from typing import Dict, Optional, Set, Tuple

import discord
from discord import app_commands, Interaction, VoiceState, TextChannel, Member
from discord.ext import commands

from core.tts.engine_selector import TTSEngineSelector
from core.tts.models import TTSQueueModel, VoiceModel
from core.tts.reference_voice import MAX_PACKAGE_BYTES
from core.tts.playback import TTSPlayback
from core.tts.queue import enqueue_tts
from core.tts.service import TTSService, create_tts_source_with_dependencies
from core.tts.text_normalizer import build_tts_text, normalize_tts_text
from core.local.ttsengine.dto import TTSEngineOption
from core.utile import is_admin

logger = logging.getLogger(__name__)


class TTS(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.service = TTSService(bot)
        self.queue: Dict[int, VoiceModel] = self.service.queue
        self.play_locks: Dict[int, asyncio.Lock] = self.service.play_locks
        # key: GuildId, value: MessageChannelId
        self.messageChannel: Dict[int, int] = self.service.message_channels
        # key: GuildId, value: MessageChannelId
        self.defaultChannel: Dict[int, int] = self.service.default_channels
        # Key: DmChannelID, value: GuildId
        self.dmChannel: Dict[int, int] = self.service.dm_channels
        self.voice_option: Dict[int, str] = self.service.voice_option
        self.tts_engine_option: Dict[int, TTSEngineOption] = self.service.tts_engine_option
        self.tts_engine_allow: Set[int] = self.service.tts_engine_allow
        self.gtts_engine = self.service.gtts_engine
        self.ai_engine = self.service.ai_engine
        self.max_queue_size = self.service.max_queue_size
        self.max_text_length = self.service.max_text_length
        self.gtts_timeout = self.service.gtts_timeout

        asyncio.run_coroutine_threadsafe(self.load_local_default_channel(), self.bot.loop)
        asyncio.run_coroutine_threadsafe(self.load_local_voice_option(), self.bot.loop)
        asyncio.run_coroutine_threadsafe(self.load_local_tts_engine_option(), self.bot.loop)
        asyncio.run_coroutine_threadsafe(self.load_local_tts_engine_allow(), self.bot.loop)


    def _playback(self) -> TTSPlayback:
        if hasattr(self, "service"):
            self.service.playback.create_source = self._create_tts_source
            return self.service.playback
        return TTSPlayback(
            queue=self.queue,
            play_locks=self.play_locks,
            bot_loop=self.bot.loop,
            create_source=self._create_tts_source,
        )


    async def load_local_default_channel(self):
        await self.service.load_local_default_channel()

    async def load_local_voice_option(self):
        await self.service.load_local_voice_option()

    async def load_local_tts_engine_option(self):
        await self.service.load_local_tts_engine_option()

    async def load_local_tts_engine_allow(self):
        await self.service.load_local_tts_engine_allow()
        self.tts_engine_allow = self.service.tts_engine_allow

    async def clear_guild_queue(self, guild_id: int, *, reason: str = "unknown"):
        if hasattr(self, "service"):
            await self.service.clear_guild_queue(guild_id, reason=reason)
            return
        await self._playback().clear_guild_queue(
            guild_id,
            reason=reason,
            message_channels=self.messageChannel,
            dm_channels=self.dmChannel,
        )

    def _discard_play_lock(self, guild_id: int) -> None:
        self._playback().discard_play_lock(guild_id)

    def _get_play_lock(self, guild_id: int) -> asyncio.Lock:
        return self._playback().get_play_lock(guild_id)

    def _ensure_guild_state(self, guild: discord.Guild) -> Optional[VoiceModel]:
        if hasattr(self, "service"):
            return self.service.ensure_guild_state(guild)
        existing = self.queue.get(guild.id)
        if existing and existing.get("vc") and existing["vc"].is_connected():
            return existing
        vc = guild.voice_client
        if vc is None:
            return None
        self.queue[guild.id] = {
            "guild_id": guild.id,
            "voice_channel_id": vc.channel.id if vc.channel else 0,
            "tts_queue": [],
            "vc": vc,
        }
        return self.queue[guild.id]

    def _get_user_engine(self, user_id: int) -> tuple:
        selection = TTSEngineSelector(self.tts_engine_option, self.tts_engine_allow).get_user_engine(user_id)
        return selection.engine, selection.model_name

    def _is_engine_change_allowed(self, user_id: int) -> bool:
        return TTSEngineSelector(self.tts_engine_option, self.tts_engine_allow).is_engine_change_allowed(user_id)

    async def _create_tts_source(
        self,
        *,
        guild_id: int,
        queue_item: TTSQueueModel,
    ) -> Tuple[discord.AudioSource, str]:
        if hasattr(self, "service"):
            self.service.gtts_engine = self.gtts_engine
            self.service.ai_engine = self.ai_engine
            self.service.gtts_timeout = self.gtts_timeout
            return await self.service.create_tts_source(
                guild_id=guild_id,
                queue_item=queue_item,
            )

        return await create_tts_source_with_dependencies(
            guild_id=guild_id,
            queue_item=queue_item,
            engine_selector=TTSEngineSelector(self.tts_engine_option, self.tts_engine_allow),
            ai_engine=self.ai_engine,
            gtts_engine=self.gtts_engine,
            gtts_timeout=self.gtts_timeout,
        )

    def _make_after_callback(self, guild_id: int):
        if not hasattr(self, "service"):
            def after(error: Optional[Exception]):
                if error:
                    logger.error(
                        "TTS playback callback error",
                        exc_info=(type(error), error, error.__traceback__),
                        extra={"guild_id": guild_id},
                    )
                else:
                    logger.info("TTS playback completed", extra={"guild_id": guild_id})

                try:
                    future = asyncio.run_coroutine_threadsafe(
                        self.safe_play_tts(guild_id),
                        self.bot.loop,
                    )
                except Exception:
                    logger.exception(
                        "Failed to schedule next TTS playback",
                        extra={"guild_id": guild_id},
                    )
                    return

                def done_callback(done_future):
                    try:
                        done_future.result()
                    except Exception:
                        logger.exception(
                            "safe_play_tts failed from playback callback",
                            extra={"guild_id": guild_id},
                        )

                future.add_done_callback(done_callback)

            return after
        return self._playback().make_after_callback(guild_id)

    async def _enqueue_tts(
        self,
        *,
        guild_id: int,
        voice_model: VoiceModel,
        text: str,
        user_id: int,
        channel_id: int,
    ) -> bool:
        if hasattr(self, "service"):
            self.service.max_queue_size = self.max_queue_size
            self.service.max_text_length = self.max_text_length
            return await self.service.enqueue(
                guild_id=guild_id,
                voice_model=voice_model,
                text=text,
                user_id=user_id,
                channel_id=channel_id,
            )
        return enqueue_tts(
            guild_id=guild_id,
            voice_model=voice_model,
            text=text,
            user_id=user_id,
            channel_id=channel_id,
            max_queue_size=self.max_queue_size,
            max_text_length=self.max_text_length,
        )

    @app_commands.command()
    @is_admin()
    async def set_default_channel(self, ctx: Interaction, channel: TextChannel):
        await self.service.set_default_channel(ctx.guild.id, channel.id)
        await ctx.response.send_message(f"TTS 수신 채널이 {channel.mention}로 설정되었습니다.", ephemeral=True)

    @app_commands.command()
    async def join(self, ctx: Interaction):
        result = await self.service.join(
            guild=ctx.guild,
            user=ctx.user,
            text_channel_id=ctx.channel.id,
        )
        await ctx.response.send_message(result.message, ephemeral=result.ephemeral)

    async def _wait_for_voice_client_release(
        self,
        guild: discord.Guild,
        previous_vc: discord.VoiceClient,
        *,
        timeout: float = 2.0,
    ) -> None:
        await self._playback().wait_for_voice_client_release(
            guild,
            previous_vc,
            timeout=timeout,
        )

    @app_commands.command()
    @app_commands.choices(lang=[
        app_commands.Choice(name='한국어', value="ko"),
        app_commands.Choice(name='영어', value="en"),
        app_commands.Choice(name='일본어', value="ja"),
        app_commands.Choice(name='스페인어', value="es"),
        app_commands.Choice(name='프랑스어', value="fr"),
        app_commands.Choice(name='러시아어', value="ru"),

    ])
    async def voice_option(self, ctx: Interaction, lang: app_commands.Choice[str]):
        await self.service.set_voice_option(ctx.user.id, lang.value)
        await ctx.response.send_message(f"TTS의 음성 설정이 {lang.name}로 변경되었어요.")

    @app_commands.command(name="tts_engine")
    @app_commands.choices(engine=[
        app_commands.Choice(name="gtts", value="gtts"),
        app_commands.Choice(name="ai", value="ai"),
    ])
    async def tts_engine(self, ctx: Interaction, engine: app_commands.Choice[str], model_name: Optional[str] = None):
        if engine.value == "ai" and not self._is_engine_change_allowed(ctx.user.id):
            return await ctx.response.send_message("You are not allowed to change TTS engine.", ephemeral=True)

        if engine.value == "ai" and not model_name:
            return await ctx.response.send_message("AI engine requires model_name.", ephemeral=True)

        await self.service.set_tts_engine_option(
            user_id=ctx.user.id,
            engine=engine.value,
            model_name=model_name,
        )
        await ctx.response.send_message("TTS engine updated.", ephemeral=True)

    @app_commands.command(name="엔진변경", description="TTS 엔진을 변경합니다")
    @app_commands.choices(엔진=[
        app_commands.Choice(name="구글 TTS", value="gtts"),
        app_commands.Choice(name="GPT-SoVITS", value="sovits"),
    ])
    async def change_engine(self, ctx: Interaction, 엔진: app_commands.Choice[str], 음성아이디: Optional[str] = None):
        if 엔진.value == "sovits":
            if not 음성아이디:
                return await ctx.response.send_message(
                    "GPT-SoVITS를 사용하려면 `/보이스목록`에서 음성 ID를 확인해 입력해주세요.", ephemeral=True)
            if not self.service.sovits.configured():
                return await ctx.response.send_message('GPT-SoVITS 서버가 설정되지 않았습니다.', ephemeral=True)
            voices = await self.service.sovits.visible(ctx.user.id)
            if not any(voice['style_id'] == 음성아이디 for voice in voices):
                return await ctx.response.send_message('사용 가능한 음성 ID가 아닙니다. `/보이스목록`을 확인해주세요.', ephemeral=True)
        await self.service.set_tts_engine_option(user_id=ctx.user.id, engine=엔진.value, model_name=음성아이디)
        name = "구글 TTS" if 엔진.value == "gtts" else "GPT-SoVITS"
        await ctx.response.send_message(f'TTS 엔진을 {name}(으)로 변경했습니다.', ephemeral=True)

    @app_commands.command(name="보이스안내", description="Voice Studio에서 참조 음성을 만드는 방법을 확인합니다")
    async def voice_guide(self, ctx: Interaction):
        await ctx.response.send_message(
            f"[Voice Studio 열기]({self.service.voice_studio_url})\n"
            "1. 한국어·영어·일본어 중 녹음 언어와 대사를 선택하세요. 직접 대사를 입력해도 됩니다.\n"
            "2. 자연스럽게 읽고 잡음 없는 3~10초 구간을 선택하세요.\n"
            "3. 실제 녹음 내용과 대사가 일치하는지 확인한 뒤 `.pgvoice`로 저장하세요.\n"
            "4. `/보이스등록`에 이름과 파일을 첨부하세요. 대사와 언어는 파일에서 자동으로 읽습니다.\n"
            "개인 음성은 한 개이며 처음에는 비공개입니다. 본인에게 사용 권한이 있는 음성을 등록해주세요.",
            ephemeral=True,
        )

    async def _register_voice_package(self, ctx, name, audio, *, sample=False):
        await ctx.response.defer(ephemeral=True, thinking=True)
        try:
            if not audio.filename.lower().endswith('.pgvoice') or not 1 <= audio.size <= MAX_PACKAGE_BYTES:
                raise ValueError('Voice Studio에서 저장한 700,000바이트 이하 .pgvoice 파일을 첨부해주세요.')
            style_id = await self.service.sovits.register_package(
                ctx.user.id, name, await audio.read(), filename=audio.filename, sample=sample,
            )
            label = '공개 샘플' if sample else '음성'
            await ctx.followup.send(
                f'{label}을 등록했습니다. ID: `{style_id}`\n`/보이스선택`으로 사용하세요.', ephemeral=True,
            )
        except ValueError as exc:
            await ctx.followup.send(f'등록하지 못했습니다: {exc}', ephemeral=True)
        except Exception:
            logger.exception('Voice package registration failed')
            await ctx.followup.send('음성 서버에 등록하지 못했습니다. 잠시 후 다시 시도해주세요.', ephemeral=True)

    @app_commands.command(name="보이스등록", description="Voice Studio의 .pgvoice 파일로 내 음성을 등록합니다")
    @app_commands.rename(name="이름", audio="파일")
    async def voice_register(self, ctx: Interaction, name: str, audio: discord.Attachment):
        await self._register_voice_package(ctx, name, audio)

    @app_commands.command(name="보이스목록", description="사용 가능한 음성 목록을 확인합니다")
    async def voice_list(self, ctx: Interaction):
        voices = await self.service.sovits.visible(ctx.user.id)
        lines = [f"{v['name']} (`{v['style_id']}`) · {'공개' if v['public'] else '비공개'}" for v in voices]
        message = '\n'.join(lines) if lines else '사용 가능한 음성이 없습니다.'
        await ctx.response.send_message(message[:1900], ephemeral=True)

    @app_commands.command(name="보이스선택", description="사용할 GPT-SoVITS 음성을 선택합니다")
    async def voice_select(self, ctx: Interaction, style_id: str):
        voices = await self.service.sovits.visible(ctx.user.id)
        if not any(v['style_id'] == style_id for v in voices):
            return await ctx.response.send_message('사용 가능한 음성 ID가 아닙니다. `/보이스목록`을 확인해주세요.', ephemeral=True)
        if not self.service.sovits.configured():
            return await ctx.response.send_message('GPT-SoVITS 서버가 설정되지 않았습니다.', ephemeral=True)
        await self.service.set_tts_engine_option(user_id=ctx.user.id, engine='sovits', model_name=style_id)
        await ctx.response.send_message('GPT-SoVITS 음성을 선택했습니다. gTTS로 돌아가려면 `/엔진변경`을 사용하세요.', ephemeral=True)

    @app_commands.command(name="보이스공개", description="내 음성의 공개 여부를 설정합니다")
    async def voice_public(self, ctx: Interaction, public: bool):
        try:
            await self.service.sovits.set_public(ctx.user.id, public)
            await ctx.response.send_message('공개 설정을 변경했습니다.', ephemeral=True)
        except ValueError as exc:
            await ctx.response.send_message(str(exc), ephemeral=True)

    @app_commands.command(name="보이스삭제", description="내 음성 또는 봇 소유자의 샘플을 삭제합니다")
    async def voice_delete(self, ctx: Interaction, style_id: str):
        await ctx.response.defer(ephemeral=True, thinking=True)
        try:
            await self.service.sovits.delete(ctx.user.id, style_id, owner=await self.bot.is_owner(ctx.user))
            await ctx.followup.send('음성을 삭제했습니다.', ephemeral=True)
        except Exception as exc:
            logger.exception('Voice deletion failed')
            await ctx.followup.send(f'삭제하지 못했습니다: {exc}', ephemeral=True)

    @app_commands.command(name="샘플보이스등록", description="봇 소유자가 .pgvoice 파일로 공개 샘플을 등록합니다")
    @app_commands.rename(name="이름", audio="파일")
    async def sample_register(self, ctx: Interaction, name: str, audio: discord.Attachment):
        if not await self.bot.is_owner(ctx.user):
            return await ctx.response.send_message('봇 소유자만 사용할 수 있습니다.', ephemeral=True)
        await self._register_voice_package(ctx, name, audio, sample=True)

    @app_commands.command(name="샘플보이스가져오기", description="서버에 이미 등록된 음성을 공개 샘플로 추가합니다")
    async def sample_import(self, ctx: Interaction, style_id: str):
        if not await self.bot.is_owner(ctx.user):
            return await ctx.response.send_message('봇 소유자만 사용할 수 있습니다.', ephemeral=True)
        await ctx.response.defer(ephemeral=True, thinking=True)
        try:
            await self.service.sovits.add_existing_sample(ctx.user.id, style_id)
            await ctx.followup.send('기존 음성을 공개 샘플로 추가했습니다.', ephemeral=True)
        except Exception as exc:
            await ctx.followup.send(f'추가하지 못했습니다: {exc}', ephemeral=True)

    @commands.command()
    async def check_state(self, ctx: commands.Context):
        if ctx.author.id != 464712715487805442:
            return

        return await ctx.send(str(self.queue))

    @commands.command(name="tts_allow")
    @commands.is_owner()
    async def tts_allow(self, ctx: commands.Context, action: str, user: Optional[discord.User] = None):
        action = (action or "").lower()

        if action == "list":
            if not self.tts_engine_allow:
                return await ctx.send("No users are allowed to change TTS engine.")
            allowed_ids = sorted(self.tts_engine_allow)
            return await ctx.send("Allowed users: " + ", ".join(str(uid) for uid in allowed_ids))

        if action in ("add", "remove"):
            if user is None:
                return await ctx.send("Usage: -tts_allow add|remove <user>")
            if action == "add":
                await self.service.add_tts_engine_allow(user.id)
                return await ctx.send(f"Allowed: {user.id}")
            await self.service.remove_tts_engine_allow(user.id)
            return await ctx.send(f"Removed: {user.id}")

        return await ctx.send("Usage: -tts_allow add|remove <user> | -tts_allow list")

    @app_commands.command(name="dm설정")
    async def dm_setting(self, ctx: Interaction):
        if (
            ctx.guild is None or
            self.queue.get(ctx.guild.id, None) is None
        ):
            return await ctx.response.send_message("해당 설정은 TTS가 사용중인 길드에서만 가능합니다.")

        channel = await ctx.user.create_dm()
        await self.service.configure_dm_channel(dm_channel_id=channel.id, guild_id=ctx.guild.id)
        await channel.send("TTS를 해당 DM 채널에서도 수신할게요!")
        return await ctx.response.send_message("설정이 완료되었습니다! DM 채널에서 사용해보세요!", ephemeral=True)


    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: VoiceState, after: VoiceState):
        await self.service.handle_voice_state_update(
            member=member,
            before=before,
            after=after,
            bot_user_id=self.bot.user.id,
        )

    async def play_tts(self, guild_id: int):
        await self._playback().play_tts(guild_id)

    async def safe_play_tts(self, guild_id: int):
        await self._playback().safe_play_tts(guild_id)
            
    def _has_image_attachment(self, message: discord.Message) -> bool:
        for attachment in message.attachments:
            content_type = attachment.content_type or ""
            if content_type.startswith("image/"):
                return True
            filename = (attachment.filename or "").lower()
            if filename.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")):
                return True
        return False

    def _normalize_tts_text(self, text: str) -> str:
        return normalize_tts_text(text)

    def _build_tts_text(self, message: discord.Message) -> str:
        return build_tts_text(
            message.clean_content or "",
            self._has_image_attachment(message),
        )



    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        await self.bot.process_commands(message)
        await self.service.handle_message(message)

async def setup(bot: commands.Bot):
    await bot.add_cog(TTS(bot))
