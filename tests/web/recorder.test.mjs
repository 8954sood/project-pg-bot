import assert from "node:assert/strict";
import test from "node:test";
import { MicrophoneRecorder } from "../../web/voice-reference/recorder.mjs";

function harness() {
  const tracks = [];
  const contexts = [];
  const recorders = [];
  const callbacks = { states: [], data: [], errors: [], ticks: [] };
  let now = 0;
  let tick;
  let requested = 0;
  const makeStream = () => {
    const track = { stopped: 0, stop() { this.stopped++; }, addEventListener(name, callback) { this[name] = callback; } };
    tracks.push(track);
    return { getTracks: () => [track], getAudioTracks: () => [track] };
  };
  class Recorder {
    static isTypeSupported(type) { return type === "audio/ogg;codecs=opus"; }
    constructor(stream, options) { this.stream = stream; this.mimeType = options?.mimeType ?? "audio/mp4"; this.state = "inactive"; recorders.push(this); }
    start() { this.state = "recording"; }
    stop() { this.state = "inactive"; this.stopCalls = (this.stopCalls ?? 0) + 1; }
    complete() { this.ondataavailable({ data: new Blob(["audio"], { type: this.mimeType }) }); this.onstop(); }
  }
  class Context {
    constructor() { this.closed = 0; contexts.push(this); }
    async resume() {}
    createAnalyser() { return { fftSize: 1024, getFloatTimeDomainData(samples) { samples.fill(0.1); } }; }
    createMediaStreamSource() { return { connect() {} }; }
    async close() { this.closed++; }
  }
  const dependencies = {
    mediaDevices: { async getUserMedia() { requested++; return makeStream(); } }, Recorder, Context,
    clock: () => now, setInterval: (callback) => { tick = callback; return 1; }, clearInterval: () => { tick = null; },
  };
  const recorder = new MicrophoneRecorder({
    onState: (value) => callbacks.states.push(value), onData: (value) => callbacks.data.push(value),
    onError: (value) => callbacks.errors.push(value), onTick: (value) => callbacks.ticks.push(value),
  }, dependencies);
  return { recorder, dependencies, tracks, contexts, recorders, callbacks, makeStream,
    get requested() { return requested; }, tick(seconds) { now = seconds * 1000; tick?.(); } };
}

test("recording chooses a supported MIME and releases the microphone and audio context after stop", async () => {
  const h = harness();
  await h.recorder.start();
  assert.equal(h.recorders[0].mimeType, "audio/ogg;codecs=opus");
  assert.equal(h.recorder.phase, "recording");
  h.tick(4.2); h.recorder.stop(); h.recorders[0].complete();
  assert.deepEqual(h.callbacks.states, ["requesting", "recording", "stopping", "idle"]);
  assert.equal(h.callbacks.data[0].size, 5);
  assert.equal(h.callbacks.data[0].type, "audio/ogg;codecs=opus");
  assert.equal(h.tracks[0].stopped, 1); assert.equal(h.contexts[0].closed, 1);
});

test("duplicate start cannot request two microphone sessions", async () => {
  const h = harness();
  await Promise.all([h.recorder.start(), h.recorder.start()]);
  assert.equal(h.requested, 1);
  h.recorder.cancel();
});

test("permission denial returns to idle without creating a context", async () => {
  const h = harness();
  h.recorder.mediaDevices = { getUserMedia: async () => { throw new Error("denied"); } };
  await h.recorder.start();
  assert.equal(h.recorder.phase, "idle"); assert.equal(h.contexts.length, 0);
  assert.equal(h.callbacks.errors[0].message, "denied");
});

test("recording automatically stops at 30 seconds and ignores duplicate stops", async () => {
  const h = harness();
  await h.recorder.start(); h.tick(31); h.recorder.stop();
  assert.equal(h.recorders[0].stopCalls, 1);
  assert.deepEqual(h.callbacks.ticks, [30]);
  h.recorders[0].complete();
});

test("cancelling a pending permission request stops the late microphone stream", async () => {
  const h = harness();
  let grant;
  h.recorder.mediaDevices = { getUserMedia: () => new Promise((resolve) => { grant = resolve; }) };
  const pending = h.recorder.start(); h.recorder.cancel(); grant(h.makeStream()); await pending;
  assert.equal(h.tracks[0].stopped, 1); assert.equal(h.contexts.length, 0);
  assert.equal(h.callbacks.data.length, 0);
});

test("a stale stop callback cannot close a newer recording session", async () => {
  const h = harness();
  await h.recorder.start(); h.recorder.cancel();
  await h.recorder.start(); h.recorders[0].complete();
  assert.equal(h.recorder.phase, "recording");
  assert.equal(h.tracks[1].stopped, 0); assert.equal(h.contexts[1].closed, 0);
  assert.equal(h.callbacks.data.length, 0);
  h.recorder.cancel();
});

test("device disconnect stops recording, and recorder errors are reported after cleanup", async () => {
  const h = harness();
  await h.recorder.start(); h.tracks[0].ended();
  assert.equal(h.recorder.phase, "stopping");
  h.recorders[0].onerror({ error: new Error("hardware error") }); h.recorders[0].complete();
  assert.equal(h.recorder.phase, "idle"); assert.equal(h.callbacks.data.length, 0);
  assert.equal(h.callbacks.errors[0].message, "hardware error"); assert.equal(h.tracks[0].stopped, 1);
});

test("a browser without optional MIME detection still records in its default format", async () => {
  const h = harness(); h.dependencies.Recorder.isTypeSupported = undefined;
  await h.recorder.start(); h.recorder.stop(); h.recorders[0].complete();
  assert.equal(h.callbacks.data[0].type, "audio/mp4");
});

test("empty recording reports an error and frees devices", async () => {
  const h = harness(); await h.recorder.start(); h.recorder.stop(); h.recorders[0].onstop();
  assert.equal(h.callbacks.data.length, 0); assert.equal(h.callbacks.errors.length, 1);
  assert.equal(h.tracks[0].stopped, 1);
});

test("native browser timer methods keep their required window binding", async (t) => {
  let checked = 0;
  t.mock.method(globalThis, "setInterval", function () { assert.equal(this, globalThis); checked++; return 1; });
  t.mock.method(globalThis, "clearInterval", function () { assert.equal(this, globalThis); checked++; });
  const h = harness();
  const recorder = new MicrophoneRecorder({}, { ...h.dependencies, setInterval: undefined, clearInterval: undefined });
  await recorder.start(); recorder.stop(); h.recorders[0].complete();
  assert.equal(checked, 2);
});
