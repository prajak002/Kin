import type { NextRequest } from "next/server";

import { voiceNote } from "@/lib/kin";

// A family voice note's audio, passed through from the backend (which needs the API token).
export async function GET(_req: NextRequest, ctx: RouteContext<"/api/voice-notes/[id]">) {
  const { id } = await ctx.params;
  const res = await voiceNote(id);
  if (!res.ok) return new Response(null, { status: res.status });
  return new Response(res.body, {
    headers: { "content-type": res.headers.get("content-type") ?? "audio/ogg", "cache-control": "private, max-age=86400" },
  });
}
