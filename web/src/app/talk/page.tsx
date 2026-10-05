import { connection } from "next/server";

import { Offline } from "@/components/ui";
import { KinOffline, listPeople, type Person } from "@/lib/kin";

import { Device } from "./device";

export const metadata = { title: "Talk to Kin" };

export default async function TalkPage() {
  await connection();
  let people: Person[];
  try {
    people = await listPeople();
  } catch (e) {
    if (e instanceof KinOffline) return <Offline message={e.message} />;
    throw e;
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-display text-3xl font-semibold tracking-tight sm:text-4xl">Talk to Kin</h1>
        <p className="mt-2 max-w-2xl text-ink-2">
          A stand-in for a smart speaker. Tap the button and speak, or type. Kin answers out loud, and anything it
          records shows up on the family page.
        </p>
      </div>
      <Device people={people.map((p) => ({ id: p.id, name: p.name, language: p.language }))} />
    </div>
  );
}
