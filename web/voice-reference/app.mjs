import {
  REFERENCE_RATE, MIN_REFERENCE_SECONDS, MAX_FILE_BYTES, MAX_FILE_SECONDS, mixToMono, selectionFrames,
  referenceIssues, audioStats, waveformPeaks, findSoundBounds, encodeWav, referenceMetadata,
} from "./audio-utils.mjs";
import { MicrophoneRecorder } from "./recorder.mjs?v=2";
import { createVoicePackage } from "./voice-package.mjs";

const $ = (id) => document.getElementById(id);
const ui = Object.fromEntries([
  "record-button", "record-button-label", "upload-button", "file-input", "record-clock", "record-footnote",
  "wave-panel", "waveform", "wave-empty", "wave-message", "wave-hint", "source-label", "source-duration",
  "notice", "editor", "start-time", "end-time", "start-range", "end-range", "selection-duration", "duration-badge",
  "play-button", "play-icon", "play-label", "trim-silence", "quality-note", "prompt-language", "prompt-preset",
  "transcript", "transcript-help", "export-button", "export-help", "metadata-preview",
].map((id) => [id, $(id)]));

const PROMPTS = {
  ko: {
    standard: "안녕하세요. 오늘은 날씨가 좋아서 잠깐 산책을 다녀왔어요. 시원한 바람이 불어서 기분이 한결 좋아졌네요.",
    short: "창문을 열었더니 시원한 바람이 들어오네요. 오늘은 기분 좋은 하루가 될 것 같아요.",
  },
  en: {
    standard: "Hello. The weather was lovely today, so I went out for a little walk. The cool breeze made me feel refreshed.",
    short: "A cool breeze is coming through the window. It feels like a lovely day.",
  },
  ja: {
    standard: "こんにちは。今日は天気がよかったので、少し散歩をしてきました。涼しい風が吹いて、気持ちがよくなりました。",
    short: "窓を開けると、涼しい風が入ってきました。今日はいい一日になりそうです。",
  },
};

const state = { buffer: null, samples: null, start: 0, end: 0, processing: false, exporting: false, playback: null, playhead: null, peaks: [], live: null, destroyed: false };
const AudioContextClass = globalThis.AudioContext ?? globalThis.webkitAudioContext;
let context;
let frame;
let recordingPrompt;
const clockText = (seconds) => `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${(seconds % 60).toFixed(1).padStart(4, "0")}`;

function notify(message, kind = "info") {
  ui.notice.textContent = message;
  ui.notice.className = `notice ${kind}`;
  ui.notice.hidden = !message;
}

function explainError(error) {
  if (error.name === "NotAllowedError") return "마이크 사용이 허용되지 않았어요. 주소 표시줄에서 마이크 권한을 허용한 뒤 다시 시도하거나 음성 파일을 가져와 주세요.";
  if (error.name === "NotFoundError") return "연결된 마이크를 찾지 못했어요. 마이크 연결을 확인하거나 음성 파일을 가져와 주세요.";
  if (error.name === "NotReadableError") return "마이크를 사용할 수 없어요. 다른 앱에서 사용 중인지 확인해 주세요.";
  if (error.name === "EncodingError") return "이 음성 형식을 읽을 수 없어요. WAV·MP3·M4A 등 브라우저가 지원하는 파일로 다시 시도해 주세요.";
  return error.message || "오디오를 처리하지 못했어요. 다른 파일로 다시 시도해 주세요.";
}

async function getContext() {
  if (!AudioContextClass) throw new Error("오디오 편집을 지원하지 않는 브라우저예요. 최신 Chrome·Safari·Firefox에서 열어 주세요.");
  if (!context || context.state === "closed") context = new AudioContextClass();
  if (context.state === "suspended") await context.resume();
  return context;
}

function selection() {
  return selectionFrames(state.start, state.end, state.buffer.sampleRate, state.buffer.length);
}

