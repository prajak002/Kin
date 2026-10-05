"use client";

// Open-source speech models that run in the visitor's browser (free to serve):
//   - Kokoro-82M (Apache-2.0, via Transformers.js): natural English voice
//   - Silero VAD v5 (MIT, via ONNX Runtime Web): hears when someone starts and
//     stops talking, for hands-free turns and interrupting Kin mid-sentence
// Both load from jsDelivr on first use and are cached by the browser.

const KOKORO_MODEL = "onnx-community/Kokoro-82M-v1.0-ONNX";
const ORT_URL = "https://cdn.jsdelivr.net/npm/onnxruntime-web@1.22.0/dist/";
const VAD_URL = "https://cdn.jsdelivr.net/npm/@ricky0123/vad-web@0.0.31/dist/";

type KokoroAudio = { toBlob(): Blob };
type Kokoro = { generate(text: string, opts: { voice: string }): Promise<KokoroAudio> };
type MicVAD = { start(): void; pause(): void; destroy(): void };

let kokoro: Promise<Kokoro> | null = null;
let playing: { stop(): void } | null = null;

export function loadKokoro(): Promise<Kokoro> {
  // @ts-expect-error -- an ES module served by the CDN at runtime, not an npm dependency
  kokoro ??= import(/* webpackIgnore: true */ /* turbopackIgnore: true */ "https://cdn.jsdelivr.net/npm/kokoro-js@1.2.1/+esm").then(
    (mod: { KokoroTTS: { from_pretrained(id: string, o: object): Promise<Kokoro> } }) =>
      // WebGPU is much faster where available; WebAssembly works everywhere.
      "gpu" in navigator
        ? mod.KokoroTTS.from_pretrained(KOKORO_MODEL, { dtype: "fp32", device: "webgpu" })
        : mod.KokoroTTS.from_pretrained(KOKORO_MODEL, { dtype: "q8", device: "wasm" }),
  );
  kokoro.catch(() => (kokoro = null)); // allow a retry after a failed download
  return kokoro;
}

export function stopSpeaking() {
  playing?.stop();
  playing = null;
  if ("speechSynthesis" in window) speechSynthesis.cancel();
}

// The language a reply is written in, from its script. "other" is a script with
// no voice mapping here; null is Latin (English, or romanised Hindi).
const SCRIPTS: [RegExp, string][] = [
  [/[\u0900-\u097F]/, "hi"],
  [/[\u0980-\u09FF]/, "bn"],
  [/[\u0600-\u06FF]/, "ur"],
  [/[\u0A00-\u0A7F]/, "pa"],
  [/[\u0A80-\u0AFF]/, "gu"],
  [/[\u0B00-\u0B7F]/, "or"],
  [/[\u0B80-\u0BFF]/, "ta"],
  [/[\u0C00-\u0C7F]/, "te"],
  [/[\u0C80-\u0CFF]/, "kn"],
  [/[\u0D00-\u0D7F]/, "ml"],
];

function scriptLang(text: string): string | null {
  for (const [script, lang] of SCRIPTS) if (script.test(text)) return lang;
  // Any other non-Latin letter: an English voice would read only the punctuation ("question mark").
  return /[^\p{Script=Latin}\P{L}]/u.test(text) ? "other" : null;
}

// Chrome fills the voice list after the page loads; asking too early gets none.
function loadVoices(): Promise<SpeechSynthesisVoice[]> {
  const now = speechSynthesis.getVoices();
  if (now.length) return Promise.resolve(now);
  return new Promise((resolve) => {
    const done = () => resolve(speechSynthesis.getVoices());
    speechSynthesis.addEventListener("voiceschanged", done, { once: true });
    setTimeout(done, 1500);
  });
}

/** Speak with the device's voice for the reply's language. Returns false, without
 *  speaking, if the device has no voice for it. */
async function deviceVoice(text: string, lang: string | null): Promise<boolean> {
  const voices = await loadVoices();
  const want = lang ? [`${lang}-IN`, lang] : ["en-IN", "en-GB", "en"];
  const voice = want.map((w) => voices.find((v) => v.lang.replace("_", "-").startsWith(w))).find(Boolean);
  if (lang && !voice) return false;
  return new Promise((resolve) => {
    const u = new SpeechSynthesisUtterance(text);
    if (voice) u.voice = voice;
    u.lang = voice?.lang ?? "en-IN";
    u.rate = 0.95;
    u.onend = u.onerror = () => resolve(true);
    playing = { stop: () => speechSynthesis.cancel() };
    speechSynthesis.speak(u);
  });
}

