"use client";

import { useEffect, useRef, useState } from "react";

type Message = { role: "you" | "kin"; text: string };
type Status = "idle" | "listening" | "transcribing" | "thinking" | "speaking";

const STATUS_TEXT: Record<Status, string> = {
  idle: "Tap to talk",
  listening: "Listening… tap when you're done",
  transcribing: "Hearing you…",
  thinking: "Kin is thinking…",
  speaking: "Kin is speaking…",
};

const slug = (name: string) => name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

function pickVoice(): SpeechSynthesisVoice | undefined {
  const voices = speechSynthesis.getVoices();
  return (
    voices.find((v) => v.lang === "en-IN") ??
    voices.find((v) => v.lang === "en-GB") ??
    voices.find((v) => v.lang.startsWith("en"))
  );
}

export function Device({ people }: { people: { id: string; name: string }[] }) {
  const [selected, setSelected] = useState(people[0]?.id ?? "__new");
  const [newName, setNewName] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [speakReplies, setSpeakReplies] = useState(true);

  const recorder = useRef<MediaRecorder | null>(null);
  const sessions = useRef<Record<string, string>>({});
  const bottom = useRef<HTMLDivElement>(null);

  const personId = selected === "__new" ? slug(newName) : selected;

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [messages]);

  // A fresh conversation for each person; the agent keeps history per session.
  function sessionFor(id: string) {
    sessions.current[id] ??= crypto.randomUUID();
    return sessions.current[id];
  }

  function speak(text: string) {
    if (!speakReplies || !("speechSynthesis" in window)) return setStatus("idle");
    const u = new SpeechSynthesisUtterance(text);
    const voice = pickVoice();
    if (voice) u.voice = voice;
    u.rate = 0.95;
    u.onend = u.onerror = () => setStatus("idle");
    setStatus("speaking");
    speechSynthesis.speak(u);
  }

  async function send(text: string) {
    if (!text.trim()) return setStatus("idle");
    if (!personId) {
      setError("Type the new person's first name before talking.");
      return setStatus("idle");
    }
    setError(null);
    setMessages((m) => [...m, { role: "you", text }]);
    setStatus("thinking");
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ text, personId, sessionId: sessionFor(personId) }),
      });
      const data = (await res.json()) as { reply?: string; error?: string };
      if (!res.ok || !data.reply) throw new Error(data.error || "Kin didn't answer");
      setMessages((m) => [...m, { role: "kin", text: data.reply! }]);
      speak(data.reply);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStatus("idle");
    }
  }

  async function startListening() {
    setError(null);
    speechSynthesis?.cancel();
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const rec = new MediaRecorder(stream);
      const chunks: Blob[] = [];
      rec.ondataavailable = (e) => chunks.push(e.data);
      rec.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        setStatus("transcribing");
        try {
          const res = await fetch("/api/transcribe", {
            method: "POST",
            headers: { "content-type": rec.mimeType },
            body: new Blob(chunks, { type: rec.mimeType }),
          });
          const data = (await res.json()) as { text?: string; error?: string };
          if (!res.ok) throw new Error(data.error || "Couldn't hear that");
          await send(data.text ?? "");
        } catch (e) {
          setError(e instanceof Error ? e.message : String(e));
          setStatus("idle");
        }
      };
      rec.start();
      recorder.current = rec;
      setStatus("listening");
    } catch {
      setError("Microphone access was blocked. Allow it in the browser's address bar, or type instead.");
    }
  }

  function onMic() {
    if (status === "listening") recorder.current?.stop();
    else if (status === "idle" || status === "speaking") startListening();
  }

  const busy = status === "transcribing" || status === "thinking";

  return (
    <div className="grid gap-6 lg:grid-cols-[20rem_1fr]">
      <div className="flex flex-col items-center gap-6 rounded-3xl border border-line bg-card p-6">
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
            className="-mt-3 w-full rounded-xl border border-line bg-surface px-3 py-2 text-sm"
          />
        )}

        <button
          type="button"
          onClick={onMic}
          disabled={busy}
          aria-label={status === "listening" ? "Stop and send" : "Start talking"}
          className="relative grid size-40 place-items-center rounded-full bg-ink text-surface shadow-lg transition-transform active:scale-95 disabled:opacity-70"
        >
          <span
            className={`absolute inset-0 rounded-full border-4 ${
              status === "listening"
                ? "animate-ping border-accent"
                : status === "speaking"
                  ? "animate-pulse border-mood"
                  : busy
                    ? "animate-pulse border-ink-3"
                    : "border-transparent"
            }`}
          />
          <svg viewBox="0 0 24 24" className="size-12" fill="currentColor" aria-hidden>
            {status === "listening" ? (
              <rect x="6" y="6" width="12" height="12" rx="2" />
            ) : (
              <path d="M12 15a3 3 0 0 0 3-3V6a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-2.08A7 7 0 0 0 19 12h-2Z" />
            )}
          </svg>
        </button>
        <p className="text-center text-sm text-ink-2" aria-live="polite">
          {STATUS_TEXT[status]}
        </p>

        <label className="flex items-center gap-2 text-sm text-ink-2">
          <input type="checkbox" checked={speakReplies} onChange={(e) => setSpeakReplies(e.target.checked)} />
          Read replies aloud
        </label>
      </div>

      <div className="flex min-h-[28rem] flex-col rounded-3xl border border-line bg-card">
        <div className="flex-1 space-y-3 overflow-y-auto p-5">
          {messages.length === 0 && (
            <p className="text-sm text-ink-3">
              Try “Good morning, I&apos;m feeling a bit tired today” or “Let&apos;s talk about the old days.”
            </p>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`flex ${m.role === "you" ? "justify-end" : "justify-start"}`}>
              <p
                className={`max-w-[85%] rounded-2xl px-4 py-2.5 text-[15px] leading-relaxed ${
                  m.role === "you" ? "bg-accent-soft" : "border border-line bg-surface"
                }`}
              >
                {m.text}
              </p>
            </div>
          ))}
          {busy && <p className="text-sm text-ink-3">{STATUS_TEXT[status]}</p>}
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
            disabled={busy}
          />
          <button type="submit" disabled={busy || !draft.trim()} className="rounded-xl bg-ink px-4 py-2 text-surface disabled:opacity-50">
            Send
          </button>
        </form>
      </div>
    </div>
  );
}
