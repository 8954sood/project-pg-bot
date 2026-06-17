"""Compatibility wrapper for core.tts.engines.ai_stream_engine.

TODO: Remove after old core.tts_engines imports are gone.
"""

from core.tts.engines import ai_stream_engine as _impl
from core.tts.engines.ai_stream_engine import AIStreamEngine

subprocess = _impl.subprocess
websockets = _impl.websockets

__all__ = ["AIStreamEngine", "subprocess", "websockets"]
