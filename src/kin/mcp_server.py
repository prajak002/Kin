"""Kin MCP server (Streamable HTTP). Alexa+ and other MCP clients connect here."""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from typing import Annotated, Literal

import httpx
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import conditions, inbox, memory, reminders
from .notify import notify_family, send_family_text
from .reminiscence import Person, build_set
from .store import local_date, open_store

load_dotenv()
log = logging.getLogger("kin.mcp")

LOW_MOOD = 2

store = open_store()
server = MCPServer(
    name="kin",
    title="Kin care companion",
    instructions=(
        "Kin keeps an older adult company and keeps their family informed. "
        "Call daily_checkin once each morning, log_medication whenever a dose is "
        "discussed, and start_reminiscence when the person wants to chat about the past. "
        "Use alert_family for anything that worries you; never give medical advice."
    ),
    version="0.2.0",
)


def _require_person(person_id: str) -> dict:
    person = store.get_person(person_id)
    if person is None:
        # ToolError, unlike other exceptions, passes its message on to the model.
        raise ToolError(f"Unknown person '{person_id}'. Call register_person first.")
    return person


def raise_alert(person: dict, level: str, reason: str) -> dict:
    alert = store.add_alert(person["id"], level, reason)
    alert["notified"] = notify_family(person, level, reason)
    return alert


@server.tool()
def register_person(
    person_id: str,
    name: str | None = None,
    birth_year: int | None = None,
    hometown: str | None = None,
    language: str | None = None,
    favourites: list[str] | None = None,
    medications: list[str] | None = None,
    lives_in: str | None = None,
) -> dict:
    """Create the profile of the person Kin looks after, or update parts of it.
    A new person needs name, birth_year and hometown; an update sends only what
    changed. hometown is where they grew up; lives_in is where they live now."""
    existing = store.get_person(person_id)
    if existing is None and not (name and birth_year and hometown):
        raise ToolError("A new person needs name, birth_year and hometown.")
    given = {"name": name, "birth_year": birth_year, "hometown": hometown, "language": language,
             "favourites": favourites, "medications": medications, "lives_in": lives_in}
    fields = {k: v for k, v in given.items() if v is not None}
    if existing is None:
        fields.setdefault("favourites", [])
        fields.setdefault("medications", [])
        fields.setdefault("lives_in", hometown)
    return store.upsert_person(person_id, **fields)


@server.tool()
def add_family_contact(
    person_id: str,
    name: str,
    channel: Literal["whatsapp", "telegram", "ntfy"],
    address: Annotated[str, Field(description="WhatsApp: phone with country code; Telegram: chat id; ntfy: topic")],
    relation: str = "",
) -> dict:
    """Add or update a family member who receives Kin's alerts."""
    person = _require_person(person_id)
    family = [c for c in person.get("family", []) if not (c["channel"] == channel and c["address"] == address)]
    family.append({"name": name, "relation": relation, "channel": channel, "address": address})
    return store.upsert_person(person_id, family=family)


@server.tool()
def remove_family_contact(person_id: str, channel: str, address: str) -> dict:
    """Stop sending alerts to one family contact."""
    person = _require_person(person_id)
    family = [c for c in person.get("family", []) if not (c["channel"] == channel and c["address"] == address)]
    return store.upsert_person(person_id, family=family)


@server.tool()
def list_people() -> list[dict]:
    """Everyone Kin looks after, with their profiles."""
    return store.list_people()


@server.tool()
def daily_checkin(
    person_id: str,
    mood: Annotated[int, Field(ge=1, le=5, description="1 = very low, 5 = very good")],
    notes: str = "",
) -> dict:
    """Record how the person is feeling today. A low mood alerts the family."""
    person = _require_person(person_id)
    checkin = store.add_checkin(person_id, mood, notes)
    alert = None
    if mood <= LOW_MOOD:
        alert = raise_alert(person, "warning", f"Low mood at check-in ({mood}/5). {notes}".strip())
    return {"checkin": checkin, "alert": alert}


@server.tool()
def log_medication(person_id: str, medication: str, taken: bool) -> dict:
    """Record whether a medication dose was taken."""
    person = _require_person(person_id)
    dose = store.add_dose(person_id, medication, taken)
    alert = None
    if not taken:
        alert = raise_alert(person, "info", f"{person['name']} skipped {medication}.")
    return {"dose": dose, "alert": alert}


@server.tool()
def set_medication_schedule(
    person_id: str,
    medication: str,
    times: Annotated[list[str], Field(description='times of day in 24-hour HH:MM, e.g. ["08:00", "20:00"]; empty to stop reminders')],
) -> dict:
    """Set when the person takes a medication, so Kin reminds them at those times
    and tells family if a dose isn't confirmed."""
    person = _require_person(person_id)
    try:
        times = sorted({reminders.normalise_time(t) for t in times})
    except ValueError as e:
        raise ToolError(str(e)) from e
    schedule = [e for e in person.get("schedule") or [] if not reminders.same_medication(e["medication"], medication)]
    if times:
        schedule.append({"medication": medication.strip(), "times": times})
    medications = person.get("medications") or []
    if times and not any(reminders.same_medication(m, medication) for m in medications):
        medications = [*medications, medication.strip()]
    return store.upsert_person(person_id, schedule=schedule, medications=medications)


