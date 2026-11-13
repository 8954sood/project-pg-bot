import asyncio
import struct

import pytest

from core.tts.engines.pcm_source import PCMStreamAudioSource


def stereo(mono):
    samples = struct.unpack('<' + 'h' * (len(mono) // 2), mono)
    return b''.join(struct.pack('<hh', sample, sample) for sample in samples)


@pytest.mark.asyncio
async def test_fragmented_pcm_waits_for_exact_frames_without_inserting_silence():
    source = PCMStreamAudioSource(asyncio.get_running_loop())
    mono = struct.pack('<hhhh', -32768, -1234, 1234, 32767) * 240
    assert source.write(mono[:13])
    read = asyncio.create_task(asyncio.to_thread(source.read))
    try:
        await asyncio.sleep(0.01)
        assert not read.done()
        assert source.write(mono[13:])
        assert await asyncio.wait_for(read, 1) == stereo(mono)
        source.finish()
        assert source.read() == b''
    finally:
        source.cleanup()
        await asyncio.gather(read, return_exceptions=True)


@pytest.mark.asyncio
async def test_only_final_frame_is_padded_and_errors_reach_discord_player():
    source = PCMStreamAudioSource(asyncio.get_running_loop())
    mono = struct.pack('<hh', -1000, 1000)
    source.write(mono)
    error = RuntimeError('stream interrupted')
    source.finish(error)
    assert source.read() == stereo(mono) + b'\0' * (3840 - 8)
    assert source.read() == b''
    assert source._current_error is error
    source.cleanup()


@pytest.mark.asyncio
async def test_backpressure_is_bounded_and_cleanup_unblocks_writer_and_reader():
    source = PCMStreamAudioSource(asyncio.get_running_loop())
    writer = asyncio.create_task(asyncio.to_thread(source.write, b'\0' * (source.BUFFER_BYTES * 3)))
    await asyncio.sleep(0.02)
    assert not writer.done()
    assert len(source._buffer) <= source.BUFFER_BYTES
    source.cleanup()
    assert await asyncio.wait_for(writer, 1) is False
    assert source.read() == b''
    source.cleanup()

    empty = PCMStreamAudioSource(asyncio.get_running_loop())
    reader = asyncio.create_task(asyncio.to_thread(empty.read))
    await asyncio.sleep(0.01)
    empty.cleanup()
    assert await asyncio.wait_for(reader, 1) == b''


@pytest.mark.asyncio
async def test_truncated_sample_reports_stream_error_without_misaligned_frames():
    source = PCMStreamAudioSource(asyncio.get_running_loop())
    source.write(b'\x01\x02\x03')
    source.finish()
    assert source.read()[:4] == b'\x01\x02\x01\x02'
    assert isinstance(source._current_error, RuntimeError)
    source.cleanup()


@pytest.mark.asyncio
async def test_network_underrun_keeps_discord_clock_without_consuming_partial_pcm():
    source = PCMStreamAudioSource(asyncio.get_running_loop())
    mono = struct.pack('<hhhh', -32768, -1234, 1234, 32767) * 240
    source.write(mono[:13])
    assert await asyncio.wait_for(asyncio.to_thread(source.read), 0.5) == b'\0' * 3840
    assert bytes(source._buffer) == mono[:13]
    source.write(mono[13:])
    assert source.read() == stereo(mono)
    source.finish()
    assert source.read() == b''
    source.cleanup()
