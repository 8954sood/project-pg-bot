import { MicrophoneRecorder } from "../../web/voice-reference/recorder.mjs?browser-check=1";
import { encodeWav } from "../../web/voice-reference/audio-utils.mjs";
import { createVoicePackage } from "../../web/voice-reference/voice-package.mjs";

const result = document.getElementById("result");
const button = document.getElementById("run");
button.addEventListener("click", async () => {
  button.disabled = true;
  document.getElementById("passed").hidden = true;
  const Context = globalThis.AudioContext ?? globalThis.webkitAudioContext;
  let producer;
  let decode;
  let recorder;
  let timer;
  let watchdog;
  let finished = false;
  const cleanup = async () => {
    clearTimeout(timer); clearTimeout(watchdog);
    recorder?.cancel(); await producer?.close(); await decode?.close();
    button.disabled = false;
  };
  const fail = async (error) => { finished = true; result.textContent = `FAIL: ${error.stack ?? error}`; await cleanup(); };
  try {
    producer = new Context();
    await producer.resume();
    const tone = producer.createOscillator();
    const gain = producer.createGain(); gain.gain.value = 0.1;
    const destination = producer.createMediaStreamDestination();
    tone.connect(gain).connect(destination); tone.start();
    recorder = new MicrophoneRecorder({
      onState(phase) { if (!finished) result.textContent = `Recording lifecycle: ${phase}`; },
      onError: fail,
      async onData(blob) {
        try {
          decode = new Context();
          const buffer = await decode.decodeAudioData(await blob.arrayBuffer());
          if (buffer.duration < 3 || buffer.duration > 10) throw new Error(`Unexpected decoded duration ${buffer.duration}`);
          const Offline = globalThis.OfflineAudioContext ?? globalThis.webkitOfflineAudioContext;
          const offline = new Offline(1, Math.round(buffer.duration * 32000), 32000);
          const source = offline.createBufferSource(); source.buffer = buffer; source.connect(offline.destination); source.start();
          const rendered = await offline.startRendering();
          const wav = encodeWav(rendered.getChannelData(0));
          const file = await createVoicePackage(wav, "Synthetic audio test.", "en");
          finished = true;
          result.textContent = JSON.stringify({ result: "PASS", mime: blob.type, duration: buffer.duration, outputRate: 32000, packageBytes: file.size, usedMicrophone: false }, null, 2);
          document.getElementById("passed").hidden = false;
          await cleanup();
        } catch (error) { await fail(error); }
      },
    }, { mediaDevices: { getUserMedia: async () => destination.stream } });
    await recorder.start();
    if (recorder.phase === "recording") timer = setTimeout(() => recorder.stop(), 3600);
    watchdog = setTimeout(() => fail(new Error("Audio check timed out")), 12000);
  } catch (error) { await fail(error); }
});
