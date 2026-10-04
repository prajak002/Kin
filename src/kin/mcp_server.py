"""Kin MCP server (Streamable HTTP). Alexa+ and other MCP clients connect here."""

from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Annotated, Literal

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from pydantic import Field

from .notify import notify_family
from .reminiscence import Person, build_set
from .store import local_date, open_store

load_dotenv()

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
        raise ValueError(f"Unknown person '{person_id}'. Call register_person first.")
    return person


def raise_alert(person: dict, level: str, reason: str) -> dict:
    alert = store.add_alert(person["id"], level, reason)
    alert["notified"] = notify_family(person["name"], level, reason)
    return alert


@server.tool()
def register_person(
    person_id: str,
    name: str,
    birth_year: int,
    hometown: str,
    language: str | None = None,
    favourites: list[str] | None = None,
    medications: list[str] | None = None,
) -> dict:
    """Create or update the profile of the person Kin looks after."""
    return store.upsert_person(
        person_id,
        name=name,
        birth_year=birth_year,
        hometown=hometown,
        language=language,
        favourites=favourites or [],
        medications=medications or [],
    )


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
async def start_reminiscence(person_id: str, take: int = 6) -> dict:
    """Films and music from the years the person grew up in, for a reminiscence chat."""
    result = await build_set(Person.from_profile(_require_person(person_id)), take=take)
    picks = [f.get("film") for f in result["because_you_love"][:2]] + [f["name"] for f in result["films"][:2]]
    picks += [m["name"] for m in result["music"][:2]]
    store.add_moment(person_id, "reminiscence", [p for p in dict.fromkeys(picks) if p])
    return result


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
