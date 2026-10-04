import { KinOffline, askKin } from "@/lib/kin";

export async function POST(request: Request) {
  const { text, personId, sessionId } = (await request.json()) as {
    text?: string;
    personId?: string;
    sessionId?: string;
  };
  if (!text?.trim() || !sessionId) {
    return Response.json({ error: "text and sessionId are required" }, { status: 400 });
  }
  try {
    const reply = await askKin(text.trim(), personId || "default", sessionId);
    return Response.json({ reply });
  } catch (e) {
    const status = e instanceof KinOffline ? 503 : 502;
    return Response.json({ error: e instanceof Error ? e.message : String(e) }, { status });
  }
}
