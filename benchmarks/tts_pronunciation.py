"""Run from the repository root: python -m benchmarks.tts_pronunciation."""

import argparse
import platform
import statistics
import time

from core.tts.language import analyze_language
from core.tts.pronunciation import PronunciationPreprocessor


def measure(callback, samples: int) -> tuple[float, float]:
    durations = []
    for _ in range(samples):
        start = time.perf_counter_ns()
        callback()
        durations.append((time.perf_counter_ns() - start) / 1_000_000)
    durations.sort()
    return statistics.median(durations), durations[min(len(durations) - 1, int(len(durations) * 0.95))]


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline TTS preprocessing benchmark (no synthesis)")
    parser.add_argument("--samples", type=int, default=1000)
    parser.add_argument("--target", choices=("ko", "en", "ja"), default="ko")
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("--samples must be positive")

    cached = PronunciationPreprocessor()
    start = time.perf_counter()
    cached.warmup()
    warmup_ms = (time.perf_counter() - start) * 1000
    uncached = PronunciationPreprocessor(cache_size=0)
    uncached.warmup()
    print(f"Python {platform.python_version()}, {platform.system()} {platform.machine()}")
    print(f"Dictionary warmup: {warmup_ms:.2f} ms (fresh instance, process/OS caches may be warm)")
    print(f"{args.samples} samples per case; median / p95 in milliseconds; target={args.target}")
    seed = "한국어 test computer 日本語 カタカナ stream! "
    for length in (20, 100, 300, 1000):
        text = (seed * (length // len(seed) + 1))[:length]
        cached.prepare(text, args.target)
        for label, callback in (
            ("script analysis", lambda: analyze_language(text)),
            ("conversion, run cache disabled", lambda: uncached.prepare(text, args.target)),
            ("conversion, repeated runs cached", lambda: cached.prepare(text, args.target)),
        ):
            median, p95 = measure(callback, args.samples)
            print(f"{length:4} chars | {label:32} | {median:9.4f} / {p95:9.4f} ms")
    print(f"Bounded run cache: {cached.cache_info()}")


if __name__ == "__main__":
    main()
