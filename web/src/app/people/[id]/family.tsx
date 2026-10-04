"use client";

import { useActionState, useState } from "react";

import type { Contact } from "@/lib/kin";

import { addContactAction, removeContactAction } from "./actions";

const CHANNEL_LABEL: Record<Contact["channel"], string> = { whatsapp: "WhatsApp", telegram: "Telegram", ntfy: "ntfy push" };

export function FamilyContacts({
  personId,
  personName,
  contacts,
  telegramBot,
}: {
  personId: string;
  personName: string;
  contacts: Contact[];
  telegramBot?: string;
}) {
  const [error, formAction, pending] = useActionState(addContactAction.bind(null, personId), null);
  const [channel, setChannel] = useState<"whatsapp" | "ntfy">("whatsapp");

  return (
    <div className="space-y-5">
      {contacts.length === 0 ? (
        <p className="text-sm text-ink-2">Nobody gets alerts about {personName} yet.</p>
      ) : (
        <ul className="divide-y divide-line">
          {contacts.map((c) => (
            <li key={c.channel + c.address} className="flex items-center gap-3 py-2.5 text-sm">
              <span className="flex-1">
                {c.name}
                {c.relation && <span className="text-ink-3"> · {c.relation}</span>}
              </span>
              <span className="text-ink-3">{CHANNEL_LABEL[c.channel]}</span>
              <button
                type="button"
                onClick={() => removeContactAction(personId, c.channel, c.address)}
                className="rounded-full px-2 py-1 text-xs text-ink-3 hover:bg-accent-soft hover:text-ink"
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}

      {telegramBot && (
        <p className="rounded-xl bg-accent-soft px-4 py-3 text-sm">
          <span className="font-medium">Telegram:</span> family members open{" "}
          <a className="underline" href={`https://t.me/${telegramBot}?start=${personId}`} target="_blank" rel="noreferrer">
            t.me/{telegramBot}?start={personId}
          </a>{" "}
          and tap Start. They&apos;re added automatically and can message the bot any time for an update.
        </p>
      )}

      <form action={formAction} className="grid gap-2 sm:grid-cols-[1fr_1fr_8rem_auto]">
        <input name="name" placeholder="Name" className="rounded-xl border border-line bg-surface px-3 py-2 text-sm" />
        <input
          name="address"
          placeholder={channel === "whatsapp" ? "+91 98765 43210" : "ntfy topic"}
          className="rounded-xl border border-line bg-surface px-3 py-2 text-sm"
        />
        <select
          name="channel"
          value={channel}
          onChange={(e) => setChannel(e.target.value as "whatsapp" | "ntfy")}
          className="rounded-xl border border-line bg-surface px-3 py-2 text-sm"
        >
          <option value="whatsapp">WhatsApp</option>
          <option value="ntfy">ntfy push</option>
        </select>
        <button type="submit" disabled={pending} className="rounded-xl bg-ink px-4 py-2 text-sm text-surface disabled:opacity-50">
          Add
        </button>
        {error && <p className="text-sm text-critical sm:col-span-4">{error}</p>}
      </form>
    </div>
  );
}
