"""Runtime configuration, read from environment variables and an optional .env file."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _get(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


MONGODB_URI = _get("MONGODB_URI", "mongodb://127.0.0.1:27017/?directConnection=true")
DB_NAME = _get("CAPY_DB", "capybarrier")

VOYAGE_API_KEY = _get("VOYAGE_API_KEY")
VOYAGE_BASE_URL = _get("VOYAGE_BASE_URL", "https://ai.mongodb.com/v1")
EMBED_MODEL = _get("EMBED_MODEL", "voyage-4-lite")
EMBED_DIM = int(_get("EMBED_DIM", "1024"))

LLM_BASE_URL = _get("LLM_BASE_URL", "https://openrouter.ai/api/v1")
LLM_API_KEY = _get("LLM_API_KEY")
AGENT_MODEL = _get("AGENT_MODEL", "openai/gpt-4o-mini")
EVOLVER_MODEL = _get("EVOLVER_MODEL", "anthropic/claude-sonnet-4.5")

# "auto" uses Atlas Vector Search when the index is ready, otherwise exact cosine in process.
RUNBOOK_SEARCH = _get("RUNBOOK_SEARCH", "auto")

CONCURRENCY = int(_get("CAPY_CONCURRENCY", "8"))
MAX_AGENT_TURNS = int(_get("CAPY_MAX_TURNS", "6"))

AE_BASE_URL = _get("AGENTENGINE_BASE_URL", "https://agentengine.mongodb.com/api/v1")
AE_CLIENT_ID = _get("AGENTENGINE_CLIENT_ID")
AE_CLIENT_SECRET = _get("AGENTENGINE_CLIENT_SECRET")
AE_PROJECT_ID = _get("AGENTENGINE_PROJECT_ID")
AE_WORKSPACE_ID = _get("AGENTENGINE_WORKSPACE_ID")

ELEVENLABS_API_KEY = _get("ELEVENLABS_API_KEY")

# Raindrop: the write key sends events and signals, the Query API key reads flagged production events back.
RAINDROP_WRITE_KEY = _get("RAINDROP_WRITE_KEY")
RAINDROP_QUERY_API_KEY = _get("RAINDROP_QUERY_API_KEY")
RAINDROP_PROJECT = _get("RAINDROP_PROJECT")
RAINDROP_ENDPOINT = _get("RAINDROP_ENDPOINT")
RAINDROP_QUERY_URL = _get("RAINDROP_QUERY_URL", "https://query.raindrop.ai/v1")
