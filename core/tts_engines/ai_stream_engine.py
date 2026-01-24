import asyncio
import json
import subprocess
import websockets

from .stream_source import FFmpegStdoutAudioSource

class AIStreamEngine:
    def __init__(self, *, ai_ws_url: str):
        self.ai_ws_url = ai_ws_url  # 예: "wss://ai.example.com/tts/stream"

    async def create_discord_source(self, *, text: str, model_name: str) -> FFmpegStdoutAudioSource:
        if not model_name:
            raise RuntimeError("AI model is not set for this user")

        loop = asyncio.get_running_loop()
        first_chunk = loop.create_future()
        failed = loop.create_future()

        # AI 서버 출력: 24k mono s16le 프레임(960 bytes/20ms)
        # Discord 입력: 48k stereo s16le(3840 bytes/20ms)로 변환
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
                                proc.stdin.write(msg)
                                if not first_chunk.done():
                                    first_chunk.set_result(True)
                        else:
                            # meta/end/error ??
                            try:
                                data = json.loads(msg)
                                if data.get("type") == "end":
                                    break
                                if data.get("type") == "error":
                                    # ??? ????????
                                    break
                            except Exception:
                                pass
            except Exception as e:
                if not failed.done():
                    failed.set_exception(e)
                print(f"AI TTS websocket error: {e}")
            finally:
                try:
                    if proc.stdin:
                        proc.stdin.close()
                except Exception:
                    pass
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
                timeout=2,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if failed in done:
                raise failed.exception()
        except Exception as e:
            try:
                proc.terminate()
            except Exception:
                pass
            raise RuntimeError("AI TTS unavailable") from e
        return FFmpegStdoutAudioSource(proc)
