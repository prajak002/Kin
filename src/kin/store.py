"""Local JSON store for people, medications and check-ins.

Used in development; dynamo.DynamoStore has the same interface for deployment.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


TABLES = ("checkins", "doses", "alerts", "moments", "traces", "memories", "reminders", "inbox")
MAX_TRACES = 500


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def local_date(stamp: str) -> date:
    return datetime.fromisoformat(stamp).astimezone().date()


class Store:
    def __init__(self, path: str | os.PathLike | None = None):
        self.path = Path(path or os.environ.get("KIN_STORE", ".kin/store.json"))
        self._lock = threading.Lock()
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._write({})

    def _read(self) -> dict[str, Any]:
        data = json.loads(self.path.read_text())
        data.setdefault("people", {})
        for table in TABLES:
            data.setdefault(table, [])
        return data

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self.path)

    def _append(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            row = {"at": row.pop("at", None) or _now(), **row}
            data[table].append(row)
            self._write(data)
            return row

    def upsert_person(self, person_id: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            person = data["people"].setdefault(person_id, {"id": person_id, "medications": [], "family": []})
            person.update(fields)
            self._write(data)
            return person

    def get_person(self, person_id: str) -> dict[str, Any] | None:
        return self._read()["people"].get(person_id)

    def list_people(self) -> list[dict[str, Any]]:
        return list(self._read()["people"].values())

    def add_checkin(self, person_id: str, mood: int, notes: str = "", at: str | None = None) -> dict[str, Any]:
        return self._append("checkins", {"person_id": person_id, "mood": mood, "notes": notes, "at": at})

    def add_dose(self, person_id: str, medication: str, taken: bool, at: str | None = None) -> dict[str, Any]:
        return self._append("doses", {"person_id": person_id, "medication": medication, "taken": taken, "at": at})

    def add_alert(self, person_id: str, level: str, reason: str, at: str | None = None) -> dict[str, Any]:
        return self._append("alerts", {"person_id": person_id, "level": level, "reason": reason, "at": at})

    def add_moment(self, person_id: str, topic: str, items: list[str], at: str | None = None) -> dict[str, Any]:
        return self._append("moments", {"person_id": person_id, "topic": topic, "items": items, "at": at})

    def add_row(self, table: str, person_id: str, **fields: Any) -> dict[str, Any]:
        return self._append(table, {"person_id": person_id, **fields})

    # Small binary files (family voice notes), next to the JSON file.
    def put_blob(self, key: str, data: bytes, mime: str) -> None:
        folder = self.path.parent / "blobs"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / key.replace(":", "_")).write_bytes(data)
        (folder / f"{key.replace(':', '_')}.type").write_text(mime)

    def get_blob(self, key: str) -> tuple[bytes, str] | None:
        file = self.path.parent / "blobs" / key.replace(":", "_")
        if not file.exists():
            return None
        return file.read_bytes(), file.with_name(file.name + ".type").read_text()

    def today(self, table: str, person_id: str) -> list[dict[str, Any]]:
        today = date.today()
        return [r for r in self._read()[table] if r["person_id"] == person_id and local_date(r["at"]) == today]

    def recent(self, table: str, person_id: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = [r for r in self._read()[table] if r["person_id"] == person_id]
        return rows[-limit:]

    def log_trace(self, trace: dict[str, Any]) -> None:
        with self._lock:
            data = self._read()
            data["traces"] = (data["traces"] + [{"person_id": "system", **trace}])[-MAX_TRACES:]
            self._write(data)

    def recent_traces(self, limit: int = 200) -> list[dict[str, Any]]:
        return self.recent("traces", "system", limit)


def open_store():
    """Redis when Upstash is configured (Vercel), DynamoDB when KIN_TABLE is set (AWS),
    the local JSON file otherwise."""
    from .redis_store import RedisStore, redis_configured

    if redis_configured():
        return RedisStore()
    if os.environ.get("KIN_TABLE"):
        from .dynamo import DynamoStore

        return DynamoStore()
    return Store()
