"""Compatibility wrapper for core.tts.engines.gtts_engine.

TODO: Remove after old core.tts_engines imports are gone.
"""

from core.tts.engines.gtts_engine import GTTSEngine, gTTS

__all__ = ["GTTSEngine", "gTTS"]
