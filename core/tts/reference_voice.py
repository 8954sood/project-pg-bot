"""Strict .pgvoice reader for future bot ingestion. No UI, extraction or DB I/O.

This validates a file format and integrity, not the identity of its producer.
A public static site cannot keep a signing secret or prove where a file was made.
"""

import hashlib
import io
import json
import re
import stat
import struct
import zipfile
from pathlib import Path

from core.tts.models import ReferenceVoice


VOICE_FORMAT = "pgvoice"
VOICE_VERSION = 1
VOICE_CREATOR = "project-pg-bot/voice-studio"
MAX_PACKAGE_BYTES = 700_000
MAX_MANIFEST_BYTES = 16_000
MAX_TRANSCRIPT_LENGTH = 2000
SAMPLE_RATE = 32000
MIN_SAMPLES = SAMPLE_RATE * 3
MAX_SAMPLES = SAMPLE_RATE * 10
WAV_HEADER = struct.Struct("<4sI4s4sIHHIIHH4sI")


class ReferenceVoiceError(ValueError):
    """A file is not a supported, intact Voice Studio reference package."""


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReferenceVoiceError("중복된 설정 항목이 있어요.")
        result[key] = value
    return result


def _keys(value, expected: set[str]) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise ReferenceVoiceError("참조 보이스 설정 형식이 올바르지 않아요.")


def _validate_manifest(manifest) -> tuple[str, str, int, str]:
    _keys(manifest, {"format", "version", "created_by", "reference", "audio"})
    if (
        manifest["format"] != VOICE_FORMAT
        or type(manifest["version"]) is not int
        or manifest["version"] != VOICE_VERSION
        or manifest["created_by"] != VOICE_CREATOR
    ):
        raise ReferenceVoiceError("지원하지 않는 참조 보이스 형식 또는 버전이에요.")
    reference = manifest["reference"]
    _keys(reference, {"prompt_text", "prompt_lang"})
    text = reference["prompt_text"]
    language = reference["prompt_lang"]
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TRANSCRIPT_LENGTH:
        raise ReferenceVoiceError("참조 대사는 비어 있지 않은 2,000자 이하의 문자열이어야 해요.")
    if language not in ("ko", "en", "ja"):
        raise ReferenceVoiceError("참조 음성 언어는 ko, en, ja만 지원해요.")
    audio = manifest["audio"]
    _keys(audio, {"file", "encoding", "sample_rate", "channels", "sample_count", "sha256"})
    if (
        audio["file"] != "reference.wav" or audio["encoding"] != "pcm_s16le"
        or type(audio["sample_rate"]) is not int or audio["sample_rate"] != SAMPLE_RATE
        or type(audio["channels"]) is not int or audio["channels"] != 1
        or type(audio["sample_count"]) is not int
        or not MIN_SAMPLES <= audio["sample_count"] <= MAX_SAMPLES
    ):
        raise ReferenceVoiceError("참조 음성은 3~10초, 32 kHz, 모노 PCM이어야 해요.")
    digest = audio["sha256"]
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ReferenceVoiceError("참조 음성 체크섬 형식이 올바르지 않아요.")
    return text.strip(), language, audio["sample_count"], digest


def _validate_wav(audio: bytes, samples: int, digest: str) -> None:
    if len(audio) != WAV_HEADER.size + samples * 2:
        raise ReferenceVoiceError("음성 데이터 길이와 설정이 일치하지 않아요.")
    expected_header = (
        b"RIFF", len(audio) - 8, b"WAVE", b"fmt ", 16, 1, 1,
        SAMPLE_RATE, SAMPLE_RATE * 2, 2, 16, b"data", samples * 2,
    )
    if WAV_HEADER.unpack_from(audio) != expected_header:
        raise ReferenceVoiceError("참조 음성 WAV 헤더가 올바르지 않아요.")
    if hashlib.sha256(audio).hexdigest() != digest:
        raise ReferenceVoiceError("음성 파일의 체크섬이 일치하지 않아요.")


def parse_reference_voice(data: bytes, *, filename: str) -> ReferenceVoice:
    """Validate attachment bytes; reject raw WAV/ZIP and malformed .pgvoice.

    Never extract archive paths. ZIP members are read only after checking their
    names, counts, encoding, encryption flags and bounded declared sizes.
    """
    if Path(filename).suffix.lower() != ".pgvoice":
        raise ReferenceVoiceError("Voice Studio에서 저장한 .pgvoice 파일을 선택해 주세요.")
    if not isinstance(data, bytes) or not 1 <= len(data) <= MAX_PACKAGE_BYTES:
        raise ReferenceVoiceError("참조 보이스 파일 크기가 올바르지 않아요.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            members = package.infolist()
            if len(members) != 2 or {member.filename for member in members} != {"manifest.json", "reference.wav"}:
                raise ReferenceVoiceError("참조 보이스 파일 구성이 올바르지 않아요.")
            for member in members:
                mode = member.external_attr >> 16
                if (
                    member.compress_type != zipfile.ZIP_STORED or member.flag_bits & 1
                    or stat.S_ISLNK(mode) or member.is_dir()
                    or member.compress_size != member.file_size
                ):
                    raise ReferenceVoiceError("지원하지 않는 압축 또는 파일 형식이에요.")
                limit = MAX_MANIFEST_BYTES if member.filename == "manifest.json" else WAV_HEADER.size + MAX_SAMPLES * 2
                if not 1 <= member.file_size <= limit:
                    raise ReferenceVoiceError("압축 내부 파일 크기가 올바르지 않아요.")
            manifest = json.loads(package.read("manifest.json"), object_pairs_hook=_unique_keys)
            text, language, samples, digest = _validate_manifest(manifest)
            audio = package.read("reference.wav")
            _validate_wav(audio, samples, digest)
    except ReferenceVoiceError:
        raise
    except (zipfile.BadZipFile, OSError, UnicodeError, ValueError, RuntimeError, RecursionError, NotImplementedError, EOFError, struct.error) as exc:
        raise ReferenceVoiceError("참조 보이스 파일이 손상되었거나 올바른 형식이 아니에요.") from exc
    return ReferenceVoice(
        audio=audio, prompt_text=text, prompt_lang=language,
        sample_count=samples, audio_sha256=digest,
    )


def load_reference_voice(path: str | Path) -> ReferenceVoice:
    """Read at most the package size limit, even if the file changes mid-read."""
    path = Path(path)
    with path.open("rb") as file:
        data = file.read(MAX_PACKAGE_BYTES + 1)
    return parse_reference_voice(data, filename=path.name)
