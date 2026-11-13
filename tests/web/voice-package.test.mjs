import assert from "node:assert/strict";
import test from "node:test";
import { encodeWav } from "../../web/voice-reference/audio-utils.mjs";
import { createVoicePackage, VOICE_CREATOR } from "../../web/voice-reference/voice-package.mjs";

function unzipStored(bytes) {
  const files = {};
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let offset = 0;
  while (view.getUint32(offset, true) === 0x04034b50) {
    const size = view.getUint32(offset + 18, true);
    const nameLength = view.getUint16(offset + 26, true);
    const extra = view.getUint16(offset + 28, true);
    const name = new TextDecoder().decode(bytes.subarray(offset + 30, offset + 30 + nameLength));
    const start = offset + 30 + nameLength + extra;
    files[name] = bytes.subarray(start, start + size);
    offset = start + size;
  }
  return files;
}

test("dedicated package contains only a versioned manifest and the selected audio", async () => {
  const wav = encodeWav(new Float32Array(32000 * 3));
  const blob = await createVoicePackage(wav, "  안녕하세요.\n오늘도 좋은 하루예요. ", "ko");
  const files = unzipStored(new Uint8Array(await blob.arrayBuffer()));
  assert.deepEqual(Object.keys(files), ["manifest.json", "reference.wav"]);
  assert.deepEqual(files["reference.wav"], wav);
  const manifest = JSON.parse(new TextDecoder().decode(files["manifest.json"]));
  assert.equal(manifest.format, "pgvoice"); assert.equal(manifest.version, 1);
  assert.equal(manifest.created_by, VOICE_CREATOR);
  assert.deepEqual(manifest.reference, { prompt_text: "안녕하세요.\n오늘도 좋은 하루예요.", prompt_lang: "ko" });
  assert.equal(manifest.audio.sample_count, 96000);
  assert.equal(manifest.audio.sha256, Buffer.from(await crypto.subtle.digest("SHA-256", wav)).toString("hex"));
});

test("language and transcript remain user-selected, with no fixed-script requirement", async () => {
  for (const [language, text] of [["ko", "자유롭게 말한 문장"], ["en", "A sentence of my own."], ["ja", "自分の言葉で話します。"]]) {
    const blob = await createVoicePackage(encodeWav(new Float32Array(32000 * 10)), text, language);
    const files = unzipStored(new Uint8Array(await blob.arrayBuffer()));
    const manifest = JSON.parse(new TextDecoder().decode(files["manifest.json"]));
    assert.deepEqual(manifest.reference, { prompt_text: text, prompt_lang: language });
  }
});

test("package creation rejects unsupported formats, lengths, languages and missing transcripts", async () => {
  const wav = encodeWav(new Float32Array(32000 * 4));
  await assert.rejects(createVoicePackage(wav, "", "ko"));
  await assert.rejects(createVoicePackage(wav, "x".repeat(2001), "ko"));
  await assert.rejects(createVoicePackage(wav, "hello", "fr"));
  await assert.rejects(createVoicePackage(encodeWav(new Float32Array(32000 * 2)), "hello", "en"));
  await assert.rejects(createVoicePackage(encodeWav(new Float32Array(32000 * 11)), "hello", "en"));
  await assert.rejects(createVoicePackage(encodeWav(new Float32Array(32000 * 4), 44100), "hello", "en"));
  await assert.rejects(createVoicePackage(wav.subarray(0, -2), "hello", "en"));
});
