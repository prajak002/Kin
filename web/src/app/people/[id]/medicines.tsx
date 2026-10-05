"use client";

import { useActionState } from "react";

import type { DoseSlot, Person } from "@/lib/kin";

import { clearScheduleAction, setScheduleAction } from "./actions";

const STATUS: Record<DoseSlot["status"], { label: string; color: string }> = {
  taken: { label: "Taken", color: "var(--good)" },
  missed: { label: "Missed", color: "var(--critical)" },
  waiting: { label: "Not confirmed yet", color: "var(--warning)" },
  upcoming: { label: "Later today", color: "var(--ink-3)" },
};

export function Medicines({
  personId,
  personName,
  schedule,
  today,
}: {
  personId: string;
  personName: string;
  schedule: NonNullable<Person["schedule"]>;
  today: DoseSlot[];
}) {
  const [error, formAction, pending] = useActionState(setScheduleAction.bind(null, personId), null);

  return (
    <div className="space-y-5">
      {today.length === 0 ? (
        <p className="text-sm text-ink-2">
          No reminders yet. Add a medicine and its times, or {personName} can tell Kin (&ldquo;I take my sugar tablet at 8
          and at night&rdquo;).
        </p>
      ) : (
        <ul className="divide-y divide-line">
          {today.map((d) => (
            <li key={d.medication + d.time} className="flex items-center gap-3 py-2.5 text-sm">
              <span className="w-14 tabular-nums text-ink-3">{d.time}</span>
              <span className="flex-1">{d.medication}</span>
              <span className="inline-flex items-center gap-1.5 text-xs text-ink-2">
                <span aria-hidden className="size-2 rounded-full" style={{ background: STATUS[d.status].color }} />
                {STATUS[d.status].label}
              </span>
            </li>
          ))}
        </ul>
      )}

      {schedule.length > 0 && (
        <ul className="flex flex-wrap gap-2 text-xs">
          {schedule.map((s) => (
            <li key={s.medication} className="inline-flex items-center gap-2 rounded-full border border-line px-3 py-1">
              {s.medication} · {s.times.join(", ")}
              <button
                type="button"
                onClick={() => clearScheduleAction(personId, s.medication)}
                className="text-ink-3 hover:text-ink"
                aria-label={`Stop reminders for ${s.medication}`}
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}

      <form action={formAction} className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
        <input name="medication" placeholder="Medicine, e.g. Metformin" className="rounded-xl border border-line bg-surface px-3 py-2 text-sm" />
        <input name="times" placeholder="Times, e.g. 08:00, 20:00" className="rounded-xl border border-line bg-surface px-3 py-2 text-sm" />
        <button type="submit" disabled={pending} className="rounded-xl bg-ink px-4 py-2 text-sm text-surface disabled:opacity-50">
          Save
        </button>
        {error && <p className="text-sm text-critical sm:col-span-3">{error}</p>}
      </form>
    </div>
  );
}
