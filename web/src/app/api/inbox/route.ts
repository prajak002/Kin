import { KinOffline, familyMessages } from "@/lib/kin";

// The talking device collects new family messages; each is returned once.
export async function GET(request: Request) {
  const personId = new URL(request.url).searchParams.get("personId");
  if (!personId) return Response.json({ error: "personId is required" }, { status: 400 });
  try {
    return Response.json(await familyMessages(personId, true));
  } catch (e) {
    const status = e instanceof KinOffline ? 503 : 502;
    return Response.json({ error: e instanceof Error ? e.message : String(e) }, { status });
  }
}
