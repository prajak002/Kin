"""Fact look-ups on Wikipedia, so Kin answers general questions from a source
instead of from what the model half-remembers.

One API call: the top search results, each with the passage that matched the
question (often the answer itself, "Tigers are the national animal there") and
the first sentences of the article. Free, no key; CC BY-SA text.
"""

from __future__ import annotations

import html
import re

import httpx

from .wikidata import USER_AGENT

API = "https://en.wikipedia.org/w/api.php"


def _plain(snippet: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", snippet)).strip()


async def look_up(question: str, results: int = 5, transport: httpx.AsyncBaseTransport | None = None) -> list[dict[str, str]]:
    params = {
        "action": "query", "format": "json", "formatversion": "2", "utf8": "1",
        "list": "search", "srsearch": question, "srlimit": results, "srprop": "snippet",
        "generator": "search", "gsrsearch": question, "gsrlimit": results,
        "prop": "extracts", "exintro": "1", "explaintext": "1", "exsentences": "2", "exlimit": results,
    }
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=10, transport=transport) as http:
        data = (await http.get(API, params=params)).raise_for_status().json()
    query = data.get("query", {})
    intros = {p["title"]: p.get("extract", "") for p in query.get("pages", [])}
    return [
        {"title": r["title"], "passage": _plain(r["snippet"]), "intro": intros.get(r["title"], "")[:400]}
        for r in query.get("search", [])
    ]
