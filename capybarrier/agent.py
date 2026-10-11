"""Kapi, the CapySpa concierge agent, running under whatever harness version is active.

The building blocks here (input screening, tool execution under policy, output guardrails) are shared
by the local evaluation loop and by the Atlas Agent Engine deployment in agentengine/.
"""

from __future__ import annotations

import json
import re
import time

import numpy as np

from . import config, llm
from .embed import cosine_matrix, embed
from .harness import all_runbook_ids, render_system_prompt
from .runbooks import DEFAULT_RESPONSE, RunbookCache, record_hit
from .world import TOOLS, UNTRUSTED_FIELDS, World, allowed_recipient, detect_breaches

GUARDRAIL_REPLY = "Kapi here! I had to hold that reply back for safety reasons. Is there anything else I can help with?"
REVIEW_REPLY = "I've passed this request to a human team member who will follow up shortly."
_THINK = re.compile(r"<think>.*?</think>", re.S)
_SPECIAL = re.compile(r"<\|[a-z_]+\|>")


def _strip_think(text: str) -> str:
    """Remove reasoning blocks and stray chat template tokens some open models emit."""
    text = _THINK.sub("", text or "")
    if "<|channel|>final<|message|>" in text:
        text = text.split("<|channel|>final<|message|>")[-1]
    elif "<|message|>" in text:
        text = text.split("<|message|>")[-1]
    return _SPECIAL.sub("", text).strip()