function updateRecordingControl() {
  const recording = recorder.phase === "recording";
  const occupied = recorder.phase !== "idle" || state.processing || state.exporting;
  ui["record-button"].disabled = recording ? !recorder.canStop : occupied;
  ui["record-button"].classList.toggle("recording", recording);
  ui["record-button-label"].textContent = recording ? (recorder.canStop ? "녹음 마치기" : "최소 3초 녹음해 주세요") : recorder.phase === "requesting" ? "마이크 연결 중…" : state.processing ? "소리 불러오는 중…" : state.buffer ? "다시 녹음" : "녹음 시작";
  if (recording) ui["record-footnote"].textContent = recorder.canStop ? "문장을 다 읽으면 ‘녹음 마치기’를 눌러 주세요." : "3초가 지나면 녹음을 마칠 수 있어요.";
}

function updateControls() {
  const recording = recorder.phase === "recording";
  const occupied = recorder.phase !== "idle" || state.processing || state.exporting;
  updateRecordingControl();
  ui["upload-button"].disabled = occupied;
  for (const id of ["prompt-language", "prompt-preset", "transcript"]) ui[id].disabled = occupied;
  for (const id of ["start-time", "end-time", "start-range", "end-range", "play-button", "trim-silence"]) ui[id].disabled = occupied || !state.buffer;
  ui["wave-panel"].classList.toggle("recording", recording);
  ui["wave-empty"].hidden = Boolean(state.buffer) || recording;
  ui.editor.hidden = !state.buffer;
  const duration = state.buffer ? selection().duration : 0;
  const issues = referenceIssues(duration, ui.transcript.value);
  const usable = Boolean(state.buffer) && !occupied && !issues.length;
  ui["export-button"].disabled = !usable;
  ui["export-button"].firstChild.textContent = state.exporting ? "저장 준비 중… " : "참조 보이스 저장 ";
  ui["export-help"].textContent = state.buffer ? (issues[0] ?? "선택 구간의 대사가 맞는지 확인한 뒤 저장하세요.") : "녹음 후 3~10초 구간을 골라 저장하세요.";
  ui["selection-duration"].textContent = `${duration.toFixed(2)}초`;
  const validLength = !referenceIssues(duration, "대사").length;
  ui["duration-badge"].textContent = validLength ? "좋아요" : "3~10초로 조절";
  ui["duration-badge"].classList.toggle("invalid", !validLength);
  ui["metadata-preview"].textContent = JSON.stringify(referenceMetadata(ui.transcript.value, ui["prompt-language"].value), null, 2);
  for (const [id, current] of [["step-record", !state.buffer], ["step-trim", Boolean(state.buffer) && !usable], ["step-export", usable]]) {
    if (current) $(id).setAttribute("aria-current", "step");
    else $(id).removeAttribute("aria-current");
  }
  $("step-record").classList.toggle("done", Boolean(state.buffer));
  $("step-trim").classList.toggle("done", usable);
}

function refreshQuality() {
  if (!state.buffer) return;
  const { first, last } = selection();
  const stats = audioStats(state.samples.subarray(first, last));
  ui["quality-note"].textContent = stats.rms < 0.002 ? "소리가 거의 들리지 않아요. 마이크와 선택 구간을 확인해 주세요." : stats.clippedRatio > 0.005 ? "소리가 너무 커서 찌그러질 수 있어요. 마이크에서 조금 떨어져 다시 녹음해 보세요." : stats.rms < 0.012 ? "목소리가 작은 편이에요. 들어 보고 필요하면 마이크에 조금 더 가까이 녹음해 주세요." : "";
}

function stopPlayback() {
  if (state.playback) {
    state.playback.onended = null;
    try { state.playback.stop(); } catch { /* Already finished. */ }
    state.playback = null;
  }
  state.playhead = null;
  cancelAnimationFrame(frame);
  ui["play-icon"].textContent = "▶";
  ui["play-label"].textContent = "선택 구간 듣기";
  drawWave();
}

function setSelection(start, end, preserveNumberInputs = false) {
  if (!state.buffer) return;
  stopPlayback();
  const { first, last } = selectionFrames(start, end, state.buffer.sampleRate, state.buffer.length);
  state.start = first / state.buffer.sampleRate;
  state.end = last / state.buffer.sampleRate;
  for (const [id, value] of [["start-time", state.start], ["end-time", state.end], ["start-range", state.start], ["end-range", state.end]]) {
    ui[id].max = state.buffer.duration;
    if (!preserveNumberInputs || !id.endsWith("time")) ui[id].value = value.toFixed(2);
  }
  updateControls();
  refreshQuality();
  drawWave();
}

