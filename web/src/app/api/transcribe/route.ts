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
  const ext = audio.type.includes("mp4") ? "m4a" : audio.type.includes("ogg") ? "ogg" : "webm";
  const form = new FormData();
  form.append("file", audio, `speech.${ext}`);
  form.append("model", process.env.KIN_STT_MODEL || "whisper-large-v3-turbo");
  form.append("response_format", "text");
  form.append("language", process.env.KIN_STT_LANGUAGE || "en");

  const res = await fetch(`${base}/audio/transcriptions`, {
    method: "POST",
    headers: { Authorization: `Bearer ${key}` },
    body: form,
  });
  if (!res.ok) {
    return Response.json({ error: `transcription failed (${res.status}): ${await res.text()}` }, { status: 502 });
  }
  return Response.json({ text: (await res.text()).trim() });
}
