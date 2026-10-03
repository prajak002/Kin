"""Kin MCP server (Streamable HTTP). Alexa+ and other MCP clients connect here."""

from __future__ import annotations

import os
from typing import Annotated, Literal

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from pydantic import Field

from .qloo import QlooClient
from .reminiscence import Person, build_set
from .store import Store

load_dotenv()

LOW_MOOD = 2

store = Store()
server = MCPServer(
    name="kin",
    title="Kin care companion",
    instructions=(
        "Kin keeps an older adult company and keeps their family informed. "
        "Call daily_checkin once each morning, log_medication whenever a dose is "
        "discussed, and start_reminiscence when the person wants to chat about the past. "
        "Use alert_family for anything that worries you; never give medical advice."
    ),
    version="0.1.0",
)


def _require_person(person_id: str) -> dict:
    person = store.get_person(person_id)
    if person is None:
        raise ValueError(f"Unknown person '{person_id}'. Call register_person first.")
    return person


@server.tool()
def register_person(
    person_id: str,
    name: str,
    birth_year: int,
    hometown: str,
    favourites: list[str] | None = None,
    medications: list[str] | None = None,
) -> dict:
    """Create or update the profile of the person Kin looks after."""
    return store.upsert_person(
        person_id,
        name=name,
        birth_year=birth_year,
        hometown=hometown,
        favourites=favourites or [],
        medications=medications or [],
    )


@server.tool()
def daily_checkin(
    person_id: str,
    mood: Annotated[int, Field(ge=1, le=5, description="1 = very low, 5 = very good")],
    notes: str = "",
) -> dict:
    """Record the morning check-in. A low mood alerts the family."""
    _require_person(person_id)
    checkin = store.add_checkin(person_id, mood, notes)
    alert = None
    if mood <= LOW_MOOD:
        alert = store.add_alert(person_id, "warning", f"Low mood at check-in ({mood}/5). {notes}".strip())
    return {"checkin": checkin, "alert": alert}


@server.tool()
def log_medication(person_id: str, medication: str, taken: bool) -> dict:
    """Record whether a medication dose was taken."""
    person = _require_person(person_id)
    dose = store.add_dose(person_id, medication, taken)
    alert = None
    if not taken:
        alert = store.add_alert(person_id, "info", f"{person['name']} skipped {medication}.")
    return {"dose": dose, "alert": alert}


@server.tool()
async def start_reminiscence(person_id: str, take: int = 6) -> dict:
    """Films and music from the years the person grew up in, for a reminiscence chat."""
    p = _require_person(person_id)
    person = Person(p["name"], p["birth_year"], p["hometown"], p.get("favourites", []))
    async with QlooClient() as qloo:
        result = await build_set(qloo, person, take=take)
    return result.to_dict()


@server.tool()
def alert_family(
    person_id: str,
    reason: str,
    level: Literal["info", "warning", "urgent"] = "warning",
) -> dict:
    """Notify the person's family about something that needs their attention."""
    _require_person(person_id)
    return store.add_alert(person_id, level, reason)


@server.tool()
def wellbeing_summary(person_id: str, days: int = 7) -> dict:
    """Recent check-ins, doses and alerts, for answering family questions."""
    person = _require_person(person_id)
    checkins = store.recent("checkins", person_id, limit=days)
    doses = store.recent("doses", person_id, limit=days * 4)
    moods = [c["mood"] for c in checkins]
    return {
        "name": person["name"],
        "average_mood": round(sum(moods) / len(moods), 1) if moods else None,
        "checkins": checkins,
        "missed_doses": [d for d in doses if not d["taken"]],
        "alerts": store.recent("alerts", person_id, limit=10),
    }


def main() -> None:
    server.run(
        transport="streamable-http",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        stateless_http=True,
    )


if __name__ == "__main__":
    main()
