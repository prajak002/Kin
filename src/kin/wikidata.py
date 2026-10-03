"""Open-data taste source backed by Wikidata (CC0), used when no Qloo key is set."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

SPARQL_URL = "https://query.wikidata.org/sparql"
API_URL = "https://www.wikidata.org/w/api.php"
USER_AGENT = os.environ.get("KIN_USER_AGENT", "kin/0.1 (open-source care companion)")

FILM = "Q11424"
SINGER = "Q177220"
PLAYBACK_SINGER = "Q2643890"
ACTOR = "Q33999"
FILM_ACTOR = "Q10800557"

LANGUAGES = {
    "assamese": "Q29401", "bengali": "Q9610", "english": "Q1860", "french": "Q150",
    "german": "Q188", "gujarati": "Q5137", "hindi": "Q1568", "italian": "Q652",
    "japanese": "Q5287", "kannada": "Q33673", "malayalam": "Q36236", "marathi": "Q1571",
    "odia": "Q33810", "portuguese": "Q5146", "punjabi": "Q58635", "spanish": "Q1321",
    "tamil": "Q5885", "telugu": "Q8097", "urdu": "Q1617",
}

_QID = re.compile(r"^Q\d+$")


@dataclass
class Place:
    qid: str
    country: str
    languages: list[str]


def _qid(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def _labelled(rows: list[dict[str, str]], key: str) -> list[dict[str, str]]:
    return [r for r in rows if r.get(key) and not _QID.match(r[key])]


class Wikidata:
    def __init__(self, cache_dir: str | os.PathLike | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self._http = httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=60, transport=transport)
        self._cache = Path(cache_dir or os.environ.get("KIN_CACHE", ".kin/cache"))
        self._cache.mkdir(parents=True, exist_ok=True)

    async def __aenter__(self) -> Wikidata:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._http.aclose()

    async def _cached(self, key: str, fetch) -> Any:
        path = self._cache / f"{hashlib.sha1(key.encode()).hexdigest()}.json"
        if path.exists():
            return json.loads(path.read_text())
        value = await fetch()
        path.write_text(json.dumps(value, ensure_ascii=False))
        return value

    async def sparql(self, query: str) -> list[dict[str, str]]:
        async def fetch():
            resp = await self._http.get(SPARQL_URL, params={"query": query, "format": "json"})
            resp.raise_for_status()
            return [{k: v["value"] for k, v in b.items()} for b in resp.json()["results"]["bindings"]]

        return await self._cached(query, fetch)

    async def search(self, text: str) -> str | None:
        async def fetch():
            resp = await self._http.get(API_URL, params={
                "action": "wbsearchentities", "search": text, "language": "en",
                "type": "item", "format": "json", "limit": 1,
            })
            resp.raise_for_status()
            hits = resp.json().get("search", [])
            return hits[0]["id"] if hits else None

        return await self._cached(f"search:{text}", fetch)

    async def language(self, name: str) -> str | None:
        return LANGUAGES.get(name.strip().lower()) or await self.search(f"{name} language")

    async def place(self, hometown: str) -> Place | None:
        qid = await self.search(hometown)
        if qid is None:
            return None
        # Prefer the city's own official language, then its region's, then the country's.
        rows = await self.sparql(f"""
            SELECT ?country ?lang ?depth WHERE {{
              wd:{qid} wdt:P17 ?country .
              {{ wd:{qid} wdt:P37 ?lang BIND(0 AS ?depth) }}
              UNION {{ wd:{qid} wdt:P131 ?a . ?a wdt:P37 ?lang BIND(1 AS ?depth) }}
              UNION {{ wd:{qid} wdt:P131/wdt:P131 ?b . ?b wdt:P37 ?lang BIND(2 AS ?depth) }}
              UNION {{ ?country wdt:P37 ?lang BIND(3 AS ?depth) }}
            }} ORDER BY ?depth""")
        if not rows:
            return None
        langs = list(dict.fromkeys(_qid(r["lang"]) for r in rows))
        return Place(qid, _qid(rows[0]["country"]), langs)

    async def films(self, lang: str, country: str, start: int, end: int, take: int) -> list[dict]:
        rows = await self.sparql(f"""
            SELECT ?f ?fLabel (MIN(?year) AS ?y) (MAX(?links) AS ?l) WHERE {{
              ?f wdt:P31 wd:{FILM}; wdt:P364 wd:{lang}; wdt:P495 wd:{country};
                 wdt:P577 ?d; wikibase:sitelinks ?links .
              BIND(YEAR(?d) AS ?year) FILTER(?year >= {start} && ?year <= {end})
              SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
            }} GROUP BY ?f ?fLabel ORDER BY DESC(?l) LIMIT {take * 2}""")
        return [{"name": r["fLabel"], "year": int(r["y"]), "id": _qid(r["f"])} for r in _labelled(rows, "fLabel")][:take]

    async def film_composers(self, lang: str, country: str, start: int, end: int, take: int) -> list[dict]:
        rows = await self.sparql(f"""
            SELECT ?c ?cLabel (COUNT(DISTINCT ?f) AS ?n) (SAMPLE(?fLabel) AS ?film) WHERE {{
              ?f wdt:P31 wd:{FILM}; wdt:P364 wd:{lang}; wdt:P495 wd:{country}; wdt:P577 ?d; wdt:P86 ?c .
              FILTER(YEAR(?d) >= {start} && YEAR(?d) <= {end})
              SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en".
                ?f rdfs:label ?fLabel . ?c rdfs:label ?cLabel . }}
            }} GROUP BY ?c ?cLabel ORDER BY DESC(?n) LIMIT {take * 2}""")
        return [
            {"name": r["cLabel"], "note": f"music for {r['n']} films, including {r['film']}", "id": _qid(r["c"])}
            for r in _labelled(rows, "cLabel")
            if not _QID.match(r.get("film", ""))
        ][:take]

    async def singers(self, lang: str, country: str, born_from: int, born_to: int, take: int) -> list[dict]:
        rows = await self.sparql(f"""
            SELECT ?p ?pLabel (MAX(?links) AS ?l) WHERE {{
              {{ ?p wdt:P106 wd:{PLAYBACK_SINGER} }}
              UNION {{ ?p wdt:P106 wd:{SINGER}
                       FILTER NOT EXISTS {{ ?p wdt:P106 wd:{ACTOR} }}
                       FILTER NOT EXISTS {{ ?p wdt:P106 wd:{FILM_ACTOR} }} }}
              {{ ?p wdt:P1412 wd:{lang} }} UNION {{ ?p wdt:P103 wd:{lang} }}
              ?p wdt:P27 wd:{country}; wdt:P569 ?b; wikibase:sitelinks ?links .
              FILTER(YEAR(?b) >= {born_from} && YEAR(?b) <= {born_to})
              SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
            }} GROUP BY ?p ?pLabel ORDER BY DESC(?l) LIMIT {take * 2}""")
        return [{"name": r["pLabel"], "note": "singer", "id": _qid(r["p"])} for r in _labelled(rows, "pLabel")][:take]

    async def films_featuring(self, people: list[str], start: int, end: int, take: int) -> list[dict]:
        if not people:
            return []
        values = " ".join(f"wd:{p}" for p in people)
        rows = await self.sparql(f"""
            SELECT ?f ?fLabel ?pLabel (MIN(?year) AS ?y) (MAX(?links) AS ?l) WHERE {{
              VALUES ?p {{ {values} }}
              {{ ?f wdt:P57 ?p }} UNION {{ ?f wdt:P161 ?p }} UNION {{ ?f wdt:P86 ?p }}
              ?f wdt:P31 wd:{FILM}; wdt:P577 ?d; wikibase:sitelinks ?links .
              BIND(YEAR(?d) AS ?year) FILTER(?year >= {start} && ?year <= {end})
              SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
            }} GROUP BY ?f ?fLabel ?pLabel ORDER BY DESC(?l) LIMIT {take * 2}""")
        return [
            {"name": r["fLabel"], "year": int(r["y"]), "because": r["pLabel"], "id": _qid(r["f"])}
            for r in _labelled(rows, "fLabel")
        ][:take]

    async def resolve_all(self, names: list[str]) -> list[str]:
        found = await asyncio.gather(*(self.search(n) for n in names))
        return [q for q in found if q]
