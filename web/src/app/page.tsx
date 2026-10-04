import Link from "next/link";
import { connection } from "next/server";

import { AlertBadge, Card, Offline } from "@/components/ui";
import { age, moodWord, timeAgo } from "@/lib/format";
import { KinOffline, listPeople, wellbeing } from "@/lib/kin";

export default async function FamilyHome() {
  await connection(); // always live data

  let rows;
  try {
    const people = await listPeople();
    rows = await Promise.all(people.map(async (p) => ({ person: p, summary: await wellbeing(p.id) })));
  } catch (e) {
    if (e instanceof KinOffline) return <Offline message={e.message} />;
    throw e;
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="font-display text-3xl font-semibold tracking-tight sm:text-4xl">How everyone is doing</h1>
        <p className="mt-2 text-ink-2">Check-ins, medication and anything Kin flagged in the last 7 days.</p>
      </div>

      {rows.length === 0 ? (
        <Card>
          <p className="text-ink-2">
            Nobody is registered yet. <Link href="/talk" className="text-accent underline">Talk to Kin</Link> and
            introduce yourself to get started.
          </p>
        </Card>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2">
          {rows.map(({ person, summary }) => {
            const last = summary.checkins.at(-1);
            const openAlerts = summary.alerts.filter((a) => a.level !== "info");
            const urgent = openAlerts.some((a) => a.level === "urgent");
            return (
              <Link key={person.id} href={`/people/${person.id}`} className="group">
                <Card className={`h-full transition-colors group-hover:border-accent ${urgent ? "border-critical" : ""}`}>
                  <div className="flex items-baseline justify-between gap-3">
                    <h2 className="font-display text-2xl">{person.name}</h2>
                    <span className="text-sm text-ink-3">
                      {age(person.birth_year)} · {person.hometown}
                    </span>
                  </div>

                  <dl className="mt-5 grid grid-cols-3 gap-3 text-sm">
                    <div>
                      <dt className="text-ink-3">Last check-in</dt>
                      <dd className="mt-1 font-medium">{moodWord(last?.mood)}</dd>
                      {last && <dd className="text-xs text-ink-3">{timeAgo(last.at)}</dd>}
                    </div>
                    <div>
                      <dt className="text-ink-3">Week average</dt>
                      <dd className="mt-1 font-medium">
                        {summary.average_mood == null ? "–" : `${summary.average_mood} / 5`}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-ink-3">Missed doses</dt>
                      <dd className="mt-1 font-medium">{summary.missed_doses.length}</dd>
                    </div>
                  </dl>

                  {openAlerts.length > 0 && (
                    <div className="mt-5 border-t border-line pt-4">
                      <AlertBadge level={urgent ? "urgent" : "warning"} />
                      <p className="mt-1 line-clamp-2 text-sm text-ink-2">{openAlerts.at(-1)!.reason}</p>
                    </div>
                  )}
                </Card>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
