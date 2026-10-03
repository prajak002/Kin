"""Builds reminiscence material from the years a person grew up in.

Autobiographical memory is densest for events between roughly ages 10 and 30
(the "reminiscence bump"), so that window drives the release-year filters.

Two sources are supported: Qloo's taste graph when QLOO_API_KEY is set, and
open data from Wikidata otherwise.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import date

from .qloo import ARTIST, MOVIE, QlooClient
from .wikidata import Wikidata

BUMP_START, BUMP_END = 10, 30


@dataclass
class Person:
    name: str
    birth_year: int
    hometown: str
    favourites: list[str] = field(default_factory=list)
    language: str | None = None

    @property
    def age(self) -> int:
        return date.today().year - self.birth_year

    @property
    def formative_years(self) -> tuple[int, int]:
        return self.birth_year + BUMP_START, self.birth_year + BUMP_END

    @classmethod
    def from_profile(cls, p: dict) -> Person:
        return cls(p["name"], p["birth_year"], p["hometown"], p.get("favourites", []), p.get("language"))


def default_source() -> str:
    return os.environ.get("KIN_TASTE_SOURCE") or ("qloo" if os.environ.get("QLOO_API_KEY") else "wikidata")


def _frame(person: Person, source: str, films: list[dict], music: list[dict], favourites: list[dict]) -> dict:
    start, end = person.formative_years
    return {
        "person": person.name,
        "formative_years": f"{start}-{end}",
        "hometown": person.hometown,
        "source": source,
        "because_you_love": favourites,
        "films": films,
        "music": music,
    }


async def build_with_qloo(qloo: QlooClient, person: Person, take: int = 6) -> dict:
    found = await asyncio.gather(*(qloo.search(f, take=1) for f in person.favourites))
    seeds = [hits[0] for hits in found if hits]
    interests = [e.entity_id for e in seeds] or None
    start, end = person.formative_years
    common = {"interests": interests, "age": person.age, "location_query": person.hometown, "take": take}

    films, music = await asyncio.gather(
        qloo.insights(MOVIE, release_year_min=start, release_year_max=end, **common),
        qloo.insights(ARTIST, **common),
    )
    return _frame(
        person,
        "qloo",
        [{"name": e.name, "year": e.year, "affinity": e.affinity} for e in films],
        [{"name": e.name, "affinity": e.affinity} for e in music],
        [{"favourite": e.name} for e in seeds],
    )


async def build_with_wikidata(wd: Wikidata, person: Person, take: int = 6) -> dict:
    start, end = person.formative_years
    place = await wd.place(person.hometown)
    if place is None:
        raise ValueError(f"Could not find '{person.hometown}' on Wikidata.")
    lang = (await wd.language(person.language)) if person.language else place.languages[0]
    if lang is None:
        raise ValueError(f"Unknown language '{person.language}'.")

    fav_ids = await wd.resolve_all(person.favourites)
    films, composers, singers, featured = await asyncio.gather(
        wd.films(lang, place.country, start, end, take),
        wd.film_composers(lang, place.country, start, end, take // 2 + 1),
        # Performers who were roughly 20-50 during the person's formative years.
        wd.singers(lang, place.country, start - 50, end - 20, take // 2 + 1),
        wd.films_featuring(fav_ids, start - 5, end + 5, take),
    )

    music, seen = [], set()
    for item in [x for pair in zip(composers, singers) for x in pair] + composers + singers:
        if item["id"] not in seen:
            seen.add(item["id"])
            music.append({"name": item["name"], "note": item["note"]})
    return _frame(
        person,
        "wikidata",
        [{"name": f["name"], "year": f["year"]} for f in films],
        music[:take],
        [{"film": f["name"], "year": f["year"], "because": f["because"]} for f in featured],
    )


async def build_set(person: Person, take: int = 6, source: str | None = None) -> dict:
    if (source or default_source()) == "qloo":
        async with QlooClient() as qloo:
            return await build_with_qloo(qloo, person, take)
    async with Wikidata() as wd:
        return await build_with_wikidata(wd, person, take)
