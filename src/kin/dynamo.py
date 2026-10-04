"""DynamoDB store with the same interface as the local JSON store.

Single table, keyed by person:
  pk = "PERSON#<id>", sk = "PROFILE"                       the profile
  pk = "PERSON#<id>", sk = "<table>#<iso time>#<suffix>"   check-ins, doses, alerts, moments

Rows sort by time inside each person, so recent() and today() are single queries.
"""

from __future__ import annotations

import os
import secrets
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr, Key

from .store import _now

PROFILE = "PROFILE"


def _pk(person_id: str) -> str:
    return f"PERSON#{person_id}"


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items() if k not in ("pk", "sk")}
    return value


def create_table(name: str, client=None) -> None:
    (client or boto3.client("dynamodb")).create_table(
        TableName=name,
        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
        BillingMode="PAY_PER_REQUEST",
    )


class DynamoStore:
    def __init__(self, table: str | None = None):
        self.table = boto3.resource("dynamodb").Table(table or os.environ["KIN_TABLE"])

    def _append(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        row = {"at": row.pop("at", None) or _now(), **row}
        sk = f"{table}#{row['at']}#{secrets.token_hex(3)}"
        self.table.put_item(Item={"pk": _pk(row["person_id"]), "sk": sk, **row})
        return row

    def upsert_person(self, person_id: str, **fields: Any) -> dict[str, Any]:
        person = self.get_person(person_id) or {"id": person_id, "medications": [], "family": []}
        person.update(fields)
        self.table.put_item(Item={"pk": _pk(person_id), "sk": PROFILE, **person})
        return person

    def get_person(self, person_id: str) -> dict[str, Any] | None:
        item = self.table.get_item(Key={"pk": _pk(person_id), "sk": PROFILE}).get("Item")
        return _plain(item) if item else None

    def list_people(self) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {"FilterExpression": Attr("sk").eq(PROFILE)}
        people = []
        while True:
            page = self.table.scan(**kwargs)
            people += [_plain(i) for i in page["Items"]]
            if "LastEvaluatedKey" not in page:
                return people
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def add_checkin(self, person_id: str, mood: int, notes: str = "", at: str | None = None) -> dict[str, Any]:
        return self._append("checkins", {"person_id": person_id, "mood": mood, "notes": notes, "at": at})

    def add_dose(self, person_id: str, medication: str, taken: bool, at: str | None = None) -> dict[str, Any]:
        return self._append("doses", {"person_id": person_id, "medication": medication, "taken": taken, "at": at})

    def add_alert(self, person_id: str, level: str, reason: str, at: str | None = None) -> dict[str, Any]:
        return self._append("alerts", {"person_id": person_id, "level": level, "reason": reason, "at": at})

    def add_moment(self, person_id: str, topic: str, items: list[str], at: str | None = None) -> dict[str, Any]:
        return self._append("moments", {"person_id": person_id, "topic": topic, "items": items, "at": at})

    def today(self, table: str, person_id: str) -> list[dict[str, Any]]:
        start = datetime.combine(date.today(), time()).astimezone().astimezone(timezone.utc)
        cond = Key("pk").eq(_pk(person_id)) & Key("sk").between(
            f"{table}#{start.isoformat(timespec='seconds')}", f"{table}#~"
        )
        return [_plain(i) for i in self.table.query(KeyConditionExpression=cond)["Items"]]

    def recent(self, table: str, person_id: str, limit: int = 20) -> list[dict[str, Any]]:
        cond = Key("pk").eq(_pk(person_id)) & Key("sk").begins_with(f"{table}#")
        page = self.table.query(KeyConditionExpression=cond, ScanIndexForward=False, Limit=limit)
        return [_plain(i) for i in reversed(page["Items"])]

    def log_trace(self, trace: dict[str, Any]) -> None:
        self._append("traces", {"person_id": "system", **trace})

    def recent_traces(self, limit: int = 200) -> list[dict[str, Any]]:
        return self.recent("traces", "system", limit)