function rebuildPeaks() {
  const width = ui.waveform.getBoundingClientRect().width;
  state.peaks = state.samples ? waveformPeaks(state.samples, Math.max(1, Math.floor((width - 36) / 4))) : [];
  drawWave();
}

function drawWave() {
  const canvas = ui.waveform;
  const { width, height } = canvas.getBoundingClientRect();
  if (!width || !height) return;
  const scale = Math.min(2, devicePixelRatio || 1);
  if (canvas.width !== Math.round(width * scale) || canvas.height !== Math.round(height * scale)) {
    canvas.width = Math.round(width * scale); canvas.height = Math.round(height * scale);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(scale, 0, 0, scale, 0, 0);
  ctx.clearRect(0, 0, width, height);
  const left = 18, span = width - 36, center = height / 2;
  ctx.strokeStyle = "#8fb49b14"; ctx.lineWidth = 1;
  for (let i = 0; i <= 6; i++) {
    const x = left + i * span / 6; ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, height); ctx.stroke();
  }
  ctx.strokeStyle = "#8fb49b23"; ctx.setLineDash([2, 5]); ctx.beginPath(); ctx.moveTo(left, center); ctx.lineTo(width - left, center); ctx.stroke(); ctx.setLineDash([]);
  const recording = recorder.phase === "recording";
  const peaks = recording && state.live ? waveformPeaks(state.live, Math.floor(span / 4)) : state.peaks;
  if (!peaks.length) return;
  const startX = state.buffer ? left + span * state.start / state.buffer.duration : left;
  const endX = state.buffer ? left + span * state.end / state.buffer.duration : width - left;
  if (!recording) { ctx.fillStyle = "#c4e5a012"; ctx.fillRect(startX, 4, endX - startX, height - 8); }
  const barWidth = span / peaks.length;
  peaks.forEach((peak, i) => {
    const x = left + i * barWidth;
    const barHeight = Math.max(2, Math.min(height - 26, peak * (height - 26) * 1.6));
    ctx.fillStyle = recording ? "#d4e9b8" : x >= startX && x <= endX ? "#c0e6a0" : "#466555";
    ctx.fillRect(x, center - barHeight / 2, Math.max(1, barWidth - 2), barHeight);
  });
  if (!recording && state.buffer) {
    for (const x of [startX, endX]) {
      ctx.fillStyle = "#cee9ad"; ctx.fillRect(x - 1, 5, 2, height - 10);
      ctx.fillRect(x - 4, center - 13, 8, 26);
      ctx.fillStyle = "#5b7752"; ctx.fillRect(x - 1, center - 6, 2, 12);
    }
    if (state.playhead !== null) {
      ctx.fillStyle = "#fff"; ctx.fillRect(left + span * state.playhead / state.buffer.duration, 2, 1, height - 4);
    }
  }
}

async function loadAudio(blob, { recorded = false, name = "가져온 음성" } = {}) {
  if (blob.size > MAX_FILE_BYTES) throw new Error("20 MB 이하의 짧은 음성 파일을 골라 주세요.");
  state.processing = true;
  stopPlayback();
  notify("소리를 불러오고 있어요…");
  updateControls();
  try {
    const audioContext = await getContext();
    const buffer = await audioContext.decodeAudioData(await blob.arrayBuffer());
    if (state.destroyed) return;
    if (!buffer.length || !Number.isFinite(buffer.duration) || buffer.duration > MAX_FILE_SECONDS) throw new Error("2분 이하의 음성 파일을 골라 주세요.");
    if (buffer.duration < MIN_REFERENCE_SECONDS) throw new Error("최소 3초 이상의 음성이 필요해요. 다시 녹음하거나 더 긴 음성 파일을 골라 주세요.");
    const samples = mixToMono(Array.from({ length: buffer.numberOfChannels }, (_, channel) => buffer.getChannelData(channel)));
    const mono = audioContext.createBuffer(1, samples.length, buffer.sampleRate);
    mono.copyToChannel(samples, 0);
    state.buffer = mono;
    state.samples = samples;
    state.live = null;
    if (recorded) {
      ui.transcript.value = recordingPrompt.text;
      ui["prompt-language"].value = recordingPrompt.language;
    } else {
      ui["prompt-preset"].value = "custom";
      ui.transcript.value = "";
    }
    ui["transcript-help"].textContent = "잘라낸 음성에서 실제로 말한 내용만 남겨 주세요. 문장이 잘렸다면 대사도 꼭 수정해 주세요.";
    ui["source-label"].textContent = recorded ? "YOUR VOICE, CAPTURED" : "IMPORTED AUDIO";
    ui["source-duration"].textContent = `${buffer.duration.toFixed(2)}초 · ${recorded ? "마이크 녹음" : name}`;
    ui["record-clock"].textContent = clockText(buffer.duration);
    ui["wave-hint"].textContent = "양끝을 움직여 구간을 조절하세요";
    setSelection(0, mono.duration);
    rebuildPeaks();
    notify(recorded ? "목소리를 담았어요. 들어 보고 3~10초 구간을 골라 주세요." : "음성을 불러왔어요. 구간을 고른 뒤 실제 대사와 녹음 언어를 확인해 주세요.", "success");
  } finally {
    state.processing = false;
    updateControls();
  }
}

