import { KinOffline, medicationReminders } from "@/lib/kin";

// The talking device asks about once a minute which doses are due.
export async function GET(request: Request) {
  const personId = new URL(request.url).searchParams.get("personId");
  if (!personId) return Response.json({ error: "personId is required" }, { status: 400 });
  try {
    return Response.json(await medicationReminders(personId, true));
  } catch (e) {
    const status = e instanceof KinOffline ? 503 : 502;
    return Response.json({ error: e instanceof Error ? e.message : String(e) }, { status });
  }
}
