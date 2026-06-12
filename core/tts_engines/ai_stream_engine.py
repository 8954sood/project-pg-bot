import asyncio
import json
import logging
import os
import subprocess

import websockets

from .stream_source import FFmpegStdoutAudioSource

logger = logging.getLogger(__name__)


class AIStreamEngine:
    def __init__(self, *, ai_ws_url: str, first_chunk_timeout: float = None):
        self.ai_ws_url = ai_ws_url  # 예: "wss://ai.example.com/tts/stream"
        self.first_chunk_timeout = (
            first_chunk_timeout
            if first_chunk_timeout is not None
            else float(os.getenv("TTS_AI_FIRST_CHUNK_TIMEOUT_SECONDS", "3"))
        )

    async def create_discord_source(self, *, text: str, model_name: str) -> FFmpegStdoutAudioSource:
        if not model_name:
            raise RuntimeError("AI model is not set for this user")

        loop = asyncio.get_running_loop()
        first_chunk = loop.create_future()
        failed = loop.create_future()
        logger.info(
            "AI TTS websocket connection starting",
            extra={"tts_engine": "ai", "ai_model": model_name},
        )

        # AI 서버 출력: 24k mono s16le 프레임(960 bytes/20ms)
        # Discord 입력: 48k stereo s16le(3840 bytes/20ms)로 변환
        try:
            proc = subprocess.Popen(
                [
                    "ffmpeg",
                    "-hide_banner", "-loglevel", "error",
                    "-f", "s16le",
                    "-ar", "24000",
                    "-ac", "1",
                    "-i", "pipe:0",
                    "-f", "s16le",
                    "-ar", "48000",
                    "-ac", "2",
                    "pipe:1",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                bufsize=0,
            )
        except Exception as exc:
            logger.exception(
                "ffmpeg process start failed",
                extra={"tts_engine": "ai", "ai_model": model_name},
            )
            raise RuntimeError("AI TTS ffmpeg start failed") from exc

        async def feed():
            try:
                async with websockets.connect(
                    self.ai_ws_url,
                    max_size=None,
                    open_timeout=10,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                ) as ws:
                    await ws.send(json.dumps({
                        "model": model_name,
                        "text": text,
                    }))

                    async for msg in ws:
                        if isinstance(msg, bytes):
                            if proc.stdin:
                                await asyncio.to_thread(proc.stdin.write, msg)
                                if not first_chunk.done():
                                    first_chunk.set_result(True)
                                    logger.info(
                                        "AI TTS first audio chunk received",
                                        extra={"tts_engine": "ai", "ai_model": model_name},
                                    )
                        else:
                            try:
                                data = json.loads(msg)
                            except (TypeError, json.JSONDecodeError):
                                logger.warning(
                                    "AI TTS websocket sent invalid metadata",
                                    extra={"tts_engine": "ai", "ai_model": model_name},
                                )
                                continue
                            if data.get("type") == "end":
                                if not first_chunk.done() and not failed.done():
                                    failed.set_result(
                                        RuntimeError("AI TTS websocket ended before first chunk")
                                    )
                                break
                            if data.get("type") == "error":
                                error_message = data.get("message") or str(data)
                                logger.error(
                                    "AI TTS websocket error message: %s",
                                    error_message,
                                    extra={"tts_engine": "ai", "ai_model": model_name},
                                )
                                raise RuntimeError(
                                    f"AI TTS websocket error: {error_message}"
                                )
            except asyncio.CancelledError:
                logger.debug(
                    "AI TTS websocket feed task cancelled",
                    extra={"tts_engine": "ai", "ai_model": model_name},
                )
                raise
            except Exception as exc:
                if not failed.done():
                    failed.set_result(exc)
                logger.exception(
                    "AI TTS websocket feed failed",
                    extra={"tts_engine": "ai", "ai_model": model_name},
                )
            finally:
                if not first_chunk.done() and not failed.done():
                    failed.set_result(
                        RuntimeError("AI TTS websocket closed before first chunk")
                    )
                try:
                    if proc.stdin:
                        proc.stdin.close()
                except Exception:
                    logger.exception(
                        "ffmpeg stdin close failed",
                        extra={"tts_engine": "ai", "ai_model": model_name},
                    )
                logger.info(
                    "AI TTS websocket connection ended",
                    extra={"tts_engine": "ai", "ai_model": model_name},
                )

        def _consume_task_exception(task: asyncio.Task) -> None:
            try:
                task.exception()
            except asyncio.CancelledError:
                pass

        task = asyncio.create_task(feed())
        task.add_done_callback(_consume_task_exception)

        try:
            done, _ = await asyncio.wait(
                [first_chunk, failed],
                timeout=self.first_chunk_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                raise RuntimeError("AI TTS first chunk timeout")
            if failed in done:
                error = failed.result()
                raise error
        except Exception as exc:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            source = FFmpegStdoutAudioSource(proc)
            source.cleanup()
            logger.exception(
                "AI TTS source creation failed",
                extra={"tts_engine": "ai", "ai_model": model_name},
            )
            if str(exc) == "AI TTS first chunk timeout":
                raise RuntimeError("AI TTS first chunk timeout") from exc
            raise RuntimeError("AI TTS unavailable") from exc
        return FFmpegStdoutAudioSource(proc, feed_task=task, loop=loop)
