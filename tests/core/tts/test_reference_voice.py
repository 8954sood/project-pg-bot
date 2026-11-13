import hashlib
import io
import json
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path

import pytest

from core.tts.reference_voice import (
    MAX_PACKAGE_BYTES, SAMPLE_RATE, VOICE_CREATOR, WAV_HEADER,
    ReferenceVoiceError, load_reference_voice, parse_reference_voice,
)


def make_audio(seconds=3):
    samples = int(SAMPLE_RATE * seconds)
    return WAV_HEADER.pack(
        b"RIFF", 36 + samples * 2, b"WAVE", b"fmt ", 16, 1, 1,
        SAMPLE_RATE, SAMPLE_RATE * 2, 2, 16, b"data", samples * 2,
    ) + b"\x01\x00" * samples


def make_manifest(audio):
    return {
        "format": "pgvoice", "version": 1, "created_by": VOICE_CREATOR,
        "reference": {"prompt_text": "안녕하세요.\n제 목소리를 녹음했어요.", "prompt_lang": "ko"},
        "audio": {"file": "reference.wav", "encoding": "pcm_s16le", "sample_rate": SAMPLE_RATE,
                  "channels": 1, "sample_count": (len(audio) - 44) // 2, "sha256": hashlib.sha256(audio).hexdigest()},
    }


def make_package(*, audio=None, manifest=None, manifest_bytes=None, extra=None, compression=zipfile.ZIP_STORED):
    audio = make_audio() if audio is None else audio
    manifest = make_manifest(audio) if manifest is None else manifest
    manifest_bytes = json.dumps(manifest, ensure_ascii=False).encode() if manifest_bytes is None else manifest_bytes
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as package:
        package.writestr("manifest.json", manifest_bytes)
        package.writestr("reference.wav", audio)
        if extra:
            package.writestr(*extra)
    return output.getvalue()


@pytest.mark.parametrize("seconds", [3, 7.5, 10])
def test_valid_reference_has_audio_prompt_language_and_duration(seconds):
    audio = make_audio(seconds)
    result = parse_reference_voice(make_package(audio=audio), filename="my-voice.pgvoice")

    assert result.audio == audio
    assert result.duration_seconds == seconds
    assert result.prompt_lang == "ko"
    assert result.prompt_text == "안녕하세요.\n제 목소리를 녹음했어요."
    assert result.audio_sha256 == hashlib.sha256(audio).hexdigest()


@pytest.mark.parametrize("filename", ["voice.wav", "voice.zip", "voice.pgvoice.zip", "voice"])
def test_only_dedicated_extension_is_accepted(filename):
    with pytest.raises(ReferenceVoiceError, match=".pgvoice"):
        parse_reference_voice(make_package(), filename=filename)


@pytest.mark.parametrize("data", [b"", b"not a ZIP", b"PK\x03\x04", b"x" * (MAX_PACKAGE_BYTES + 1)])
def test_invalid_or_oversized_package_is_rejected(data):
    with pytest.raises(ReferenceVoiceError):
        parse_reference_voice(data, filename="voice.pgvoice")


@pytest.mark.parametrize("extra", [("../escape.wav", b"bad"), ("folder/", b""), ("extra.json", b"{}")])
def test_extra_members_and_traversal_paths_are_rejected_without_extraction(extra):
    with pytest.raises(ReferenceVoiceError, match="구성"):
        parse_reference_voice(make_package(extra=extra), filename="voice.pgvoice")


def test_duplicate_archive_members_are_rejected():
    with pytest.warns(UserWarning, match="Duplicate"):
        package = make_package(extra=("manifest.json", b"{}"))
    with pytest.raises(ReferenceVoiceError, match="구성"):
        parse_reference_voice(package, filename="voice.pgvoice")


def test_compressed_members_are_rejected_before_any_decompression(monkeypatch):
    package = make_package(compression=zipfile.ZIP_DEFLATED)
    monkeypatch.setattr(zipfile.ZipFile, "read", lambda *args, **kwargs: pytest.fail("Must reject before reading"))
    with pytest.raises(ReferenceVoiceError, match="압축"):
        parse_reference_voice(package, filename="voice.pgvoice")


def test_symlink_member_is_rejected():
    audio = make_audio()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as package:
        package.writestr("manifest.json", json.dumps(make_manifest(audio)))
        member = zipfile.ZipInfo("reference.wav")
        member.create_system = 3
        member.external_attr = (stat.S_IFLNK | 0o777) << 16
        package.writestr(member, audio)
    with pytest.raises(ReferenceVoiceError, match="파일 형식"):
        parse_reference_voice(output.getvalue(), filename="voice.pgvoice")


@pytest.mark.parametrize("field,value", [("version", 2), ("version", True), ("format", "zip"), ("created_by", "unknown")])
def test_format_version_and_creator_marker_are_checked(field, value):
    audio = make_audio()
    manifest = make_manifest(audio)
    manifest[field] = value
    with pytest.raises(ReferenceVoiceError, match="형식 또는 버전"):
        parse_reference_voice(make_package(audio=audio, manifest=manifest), filename="voice.pgvoice")


@pytest.mark.parametrize("reference", [
    {"prompt_text": "", "prompt_lang": "ko"}, {"prompt_text": "x" * 2001, "prompt_lang": "en"},
    {"prompt_text": 1, "prompt_lang": "ko"}, {"prompt_text": "hello", "prompt_lang": "fr"},
    {"prompt_text": "hello"}, {"prompt_text": "hello", "prompt_lang": "en", "extra": True},
])
def test_reference_fields_are_strict_but_transcript_is_not_fixed(reference):
    manifest = make_manifest(make_audio())
    manifest["reference"] = reference
    with pytest.raises(ReferenceVoiceError):
        parse_reference_voice(make_package(manifest=manifest), filename="voice.pgvoice")


@pytest.mark.parametrize("field,value", [
    ("sample_rate", 44100), ("sample_rate", 32000.0), ("channels", True), ("channels", 2),
    ("sample_count", 95999), ("sample_count", 320001), ("sample_count", "96000"),
    ("sha256", "bad"), ("file", "../reference.wav"), ("encoding", "float32"),
])
def test_audio_metadata_is_strict(field, value):
    manifest = make_manifest(make_audio())
    manifest["audio"][field] = value
    with pytest.raises(ReferenceVoiceError):
        parse_reference_voice(make_package(manifest=manifest), filename="voice.pgvoice")


def test_tampered_audio_fails_sha256_even_when_zip_crc_is_valid():
    audio = make_audio()
    manifest = make_manifest(audio)
    tampered = audio[:-2] + b"\x02\x00"
    with pytest.raises(ReferenceVoiceError, match="체크섬이 일치"):
        parse_reference_voice(make_package(audio=tampered, manifest=manifest), filename="voice.pgvoice")


def test_wrong_wav_header_fails_even_with_matching_sha256():
    audio = bytearray(make_audio())
    audio[22] = 2
    with pytest.raises(ReferenceVoiceError, match="헤더"):
        parse_reference_voice(make_package(audio=bytes(audio)), filename="voice.pgvoice")


@pytest.mark.parametrize("manifest", [b"[]", b"null", b"\xff", b'{"format":"pgvoice","format":"zip"}', b"x" * 16001])
def test_malformed_duplicate_or_oversized_manifest_is_rejected(manifest):
    with pytest.raises(ReferenceVoiceError):
        parse_reference_voice(make_package(manifest_bytes=manifest), filename="voice.pgvoice")


def test_local_file_reader_bounds_reads_and_supports_uppercase_extension(tmp_path):
    path = tmp_path / "voice.PGVOICE"
    path.write_bytes(make_package())
    assert load_reference_voice(path).duration_seconds == 3
    path.write_bytes(b"x" * (MAX_PACKAGE_BYTES + 1))
    with pytest.raises(ReferenceVoiceError):
        load_reference_voice(path)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for browser-writer interoperability")
def test_javascript_package_writer_interoperates_with_python_reader():
    root = Path(__file__).resolve().parents[3]
    script = """
        import { encodeWav } from './web/voice-reference/audio-utils.mjs';
        import { createVoicePackage } from './web/voice-reference/voice-package.mjs';
        const samples = new Float32Array(32000 * 4).fill(0.1);
        const blob = await createVoicePackage(encodeWav(samples), '自分の言葉で話します。', 'ja');
        process.stdout.write(Buffer.from(await blob.arrayBuffer()));
    """
    result = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script], cwd=root, capture_output=True, check=True)
    voice = parse_reference_voice(result.stdout, filename="browser.pgvoice")

    assert voice.duration_seconds == 4
    assert voice.prompt_lang == "ja"
    assert voice.prompt_text == "自分の言葉で話します。"
