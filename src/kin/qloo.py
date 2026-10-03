"""Thin async client for the Qloo Taste API (v2)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://hackathon.api.qloo.com"

ARTIST = "urn:entity:artist"
MOVIE = "urn:entity:movie"
PLACE = "urn:entity:place"
BOOK = "urn:entity:book"
TV_SHOW = "urn:entity:tv_show"


class QlooError(RuntimeError):
    pass


@dataclass
class Entity:
    entity_id: str
    name: str
    types: list[str]
    affinity: float | None = None
    properties: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Entity:
        query = raw.get("query") or {}
        return cls(
            entity_id=raw.get("entity_id", ""),
            name=raw.get("name", ""),
            types=raw.get("types") or ([raw["subtype"]] if raw.get("subtype") else []),
            affinity=query.get("affinity"),
            properties=raw.get("properties") or {},
        )

    @property
    def year(self) -> int | None:
        for key in ("release_year", "publication_year"):
            if self.properties.get(key):
                return int(self.properties[key])
        date = self.properties.get("release_date") or ""
        return int(date[:4]) if date[:4].isdigit() else None


def age_bucket(age: int) -> str:
    if age <= 35:
        return "35_and_younger"
    if age <= 55:
        return "36_to_55"
    return "55_and_older"


class QlooClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        key = api_key or os.environ.get("QLOO_API_KEY")
        if not key:
            raise QlooError("QLOO_API_KEY is not set")
        self._http = httpx.AsyncClient(
            base_url=base_url or os.environ.get("QLOO_BASE_URL", DEFAULT_BASE_URL),
            headers={"X-Api-Key": key, "Accept": "application/json"},
            timeout=20,
            transport=transport,
        )

    async def __aenter__(self) -> QlooClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        params = {k: v for k, v in params.items() if v is not None}
        resp = await self._http.get(path, params=params)
        if resp.status_code == 404:
            return {"results": []}
        if resp.is_error:
            raise QlooError(f"{resp.status_code} {path}: {resp.text[:300]}")
        return resp.json()

    async def search(self, query: str, types: list[str] | None = None, take: int = 5) -> list[Entity]:
        data = await self._get(
            "/search",
            {"query": query, "types": ",".join(types) if types else None, "take": take},
        )
        return [Entity.from_api(r) for r in data.get("results", [])]

    async def insights(
        self,
        entity_type: str,
        *,
        interests: list[str] | None = None,
        age: int | None = None,
        location_query: str | None = None,
        filter_location_query: str | None = None,
        tags: list[str] | None = None,
        release_year_min: int | None = None,
        release_year_max: int | None = None,
        take: int = 10,
    ) -> list[Entity]:
        params = {
            "filter.type": entity_type,
            "signal.interests.entities": ",".join(interests) if interests else None,
            "signal.demographics.age": age_bucket(age) if age is not None else None,
            "signal.location.query": location_query,
            "filter.location.query": filter_location_query,
            "filter.tags": ",".join(tags) if tags else None,
            "filter.release_year.min": release_year_min,
            "filter.release_year.max": release_year_max,
            "take": take,
        }
        data = await self._get("/v2/insights", params)
        results = data.get("results") or {}
        entities = results.get("entities", []) if isinstance(results, dict) else results
        return [Entity.from_api(r) for r in entities]
