"""Redis store over Upstash's REST API, for serverless hosts such as Vercel.

Same interface as the JSON and DynamoDB stores. Keys:
  kin:people                  set of person ids
  kin:person:<id>             profile JSON
  kin:<table>:<id>            list of row JSON, oldest first
  kin:chat:<session>          agent conversation history JSON (expires)

Uses KV_REST_API_URL / KV_REST_API_TOKEN (set by Vercel's Upstash integration)
or UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN.
"""

from __future__ import annotations

import json
import os
from datetime import date
from typing import Any

import httpx

from .store import _now, local_date

CHAT_TTL = 6 * 3600


def redis_configured() -> bool:
    return bool(os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL"))


class RedisStore:
    def __init__(self, url: str | None = None, token: str | None = None, transport: httpx.BaseTransport | None = None):
        url = url or os.environ.get("KV_REST_API_URL") or os.environ["UPSTASH_REDIS_REST_URL"]
        token = token or os.environ.get("KV_REST_API_TOKEN") or os.environ["UPSTASH_REDIS_REST_TOKEN"]
        self._http = httpx.Client(base_url=url, headers={"Authorization": f"Bearer {token}"}, timeout=10, transport=transport)

    def _cmd(self, *args: Any) -> Any:
        r = self._http.post("/", json=[str(a) for a in args])
        r.raise_for_status()
        return r.json()["result"]

    def _pipeline(self, *commands: list[Any]) -> list[Any]:
        r = self._http.post("/pipeline", json=[[str(a) for a in c] for c in commands])
        r.raise_for_status()
        return [item["result"] for item in r.json()]

    def _append(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        row = {"at": row.pop("at", None) or _now(), **row}
        self._cmd("RPUSH", f"kin:{table}:{row['person_id']}", json.dumps(row))
        return row

    def upsert_person(self, person_id: str, **fields: Any) -> dict[str, Any]:
        person = self.get_person(person_id) or {"id": person_id, "medications": [], "family": []}
        person.update(fields)
        self._pipeline(["SET", f"kin:person:{person_id}", json.dumps(person)], ["SADD", "kin:people", person_id])
        return person

    def get_person(self, person_id: str) -> dict[str, Any] | None:
        raw = self._cmd("GET", f"kin:person:{person_id}")
        return json.loads(raw) if raw else None

    def list_people(self) -> list[dict[str, Any]]:
        ids = sorted(self._cmd("SMEMBERS", "kin:people") or [])
        if not ids:
            return []
        raws = self._cmd("MGET", *(f"kin:person:{i}" for i in ids))
        return [json.loads(r) for r in raws if r]

    def add_checkin(self, person_id: str, mood: int, notes: str = "", at: str | None = None) -> dict[str, Any]:
        return self._append("checkins", {"person_id": person_id, "mood": mood, "notes": notes, "at": at})

    def add_dose(self, person_id: str, medication: str, taken: bool, at: str | None = None) -> dict[str, Any]:
        return self._append("doses", {"person_id": person_id, "medication": medication, "taken": taken, "at": at})

    def add_alert(self, person_id: str, level: str, reason: str, at: str | None = None) -> dict[str, Any]:
        return self._append("alerts", {"person_id": person_id, "level": level, "reason": reason, "at": at})

    def add_moment(self, person_id: str, topic: str, items: list[str], at: str | None = None) -> dict[str, Any]:
        return self._append("moments", {"person_id": person_id, "topic": topic, "items": items, "at": at})

    def recent(self, table: str, person_id: str, limit: int = 20) -> list[dict[str, Any]]:
        return [json.loads(r) for r in self._cmd("LRANGE", f"kin:{table}:{person_id}", -limit, -1)]

    def today(self, table: str, person_id: str) -> list[dict[str, Any]]:
        return [r for r in self.recent(table, person_id, 200) if local_date(r["at"]) == date.today()]

    # Agent conversation history, so serverless instances can pick up a chat.
    def load_chat(self, session: str) -> list[dict[str, Any]]:
        raw = self._cmd("GET", f"kin:chat:{session}")
        return json.loads(raw) if raw else []

    def save_chat(self, session: str, messages: list[dict[str, Any]]) -> None:
        self._cmd("SET", f"kin:chat:{session}", json.dumps(messages), "EX", CHAT_TTL)
