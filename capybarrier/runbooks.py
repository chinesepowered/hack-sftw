"""The runbook cache: Capybarrier's immune memory.

Each runbook pairs an attack fingerprint (a Voyage embedding of a known malicious span) with
what to do when something similar shows up again (block, sanitize, or send to a human).
Lookups run as Atlas Vector Search queries when the index is ready, so recognizing a known
attack costs one embedding and one $vectorSearch instead of an LLM call.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
import uuid

import numpy as np
from pymongo.errors import OperationFailure

from . import config
from .db import VECTOR_INDEX, db, vector_index_ready
from .embed import cosine_matrix, embed, embed_one

DEFAULT_RESPONSE = (
    "Kapi here! I can't help with that one, but I'm happy to help with your bookings, refunds or spa questions."
)

_ready_cache = {"value": False, "checked": 0.0}
_ready_lock = threading.Lock()


def atlas_search_enabled() -> bool:
    if config.RUNBOOK_SEARCH == "local":
        return False
    with _ready_lock:
        if time.time() - _ready_cache["checked"] > 60:
            _ready_cache["value"] = vector_index_ready()
            _ready_cache["checked"] = time.time()
        return _ready_cache["value"]


def create_runbooks(patch: dict, created_in: int, source_cases: list[str]) -> list[str]:
    """Create one runbook document per exemplar. Status starts as 'candidate' until a receipt passes."""
    exemplars = [str(e).strip() for e in patch.get("exemplars", []) if str(e).strip()][:4]
    if not exemplars:
        raise ValueError("add_runbook needs at least one exemplar")
    action = patch.get("action", "block")
    if action not in ("block", "sanitize", "review"):
        action = "block"
    vectors = embed(exemplars)
    rids = []
    docs = []
    for text, vec in zip(exemplars, vectors):
        rid = f"rb-{uuid.uuid4().hex[:8]}"
        rids.append(rid)
        docs.append({
            "rid": rid,
            "label": str(patch.get("label", "suspicious request"))[:120],
            "exemplar": text[:2000],
            "embedding": vec,
            "action": action,
            "response": str(patch.get("response") or DEFAULT_RESPONSE)[:500],
            "runbook": str(patch.get("runbook", ""))[:2000],
            "created_in": created_in,
            "source_cases": source_cases,
            "status": "candidate",
            "hits": 0,
            "created_at": dt.datetime.now(dt.timezone.utc),
        })
    db().runbooks.insert_many(docs)
    return rids


def set_status(rids: list[str], status: str) -> None:
    if rids:
        db().runbooks.update_many({"rid": {"$in": rids}}, {"$set": {"status": status}})


def wait_searchable(rids: list[str], timeout: float = 30.0) -> None:
    """Atlas Vector Search indexes new documents within seconds. Wait so receipts are honest."""
    if not rids or not atlas_search_enabled():
        return
    deadline = time.time() + timeout
    pending = set(rids)
    docs = {d["rid"]: d["embedding"] for d in db().runbooks.find({"rid": {"$in": rids}}, {"rid": 1, "embedding": 1})}
    while pending and time.time() < deadline:
        for rid in list(pending):
            pipeline = [{"$vectorSearch": {"index": VECTOR_INDEX, "path": "embedding", "queryVector": docs[rid],
                                           "numCandidates": 10, "limit": 1, "filter": {"rid": {"$in": [rid]}}}},
                        {"$project": {"rid": 1}}]
            try:
                if list(db().runbooks.aggregate(pipeline)):
                    pending.discard(rid)
            except OperationFailure:
                return
        if pending:
            time.sleep(1.0)


class RunbookCache:
    def __init__(self, rids: list[str]):
        self.rids = list(rids)
        self.mode = "atlas" if atlas_search_enabled() else "local"
        self._docs: list[dict] | None = None
        self._matrix: np.ndarray | None = None

    @property
    def size(self) -> int:
        return len(self.rids)

    def _load(self) -> None:
        if self._docs is None:
            self._docs = list(db().runbooks.find({"rid": {"$in": self.rids}}))
            self._matrix = (
                np.asarray([d["embedding"] for d in self._docs], dtype=np.float32) if self._docs else None
            )

    def match(self, text: str, threshold: float) -> tuple[dict, float] | None:
        if not self.rids or not text or len(text.strip()) < 8:
            return None
        query = embed_one(text[:4000])
        if self.mode == "atlas":
            pipeline = [
                {"$vectorSearch": {"index": VECTOR_INDEX, "path": "embedding", "queryVector": query,
                                   "numCandidates": 100, "limit": 1, "filter": {"rid": {"$in": self.rids}}}},
                {"$project": {"embedding": 0, "score": {"$meta": "vectorSearchScore"}}},
            ]
            try:
                rows = list(db().runbooks.aggregate(pipeline))
            except OperationFailure:
                rows = None
            if rows is not None:
                if not rows:
                    return None
                doc = rows[0]
                cosine = 2.0 * float(doc.pop("score")) - 1.0  # Atlas normalizes cosine to (1 + cos) / 2
                doc["served_by"] = "atlas_vector_search"
                return (doc, cosine) if cosine >= threshold else None
        self._load()
        if self._matrix is None:
            return None
        sims = cosine_matrix(query, self._matrix)
        best = int(np.argmax(sims))
        cosine = float(sims[best])
        if cosine < threshold:
            return None
        doc = {k: v for k, v in self._docs[best].items() if k != "embedding"}
        doc["served_by"] = "local_exact_cosine"
        return doc, cosine


def record_hit(rid: str) -> None:
    db().runbooks.update_one({"rid": rid}, {"$inc": {"hits": 1}, "$set": {"last_hit_at": dt.datetime.now(dt.timezone.utc)}})
