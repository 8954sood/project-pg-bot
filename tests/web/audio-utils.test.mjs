import assert from "node:assert/strict";
import test from "node:test";
import {
  mixToMono, selectionFrames, referenceIssues, audioStats, waveformPeaks,
  findSoundBounds, encodeWav, referenceMetadata, createZip, crc32,
} from "../../web/voice-reference/audio-utils.mjs";

test("stereo downmix preserves amplitude and does not change source channels", () => {
  const left = Float32Array.of(1, 0.5, -1);
  const right = Float32Array.of(0, -0.5, 1);
  assert.deepEqual([...mixToMono([left, right])], [0.5, 0, 0]);
  assert.deepEqual([...left], [1, 0.5, -1]);
  assert.throws(() => mixToMono([]), RangeError);
  assert.throws(() => mixToMono([left, new Float32Array(1)]), RangeError);
});

test("trim converts to sample boundaries and clamps out-of-range positions", () => {
  assert.deepEqual(selectionFrames(1.25, 4.75, 32000, 320000), { first: 40000, last: 152000, duration: 3.5 });
  assert.deepEqual(selectionFrames(-3, 15, 32000, 320000), { first: 0, last: 320000, duration: 10 });
  assert.equal(selectionFrames(10, 10, 32000, 320000).last, 320000);
  assert.throws(() => selectionFrames(NaN, 5, 32000, 320000), RangeError);
});

test("SoVITS duration limits include exactly 3 and 10 seconds; missing transcript blocks export", () => {
  for (const duration of [3, 7.5, 10]) assert.deepEqual(referenceIssues(duration, "안녕하세요"), []);
  for (const duration of [0, 2.999, 10.001, NaN]) assert.equal(referenceIssues(duration, "대사").length, 1);
  assert.equal(referenceIssues(5, " \n ").length, 1);
});

test("PCM WAV has valid mono 16-bit headers, sample count, clipping and signed samples", () => {
  const bytes = encodeWav(Float32Array.of(-2, -0.5, 0, 0.5, 2, NaN));
  const view = new DataView(bytes.buffer);
  const text = (first, end) => new TextDecoder().decode(bytes.subarray(first, end));
  assert.equal(text(0, 4), "RIFF"); assert.equal(text(8, 12), "WAVE");
  assert.equal(view.getUint32(4, true), bytes.length - 8);
  assert.equal(view.getUint16(20, true), 1); assert.equal(view.getUint16(22, true), 1);
  assert.equal(view.getUint32(24, true), 32000); assert.equal(view.getUint32(28, true), 64000);
  assert.equal(view.getUint16(32, true), 2); assert.equal(view.getUint16(34, true), 16);
  assert.equal(view.getUint32(40, true), 12);
  assert.deepEqual(Array.from({ length: 6 }, (_, i) => view.getInt16(44 + i * 2, true)), [-32768, -16384, 0, 16384, 32767, 0]);
  const clip = encodeWav(new Float32Array(3 * 32000));
  assert.equal((clip.length - 44) / 2 / 32000, 3);
});

test("metadata uses the exact GPT-SoVITS API reference fields and preserves spoken text", () => {
  assert.deepEqual(referenceMetadata("  안녕하세요.\n좋은 하루예요. ", "ko"), {
    ref_audio_path: "reference.wav", prompt_text: "안녕하세요.\n좋은 하루예요.", prompt_lang: "ko",
  });
  assert.throws(() => referenceMetadata("你好", "zh"), RangeError);
});

test("waveform and quality analysis keep short transients and clipping", () => {
  assert.deepEqual(waveformPeaks(Float32Array.of(0, -0.8, 0.2, 0, 1), 2).map((x) => Math.round(x * 10) / 10), [0.8, 1]);
  assert.deepEqual(waveformPeaks(new Float32Array(0), 2), []);
  assert.deepEqual(audioStats(Float32Array.of(1, -1)), { rms: 1, peak: 1, clippedRatio: 1 });
  assert.equal(audioStats(new Float32Array(0)).rms, 0);
});

test("silence trim finds RMS windows with padding and leaves fully silent clips unchanged", () => {
  const samples = new Float32Array(5000);
  samples.fill(0.2, 1000, 4000);
  const bounds = findSoundBounds(samples, 1000);
  assert.ok(Math.abs(bounds.start - 0.82) < 1e-6);
  assert.ok(Math.abs(bounds.end - 4.18) < 1e-6);
  assert.equal(findSoundBounds(new Float32Array(1000), 1000), null);
});

test("ZIP stores UTF-8 names, CRC32, central directory offsets and original file data", async () => {
  assert.equal(crc32(new TextEncoder().encode("123456789")), 0xcbf43926);
  const files = { "reference.wav": encodeWav(Float32Array.of(-0.5, 0.5)), "대사.txt": "안녕하세요.\n", "reference.json": '{"prompt_lang":"ko"}' };
  const zip = createZip(files);
  const bytes = new Uint8Array(await zip.arrayBuffer());
  const view = new DataView(bytes.buffer);
  const ending = bytes.length - 22;
  assert.equal(view.getUint32(ending, true), 0x06054b50);
  assert.equal(view.getUint16(ending + 10, true), 3);
  const centralStart = view.getUint32(ending + 16, true);
  let offset = 0;
  for (const [name, content] of Object.entries(files)) {
    assert.equal(view.getUint32(offset, true), 0x04034b50);
    assert.equal(view.getUint16(offset + 6, true), 0x0800);
    const length = view.getUint32(offset + 18, true);
    const nameLength = view.getUint16(offset + 26, true);
    assert.equal(new TextDecoder().decode(bytes.subarray(offset + 30, offset + 30 + nameLength)), name);
    const data = bytes.subarray(offset + 30 + nameLength, offset + 30 + nameLength + length);
    assert.equal(view.getUint32(offset + 14, true), crc32(data));
    assert.deepEqual([...data], [...(typeof content === "string" ? new TextEncoder().encode(content) : content)]);
    offset += 30 + nameLength + length;
  }
  assert.equal(offset, centralStart);
  assert.equal(view.getUint32(centralStart, true), 0x02014b50);
  assert.equal(view.getUint32(centralStart + 42, true), 0);
});