/** Speak a reply. Kokoro for English when chosen; Kin's own voice (edge-tts on the
 *  server) for Hindi, Bengali and other Indian languages; the device's voice otherwise,
 *  or if those fail. Resolves when playback
 *  ends; "none" when the device has no voice for the reply's language. */
export async function speak(text: string, natural: boolean): Promise<"kokoro" | "kin" | "device" | "none"> {
  stopSpeaking();
  const lang = scriptLang(text);
  if (natural && !lang) {
    try {
      const tts = await loadKokoro();
      // Sentence by sentence: the first plays while the next is generated.
      const sentences = text.match(/[^.!?]+[.!?]*\s*/g) ?? [text];
      let stopped = false;
      let next = tts.generate(sentences[0], { voice: "bf_emma" });
      for (let i = 0; i < sentences.length && !stopped; i++) {
        const clip = await next;
        if (i + 1 < sentences.length) next = tts.generate(sentences[i + 1], { voice: "bf_emma" });
        const url = URL.createObjectURL(clip.toBlob());
        const audio = new Audio(url);
        await new Promise<void>((resolve) => {
          audio.onended = audio.onerror = () => resolve();
          playing = { stop: () => { stopped = true; audio.pause(); resolve(); } };
          audio.play().catch(() => resolve());
        });
        URL.revokeObjectURL(url);
      }
      return "kokoro";
    } catch {
      // fall through to the device voice
    }
  }
  if (lang === "other") return "none";
  // Indian languages: Kin's own voice only. Device voices proved unreliable for them
  // (Chrome hands the text to an English voice that reads just "comma, question mark").
  if (lang) return (await serverVoice(text, lang)) ? "kin" : "none";
  return (await deviceVoice(text, lang)) ? "device" : "none";
}

/** Fetch the reply as audio from Kin and play it. False if the voice service failed. */
async function serverVoice(text: string, lang: string): Promise<boolean> {
  try {
    const res = await fetch("/api/speak", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text, lang }),
    });
    if (!res.ok) return false;
    const url = URL.createObjectURL(await res.blob());
    await playClip(url);
    URL.revokeObjectURL(url);
    return true;
  } catch {
    return false;
  }
}

/** Play a recording (a family voice note). Talking interrupts it, like Kin's voice. */
export function playClip(url: string): Promise<void> {
  stopSpeaking();
  return new Promise((resolve) => {
    const audio = new Audio(url);
    audio.onended = audio.onerror = () => resolve();
    playing = { stop: () => { audio.pause(); resolve(); } };
    audio.play().catch(() => resolve()); // autoplay blocked: the card still has a play button
  });
}

function loadScript(src: string): Promise<void> {
  if (document.querySelector(`script[src="${src}"]`)) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const el = document.createElement("script");
    el.src = src;
    el.onload = () => resolve();
    el.onerror = () => reject(new Error(`Couldn't load ${src}`));
    document.head.appendChild(el);
  });
}

/** Start hands-free listening. onUtterance gets each finished utterance as WAV. */
export async function startHandsFree(handlers: {
  onSpeechStart: () => void;
  onUtterance: (wav: Blob) => void;
}): Promise<MicVAD> {
  await loadScript(`${ORT_URL}ort.wasm.min.js`);
  await loadScript(`${VAD_URL}bundle.min.js`);
  const vad = (window as unknown as {
    vad: {
      MicVAD: { "new": (o: object) => Promise<MicVAD> };
      utils: { encodeWAV(audio: Float32Array): ArrayBuffer };
    };
  }).vad;
  const mic = await vad.MicVAD.new({
    model: "v5",
    baseAssetPath: VAD_URL,
    onnxWASMBasePath: ORT_URL,
    onSpeechStart: handlers.onSpeechStart,
    onSpeechEnd: (audio: Float32Array) => handlers.onUtterance(new Blob([vad.utils.encodeWAV(audio)], { type: "audio/wav" })),
  });
  mic.start();
  return mic;
}
