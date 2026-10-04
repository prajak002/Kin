import Link from "next/link";
import { notFound } from "next/navigation";
import { connection } from "next/server";

import { MoodChart, type MoodDay } from "@/components/mood-chart";
import { AlertBadge, Card, Offline } from "@/components/ui";
import { age, dayKey, timeAgo } from "@/lib/format";
import { KinOffline, getPerson, wellbeing, type Summary } from "@/lib/kin";

function moodDays(summary: Summary): MoodDay[] {
  return Array.from({ length: 7 }, (_, i) => {
    const date = new Date();
    date.setDate(date.getDate() - (6 - i));
    const key = dayKey(date);
    // The latest check-in of each day is the one that counts.
    const checkin = summary.checkins.filter((c) => dayKey(c.at) === key).at(-1);
    return {
      day: key,
      label: date.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" }),
      mood: checkin?.mood ?? null,
      notes: checkin?.notes ?? "",
    };
  });
}

type Event = { at: string; kind: string; text: string; alert?: Summary["alerts"][number] };

function timeline(s: Summary): Event[] {
  return [
    ...s.checkins.map((c) => ({ at: c.at, kind: "Check-in", text: `Mood ${c.mood}/5${c.notes ? ` – “${c.notes}”` : ""}` })),
    ...s.doses.map((d) => ({ at: d.at, kind: "Medication", text: `${d.medication}: ${d.taken ? "taken" : "missed"}` })),
    ...s.alerts.map((a) => ({ at: a.at, kind: "Alert", text: a.reason, alert: a })),
    ...s.moments.map((m) => ({ at: m.at, kind: "Memories", text: `Talked about ${m.items.join(", ")}` })),
  ].sort((a, b) => b.at.localeCompare(a.at));
}

export default async function PersonPage({ params }: PageProps<"/people/[id]">) {
  await connection();
  const { id } = await params;

  let person, summary;
  try {
    [person, summary] = await Promise.all([getPerson(id), wellbeing(id)]);
  } catch (e) {
    if (e instanceof KinOffline) return <Offline message={e.message} />;
    if (e instanceof Error && e.message.includes("Unknown person")) notFound();
    throw e;
  }
  if (!person) notFound();

  const events = timeline(summary);
  const alerts = summary.alerts.filter((a) => a.level !== "info").reverse();

  return (
    <div className="space-y-6">
      <div>
        <Link href="/" className="text-sm text-ink-3 hover:text-ink">
          ← Everyone
        </Link>
        <h1 className="mt-2 font-display text-3xl font-semibold tracking-tight sm:text-4xl">{person.name}</h1>
        <p className="mt-1 text-ink-2">
          {age(person.birth_year)} · grew up in {person.hometown}
          {person.language ? ` · speaks ${person.language}` : ""}
        </p>
      </div>

      {alerts.length > 0 && (
        <Card className={alerts.some((a) => a.level === "urgent") ? "border-critical" : ""}>
          <h2 className="font-display text-xl">Needs your attention</h2>
          <ul className="mt-3 space-y-3">
            {alerts.slice(0, 5).map((a) => (
              <li key={a.at + a.reason} className="flex flex-col gap-0.5 sm:flex-row sm:items-baseline sm:gap-4">
                <span className="w-36 shrink-0">
                  <AlertBadge level={a.level} />
                </span>
                <span className="flex-1 text-sm">{a.reason}</span>
                <span className="text-xs text-ink-3">{timeAgo(a.at)}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <div className="grid gap-6 lg:grid-cols-[3fr_2fr]">
        <Card>
          <h2 className="font-display text-xl">Mood this week</h2>
          <p className="mb-6 text-sm text-ink-3">From daily check-ins with Kin</p>
          <MoodChart days={moodDays(summary)} />
        </Card>

        <Card>
          <h2 className="font-display text-xl">About {person.name}</h2>
          <dl className="mt-3 space-y-3 text-sm">
            <div>
              <dt className="text-ink-3">Medications</dt>
              <dd>{person.medications.length ? person.medications.join(", ") : "None recorded"}</dd>
            </div>
            <div>
              <dt className="text-ink-3">Loves</dt>
              <dd>{person.favourites.length ? person.favourites.join(", ") : "Not shared yet"}</dd>
            </div>
            <div>
              <dt className="text-ink-3">Missed doses this week</dt>
              <dd>{summary.missed_doses.length}</dd>
            </div>
          </dl>
        </Card>
      </div>

      <Card>
        <h2 className="font-display text-xl">This week</h2>
        {events.length === 0 ? (
          <p className="mt-3 text-sm text-ink-2">Nothing yet. Kin records check-ins as {person.name} chats.</p>
        ) : (
          <ol className="mt-3 divide-y divide-line">
            {events.map((e) => (
              <li key={e.kind + e.at + e.text} className="grid gap-1 py-3 sm:grid-cols-[8rem_1fr_6rem] sm:gap-4">
                <span className="text-sm text-ink-3">{e.alert ? <AlertBadge level={e.alert.level} /> : e.kind}</span>
                <span className="text-sm">{e.text}</span>
                <span className="text-xs text-ink-3 sm:text-right">{timeAgo(e.at)}</span>
              </li>
            ))}
          </ol>
        )}
      </Card>
    </div>
  );
}
