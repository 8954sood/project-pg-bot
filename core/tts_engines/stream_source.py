"""Compatibility wrapper for core.tts.engines.stream_source.

TODO: Remove after old core.tts_engines imports are gone.
"""

from core.tts.engines.stream_source import FFmpegStdoutAudioSource

__all__ = ["FFmpegStdoutAudioSource"]