def new_trace(case_id: str, h: dict, cache: RunbookCache) -> dict:
    return {"case_id": case_id, "harness_version": h.get("version"), "steps": [], "runbook_hits": [],
            "guardrail_hits": [], "policy_blocks": [], "llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "blocked_by": None, "runbook_mode": cache.mode}


def select_skills(h: dict, message: str) -> list[dict]:
    skills = h.get("skills", [])
    limit = h["context_policy"].get("max_skills", 4)
    if len(skills) <= limit:
        return skills
    vecs = embed([message] + [f"{s['name']}: {s['description']}" for s in skills])
    sims = cosine_matrix(vecs[0], np.asarray(vecs[1:], dtype=np.float32))
    order = np.argsort(-sims)[:limit]
    return [skills[i] for i in order]


def _guardrail_hits(text: str, guardrails: list[dict]) -> list[dict]:
    hits = []
    for g in guardrails:
        try:
            if re.search(g["pattern"], text or "", re.I):
                hits.append(g)
        except re.error:
            continue
    return hits


def _redact(text: str, guardrails: list[dict]) -> str:
    for g in guardrails:
        if g["action"] == "redact":
            try:
                text = re.sub(g["pattern"], "[redacted]", text, flags=re.I)
            except re.error:
                pass
    return text


def _strings(obj, path=""):
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _strings(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _strings(v, f"{path}[{i}]")


def _set_path(obj, path: str, value):
    tokens = re.findall(r"[^.\[\]]+|\[\d+\]", path)
    cur = obj
    for tok in tokens[:-1]:
        cur = cur[int(tok[1:-1])] if tok.startswith("[") else cur[tok]
    last = tokens[-1]
    if last.startswith("["):
        cur[int(last[1:-1])] = value
    else:
        cur[last] = value


def _field_name(path: str) -> str:
    return re.findall(r"[^.\[\]]+", path)[-1] if path else ""


def policy_block(name: str, args: dict, world: World, guest: dict, h: dict) -> str | None:
    tp = h["tool_policy"]
    if name in tp.get("blocked_tools", []):
        return f"tool {name} is disabled by policy"
    if name == "issue_refund":
        booking = world.bookings.get(str(args.get("booking_id", "")).strip())
        try:
            amount = float(args.get("amount", 0))
        except (TypeError, ValueError):
            amount = 0.0
        if booking and tp.get("refund_requires_ownership") and booking["guest_id"] != guest["guest_id"]:
            return "refunds are only allowed on the authenticated guest's own bookings"
        if booking and tp.get("refund_cap_to_price"):
            already = sum(r["amount"] for r in world.refunds if r["booking_id"] == booking["booking_id"])
            if already + amount > booking["price"] + 0.01:
                return f"refunds on {booking['booking_id']} cannot exceed the booking price of {booking['price']} USD"
        if tp.get("max_refund") is not None and amount > float(tp["max_refund"]):
            return f"refunds above {tp['max_refund']} USD need manager approval"
    if name == "send_email" and tp.get("email_recipient_allowlist"):
        if not allowed_recipient(str(args.get("to", "")), guest):
            return "emails can only go to the authenticated guest or to @capyspa.com staff"
    if name == "lookup_guest" and tp.get("lookup_self_only"):
        if str(args.get("email", "")).strip().lower() != guest["email"].lower():
            return "guests can only look up their own profile"
    if name == "list_bookings" and tp.get("lookup_self_only"):
        if str(args.get("guest_id", "")).strip() != guest["guest_id"]:
            return "guests can only list their own bookings"
    return None


def screen_input(message: str, h: dict, cache: RunbookCache, trace: dict, record_hits: bool = True) -> str | None:
    """Fast path. Returns a reply if the runbook cache recognizes the message as a known attack."""
    cp = h["context_policy"]
    if not (cp.get("scan_inputs", True) and cache.size):
        return None
    hit = cache.match(message, cp["runbook_threshold"])
    if not hit:
        return None
    doc, score = hit
    trace["runbook_hits"].append({"rid": doc["rid"], "label": doc["label"], "score": round(score, 3),
                                  "where": "guest_message", "action": doc["action"], "served_by": doc["served_by"]})
    if record_hits:
        record_hit(doc["rid"])
    trace["blocked_by"] = "runbook"
    if doc["action"] == "review":
        return REVIEW_REPLY
    return doc.get("response") or DEFAULT_RESPONSE


def execute_tool(name: str, args: dict, world: World, guest: dict, h: dict, cache: RunbookCache, trace: dict,
                 record_hits: bool = True) -> str:
    """Run one tool call under the harness: tool policy, email guardrails, untrusted data screening, quarantine."""
    cp = h["context_policy"]
    step = {"tool": name, "args": args, "blocked": None}
    reason = policy_block(name, args, world, guest, h)
    if name == "send_email" and not reason:
        body = f"{args.get('subject', '')}\n{args.get('body', '')}"
        g_hits = _guardrail_hits(body, h.get("guardrails", []))
        if any(g["action"] == "block" for g in g_hits):
            reason = "email content blocked by guardrail"
        elif g_hits:
            args = {**args, "body": _redact(str(args.get("body", "")), g_hits)}
        trace["guardrail_hits"].extend({"label": g.get("label", ""), "where": "email"} for g in g_hits)
    if reason:
        step["blocked"] = reason
        trace["policy_blocks"].append({"tool": name, "reason": reason})
        result = {"error": f"blocked by policy: {reason}"}
    else:
        result = world.call(name, args)
        if cp.get("scan_tool_outputs") and cache.size:
            for path, text in list(_strings(result)):
                if _field_name(path) not in UNTRUSTED_FIELDS or len(text) < 24:
                    continue
                hit = cache.match(text, cp["runbook_threshold"])
                if not hit:
                    continue
                doc, score = hit
                trace["runbook_hits"].append({"rid": doc["rid"], "label": doc["label"], "score": round(score, 3),
                                              "where": f"tool:{name}", "action": doc["action"], "served_by": doc["served_by"]})
                if record_hits:
                    record_hit(doc["rid"])
                if doc["action"] == "block":
                    result = {"error": "content withheld by security policy"}
                    break
                _set_path(result, path, f"[removed by Capybarrier runbook {doc['rid']}: suspected instructions hidden in data]")
    step["result_preview"] = json.dumps(result, ensure_ascii=False)[:400]
    trace["steps"].append(step)
    content = json.dumps(result, ensure_ascii=False)
    if cp.get("quarantine_tool_outputs"):
        content = f"<tool_output trust=\"untrusted\">\n{content}\n</tool_output>"
    return content


def guard_output(final: str, h: dict, trace: dict) -> str:
    g_hits = _guardrail_hits(final, h.get("guardrails", []))
    if not g_hits:
        return final
    trace["guardrail_hits"].extend({"label": g.get("label", ""), "where": "reply"} for g in g_hits)
    if any(g["action"] == "block" for g in g_hits):
        trace["blocked_by"] = "guardrail"
        return GUARDRAIL_REPLY
    return _redact(final, g_hits)


def run_episode(case: dict, h: dict, cache: RunbookCache | None = None, model: str | None = None,
                record_hits: bool = True) -> dict:
    """Run one guest request end to end. Returns a JSON serializable trace including breaches."""
    model = model or config.AGENT_MODEL
    cache = cache or RunbookCache(all_runbook_ids(h))
    world = World(case.get("poison"))
    guest = world.guest(case["guest"])
    trace = new_trace(case["id"], h, cache)
    started = time.time()

    def finish(final: str) -> dict:
        trace["final"] = final
        trace["latency_ms"] = int((time.time() - started) * 1000)
        trace["effects"] = {"refunds": world.refunds, "emails": world.emails}
        trace["breaches"] = detect_breaches(final, world, guest["guest_id"])
        return trace

    # 1) fast path: does this look like an attack we have seen before?
    reply = screen_input(case["message"], h, cache, trace, record_hits)
    if reply is not None:
        return finish(reply)

    # 2) slow path: the LLM with tools, under the harness's rules, playbooks and policies
    skills = select_skills(h, case["message"])
    tool_specs = [t for t in TOOLS if t["function"]["name"] not in h["tool_policy"].get("blocked_tools", [])]
    messages = [{"role": "system", "content": render_system_prompt(h, guest, skills)},
                {"role": "user", "content": case["message"]}]
    final = ""
    for _ in range(config.MAX_AGENT_TURNS):
        msg, usage = llm.chat(messages, model=model, tools=tool_specs)
        trace["llm_calls"] += 1
        trace["prompt_tokens"] += usage["prompt_tokens"]
        trace["completion_tokens"] += usage["completion_tokens"]
        calls = msg.tool_calls or []
        if not calls:
            final = _strip_think(msg.content or "")
            break
        messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"}}
            for c in calls]})
        for c in calls:
            try:
                args = json.loads(c.function.arguments or "{}")
                if not isinstance(args, dict):
                    args = {}
            except json.JSONDecodeError:
                args = {}
            content = execute_tool(c.function.name, args, world, guest, h, cache, trace, record_hits)
            messages.append({"role": "tool", "tool_call_id": c.id, "content": content})
    else:
        final = final or "Sorry, I got a bit tangled up there. Could you ask me again?"

    # 3) output guardrails
    return finish(guard_output(final, h, trace))
