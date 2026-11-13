import { REFERENCE_RATE, referenceIssues, createZip } from "./audio-utils.mjs";

export const VOICE_FORMAT = "pgvoice";
export const VOICE_VERSION = 1;
export const VOICE_CREATOR = "project-pg-bot/voice-studio";
export const MAX_TRANSCRIPT_LENGTH = 2000;

export async function createVoicePackage(wav, transcript, language) {
  if (!(wav instanceof Uint8Array) || wav.length < 44) throw new Error("올바른 음성 파일이 필요해요.");
  const view = new DataView(wav.buffer, wav.byteOffset, wav.byteLength);
  const frames = view.getUint32(40, true) / 2;
  const duration = frames / REFERENCE_RATE;
  const issues = referenceIssues(duration, transcript);
  if (issues.length) throw new Error(issues[0]);
  if (transcript.length > MAX_TRANSCRIPT_LENGTH) throw new Error("대사는 2,000자 이하로 적어 주세요.");
  if (!["ko", "en", "ja"].includes(language)) throw new Error("녹음 언어를 확인해 주세요.");
  const textAt = (start, end) => new TextDecoder().decode(wav.subarray(start, end));
  if (
    textAt(0, 4) !== "RIFF" || textAt(8, 16) !== "WAVEfmt " || textAt(36, 40) !== "data"
    || view.getUint32(4, true) !== wav.length - 8 || view.getUint32(16, true) !== 16
    || view.getUint16(20, true) !== 1 || view.getUint16(22, true) !== 1
    || view.getUint32(24, true) !== REFERENCE_RATE || view.getUint32(28, true) !== REFERENCE_RATE * 2
    || view.getUint16(32, true) !== 2 || view.getUint16(34, true) !== 16
    || !Number.isInteger(frames) || wav.length !== 44 + frames * 2
  ) throw new Error("32 kHz 모노 PCM WAV만 저장할 수 있어요.");
  const digest = await crypto.subtle.digest("SHA-256", wav);
  const sha256 = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
  const manifest = {
    format: VOICE_FORMAT,
    version: VOICE_VERSION,
    created_by: VOICE_CREATOR,
    reference: { prompt_text: transcript.trim(), prompt_lang: language },
    audio: { file: "reference.wav", encoding: "pcm_s16le", sample_rate: REFERENCE_RATE, channels: 1, sample_count: frames, sha256 },
  };
  return createZip({ "manifest.json": JSON.stringify(manifest, null, 2) + "\n", "reference.wav": wav });
}
