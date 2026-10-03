"""Builds reminiscence material from the years a person grew up in.

Autobiographical memory is densest for events between roughly ages 10 and 30
(the "reminiscence bump"), so that window drives the release-year filters.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date

from .qloo import ARTIST, MOVIE, Entity, QlooClient

BUMP_START, BUMP_END = 10, 30


@dataclass
class Person:
    name: str
    birth_year: int
    hometown: str
    favourites: list[str] = field(default_factory=list)

    @property
    def age(self) -> int:
        return date.today().year - self.birth_year

    @property
    def formative_years(self) -> tuple[int, int]:
        return self.birth_year + BUMP_START, self.birth_year + BUMP_END


@dataclass
class ReminiscenceSet:
    person: Person
    films: list[Entity]
    music: list[Entity]
    seeds: list[Entity]

    def to_dict(self) -> dict:
        def pack(items: list[Entity]) -> list[dict]:
            return [{"name": e.name, "year": e.year, "affinity": e.affinity} for e in items]

        start, end = self.person.formative_years
        return {
            "person": self.person.name,
            "formative_years": f"{start}-{end}",
            "hometown": self.person.hometown,
            "seeds": [e.name for e in self.seeds],
            "films": pack(self.films),
            "music": pack(self.music),
        }


async def resolve_favourites(qloo: QlooClient, favourites: list[str]) -> list[Entity]:
    found = await asyncio.gather(*(qloo.search(f, take=1) for f in favourites))
    return [hits[0] for hits in found if hits]


async def build_set(qloo: QlooClient, person: Person, take: int = 8) -> ReminiscenceSet:
    seeds = await resolve_favourites(qloo, person.favourites)
    interests = [e.entity_id for e in seeds] or None
    start, end = person.formative_years

    films, music = await asyncio.gather(
        qloo.insights(
            MOVIE,
            interests=interests,
            age=person.age,
            location_query=person.hometown,
            release_year_min=start,
            release_year_max=end,
            take=take,
        ),
        qloo.insights(
            ARTIST,
            interests=interests,
            age=person.age,
            location_query=person.hometown,
            take=take,
        ),
    )
    return ReminiscenceSet(person=person, films=films, music=music, seeds=seeds)
