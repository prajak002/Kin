"use client";

import { useEffect, useRef, useState } from "react";

import { speak, startHandsFree, stopSpeaking } from "./speech";

type Message = { id: string; role: "you" | "kin"; text: string; meta?: string };
type Status = "idle" | "listening" | "transcribing" | "thinking" | "speaking";
type Trace = { latency_ms?: number; tools?: { name: string }[]; guardrails?: { rule: string; action: string }[] };

const STATUS_TEXT: Record<Status, string> = {
  idle: "Tap to talk",
  listening: "Listening…",
  transcribing: "Hearing you…",
  thinking: "Kin is thinking…",
  speaking: "Kin is speaking… (talk to interrupt)",
};

const slug = (name: string) => name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

function describe(trace: Trace | undefined, voice: string): string {
  if (!trace) return "";
  const parts = [`${((trace.latency_ms ?? 0) / 1000).toFixed(1)} s`];
  if (trace.tools?.length) parts.push(trace.tools.map((t) => t.name).join(", "));
  const stepped = trace.guardrails?.filter((g) => g.action !== "model_already_alerted") ?? [];
  if (stepped.length) parts.push(`guardrail: ${stepped.map((g) => g.rule.replace("_", " ")).join(", ")}`);
  parts.push(voice === "kokoro" ? "Kokoro voice" : "device voice");
  return parts.join(" · ");
}

