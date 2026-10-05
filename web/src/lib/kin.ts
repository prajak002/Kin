import "server-only";
import "./env";

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

// The dashboard reads Kin's data through the same MCP server Alexa+ uses.
// On Vercel, KIN_BACKEND_URL is the Python service's private binding.
const backend = (path: string) => process.env.KIN_BACKEND_URL && new URL(path, process.env.KIN_BACKEND_URL).toString();
const MCP_URL = backend("/mcp") || process.env.KIN_MCP_URL || "http://127.0.0.1:8000/mcp";
const AGENT_URL = backend("/invocations") || process.env.KIN_AGENT_URL || "http://127.0.0.1:8000/invocations";
const TRACES_URL = backend("/traces") || "http://127.0.0.1:8000/traces";

export type Contact = { name: string; relation: string; channel: "whatsapp" | "telegram" | "ntfy"; address: string };

export type Person = {
  id: string;
  name: string;
  birth_year: number;
  hometown: string;
  language?: string | null;
  favourites: string[];
  medications: string[];
  family: Contact[];
  schedule?: { medication: string; times: string[] }[];
  timezone?: string;
};

export type DoseSlot = { medication: string; time: string; status: "taken" | "missed" | "waiting" | "upcoming" };
export type Reminders = { today: DoseSlot[]; due: { medication: string; time: string }[]; escalated: unknown[] };

export type Checkin = { at: string; mood: number; notes: string };
export type Dose = { at: string; medication: string; taken: boolean };
export type Alert = { at: string; level: "info" | "warning" | "urgent"; reason: string };
export type Moment = { at: string; topic: string; items: string[] };

export type Summary = {
  name: string;
  average_mood: number | null;
  checkins: Checkin[];
  doses: Dose[];
  missed_doses: Dose[];
  alerts: Alert[];
  moments: Moment[];
};

export class KinOffline extends Error {}

// Shared secret for the Python backend's /mcp and /invocations (unset locally).
const authHeaders = (): Record<string, string> =>
  process.env.KIN_API_TOKEN ? { Authorization: `Bearer ${process.env.KIN_API_TOKEN}` } : {};

async function callTool<T>(name: string, args: Record<string, unknown> = {}): Promise<T> {
  const client = new Client({ name: "kin-web", version: "0.1.0" });
  try {
    await client.connect(new StreamableHTTPClientTransport(new URL(MCP_URL), { requestInit: { headers: authHeaders() } }));
  } catch (e) {
    throw new KinOffline(`Kin's MCP server is not reachable at ${MCP_URL}`, { cause: e });
  }
  try {
    const result = await client.callTool({ name, arguments: args });
    const text = (result.content as { type: string; text?: string }[]).find((c) => c.type === "text")?.text ?? "";
    if (result.isError) throw new Error(text || `${name} failed`);
    // Python tools returning a list are wrapped as {result: [...]}; dicts arrive as JSON text.
    const structured = result.structuredContent as Record<string, unknown> | undefined;
    if (structured && "result" in structured) return structured.result as T;
    return (structured ?? JSON.parse(text)) as T;
  } finally {
    await client.close();
  }
}

export const listPeople = () => callTool<Person[]>("list_people");

export const getPerson = async (id: string) => (await listPeople()).find((p) => p.id === id) ?? null;

export const wellbeing = (personId: string, days = 7) =>
  callTool<Summary>("wellbeing_summary", { person_id: personId, days });

export const addContact = (personId: string, c: Contact) =>
  callTool<Person>("add_family_contact", { person_id: personId, ...c });

export const removeContact = (personId: string, channel: string, address: string) =>
  callTool<Person>("remove_family_contact", { person_id: personId, channel, address });

export type Action = { kind: string; text: string };

export const setSchedule = (personId: string, medication: string, times: string[]) =>
  callTool<Person>("set_medication_schedule", { person_id: personId, medication, times });

/** Today's doses; with deliver, also the reminders due now (each returned once). */
export const medicationReminders = (personId: string, deliver = false) =>
  callTool<Reminders>("medication_reminders", { person_id: personId, deliver });

export type FamilyMessage = { id: string; sender: string; relation: string; text: string; audio: string | null; at: string };

/** Messages and voice notes from family; with deliver, each is returned once. */
export const familyMessages = (personId: string, deliver = false) =>
  callTool<FamilyMessage[]>("family_messages", { person_id: personId, deliver });

/** A family voice note's audio, from the backend. */
export async function voiceNote(id: string): Promise<Response> {
  // Same host as the MCP server, wherever that runs.
  const url = new URL(`/voice-notes/${encodeURIComponent(id)}`, MCP_URL).toString();
  try {
    return await fetch(url, { headers: authHeaders(), cache: "no-store" });
  } catch (e) {
    throw new KinOffline(`Kin's backend is not reachable at ${url}`, { cause: e });
  }
}

/** A reply spoken in an Indian language, as MP3 from the backend. */
export async function speechAudio(text: string, lang: string): Promise<Response> {
  const url = new URL("/speech", MCP_URL).toString();
  try {
    return await fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json", ...authHeaders() },
      body: JSON.stringify({ text, lang }),
    });
  } catch (e) {
    throw new KinOffline(`Kin's backend is not reachable at ${url}`, { cause: e });
  }
}

export async function askKin(
  prompt: string,
  personId: string,
  sessionId: string,
): Promise<{ reply: string; trace?: Trace; actions: Action[] }> {
  let res: Response;
  try {
    res = await fetch(AGENT_URL, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        ...authHeaders(),
        // AgentCore's session header: the agent keeps one conversation per session.
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": sessionId,
      },
      body: JSON.stringify({ prompt, person_id: personId }),
    });
  } catch (e) {
    throw new KinOffline(`Kin's agent is not reachable at ${AGENT_URL}`, { cause: e });
  }
  const data = (await res.json()) as { reply?: string; trace?: Trace; actions?: Action[]; error?: string };
  if (!res.ok || data.error) throw new Error(data.error || `agent returned ${res.status}`);
  return { reply: data.reply ?? "", trace: data.trace, actions: data.actions ?? [] };
}

export type Trace = {
  at: string;
  channel: string;
  person: string;
  model: string;
  latency_ms: number;
  tools?: { name: string; ok: boolean }[];
  tokens?: { input: number; output: number };
  guardrails: { rule: string; action: string }[];
  error?: string;
};

export async function getTraces(limit = 200): Promise<Trace[]> {
  let res: Response;
  try {
    res = await fetch(`${TRACES_URL}?limit=${limit}`, { headers: authHeaders(), cache: "no-store" });
  } catch (e) {
    throw new KinOffline(`Kin's backend is not reachable at ${TRACES_URL}`, { cause: e });
  }
  if (!res.ok) throw new Error(`traces returned ${res.status}`);
  return ((await res.json()) as { traces: Trace[] }).traces;
}
