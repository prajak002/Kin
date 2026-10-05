import "@/lib/env";

// Speech to text with Whisper on the same OpenAI-compatible host the agent uses
// (Groq's free tier by default). The key stays on the server.
export async function POST(request: Request) {
  const key = process.env.KIN_OPENAI_API_KEY;
  if (!key) {
    return Response.json({ error: "KIN_OPENAI_API_KEY is not set in the project's .env" }, { status: 500 });
  }
  const base = (process.env.KIN_OPENAI_BASE_URL || "https://api.groq.com/openai/v1").replace(/\/$/, "");

  const audio = await request.blob();
  const ext = audio.type.includes("mp4") ? "m4a" : audio.type.includes("ogg") ? "ogg" : audio.type.includes("wav") ? "wav" : "webm";
  const params = new URL(request.url).searchParams;
  const code = (v: string | null) => (v && /^[a-z]{2}$/.test(v) ? v : null);

  async function whisper(language: string | null | undefined) {
    const form = new FormData();
    form.append("file", audio, `speech.${ext}`);
    form.append("model", process.env.KIN_STT_MODEL || "whisper-large-v3-turbo");
    // verbose_json includes the detected language.
    form.append("response_format", "verbose_json");
    if (language) form.append("language", language);
    const res = await fetch(`${base}/audio/transcriptions`, {
      method: "POST",
      headers: { Authorization: `Bearer ${key}` },
      body: form,
    });
    if (!res.ok) throw new Error(`transcription failed (${res.status}): ${await res.text()}`);
    return (await res.json()) as { text: string; language?: string };
  }

  try {
    // The talk page passes the language the person speaks; auto-detection often hears
    // Bengali as Hindi, or Hindi as Urdu (written in a script Kin can't speak back).
    const chosen = code(params.get("language")) ?? process.env.KIN_STT_LANGUAGE;
    let data = await whisper(chosen);
    const expected = code(params.get("expect"));
    if (!chosen && expected && !["english", "hindi", "bengali"].includes((data.language ?? "").toLowerCase())) {
      data = await whisper(expected); // heard something unlikely: try their usual language
    }
    return Response.json({ text: data.text.trim(), language: data.language ?? null });
  } catch (e) {
    return Response.json({ error: e instanceof Error ? e.message : String(e) }, { status: 502 });
  }
}