export function Device({ people }: { people: { id: string; name: string }[] }) {
  const [selected, setSelected] = useState(people[0]?.id ?? "__new");
  const [newName, setNewName] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [speakReplies, setSpeakReplies] = useState(true);
  const [natural, setNatural] = useState(false);
  const [handsFree, setHandsFree] = useState(false);
  const [loading, setLoading] = useState<string | null>(null);

  const recorder = useRef<MediaRecorder | null>(null);
  const vad = useRef<{ destroy(): void } | null>(null);
  const busy = useRef(false);
  const starting = useRef(false);
  const sessions = useRef<Record<string, string>>({});
  const bottom = useRef<HTMLDivElement>(null);
  const personId = selected === "__new" ? slug(newName) : selected;
  // Hands-free callbacks outlive renders, so they read current settings from refs.
  const settings = useRef({ speakReplies, natural });
  const personRef = useRef(personId);
  useEffect(() => {
    settings.current = { speakReplies, natural };
    personRef.current = personId;
  }, [speakReplies, natural, personId]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [messages]);

  useEffect(() => () => vad.current?.destroy(), []);

  function sessionFor(id: string) {
    sessions.current[id] ??= crypto.randomUUID();
    return sessions.current[id];
  }

  async function send(text: string, language?: string | null) {
    const id = personRef.current;
    if (!text.trim()) {
      busy.current = false;
      return setStatus(vad.current ? "listening" : "idle");
    }
    if (!id) {
      busy.current = false;
      setError("Type the new person's first name before talking.");
      return setStatus("idle");
    }
    busy.current = true;
    setError(null);
    setMessages((m) => [...m, { id: crypto.randomUUID(), role: "you", text, meta: language && language !== "english" ? language : undefined }]);
    setStatus("thinking");
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ text, personId: id, sessionId: sessionFor(id) }),
      });
      const data = (await res.json()) as { reply?: string; trace?: Trace; error?: string };
      if (!res.ok || !data.reply) throw new Error(data.error || "Kin didn't answer");
      const replyId = crypto.randomUUID();
      setMessages((m) => [...m, { id: replyId, role: "kin", text: data.reply! }]);
      busy.current = false;
      if (settings.current.speakReplies) {
        setStatus("speaking");
        const voice = await speak(data.reply, settings.current.natural);
        setMessages((m) => m.map((msg) => (msg.id === replyId ? { ...msg, meta: describe(data.trace, voice) } : msg)));
      } else {
        setMessages((m) => m.map((msg) => (msg.id === replyId ? { ...msg, meta: describe(data.trace, "none") } : msg)));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      busy.current = false;
      setStatus((s) => (s === "speaking" || s === "thinking" ? (vad.current ? "listening" : "idle") : s));
    }
  }

  async function transcribeAndSend(audio: Blob) {
    busy.current = true; // claim the turn before transcribing, so a second utterance can't slip in
    setStatus("transcribing");
    try {
      const res = await fetch("/api/transcribe", { method: "POST", headers: { "content-type": audio.type }, body: audio });
      const data = (await res.json()) as { text?: string; language?: string | null; error?: string };
      if (!res.ok) throw new Error(data.error || "Couldn't hear that");
      await send(data.text ?? "", data.language);
    } catch (e) {
      busy.current = false;
      setError(e instanceof Error ? e.message : String(e));
      setStatus(vad.current ? "listening" : "idle");
    }
  }

  async function toggleHandsFree(on: boolean) {
    if (starting.current) return; // a second click while loading would start a second detector
    setError(null);
    vad.current?.destroy();
    vad.current = null;
    if (!on) {
      setHandsFree(false);
      return setStatus("idle");
    }
    starting.current = true;
    setLoading("Loading the open-source voice detector (Silero VAD)…");
    try {
      vad.current = await startHandsFree({
        onSpeechStart: () => stopSpeaking(), // barge-in: talking interrupts Kin
        onUtterance: (wav) => {
          if (!busy.current) transcribeAndSend(wav);
        },
      });
      setHandsFree(true);
      setStatus("listening");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't start hands-free mode. Allow the microphone and try again.");
    } finally {
      starting.current = false;
      setLoading(null);
    }
  }

  async function tapToTalk() {
    if (status === "listening" && recorder.current) return recorder.current.stop();
    stopSpeaking();
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const rec = new MediaRecorder(stream);
      const chunks: Blob[] = [];
      rec.ondataavailable = (e) => chunks.push(e.data);
      rec.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        recorder.current = null;
        transcribeAndSend(new Blob(chunks, { type: rec.mimeType }));
      };
      rec.start();
      recorder.current = rec;
      setStatus("listening");
    } catch {
      setError("Microphone access was blocked. Allow it in the browser's address bar, or type instead.");
    }
  }

  const thinking = status === "transcribing" || status === "thinking";

  return (
    <div className="grid gap-6 lg:grid-cols-[20rem_1fr]">
      <div className="flex flex-col items-center gap-5 rounded-3xl border border-line bg-card p-6">
        <label className="w-full text-sm">
          <span className="text-ink-3">Talking as</span>
          <select
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
            className="mt-1 w-full rounded-xl border border-line bg-surface px-3 py-2"
          >
            {people.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
            <option value="__new">Someone new…</option>
          </select>
        </label>
        {selected === "__new" && (
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="First name"
            className="-mt-2 w-full rounded-xl border border-line bg-surface px-3 py-2 text-sm"
          />
        )}

        <button
          type="button"
          onClick={handsFree ? () => stopSpeaking() : tapToTalk}
          disabled={thinking || (handsFree && status !== "speaking")}
          aria-label={handsFree ? "Hands-free is on" : status === "listening" ? "Stop and send" : "Start talking"}
          className="relative grid size-40 place-items-center rounded-full bg-ink text-surface shadow-lg transition-transform active:scale-95 disabled:opacity-80"
        >
          <span
            className={`absolute inset-0 rounded-full border-4 ${
              status === "listening"
                ? "animate-pulse border-accent"
                : status === "speaking"
                  ? "animate-pulse border-mood"
                  : thinking
                    ? "animate-pulse border-ink-3"
                    : "border-transparent"
            }`}
          />
          <svg viewBox="0 0 24 24" className="size-12" fill="currentColor" aria-hidden>
            {status === "listening" && !handsFree ? (
              <rect x="6" y="6" width="12" height="12" rx="2" />
            ) : (
              <path d="M12 15a3 3 0 0 0 3-3V6a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-2.08A7 7 0 0 0 19 12h-2Z" />
            )}
          </svg>
        </button>
        <p className="min-h-5 text-center text-sm text-ink-2" aria-live="polite">
          {loading ?? (handsFree && status === "listening" ? "Listening. Just start talking." : STATUS_TEXT[status])}
        </p>

        <div className="w-full space-y-2.5 border-t border-line pt-4 text-sm text-ink-2">
          <label className="flex items-start gap-2">
            <input type="checkbox" className="mt-1" checked={handsFree} disabled={loading !== null} onChange={(e) => toggleHandsFree(e.target.checked)} />
            <span>
              Hands-free
              <span className="block text-xs text-ink-3">Silero VAD (open source) hears when you start and stop.</span>
            </span>
          </label>
          <label className="flex items-start gap-2">
            <input type="checkbox" className="mt-1" checked={speakReplies} onChange={(e) => setSpeakReplies(e.target.checked)} />
            Read replies aloud
          </label>
          <label className="flex items-start gap-2">
            <input
              type="checkbox"
              className="mt-1"
              checked={natural}
              disabled={!speakReplies}
              onChange={(e) => setNatural(e.target.checked)}
            />
            <span>
              Natural voice
              <span className="block text-xs text-ink-3">
                Kokoro-82M, an open model, runs in your browser (about 90 MB once; fastest with WebGPU). Hindi and Bengali
                use your device&apos;s voice.
              </span>
            </span>
          </label>
        </div>
      </div>

      <div className="flex min-h-[28rem] flex-col rounded-3xl border border-line bg-card">
        <div className="flex-1 space-y-3 overflow-y-auto p-5">
          {messages.length === 0 && (
            <p className="text-sm text-ink-3">
              Try “Good morning, I&apos;m feeling a bit tired today”, “Let&apos;s talk about the old days”, or speak in Hindi or
              Bengali.
            </p>
          )}
          {messages.map((m) => (
            <div key={m.id} className={`flex flex-col ${m.role === "you" ? "items-end" : "items-start"}`}>
              <p
                className={`max-w-[85%] rounded-2xl px-4 py-2.5 text-[15px] leading-relaxed ${
                  m.role === "you" ? "bg-accent-soft" : "border border-line bg-surface"
                }`}
              >
                {m.text}
              </p>
              {m.meta && <span className="mt-1 px-1 text-[11px] text-ink-3">{m.meta}</span>}
            </div>
          ))}
          {thinking && <p className="text-sm text-ink-3">{STATUS_TEXT[status]}</p>}
          <div ref={bottom} />
        </div>
        {error && <p className="mx-5 mb-3 rounded-xl bg-accent-soft px-3 py-2 text-sm text-ink">{error}</p>}
        <form
          className="flex gap-2 border-t border-line p-3"
          onSubmit={(e) => {
            e.preventDefault();
            const text = draft;
            setDraft("");
            send(text);
          }}
        >
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Or type to Kin…"
            className="flex-1 rounded-xl border border-line bg-surface px-3 py-2"
            disabled={thinking}
          />
          <button type="submit" disabled={thinking || !draft.trim()} className="rounded-xl bg-ink px-4 py-2 text-surface disabled:opacity-50">
            Send
          </button>
        </form>
      </div>
    </div>
  );
}
