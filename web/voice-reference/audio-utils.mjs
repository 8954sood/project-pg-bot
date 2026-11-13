export const REFERENCE_RATE = 32000;
export const MIN_REFERENCE_SECONDS = 3;
export const MAX_REFERENCE_SECONDS = 10;
export const MAX_RECORDING_SECONDS = 30;
export const MAX_FILE_SECONDS = 120;
export const MAX_FILE_BYTES = 20 * 1024 * 1024;

export function mixToMono(channels) {
  if (!channels.length || channels.some((channel) => channel.length !== channels[0].length)) {
    throw new RangeError("오디오 채널을 읽을 수 없어요.");
  }
  const mono = new Float32Array(channels[0].length);
  for (const channel of channels) {
    for (let i = 0; i < mono.length; i++) mono[i] += channel[i] / channels.length;
  }
  return mono;
}

export function selectionFrames(start, end, rate, length) {
  if (![start, end, rate, length].every(Number.isFinite) || rate <= 0 || length < 1) {
    throw new RangeError("올바른 오디오 구간이 필요해요.");
  }
  const first = Math.max(0, Math.min(length - 1, Math.round(start * rate)));
  const last = Math.max(first + 1, Math.min(length, Math.round(end * rate)));
  return { first, last, duration: (last - first) / rate };
}

export function referenceIssues(duration, transcript) {
  const issues = [];
  if (!Number.isFinite(duration) || duration < MIN_REFERENCE_SECONDS || duration > MAX_REFERENCE_SECONDS) {
    issues.push("선택한 구간을 3~10초로 맞춰 주세요.");
  }
  if (!transcript.trim()) issues.push("선택한 음성에서 실제로 말한 대사를 적어 주세요.");
  return issues;
}

export function audioStats(samples) {
  let squareSum = 0;
  let peak = 0;
  let clipped = 0;
  for (const sample of samples) {
    const amplitude = Math.abs(sample);
    peak = Math.max(peak, amplitude);
    squareSum += sample * sample;
    if (amplitude >= 0.995) clipped++;
  }
  return { peak, rms: Math.sqrt(squareSum / Math.max(1, samples.length)), clippedRatio: clipped / Math.max(1, samples.length) };
}

export function waveformPeaks(samples, bars) {
  if (!samples.length) return [];
  const result = [];
  const count = Math.max(1, Math.min(samples.length, Math.floor(bars)));
  for (let bar = 0; bar < count; bar++) {
    const first = Math.floor(bar * samples.length / count);
    const last = Math.floor((bar + 1) * samples.length / count);
    let peak = 0;
    for (let i = first; i < last; i++) peak = Math.max(peak, Math.abs(samples[i]));
    result.push(peak);
  }
  return result;
}

export function findSoundBounds(samples, rate, threshold = 0.012, padding = 0.18) {
  // Windowed RMS avoids trimming to individual waveform zero crossings.
  const window = Math.max(1, Math.round(rate * 0.02));
  let first = null;
  let last = null;
  for (let i = 0; i < samples.length; i += window) {
    let sum = 0;
    const end = Math.min(samples.length, i + window);
    for (let j = i; j < end; j++) sum += samples[j] ** 2;
    if (Math.sqrt(sum / (end - i)) >= threshold) {
      first ??= i;
      last = end;
    }
  }
  if (first === null) return null;
  return { start: Math.max(0, first / rate - padding), end: Math.min(samples.length / rate, last / rate + padding) };
}

export function encodeWav(samples, rate = REFERENCE_RATE) {
  const bytes = new Uint8Array(44 + samples.length * 2);
  const view = new DataView(bytes.buffer);
  const text = (offset, value) => [...value].forEach((char, i) => view.setUint8(offset + i, char.charCodeAt(0)));
  text(0, "RIFF"); view.setUint32(4, bytes.length - 8, true); text(8, "WAVE");
  text(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
  view.setUint16(22, 1, true); view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  text(36, "data"); view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const value = Number.isFinite(samples[i]) ? Math.max(-1, Math.min(1, samples[i])) : 0;
    view.setInt16(44 + i * 2, Math.round(value * (value < 0 ? 32768 : 32767)), true);
  }
  return bytes;
}

export function referenceMetadata(transcript, language, filename = "reference.wav") {
  if (!["ko", "en", "ja"].includes(language)) throw new RangeError("지원하지 않는 녹음 언어예요.");
  return { ref_audio_path: filename, prompt_text: transcript.trim(), prompt_lang: language };
}

const CRC_TABLE = Uint32Array.from({ length: 256 }, (_, number) => {
  let crc = number;
  for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
  return crc >>> 0;
});

export function crc32(data) {
  let crc = 0xffffffff;
  for (const byte of data) crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

export function createZip(files) {
  // ZIP STORE: short PCM clips need no extra compression library or CDN.
  const encoder = new TextEncoder();
  const localParts = [];
  const centralParts = [];
  let localSize = 0;
  for (const [filename, contents] of Object.entries(files)) {
    const name = encoder.encode(filename);
    const data = typeof contents === "string" ? encoder.encode(contents) : contents;
    const crc = crc32(data);
    const header = new Uint8Array(30 + name.length);
    const view = new DataView(header.buffer);
    view.setUint32(0, 0x04034b50, true); view.setUint16(4, 20, true);
    view.setUint16(6, 0x0800, true); view.setUint16(12, 0x0021, true);
    view.setUint32(14, crc, true); view.setUint32(18, data.length, true); view.setUint32(22, data.length, true);
    view.setUint16(26, name.length, true); header.set(name, 30);
    localParts.push(header, data);
    const central = new Uint8Array(46 + name.length);
    const entry = new DataView(central.buffer);
    entry.setUint32(0, 0x02014b50, true); entry.setUint16(4, 20, true); entry.setUint16(6, 20, true);
    entry.setUint16(8, 0x0800, true); entry.setUint16(14, 0x0021, true);
    entry.setUint32(16, crc, true); entry.setUint32(20, data.length, true); entry.setUint32(24, data.length, true);
    entry.setUint16(28, name.length, true); entry.setUint32(42, localSize, true); central.set(name, 46);
    centralParts.push(central);
    localSize += header.length + data.length;
  }
  const centralSize = centralParts.reduce((sum, part) => sum + part.length, 0);
  const end = new Uint8Array(22);
  const view = new DataView(end.buffer);
  view.setUint32(0, 0x06054b50, true); view.setUint16(8, centralParts.length, true); view.setUint16(10, centralParts.length, true);
  view.setUint32(12, centralSize, true); view.setUint32(16, localSize, true);
  return new Blob([...localParts, ...centralParts, end], { type: "application/zip" });
}
