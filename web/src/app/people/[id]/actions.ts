"use server";

import { revalidatePath } from "next/cache";

import { addContact, removeContact, setSchedule, type Contact } from "@/lib/kin";

export async function addContactAction(personId: string, _prev: string | null, form: FormData): Promise<string | null> {
  const channel = form.get("channel") as Contact["channel"];
  const name = String(form.get("name") ?? "").trim();
  let address = String(form.get("address") ?? "").trim();
  if (!name || !address) return "Add a name and a number or topic.";
  if (channel === "whatsapp") {
    address = address.replace(/[\s()-]/g, "");
    if (!/^\+?\d{8,15}$/.test(address)) return "Use the full number with country code, e.g. +91 98765 43210.";
  }
  try {
    await addContact(personId, { name, relation: String(form.get("relation") ?? ""), channel, address });
  } catch (e) {
    return e instanceof Error ? e.message : String(e);
  }
  revalidatePath(`/people/${personId}`);
  return null;
}

export async function removeContactAction(personId: string, channel: string, address: string) {
  await removeContact(personId, channel, address);
  revalidatePath(`/people/${personId}`);
}

export async function setScheduleAction(personId: string, _prev: string | null, form: FormData): Promise<string | null> {
  const medication = String(form.get("medication") ?? "").trim();
  const times = String(form.get("times") ?? "")
    .split(/[,\s]+/)
    .filter(Boolean);
  if (!medication || !times.length) return "Add the medicine and at least one time, e.g. 08:00, 20:00.";
  try {
    await setSchedule(personId, medication, times);
  } catch (e) {
    return e instanceof Error ? e.message : String(e);
  }
  revalidatePath(`/people/${personId}`);
  return null;
}

export async function clearScheduleAction(personId: string, medication: string) {
  await setSchedule(personId, medication, []);
  revalidatePath(`/people/${personId}`);
}
