import { MIN_REFERENCE_SECONDS, MAX_RECORDING_SECONDS } from "./audio-utils.mjs";

export const RECORDING_MIME_TYPES = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4", "audio/webm"];

export class MicrophoneRecorder {
  constructor(callbacks = {}, dependencies = {}) {
    this.callbacks = callbacks;
    this.mediaDevices = dependencies.mediaDevices ?? globalThis.navigator?.mediaDevices;
    this.Recorder = dependencies.Recorder ?? globalThis.MediaRecorder;
    this.Context = dependencies.Context ?? globalThis.AudioContext ?? globalThis.webkitAudioContext;
    this.clock = dependencies.clock ?? (() => performance.now());
    this.setInterval = dependencies.setInterval ?? globalThis.setInterval.bind(globalThis);
    this.clearInterval = dependencies.clearInterval ?? globalThis.clearInterval.bind(globalThis);
    this.generation = 0;
    this.phase = "idle";
  }

  setPhase(phase) {
    this.phase = phase;
    this.callbacks.onState?.(phase);
  }

  get canStop() {
    return this.phase === "recording" && this.clock() - this.startedAt >= MIN_REFERENCE_SECONDS * 1000;
  }

  async start() {
    if (this.phase !== "idle") return;
    const generation = ++this.generation;
    this.error = null;
    this.stoppedAt = null;
    this.setPhase("requesting");
    try {
      if (!this.mediaDevices?.getUserMedia || !this.Recorder || !this.Context) {
        throw new Error("이 브라우저에서는 마이크 녹음을 지원하지 않아요. HTTPS 주소나 최신 Chrome·Safari에서 열거나 음성 파일을 가져와 주세요.");
      }
      const stream = await this.mediaDevices.getUserMedia({ audio: { channelCount: { ideal: 1 }, echoCancellation: false, noiseSuppression: false, autoGainControl: false }, video: false });
      if (generation !== this.generation) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      this.stream = stream;
      this.context = new this.Context();
      await this.context.resume();
      if (generation !== this.generation) return;
      this.analyser = this.context.createAnalyser();
      this.analyser.fftSize = 1024;
      this.context.createMediaStreamSource(stream).connect(this.analyser);
      const mimeType = RECORDING_MIME_TYPES.find((type) => this.Recorder.isTypeSupported?.(type));
      this.recorder = mimeType ? new this.Recorder(stream, { mimeType }) : new this.Recorder(stream);
      const recorder = this.recorder;
      const chunks = [];
      this.recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
      this.recorder.onerror = (event) => {
        if (generation !== this.generation) return;
        this.error = event.error ?? new Error("녹음 중 문제가 생겼어요. 다시 녹음해 주세요.");
        this.stop({ force: true });
      };
      this.recorder.onstop = () => {
        if (generation !== this.generation) return;
        const type = recorder.mimeType || chunks[0]?.type || "audio/webm";
        const duration = ((this.stoppedAt ?? this.clock()) - this.startedAt) / 1000;
        this.release();
        this.setPhase("idle");
        if (this.error) this.callbacks.onError?.(this.error);
        else if (!chunks.length) this.callbacks.onError?.(new Error("녹음된 소리가 없어요. 마이크를 확인하고 다시 녹음해 주세요."));
        else if (duration < MIN_REFERENCE_SECONDS) this.callbacks.onError?.(new Error("녹음이 너무 일찍 끝났어요. 최소 3초 이상 다시 녹음해 주세요."));
        else this.callbacks.onData?.(new Blob(chunks, { type }));
      };
      stream.getAudioTracks().forEach((track) => track.addEventListener("ended", () => this.stop({ force: true }), { once: true }));
      this.recorder.start(250);
      this.startedAt = this.clock();
      this.setPhase("recording");
      const samples = new Float32Array(this.analyser.fftSize);
      this.timer = this.setInterval(() => {
        if (generation !== this.generation) return;
        const seconds = (this.clock() - this.startedAt) / 1000;
        this.analyser.getFloatTimeDomainData(samples);
        this.callbacks.onTick?.(Math.min(seconds, MAX_RECORDING_SECONDS), samples);
        if (seconds >= MAX_RECORDING_SECONDS) this.stop();
      }, 80);
    } catch (error) {
      if (generation !== this.generation) return;
      this.release();
      this.setPhase("idle");
      this.callbacks.onError?.(error);
    }
  }

  stop({ force = false } = {}) {
    if (this.phase !== "recording") return;
    if (!force && !this.canStop) return;
    this.stoppedAt = this.clock();
    this.setPhase("stopping");
    try { this.recorder.stop(); }
    catch (error) {
      this.generation++;
      this.release();
      this.setPhase("idle");
      this.callbacks.onError?.(error);
    }
  }

  cancel() {
    this.generation++;
    try { if (this.recorder?.state === "recording") this.recorder.stop(); }
    catch { /* Release devices even if the recorder has already stopped. */ }
    this.release();
    this.setPhase("idle");
  }

  release() {
    if (this.timer !== undefined) this.clearInterval(this.timer);
    this.timer = undefined;
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    this.context?.close().catch(() => {});
    this.context = null;
  }
}