@server.tool()
def medication_reminders(person_id: str, deliver: bool = False) -> dict:
    """Today's scheduled doses with their status (taken, missed, waiting, upcoming).
    Kin's device passes deliver=true to also get the reminders due now (each is
    returned once) and to tell family about doses still unconfirmed."""
    return reminders.check(_require_person(person_id), store, raise_alert=raise_alert, deliver=deliver)


@server.tool()
def family_messages(person_id: str, deliver: bool = False) -> list[dict]:
    """Voice notes and messages family sent the person that they haven't heard yet:
    {id, sender, relation, text, audio, at}. Kin's device passes deliver=true when it
    plays them, so each is delivered once."""
    return inbox.collect(store, _require_person(person_id), deliver=deliver)


@server.tool()
def reply_to_family(
    person_id: str,
    to: Annotated[str, Field(description='who it is for, by name or relation ("Ravi", "my son")')],
    message: Annotated[str, Field(description="what the person wants to say, in their own words")],
) -> dict:
    """Send the person's words to a family member on WhatsApp or Telegram, e.g.
    after they hear a voice note and say "tell Ravi I'm fine"."""
    person = _require_person(person_id)
    contact = inbox.contact_named(person, to)
    if contact is None:
        names = ", ".join(c["name"] for c in person.get("family", [])) or "nobody yet"
        raise ToolError(f"No family member called '{to}' can get messages. Family: {names}.")
    try:
        send_family_text(contact, f"💬 {person['name']} says: {message.strip()}")
    except Exception as e:
        log.exception("reply_to_family failed")
        raise ToolError(f"Couldn't reach {contact['name']} right now ({type(e).__name__}).") from e
    store.add_moment(person_id, "message", [f"to {contact['name']}"])
    return {"sent_to": contact["name"], "channel": contact["channel"]}


@server.tool()
async def start_reminiscence(person_id: str, take: int = 6) -> dict:
    """Films and music from the years the person grew up in, for a reminiscence chat."""
    try:
        result = await build_set(Person.from_profile(_require_person(person_id)), take=take)
    except ValueError as e:
        raise ToolError(str(e)) from e
    except httpx.HTTPError as e:
        raise ToolError("The film and music source is unreachable right now. Chat about their favourites instead.") from e
    picks = [f.get("film") for f in result["because_you_love"][:2]] + [f["name"] for f in result["films"][:2]]
    picks += [m["name"] for m in result["music"][:2]]
    store.add_moment(person_id, "reminiscence", [p for p in dict.fromkeys(picks) if p])
    return result


@server.tool()
def save_memory(person_id: str, fact: str) -> dict:
    """WRITE a new life detail to long-term memory. Call this whenever the person
    tells you about someone by name, a place they lived or worked, or an event in
    their life. fact: one short sentence in English, e.g. "Her husband Arun was a
    schoolteacher in Shillong." Without this, it is forgotten after today."""
    _require_person(person_id)
    try:
        return memory.open_memory(store).remember(person_id, fact.strip())
    except Exception as e:
        log.exception("save_memory failed")
        raise ToolError(f"Memory is unavailable right now ({type(e).__name__}).") from e


@server.tool()
def search_memories(person_id: str, about: str) -> list[dict]:
    """READ details the person shared in earlier conversations about a topic, person
    or place. Use when they ask "do you remember…" or bring up someone again."""
    _require_person(person_id)
    try:
        return memory.open_memory(store).recall(person_id, about)
    except Exception as e:
        log.exception("search_memories failed")
        raise ToolError(f"Memory is unavailable right now ({type(e).__name__}).") from e


@server.tool()
async def local_conditions(person_id: str) -> dict:
    """Today's weather and air quality where the person lives, with plain advice
    (heat, cold, poor air, strong sun, rain) to pass on gently."""
    person = _require_person(person_id)
    try:
        return await conditions.local_conditions(person.get("lives_in") or person["hometown"])
    except (ValueError, httpx.HTTPError) as e:
        raise ToolError(f"Weather is unavailable right now ({e}).") from e


@server.tool()
def alert_family(
    person_id: str,
    reason: str,
    level: Literal["info", "warning", "urgent"] = "warning",
) -> dict:
    """Notify the person's family about something that needs their attention."""
    return raise_alert(_require_person(person_id), level, reason)


@server.tool()
def wellbeing_summary(person_id: str, days: int = 7) -> dict:
    """Recent check-ins, doses and alerts, for answering family questions."""
    person = _require_person(person_id)
    since = date.today() - timedelta(days=days - 1)

    def window(table: str) -> list[dict]:
        return [r for r in store.recent(table, person_id, limit=1000) if local_date(r["at"]) >= since]

    checkins = window("checkins")
    moods = [c["mood"] for c in checkins]
    return {
        "name": person["name"],
        "average_mood": round(sum(moods) / len(moods), 1) if moods else None,
        "checkins": checkins,
        "doses": window("doses"),
        "missed_doses": [d for d in window("doses") if not d["taken"]],
        "alerts": window("alerts"),
        "moments": window("moments"),
    }


def main() -> None:
    if os.environ.get("KIN_MCP_TRANSPORT") == "stdio":
        server.run(transport="stdio")
        return
    server.run(
        transport="streamable-http",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        stateless_http=True,
    )


if __name__ == "__main__":
    main()
