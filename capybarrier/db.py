"""MongoDB access: collections, indexes, schema validation and the vector index."""

from __future__ import annotations

from functools import lru_cache

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database
from pymongo.errors import OperationFailure
from pymongo.operations import SearchIndexModel

from . import config

VECTOR_INDEX = "runbook_vec"

# The harness is the agent's genome. The database itself rejects malformed self patches.
HARNESS_SCHEMA = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["version", "rules", "skills", "guardrails", "tool_policy", "context_policy", "runbook_ids"],
        "properties": {
            "version": {"bsonType": "int", "minimum": 0},
            "rules": {"bsonType": "array", "maxItems": 40, "items": {"bsonType": "string", "maxLength": 600}},
            "skills": {
                "bsonType": "array",
                "maxItems": 16,
                "items": {
                    "bsonType": "object",
                    "required": ["name", "description", "body"],
                    "properties": {
                        "name": {"bsonType": "string", "pattern": "^[a-z0-9-]{3,48}$"},
                        "description": {"bsonType": "string", "maxLength": 300},
                        "body": {"bsonType": "string", "maxLength": 4000},
                    },
                },
            },
            "guardrails": {
                "bsonType": "array",
                "maxItems": 24,
                "items": {
                    "bsonType": "object",
                    "required": ["pattern", "action"],
                    "properties": {"action": {"enum": ["redact", "block"]}},
                },
            },
            "context_policy": {
                "bsonType": "object",
                "properties": {"runbook_threshold": {"bsonType": ["double", "int"], "minimum": 0.5, "maximum": 0.99}},
            },
            "runbook_ids": {"bsonType": "array", "items": {"bsonType": "string"}},
        },
    }
}


@lru_cache(maxsize=1)
def client() -> MongoClient:
    return MongoClient(config.MONGODB_URI, appname="capybarrier", serverSelectionTimeoutMS=20000)


def db() -> Database:
    return client()[config.DB_NAME]


def ensure_collections() -> None:
    d = db()
    existing = set(d.list_collection_names())
    if "harness" not in existing:
        d.create_collection("harness", validator=HARNESS_SCHEMA, validationLevel="strict")
    else:
        d.command("collMod", "harness", validator=HARNESS_SCHEMA, validationLevel="strict")
    for name in ("runbooks", "episodes", "generations", "embed_cache", "guests", "bookings", "events", "evolver_stats"):
        if name not in existing:
            d.create_collection(name)


def ensure_indexes() -> str:
    ensure_collections()
    d = db()
    d.harness.create_index([("version", DESCENDING)], unique=True)
    d.harness.create_index("active")
    d.episodes.create_index([("generation", ASCENDING), ("split", ASCENDING)])
    d.episodes.create_index("case_id")
    d.generations.create_index([("generation", ASCENDING), ("split", ASCENDING)])
    d.embed_cache.create_index("key", unique=True)
    d.runbooks.create_index("rid", unique=True)
    d.events.create_index([("ts", DESCENDING)])
    return ensure_vector_index()


def ensure_vector_index() -> str:
    """Create the Atlas Vector Search index over runbook fingerprints. Returns its status."""
    coll = db().runbooks
    try:
        existing = list(coll.list_search_indexes(VECTOR_INDEX))
    except OperationFailure as exc:
        return f"unavailable: {exc.details.get('errmsg', exc) if exc.details else exc}"
    if existing:
        return str(existing[0].get("status", "unknown"))
    definition = {
        "fields": [
            {"type": "vector", "path": "embedding", "numDimensions": config.EMBED_DIM, "similarity": "cosine"},
            {"type": "filter", "path": "rid"},
        ]
    }
    try:
        coll.create_search_index(SearchIndexModel(definition=definition, name=VECTOR_INDEX, type="vectorSearch"))
    except OperationFailure as exc:
        return f"create failed: {exc}"
    return "building"


def vector_index_ready() -> bool:
    try:
        indexes = list(db().runbooks.list_search_indexes(VECTOR_INDEX))
    except OperationFailure:
        return False
    return bool(indexes) and indexes[0].get("status") == "READY" and indexes[0].get("queryable", True)


def log_event(kind: str, **fields) -> None:
    import datetime as _dt

    db().events.insert_one({"kind": kind, "ts": _dt.datetime.now(_dt.timezone.utc), **fields})
