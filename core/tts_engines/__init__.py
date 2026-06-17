"""Compatibility imports for the old TTS engine package.

TODO: Remove this package after downstream imports use core.tts.engines.
"""

from core.tts.engines import AIStreamEngine, FFmpegStdoutAudioSource, GTTSEngine

__all__ = ["AIStreamEngine", "GTTSEngine", "FFmpegStdoutAudioSource"]
