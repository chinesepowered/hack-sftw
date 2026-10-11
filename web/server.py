"""Capybarrier web app: one page, backed by live MongoDB Atlas.

    uv run capybarrier web                                    live, at http://127.0.0.1:8000
    uv run capybarrier web --snapshot results/snapshot.json   offline, from the results exported from Atlas

The page (web/index.html) calls these JSON endpoints, which wrap capybarrier.live: the same agent code and the same
Atlas data the deployed agent on Atlas Agent Engine uses.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from capybarrier import live  # noqa: E402
from capybarrier.mascot import capybara_svg  # noqa: E402
from capybarrier.world import GUESTS  # noqa: E402

SNAPSHOT = os.getenv("CAPY_SNAPSHOT")
GUEST_IDS = {g["guest_id"] for g in GUESTS}
OFFLINE = "Offline mode: this needs the live MongoDB Atlas cluster."

app = FastAPI(title="Capybarrier", docs_url=None, redoc_url=None, openapi_url=None)


class TryRequest(BaseModel):
    message: str
    guest: str


def _snapshot() -> dict:
    with open(SNAPSHOT, encoding="utf-8") as fh:
        return json.load(fh)


def _atlas(fn, *args):
    try:
        return fn(*args)
    except HTTPException:
        raise
    except Exception as exc:  # show the real reason on the page instead of a bare 500
        return JSONResponse(status_code=502, content={"detail": f"MongoDB Atlas or a model endpoint failed: {exc}"})


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "web" / "index.html", media_type="text/html", headers={"Cache-Control": "no-store"})


@app.get("/kapi/{mood}.svg")
def kapi(mood: str) -> Response:
    if mood not in ("chill", "guard", "onsen"):
        raise HTTPException(status_code=404)
    return Response(capybara_svg(mood, size=None), media_type="image/svg+xml")


@app.get("/api/config")
def page_config() -> dict:
    return {"live": not SNAPSHOT, "examples": live.EXAMPLES, "probe": live.PROBE,
            "guests": [{"id": g["guest_id"], "name": g["name"]} for g in GUESTS]}


@app.get("/api/status")
def status():
    if SNAPSHOT:
        meta = _snapshot().get("meta", {})
        return {"live": False, "host": meta.get("mongodb_host"), "exported_at": meta.get("exported_at")}
    return _atlas(live.atlas_status)


@app.get("/api/results")
def results():
    if SNAPSHOT:
        snap = _snapshot()
        return {k: snap.get(k, []) for k in ("harness", "generations", "runbooks", "candidates", "production")}
    return _atlas(live.results)


@app.post("/api/try")
def try_message(req: TryRequest):
    if SNAPSHOT:
        raise HTTPException(status_code=503, detail=OFFLINE)
    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Write a message first.")
    if req.guest not in GUEST_IDS:
        raise HTTPException(status_code=400, detail="Pick a guest from the list.")
    return _atlas(live.run_try, message[:2000], req.guest)


@app.post("/api/probe")
def probe():
    if SNAPSHOT:
        raise HTTPException(status_code=503, detail=OFFLINE)
    return _atlas(live.probe_validator)