const recorder = new MicrophoneRecorder({
  onState(phase) {
    if (phase === "idle") ui["record-footnote"].textContent = "최소 3초 이상 녹음해 주세요.";
    if (phase === "recording") {
      ui["record-clock"].textContent = clockText(0);
      ui["source-label"].textContent = "● RECORDING";
      ui["source-duration"].textContent = "30초가 되면 자동으로 마쳐요";
      ui["wave-hint"].textContent = "평소 말투로 편안하게 읽어 주세요";
    }
    updateControls(); drawWave();
  },
  onTick(seconds, samples) {
    state.live = samples;
    ui["record-clock"].textContent = clockText(seconds);
    updateRecordingControl();
    drawWave();
  },
  onData(blob) { loadAudio(blob, { recorded: true }).catch((error) => notify(explainError(error), "error")); },
  onError(error) { state.live = null; notify(explainError(error), "error"); updateControls(); drawWave(); },
});

ui["record-button"].addEventListener("click", () => {
  if (recorder.phase === "recording") { recorder.stop(); return; }
  stopPlayback();
  recordingPrompt = { text: ui.transcript.value, language: ui["prompt-language"].value };
  notify("");
  recorder.start();
});
ui["upload-button"].addEventListener("click", () => ui["file-input"].click());
ui["file-input"].addEventListener("change", async (event) => {
  const file = event.target.files[0];
  event.target.value = "";
  if (!file) return;
  try { await loadAudio(file, { name: file.name }); }
  catch (error) { notify(explainError(error), "error"); }
});

for (const id of ["start-time", "start-range"]) ui[id].addEventListener("input", () => {
  const value = Number.parseFloat(ui[id].value);
  if (Number.isFinite(value)) setSelection(Math.min(value, state.end - 0.01), state.end, id.endsWith("time"));
});
for (const id of ["end-time", "end-range"]) ui[id].addEventListener("input", () => {
  const value = Number.parseFloat(ui[id].value);
  if (Number.isFinite(value)) setSelection(state.start, Math.max(value, state.start + 0.01), id.endsWith("time"));
});
for (const id of ["start-time", "end-time"]) ui[id].addEventListener("change", () => setSelection(state.start, state.end));
let draggedEdge;
ui.waveform.addEventListener("pointerdown", (event) => {
  if (!state.buffer || recorder.phase !== "idle" || state.processing || state.exporting) return;
  const rect = ui.waveform.getBoundingClientRect();
  const time = Math.max(0, Math.min(1, (event.clientX - rect.left - 18) / (rect.width - 36))) * state.buffer.duration;
  draggedEdge = Math.abs(time - state.start) <= Math.abs(time - state.end) ? "start" : "end";
  ui.waveform.setPointerCapture(event.pointerId);
  dragSelection(event);
});
function dragSelection(event) {
  if (!draggedEdge) return;
  const rect = ui.waveform.getBoundingClientRect();
  const time = Math.max(0, Math.min(1, (event.clientX - rect.left - 18) / (rect.width - 36))) * state.buffer.duration;
  if (draggedEdge === "start") setSelection(Math.min(time, state.end - 0.01), state.end);
  else setSelection(state.start, Math.max(time, state.start + 0.01));
}
ui.waveform.addEventListener("pointermove", dragSelection);
ui.waveform.addEventListener("pointerup", () => { draggedEdge = null; });
ui.waveform.addEventListener("pointercancel", () => { draggedEdge = null; });
ui["trim-silence"].addEventListener("click", () => {
  const { first, last } = selection();
  const bounds = findSoundBounds(state.samples.subarray(first, last), state.buffer.sampleRate);
  if (!bounds) { notify("선택 구간에서 충분한 크기의 소리를 찾지 못했어요. 직접 구간을 조절해 주세요."); return; }
  setSelection(state.start + bounds.start, state.start + bounds.end);
  notify("양끝의 작은 소리를 줄였어요. 조용한 말소리가 잘리지 않았는지 꼭 들어 보세요.");
});
ui["play-button"].addEventListener("click", async () => {
  if (state.playback) { stopPlayback(); return; }
  try {
    const audioContext = await getContext();
    if (state.processing || state.exporting || recorder.phase !== "idle") return;
    const source = audioContext.createBufferSource();
    source.buffer = state.buffer;
    source.connect(audioContext.destination);
    const { first, duration } = selection();
    const offset = first / state.buffer.sampleRate;
    source.onended = stopPlayback;
    source.start(0, offset, duration);
    state.playback = source;
    const started = audioContext.currentTime;
    ui["play-icon"].textContent = "■"; ui["play-label"].textContent = "재생 멈추기";
    const animate = () => {
      if (!state.playback) return;
      state.playhead = Math.min(state.end, offset + audioContext.currentTime - started);
      drawWave(); frame = requestAnimationFrame(animate);
    };
    animate();
  } catch (error) { notify(explainError(error), "error"); }
});

