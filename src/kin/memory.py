"""Long-term memory: life details a person shares, recalled later (RAG).

"My husband Arun was a schoolteacher." "We lived in Shillong in the sixties."
The agent saves these with `save_memory` and looks them up with
`search_memories`, so conversations build on each other instead of starting over.

Two backends behind one interface:
  VectorMemory   Upstash Vector, with embeddings from the open Hugging Face
                 model BAAI/bge-small-en-v1.5 run in-process (fastembed,
                 ONNX), so retrieval is semantic. Used when
                 UPSTASH_VECTOR_REST_URL / _TOKEN are set.
  StoreMemory    keyword overlap over memories kept in Kin's main store.
                 No extra service; used everywhere else.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

import httpx

EMBED_MODEL = "BAAI/bge-small-en-v1.5"  # 384 dimensions, MIT licence
_embedder = None


def embed(text: str) -> list[float]:
    """Embed with the open BGE model. Loaded on first use and kept for the
    life of the process (about 64 MB, cached under /tmp on serverless)."""
    global _embedder
    if _embedder is None:
        if os.environ.get("VERCEL"):  # only /tmp is writable; the Hub client writes under HF_HOME
            os.environ.setdefault("HF_HOME", "/tmp/hf-home")
        from fastembed import TextEmbedding

        cache = "/tmp/kin-fastembed" if os.environ.get("VERCEL") else ".kin/models"
        _embedder = TextEmbedding(EMBED_MODEL, cache_dir=os.environ.get("KIN_MODEL_CACHE", cache))
    return [float(x) for x in next(iter(_embedder.embed([text])))]


STOP = set("a an and are as at be but by for from had has have he her his i in is it its me my of on or our she so that the their them they this to was we were with you your".split())


def _words(text: str) -> set[str]:
    return {w.rstrip("s") for w in re.findall(r"[a-zऀ-৿]+", text.lower()) if w not in STOP and len(w) > 2}


class Memory(Protocol):
    def remember(self, person_id: str, fact: str) -> dict[str, Any]: ...
    def recall(self, person_id: str, query: str, k: int = 5) -> list[dict[str, Any]]: ...


class StoreMemory:
    def __init__(self, store):
        self.store = store

    def remember(self, person_id: str, fact: str) -> dict[str, Any]:
        return self.store._append("memories", {"person_id": person_id, "fact": fact})

    def recall(self, person_id: str, query: str, k: int = 5) -> list[dict[str, Any]]:
        q = _words(query)
        scored = [(len(q & _words(m["fact"])), m) for m in self.store.recent("memories", person_id, 500)]
        hits = [m for score, m in sorted(scored, key=lambda s: -s[0]) if score > 0][:k]
        return hits or self.store.recent("memories", person_id, k)[::-1]  # nothing matched: most recent


class VectorMemory:
    def __init__(self, url: str | None = None, token: str | None = None, transport: httpx.BaseTransport | None = None,
                 embedder=embed):
        self._embed = embedder
        self._http = httpx.Client(
            base_url=url or os.environ["UPSTASH_VECTOR_REST_URL"],
            headers={"Authorization": f"Bearer {token or os.environ['UPSTASH_VECTOR_REST_TOKEN']}"},
            timeout=10,
            transport=transport,
        )

    def remember(self, person_id: str, fact: str) -> dict[str, Any]:
        row = {"person_id": person_id, "fact": fact, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        self._http.post("/upsert", json=[{"id": str(uuid.uuid4()), "vector": self._embed(fact), "metadata": row}]).raise_for_status()
        return row

    def recall(self, person_id: str, query: str, k: int = 5) -> list[dict[str, Any]]:
        r = self._http.post("/query", json={
            "vector": self._embed(query), "topK": k, "includeMetadata": True, "filter": f"person_id = '{person_id}'",
        })
        r.raise_for_status()
        return [{**hit["metadata"], "score": round(hit["score"], 3)} for hit in r.json()["result"]]


def open_memory(store) -> Memory:
    if os.environ.get("UPSTASH_VECTOR_REST_URL"):
        return VectorMemory()
    return StoreMemory(store)
