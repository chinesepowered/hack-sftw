"""Voyage AI embeddings through MongoDB Atlas (ai.mongodb.com), cached in MongoDB."""

from __future__ import annotations

import hashlib
import threading
import time

import httpx
import numpy as np

from . import config
from .db import db

_memory: dict[str, list[float]] = {}
_lock = threading.Lock()


class EmbeddingError(RuntimeError):
    pass


def _key(text: str) -> str:
    return hashlib.sha1(f"{config.EMBED_MODEL}|{text}".encode("utf-8")).hexdigest()


def _fetch(texts: list[str]) -> list[list[float]]:
    if not config.VOYAGE_API_KEY:
        raise EmbeddingError("VOYAGE_API_KEY is not set")
    payload = {"input": texts, "model": config.EMBED_MODEL}
    headers = {"Authorization": f"Bearer {config.VOYAGE_API_KEY}"}
    delay = 1.0
    for attempt in range(6):
        try:
            resp = httpx.post(f"{config.VOYAGE_BASE_URL}/embeddings", json=payload, headers=headers, timeout=60)
        except httpx.HTTPError as exc:
            if attempt == 5:
                raise EmbeddingError(str(exc)) from exc
            time.sleep(delay)
            delay *= 2
            continue
        if resp.status_code == 200:
            data = sorted(resp.json()["data"], key=lambda d: d["index"])
            return [d["embedding"] for d in data]
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < 5:
            time.sleep(delay)
            delay *= 2
            continue
        raise EmbeddingError(f"Voyage error {resp.status_code}: {resp.text[:300]}")
    raise EmbeddingError("unreachable")


def embed(texts: list[str]) -> list[list[float]]:
    """Embed texts symmetrically (no input_type) so attack-to-attack similarity is meaningful."""
    keys = [_key(t) for t in texts]
    out: dict[str, list[float]] = {}
    with _lock:
        for k in keys:
            if k in _memory:
                out[k] = _memory[k]
    missing = [k for k in keys if k not in out]
    if missing:
        for doc in db().embed_cache.find({"key": {"$in": missing}}, {"key": 1, "v": 1}):
            out[doc["key"]] = doc["v"]
    todo = [(k, t) for k, t in zip(keys, texts) if k not in out]
    # de-duplicate while keeping order
    seen: set[str] = set()
    todo = [(k, t) for k, t in todo if not (k in seen or seen.add(k))]
    for i in range(0, len(todo), 64):
        batch = todo[i : i + 64]
        vectors = _fetch([t for _, t in batch])
        docs = []
        for (k, _), v in zip(batch, vectors):
            out[k] = v
            docs.append({"key": k, "v": v})
        try:
            db().embed_cache.insert_many(docs, ordered=False)
        except Exception:
            pass  # duplicates from concurrent workers are fine
    with _lock:
        _memory.update(out)
    return [out[k] for k in keys]


def embed_one(text: str) -> list[float]:
    return embed([text])[0]


def cosine_matrix(query: list[float], matrix: np.ndarray) -> np.ndarray:
    q = np.asarray(query, dtype=np.float32)
    q = q / (np.linalg.norm(q) + 1e-9)
    m = matrix / (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9)
    return m @ q