function updatePrompt() {
  if (ui["prompt-preset"].value !== "custom") ui.transcript.value = PROMPTS[ui["prompt-language"].value][ui["prompt-preset"].value];
  updateControls();
}
ui["prompt-language"].addEventListener("change", updatePrompt);
ui["prompt-preset"].addEventListener("change", updatePrompt);
ui.transcript.addEventListener("input", () => { ui["prompt-preset"].value = "custom"; updateControls(); });

async function renderWav() {
  const { first, last, duration } = selection();
  const issues = referenceIssues(duration, ui.transcript.value);
  if (issues.length) throw new Error(issues[0]);
  const OfflineContext = globalThis.OfflineAudioContext ?? globalThis.webkitOfflineAudioContext;
  if (!OfflineContext) throw new Error("WAV 저장을 지원하지 않는 브라우저예요. 최신 브라우저에서 다시 시도해 주세요.");
  const offline = new OfflineContext(1, Math.round(duration * REFERENCE_RATE), REFERENCE_RATE);
  const clip = offline.createBuffer(1, last - first, state.buffer.sampleRate);
  clip.copyToChannel(state.samples.subarray(first, last), 0);
  const source = offline.createBufferSource();
  source.buffer = clip; source.connect(offline.destination); source.start();
  const rendered = await offline.startRendering();
  return encodeWav(rendered.getChannelData(0), REFERENCE_RATE);
}

function download(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url; link.download = filename;
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}

async function exportReference() {
  if (!state.buffer || state.exporting || state.processing || recorder.phase !== "idle") return;
  stopPlayback(); state.exporting = true; updateControls();
  try {
    const wav = await renderWav();
    const file = await createVoicePackage(wav, ui.transcript.value, ui["prompt-language"].value);
    download(file, "voice-reference.pgvoice");
    notify("저장할 파일을 준비했어요. 브라우저의 다운로드 목록을 확인해 주세요.", "success");
  } catch (error) { notify(explainError(error), "error"); }
  finally { state.exporting = false; updateControls(); }
}
ui["export-button"].addEventListener("click", () => exportReference());
new ResizeObserver(rebuildPeaks).observe(ui.waveform);
window.addEventListener("pagehide", () => {
  state.destroyed = true; stopPlayback(); recorder.cancel();
  context?.close().catch(() => {});
});
window.addEventListener("pageshow", () => { state.destroyed = false; });
updateControls();
if (!window.isSecureContext) notify("마이크 녹음에는 HTTPS 주소가 필요해요. 음성 파일 가져오기는 계속 사용할 수 있어요.");
