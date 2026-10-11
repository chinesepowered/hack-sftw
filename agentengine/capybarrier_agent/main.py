"""Kapi on Atlas Agent Engine.

The graph is: immune (runbook cache) -> agent (LLM through app.llm) <-> tools (app.tool) -> guard (guardrails).
Every request loads the harness version that is currently active in MongoDB Atlas, so whatever the
evolver promotes goes live in production on the next request, without a redeploy.
"""

from __future__ import annotations

import datetime as dt
import os
from typing import Annotated, TypedDict

from agent_engine_sdk_langgraph import App
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from capybarrier.agent import _strip_think, execute_tool, guard_output, new_trace, screen_input, select_skills
from capybarrier.db import db
from capybarrier.harness import all_runbook_ids, ensure_default, render_system_prompt
from capybarrier.runbooks import RunbookCache
from capybarrier.world import GUESTS, World

app = App(app_name="capybarrier")

DEFAULT_GUEST = os.getenv("CAPY_DEMO_GUEST", "G-103")
GUEST_IDS = {g["guest_id"] for g in GUESTS}
_ctx: dict = {}


class KapiState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    guest: str
    harness_version: int
    blocked_by: str


def _start_request(guest_id: str) -> None:
    h = ensure_default()  # the active harness version in Atlas (creates v0 on a fresh cluster)
    cache = RunbookCache(all_runbook_ids(h))
    world = World()
    _ctx.update(h=h, cache=cache, world=world, guest=world.guest(guest_id), trace=new_trace("agentengine", h, cache))


def _run_tool(name: str, **args) -> str:
    if not _ctx:
        _start_request(DEFAULT_GUEST)
    return execute_tool(name, args, _ctx["world"], _ctx["guest"], _ctx["h"], _ctx["cache"], _ctx["trace"])


@app.tool()
def lookup_guest(email: str) -> str:
    """Look up a guest profile by email. Returns name, room, tier, contact details and notes."""
    return _run_tool("lookup_guest", email=email)


@app.tool()
def list_bookings(guest_id: str) -> str:
    """List the bookings for a guest id, including service, time, price, status and memo."""
    return _run_tool("list_bookings", guest_id=guest_id)


@app.tool()
def issue_refund(booking_id: str, amount: float, reason: str) -> str:
    """Issue a refund in USD for a booking."""
    return _run_tool("issue_refund", booking_id=booking_id, amount=amount, reason=reason)


@app.tool()
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email on behalf of the spa."""
    return _run_tool("send_email", to=to, subject=subject, body=body)


@app.tool()
def get_spa_policy(topic: str) -> str:
    """Get spa policy text. Topics: cancellation, keys, hours, loyalty, privacy."""
    return _run_tool("get_spa_policy", topic=topic)


@app.prepare_agent_input
def prepare(agent_input, ctx):
    payload = getattr(agent_input, "payload", agent_input)
    if not isinstance(payload, dict):
        payload = {"message": str(payload)}
    guest = str(payload.get("guest") or DEFAULT_GUEST)
    if guest not in GUEST_IDS:
        guest = DEFAULT_GUEST
    return {"messages": [HumanMessage(content=str(payload.get("message", "")))], "guest": guest}


def _last_human(state: KapiState) -> str:
    for m in reversed(state.get("messages", [])):
        if isinstance(m, HumanMessage):
            return m.content if isinstance(m.content, str) else str(m.content)
    return ""


def _log(state: KapiState, final: str) -> None:
    trace = _ctx.get("trace", {})
    try:
        db().production.insert_one({
            "ts": dt.datetime.now(dt.timezone.utc), "source": "atlas-agent-engine", "guest": state.get("guest"),
            "message": _last_human(state), "final": final, "harness_version": _ctx.get("h", {}).get("version"),
            "runbook_hits": trace.get("runbook_hits", []), "policy_blocks": trace.get("policy_blocks", []),
            "guardrail_hits": trace.get("guardrail_hits", []), "blocked_by": trace.get("blocked_by"),
            "effects": {"refunds": _ctx["world"].refunds, "emails": _ctx["world"].emails} if _ctx.get("world") else {},
        })
    except Exception:
        pass  # logging must never break a guest request


@app.entrypoint
def build_agent():
    llm = app.llm(ChatOpenAI(
        model=os.getenv("AGENT_MODEL", "google/gemma-4-26B-A4B-it"),
        base_url=os.getenv("LLM_BASE_URL", "https://api.inference.wandb.ai/v1"),
        api_key=os.getenv("LLM_API_KEY"),
        temperature=0,
    ))
    llm_with_tools = llm.bind_tools(app.get_tool_schemas())
    tool_node = ToolNode(app.get_tools())

    def immune(state: KapiState):
        _start_request(state.get("guest") or DEFAULT_GUEST)
        reply = screen_input(_last_human(state), _ctx["h"], _ctx["cache"], _ctx["trace"])
        if reply is not None:
            _log(state, reply)
            return {"messages": [AIMessage(content=reply)], "blocked_by": "runbook", "harness_version": _ctx["h"]["version"]}
        return {"harness_version": _ctx["h"]["version"]}

    def agent(state: KapiState):
        h, guest = _ctx["h"], _ctx["guest"]
        system = SystemMessage(content=render_system_prompt(h, guest, select_skills(h, _last_human(state))))
        return {"messages": [llm_with_tools.invoke([system] + state["messages"])]}

    def guard(state: KapiState):
        last = state["messages"][-1]
        text = _strip_think(last.content if isinstance(last.content, str) else str(last.content))
        safe = guard_output(text, _ctx["h"], _ctx["trace"])
        _log(state, safe)
        if safe != (last.content if isinstance(last.content, str) else str(last.content)):
            return {"messages": [AIMessage(content=safe, id=last.id)]}
        return {}

    def after_immune(state: KapiState) -> str:
        return "end" if state.get("blocked_by") else "agent"

    def after_agent(state: KapiState) -> str:
        return "tools" if getattr(state["messages"][-1], "tool_calls", None) else "guard"

    graph = StateGraph(KapiState)
    graph.add_node("immune", immune)
    graph.add_node("agent", agent)
    graph.add_node("tools", tool_node)
    graph.add_node("guard", guard)
    graph.set_entry_point("immune")
    graph.add_conditional_edges("immune", after_immune, {"agent": "agent", "end": END})
    graph.add_conditional_edges("agent", after_agent, {"tools": "tools", "guard": "guard"})
    graph.add_edge("tools", "agent")
    graph.add_edge("guard", END)
    return graph.compile(checkpointer=app.checkpointer())


app.run()
