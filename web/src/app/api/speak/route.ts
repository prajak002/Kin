import { KinOffline, speechAudio } from "@/lib/kin";

// Replies in Indian languages, spoken by Kin's backend (the device may have no voice for them).
export async function POST(request: Request) {
  const { text, lang } = (await request.json()) as { text?: string; lang?: string };
  if (!text?.trim() || !lang) return Response.json({ error: "text and lang are required" }, { status: 400 });
  try {
    const res = await speechAudio(text, lang);
    if (!res.ok) return new Response(await res.text(), { status: res.status });
    return new Response(res.body, { headers: { "content-type": "audio/mpeg", "cache-control": "no-store" } });
  } catch (e) {
    const status = e instanceof KinOffline ? 503 : 502;
    return Response.json({ error: e instanceof Error ? e.message : String(e) }, { status });
  }
}
