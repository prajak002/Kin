"""Medication reminders: what's due now, and who to tell when a dose isn't confirmed.

A person's schedule lives on their profile:

    "schedule": [{"medication": "Metformin", "times": ["08:00", "20:00"]}]
    "timezone": "Asia/Kolkata"

check() is called about once a minute by the talking device (and by the daily
job). For each of today's doses whose time has come and that hasn't been logged
as taken, it asks Kin to remind the person once, then tells the family once if
the dose still isn't confirmed ESCALATE_AFTER later. Both steps are recorded in
the "reminders" table, so repeated checks never repeat a reminder or an alert.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

ESCALATE_AFTER = timedelta(minutes=45)
REMIND_WITHIN = timedelta(hours=3)  # an 8 am dose isn't announced at 6 pm
EARLY = timedelta(hours=2)  # a dose taken a little before its time still counts
TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def tz(person: dict[str, Any]) -> ZoneInfo:
    return ZoneInfo(person.get("timezone") or os.environ.get("KIN_TIMEZONE", "Asia/Kolkata"))


def normalise_time(value: str) -> str:
    """'8:00' -> '08:00'. Raises ValueError for anything that isn't HH:MM."""
    m = TIME.match(value.strip())
    if not m:
        raise ValueError(f"'{value}' isn't a time like 08:00 or 20:30.")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def same_medication(a: str, b: str) -> bool:
    """'Metformin' matches 'metformin 500mg' and 'my metformin tablet'."""
    a, b = a.casefold().strip(), b.casefold().strip()
    first = lambda s: s.split()[0] if s.split() else s  # noqa: E731
    return bool(a and b) and (a in b or b in a or first(a) == first(b))


def slots_today(person: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    """Today's doses in time order. A dose logged between a slot's `at - EARLY`
    and `until` (the same medicine's next slot, less EARLY, or midnight) counts for it."""
    local = now.astimezone(tz(person))
    midnight = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    slots = []
    for entry in person.get("schedule") or []:
        times = sorted(entry.get("times", []))
        ats = [local.replace(hour=int(t[:2]), minute=int(t[3:]), second=0, microsecond=0) for t in times]
        for i, (t, at) in enumerate(zip(times, ats)):
            until = ats[i + 1] - EARLY if i + 1 < len(ats) else midnight
            slots.append({"medication": entry["medication"], "time": t,
                          "at": at.astimezone(timezone.utc), "until": until.astimezone(timezone.utc)})
    return sorted(slots, key=lambda s: s["at"])


def check(person: dict[str, Any], store, now: datetime | None = None, raise_alert=None, deliver: bool = True) -> dict[str, Any]:
    """Today's doses with their status, the reminders due now, and any family
    alerts raised. raise_alert(person, level, reason) defaults to mcp_server's.
    deliver=False only looks: nothing is recorded, nobody is alerted."""
    now = now or datetime.now(timezone.utc)
    pid = person["id"]
    doses = store.recent("doses", pid, 200)
    sent = {(r["medication"], r["slot"], r["stage"]) for r in store.recent("reminders", pid, 200)}

    today, due, escalated = [], [], []
    for slot in slots_today(person, now):
        key = slot["at"].isoformat(timespec="minutes")
        log = [d for d in doses if same_medication(d["medication"], slot["medication"])
               and slot["at"] - EARLY <= datetime.fromisoformat(d["at"]) < slot["until"]]
        status = ("taken" if any(d["taken"] for d in log) else "missed" if log
                  else "upcoming" if now < slot["at"] else "waiting")
        today.append({"medication": slot["medication"], "time": slot["time"], "status": status})
        if status != "waiting":
            continue

        late = now - slot["at"]
        if not deliver:
            continue
        if (slot["medication"], key, "reminded") not in sent and late <= REMIND_WITHIN:
            store.add_row("reminders", pid, medication=slot["medication"], slot=key, stage="reminded")
            due.append({"medication": slot["medication"], "time": slot["time"]})
        if late >= ESCALATE_AFTER and (slot["medication"], key, "escalated") not in sent:
            store.add_row("reminders", pid, medication=slot["medication"], slot=key, stage="escalated")
            reason = f"{person['name']} hasn't confirmed the {slot['time']} {slot['medication']} yet. Maybe give them a call?"
            if raise_alert is None:
                from .mcp_server import raise_alert
            raise_alert(person, "warning", reason)
            escalated.append({"medication": slot["medication"], "time": slot["time"]})

    return {"today": today, "due": due, "escalated": escalated}
