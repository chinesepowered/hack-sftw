"""Snapshot the evolution history from MongoDB into one JSON file (for the dashboard and the demo video)."""

from __future__ import annotations

import datetime as dt
import json
import os

from . import config
from .db import db, vector_index_ready


def _clean(doc: dict) -> dict:
    out = {}
    for k, v in doc.items():
        if k == "embedding":
            continue
        if k == "_id":
            v = str(v)
        out[k] = v
    return out


def snapshot_data(max_episodes: int = 400) -> dict:
    d = db()
    harness = [_clean(h) for h in d.harness.find({}, sort=[("version", 1)])]
    generations = [_clean(g) for g in d.generations.find({}, sort=[("generation", 1), ("split", 1)])]
    runbooks = [_clean(r) for r in d.runbooks.find({"status": "active"}, sort=[("created_at", 1)])]
    gens = [g["generation"] for g in generations]
    keep = {min(gens), max(gens)} if gens else set()
    episodes = [_clean(e) for e in d.episodes.find({"generation": {"$in": list(keep)}}, sort=[("ts", 1)]).limit(max_episodes)]
    events = [_clean(e) for e in d.events.find({}, sort=[("ts", -1)]).limit(60)]
    return {
        "meta": {
            "exported_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "database": config.DB_NAME,
            "atlas_vector_search": vector_index_ready(),
            "agent_model": config.AGENT_MODEL,
            "evolver_model": config.EVOLVER_MODEL,
            "embed_model": config.EMBED_MODEL,
        },
        "harness": harness,
        "generations": generations,
        "runbooks": runbooks,
        "episodes": episodes,
        "events": events,
    }


def export_snapshot(path: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(snapshot_data(), fh, ensure_ascii=False, indent=1, default=str)
    return path


def load_snapshot(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
