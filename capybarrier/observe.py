"""Raindrop: the sensor in Capybarrier's self improvement loop.

Write side (RAINDROP_WRITE_KEY). Every scored episode, every evolver candidate and every production request is sent
to Raindrop as an AI event, and what happened is attached to it as a signal:

    breach              NEGATIVE  an attack got through (master key leak, PII leak, exfiltration, refund abuse)
    benign_failed       NEGATIVE  a normal guest request was refused or mishandled
    attack_stopped      POSITIVE  an attack was stopped (by the runbook cache, a policy, a guardrail or the model)
    runbook_block       POSITIVE  stopped at the door by Atlas Vector Search, with no LLM call
    guardrail_block     POSITIVE  an output guardrail replaced the reply
    receipts_failed     NEGATIVE  an evolver candidate did not earn its receipts
    candidate_promoted  POSITIVE  an evolver candidate earned its receipts and went live

Every event carries `harness_version`, so Raindrop charts breach and false refusal rates per version, and Raindrop
Experiments can compare harness cohorts on live traffic.

Read side (RAINDROP_QUERY_API_KEY). `intake()` pulls production events that carry a negative signal back out of
Raindrop through the Query API: our own `breach` signal, plus any signal Raindrop itself detects (a jailbreak topic,
for example). They are stored as cases for the evolver's next generation, which has to fix them with receipts like
any other breach. What goes wrong in production becomes the next version's training data.

All of it is best effort. Without keys these functions do nothing, and a Raindrop error never changes Kapi's behavior.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import threading
import uuid

import httpx

from . import config

EPISODE_EVENT = "kapi_episode"
PRODUCTION_EVENT = "kapi_production"
CANDIDATE_EVENT = "evolver_candidate"
_lock = threading.Lock()
_sdk_state: dict = {"tried": False, "mod": None}


def write_enabled() -> bool:
    return bool(config.RAINDROP_WRITE_KEY)


def query_enabled() -> bool:
    return bool(config.RAINDROP_QUERY_API_KEY)


def _sdk():
    """The Raindrop SDK, initialized once per process, or None when there is no write key."""
    with _lock:
        if not _sdk_state["tried"]:
            _sdk_state["tried"] = True
            if config.RAINDROP_WRITE_KEY:
                try:
                    import raindrop.analytics as raindrop

                    opts = {"tracing_enabled": False, "app_git": False}
                    if config.RAINDROP_PROJECT:
                        opts["project_id"] = config.RAINDROP_PROJECT
                    if config.RAINDROP_ENDPOINT:
                        opts["endpoint"] = config.RAINDROP_ENDPOINT
                    raindrop.init(config.RAINDROP_WRITE_KEY, **opts)
                    _sdk_state["mod"] = raindrop
                except Exception:
                    _sdk_state["mod"] = None
        return _sdk_state["mod"]


def _props(**kw) -> dict:
    """Raindrop properties are flat scalars: drop None, join lists, serialize dicts."""
    out = {}
    for key, value in kw.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            value = ",".join(str(v) for v in value)
        elif isinstance(value, dict):
            value = json.dumps(value, default=str)[:2000]
        out[key] = value
    return out


def _send(*, user_id: str, event: str, input: str, output: str, properties: dict, signals: list[tuple],
          convo_id: str | None = None, model: str | None = None) -> str | None:
    rd = _sdk()
    if rd is None:
        return None
    try:
        event_id = str(uuid.uuid4())
        rd.track_ai(user_id=user_id, event=event, event_id=event_id, model=model, input=(input or "")[:20000],
                    output=(output or "")[:20000], convo_id=convo_id, properties=properties)
        for name, sentiment, props in signals:
            rd.track_signal(event_id=event_id, name=name, sentiment=sentiment, properties=props or None)
        return event_id
    except Exception:
        return None


def signal(event_id: str | None, name: str, sentiment: str, **props) -> None:
    rd = _sdk()
    if rd is None or not event_id:
        return
    try:
        rd.track_signal(event_id=event_id, name=name, sentiment=sentiment, properties=_props(**props) or None)
    except Exception:
        pass


def flush() -> None:
    rd = _sdk()
    if rd is not None:
        try:
            rd.flush()
        except Exception:
            pass


# ---------------------------------------------------------------- write side

def _top_hit(t: dict) -> dict:
    hits = t.get("runbook_hits") or []
    return max(hits, key=lambda x: x.get("score") or 0) if hits else {}


def _signals(t: dict, kind: str | None) -> list[tuple]:
    out = []
    breaches = sorted(set(t.get("breaches") or []))
    blocked_by = t.get("blocked_by")
    if breaches:
        out.append(("breach", "NEGATIVE", {"types": ",".join(breaches)}))
    elif kind == "attack":
        out.append(("attack_stopped", "POSITIVE", {"stopped_by": blocked_by or "model"}))
    if kind == "benign" and not t.get("ok", True):
        out.append(("benign_failed", "NEGATIVE", {"blocked_by": blocked_by or "none"}))
    if blocked_by == "runbook":
        top = _top_hit(t)
        out.append(("runbook_block", "POSITIVE", _props(runbook=top.get("label"), similarity=top.get("score"))))
    elif blocked_by == "guardrail":
        out.append(("guardrail_block", "POSITIVE", {}))
    return out


def _common(t: dict) -> dict:
    top = _top_hit(t)
    breaches = sorted(set(t.get("breaches") or []))
    return {"harness_version": t.get("harness_version"), "blocked_by": t.get("blocked_by") or "none",
            "breached": bool(breaches), "breaches": breaches or None, "runbook_label": top.get("label"),
            "runbook_score": top.get("score"), "runbook_served_by": top.get("served_by"),
            "policy_blocks": len(t.get("policy_blocks") or []), "guardrail_hits": len(t.get("guardrail_hits") or []),
            "llm_calls": t.get("llm_calls"), "latency_ms": t.get("latency_ms")}


def episode(t: dict, *, generation: int | None, label: str) -> str | None:
    """One scored arena episode (a train or held out evaluation of a harness version)."""
    if not write_enabled() or t.get("error"):
        return None
    props = _props(source="arena", generation=generation, label=label, split=t.get("split"), kind=t.get("kind"),
                   family=t.get("family"), case_id=t.get("case_id"), ok=t.get("ok"), **_common(t))
    return _send(user_id=t.get("guest") or "arena", event=EPISODE_EVENT, input=t.get("message", ""),
                 output=t.get("final", ""), properties=props, signals=_signals(t, t.get("kind")),
                 convo_id=f"{label}-g{generation}-{t.get('case_id')}", model=config.AGENT_MODEL)


def production(t: dict, *, guest: str, message: str, final: str, source: str) -> str | None:
    """One live request to the deployed agent."""
    if not write_enabled():
        return None
    try:
        props = _props(source=source, **_common(t))
        return _send(user_id=guest, event=PRODUCTION_EVENT, input=message, output=final, properties=props,
                     signals=_signals(t, None), model=config.AGENT_MODEL)
    except Exception:
        return None


def candidate(c: dict) -> str | None:
    """One evolver candidate and its receipts."""
    if not write_enabled():
        return None
    r = c.get("receipts", {})
    broke = len(r.get("benign_broken_ids") or [])
    props = _props(source="evolver", generation=c.get("generation"), parent_version=c.get("parent_version"),
                   name=c.get("name"), passed=c.get("passed"), fixed=r.get("fixed"),
                   replayed_breaches=r.get("replayed_breaches"), benign_passed=r.get("benign_passed"),
                   benign_total=r.get("benign_total"), benign_broken=broke,
                   patch_types=sorted({str(p.get("type", "")) for p in c.get("patches", [])}))
    signals = [] if c.get("passed") else [("receipts_failed", "NEGATIVE", _props(fixed=r.get("fixed"), broke=broke))]
    return _send(user_id="capybarrier-evolver", event=CANDIDATE_EVENT, input=c.get("hypothesis") or c.get("name", ""),
                 output=json.dumps(c.get("patches", []), default=str), properties=props, signals=signals,
                 convo_id=f"generation-{c.get('generation')}", model=config.EVOLVER_MODEL)


# ---------------------------------------------------------------- read side

def intake(limit: int = 100, signal_names: list[str] | None = None, log=print) -> list[dict]:
    """Pull production events with negative signals out of Raindrop and store them as cases for the evolver."""
    from .arena import ALL
    from .db import db
    from .world import GUESTS

    if not query_enabled():
        log("raindrop intake skipped: RAINDROP_QUERY_API_KEY is not set")
        return []
    headers = {"Authorization": f"Bearer {config.RAINDROP_QUERY_API_KEY}"}
    if config.RAINDROP_PROJECT:
        headers["X-Raindrop-Project-Id"] = config.RAINDROP_PROJECT
    guest_ids = {g["guest_id"] for g in GUESTS}
    # held out attacks stay held out, even if someone replays them against production
    heldout = {c["message"] for c in ALL if c["split"] == "heldout"}
    names = set(signal_names or [])
    new: list[dict] = []
    with httpx.Client(base_url=config.RAINDROP_QUERY_URL, headers=headers, timeout=30) as http:
        resp = http.get("/signals", params={"limit": 500})
        resp.raise_for_status()
        signals = resp.json().get("data", [])
        wanted = [s for s in signals if (s.get("name") in names if names else s.get("sentiment") == "NEGATIVE")]
        for s in wanted:
            resp = http.get("/events", params={"signal": s["id"], "event_name": PRODUCTION_EVENT, "limit": limit})
            resp.raise_for_status()
            for e in resp.json().get("data", []):
                text = (e.get("user_input") or "").strip()
                if not text or text.startswith("/") or text in heldout:
                    continue  # operator commands and held out cases are never training data
                cid = "rd-" + hashlib.sha1(text.encode()).hexdigest()[:10]
                case = {"id": cid, "guest": e.get("user_id") if e.get("user_id") in guest_ids else "G-103",
                        "message": text, "kind": "attack", "family": f"raindrop:{s.get('name')}", "split": "intake",
                        "source": "raindrop", "raindrop_event_id": e.get("id"), "raindrop_signal": s.get("name")}
                res = db().intake.update_one({"_id": cid}, {"$setOnInsert": {**case, "ts": dt.datetime.now(dt.timezone.utc)}},
                                             upsert=True)
                if res.upserted_id is not None:
                    new.append(case)
    log(f"raindrop intake: {len(new)} new production cases from {len(wanted)} negative signals")
    return new


def intake_cases() -> list[dict]:
    """Every production case pulled from Raindrop so far."""
    from .db import db

    return [{k: v for k, v in c.items() if k not in ("_id", "ts")} for c in db().intake.find({})]
