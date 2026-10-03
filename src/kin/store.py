"""Local JSON store for people, medications and check-ins.

Stands in for DynamoDB during development; the interface is what the agent
and MCP server depend on.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | os.PathLike | None = None):
        self.path = Path(path or os.environ.get("KIN_STORE", ".kin/store.json"))
        self._lock = threading.Lock()
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._write({"people": {}, "checkins": [], "doses": [], "alerts": []})

    def _read(self) -> dict[str, Any]:
        return json.loads(self.path.read_text())

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self.path)

    def _append(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            row = {"at": _now(), **row}
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

    def add_checkin(self, person_id: str, mood: int, notes: str = "") -> dict[str, Any]:
        return self._append("checkins", {"person_id": person_id, "mood": mood, "notes": notes})

    def add_dose(self, person_id: str, medication: str, taken: bool) -> dict[str, Any]:
        return self._append("doses", {"person_id": person_id, "medication": medication, "taken": taken})

    def add_alert(self, person_id: str, level: str, reason: str) -> dict[str, Any]:
        return self._append("alerts", {"person_id": person_id, "level": level, "reason": reason})

    def recent(self, table: str, person_id: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = [r for r in self._read()[table] if r["person_id"] == person_id]
        return rows[-limit:]
