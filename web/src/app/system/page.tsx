import { connection } from "next/server";

import { Card, Offline } from "@/components/ui";
import { timeAgo } from "@/lib/format";
import { KinOffline, getTraces, type Trace } from "@/lib/kin";

import { LatencyChart } from "./latency-chart";

export const metadata = { title: "System · Kin" };

function percentile(values: number[], p: number): number {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length))];
}

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <Card>
      <p className="text-sm text-ink-3">{label}</p>
      <p className="mt-1 font-display text-3xl">{value}</p>
      {note && <p className="mt-1 text-xs text-ink-3">{note}</p>}
    </Card>
  );
}

export default async function SystemPage() {
  await connection();
  let traces: Trace[];
  try {
    traces = await getTraces(200);
  } catch (e) {
    if (e instanceof KinOffline) return <Offline message={e.message} />;
    throw e;
  }

  const ok = traces.filter((t) => !t.error);
  const latencies = ok.map((t) => t.latency_ms);
  const calls = traces.flatMap((t) => t.tools ?? []);
  const failedCalls = calls.filter((c) => !c.ok).length;
  const interventions = traces.flatMap((t) => t.guardrails.filter((g) => g.action !== "model_already_alerted"));
  const recent = [...traces].reverse();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-display text-3xl font-semibold tracking-tight sm:text-4xl">How Kin is running</h1>
        <p className="mt-2 max-w-2xl text-ink-2">
          Every turn is traced: speed, which tools the agent called, and when a guardrail stepped in. Traces never include what
          anyone said.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Turns traced" value={String(traces.length)} note={traces[0] ? `since ${timeAgo(traces[0].at)}` : undefined} />
        <Stat
          label="Reply time"
          value={`${(percentile(latencies, 50) / 1000).toFixed(1)} s`}
          note={`median · p95 ${(percentile(latencies, 95) / 1000).toFixed(1)} s`}
        />
        <Stat
          label="Tool calls succeeded"
          value={calls.length ? `${Math.round(((calls.length - failedCalls) / calls.length) * 100)}%` : "–"}
          note={`${calls.length} calls · ${failedCalls} failed`}
        />
        <Stat
          label="Guardrail interventions"
          value={String(interventions.length)}
          note={`${traces.filter((t) => t.error).length} turns errored`}
        />
      </div>

      <Card>
        <h2 className="font-display text-xl">Reply time per turn</h2>
        <p className="mb-6 text-sm text-ink-3">Last {Math.min(ok.length, 60)} turns, from message received to reply ready</p>
        {ok.length ? (
          <LatencyChart
            p95={percentile(latencies, 95)}
            points={ok.slice(-60).map((t) => ({
              at: t.at,
              label: `${new Date(t.at).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" })} · ${t.channel}`,
              ms: t.latency_ms,
              tools: (t.tools ?? []).map((c) => c.name).join(", "),
              flagged: t.guardrails.some((g) => g.action !== "model_already_alerted"),
            }))}
          />
        ) : (
          <p className="text-sm text-ink-2">No turns yet. Talk to Kin and come back.</p>
        )}
      </Card>

      <Card>
        <h2 className="font-display text-xl">Recent turns</h2>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[40rem] text-left text-sm">
            <thead className="text-ink-3">
              <tr className="border-b border-line">
                <th className="py-2 pr-4 font-normal">When</th>
                <th className="py-2 pr-4 font-normal">Channel</th>
                <th className="py-2 pr-4 text-right font-normal">Time</th>
                <th className="py-2 pr-4 font-normal">Tools</th>
                <th className="py-2 pr-4 text-right font-normal">Tokens</th>
                <th className="py-2 font-normal">Guardrails</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {recent.slice(0, 25).map((t, i) => (
                <tr key={t.at + i}>
                  <td className="py-2 pr-4 text-ink-3">{timeAgo(t.at)}</td>
                  <td className="py-2 pr-4">{t.channel}</td>
                  <td className="py-2 pr-4 text-right tabular-nums">{t.error ? "error" : `${(t.latency_ms / 1000).toFixed(1)} s`}</td>
                  <td className="py-2 pr-4">
                    {(t.tools ?? []).map((c, j) => (
                      <span key={j} className={`mr-1.5 ${c.ok ? "" : "text-critical"}`}>
                        {c.name}
                        {!c.ok && " ✕"}
                      </span>
                    ))}
                  </td>
                  <td className="py-2 pr-4 text-right tabular-nums text-ink-3">
                    {t.tokens ? `${t.tokens.input + t.tokens.output}` : "–"}
                  </td>
                  <td className="py-2 text-ink-2">{t.guardrails.map((g) => g.rule.replace("_", " ")).join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
