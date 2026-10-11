"""Command line interface: `uv run capybarrier <command>` (or python -m capybarrier <command>)."""

from __future__ import annotations

import argparse
import json
import sys

from . import config


def cmd_setup(_args) -> None:
    from .db import ensure_indexes
    from .harness import ensure_default

    status = ensure_indexes()
    h = ensure_default()
    print(f"database: {config.DB_NAME} | vector index 'runbook_vec': {status} | active harness v{h['version']}")


def cmd_baseline(_args) -> None:
    from .evaluate import next_generation
    from .evolver import run_generation

    run_generation(next_generation(), n_candidates=0)


def cmd_evolve(args) -> None:
    from .evolver import evolve

    evolve(generations=args.gens, n_candidates=args.candidates, ship=args.ship)


def cmd_eval(args) -> None:
    from .arena import cases
    from .evaluate import run_cases, summarize
    from .harness import get_active, get_version

    h = get_version(args.version) if args.version is not None else get_active()
    traces = run_cases(h, cases(args.split), label=f"adhoc-{args.split}", persist=False, use_memory=not args.no_memory)
    print(json.dumps(summarize(traces), indent=2))
    for t in traces:
        flag = "ok " if t["ok"] else "BAD"
        print(f"{flag} {t['case_id']:<20} {t['kind']:<7} breaches={t.get('breaches')} blocked_by={t.get('blocked_by')} :: {(t.get('final') or t.get('error') or '')[:90]!r}")


def cmd_try(args) -> None:
    from .agent import run_episode
    from .harness import get_active

    case = {"id": "adhoc", "guest": args.guest, "message": args.message, "kind": "adhoc", "family": "adhoc", "split": "adhoc"}
    trace = run_episode(case, get_active(), record_hits=False)
    print(json.dumps({k: trace[k] for k in ("final", "breaches", "blocked_by", "runbook_hits", "guardrail_hits",
                                            "policy_blocks", "steps", "latency_ms")}, indent=2, default=str))


def cmd_status(_args) -> None:
    from .db import vector_index_ready, db
    from .harness import get_active

    h = get_active()
    print(f"active harness v{h['version']} (parent {h.get('parent_version')}) | rules {len(h['rules'])} | skills {len(h['skills'])} "
          f"| guardrails {len(h['guardrails'])} | designed runbooks {len(h.get('runbook_ids', []))} "
          f"| immune memory {len(h.get('memory_ids', []))} | vector index ready: {vector_index_ready()}")
    for row in db().generations.find({}, sort=[("generation", 1), ("split", 1)]):
        print(f"gen {row['generation']:>2} v{row['version']:<3} {row['split']:<8} ASR {row['asr']} utility {row['utility']} "
              f"runbook catch {row['runbook_catch_rate']} false blocks {row['false_block_rate']}")


def cmd_export(args) -> None:
    from .export import export_snapshot

    path = export_snapshot(args.out)
    print(f"wrote {path}")


def cmd_reset(args) -> None:
    from .db import client

    if not args.yes:
        print("refusing to drop the database without --yes")
        sys.exit(1)
    client().drop_database(config.DB_NAME)
    print(f"dropped database {config.DB_NAME}")


def cmd_ae_deploy(args) -> None:
    from . import agentengine

    status = agentengine.build(args.label, auto_deploy=True, wait=not args.no_wait)
    print(json.dumps({k: status.get(k) for k in ("build_id", "status", "error_message", "image_uri")}, indent=2))


def cmd_ae_status(_args) -> None:
    from . import agentengine

    print(agentengine.describe())
    print(json.dumps(agentengine.current_deployment(), indent=2))


def cmd_ae_invoke(args) -> None:
    from . import agentengine

    print(json.dumps(agentengine.invoke(args.message, guest=args.guest), indent=2, default=str))


def cmd_ae_sync(_args) -> None:
    from . import agentengine
    from .harness import get_active

    agentengine.sync(get_active())


def cmd_raindrop_intake(args) -> None:
    from . import observe

    for c in observe.intake(limit=args.limit, signal_names=args.signal):
        print(f"new  {c['id']}  [{c['raindrop_signal']}]  {c['message'][:100]!r}")
    print(f"{len(observe.intake_cases())} production cases from Raindrop are queued for the evolver")


def main() -> None:
    p = argparse.ArgumentParser(prog="capybarrier", description="An adaptive immune system for AI agents.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup", help="create collections, indexes, the vector index and harness v0").set_defaults(fn=cmd_setup)
    sub.add_parser("baseline", help="measure the active harness on train and held out cases").set_defaults(fn=cmd_baseline)
    e = sub.add_parser("evolve", help="run evolution generations")
    e.add_argument("--gens", type=int, default=4)
    e.add_argument("--candidates", type=int, default=3)
    e.add_argument("--ship", action="store_true", help="after each promotion, build and deploy the new version on Atlas Agent Engine")
    e.set_defaults(fn=cmd_evolve)
    ev = sub.add_parser("eval", help="evaluate a harness version without saving")
    ev.add_argument("--split", default="heldout", choices=["train", "heldout", "all"])
    ev.add_argument("--version", type=int, default=None)
    ev.add_argument("--no-memory", action="store_true", help="switch immune memory off")
    ev.set_defaults(fn=cmd_eval)
    t = sub.add_parser("try", help="send one message to the agent")
    t.add_argument("message")
    t.add_argument("--guest", default="G-100")
    t.set_defaults(fn=cmd_try)
    sub.add_parser("status", help="show the active harness and metric history").set_defaults(fn=cmd_status)
    x = sub.add_parser("export", help="export a JSON snapshot for the dashboard and video")
    x.add_argument("--out", default="results/snapshot.json")
    x.set_defaults(fn=cmd_export)
    r = sub.add_parser("reset", help="drop the capybarrier database")
    r.add_argument("--yes", action="store_true")
    r.set_defaults(fn=cmd_reset)
    d = sub.add_parser("ae-deploy", help="build and deploy Kapi on Atlas Agent Engine")
    d.add_argument("--label", default="capybarrier manual deploy")
    d.add_argument("--no-wait", action="store_true")
    d.set_defaults(fn=cmd_ae_deploy)
    sub.add_parser("ae-status", help="show the current Agent Engine deployment").set_defaults(fn=cmd_ae_status)
    i = sub.add_parser("ae-invoke", help="send a message to the deployed agent")
    i.add_argument("message")
    i.add_argument("--guest", default="G-103")
    i.set_defaults(fn=cmd_ae_invoke)
    sub.add_parser("ae-sync", help="push the active harness's guardrails and policies to Agent Engine").set_defaults(fn=cmd_ae_sync)
    rd = sub.add_parser("raindrop-intake", help="pull production requests that Raindrop flagged into the evolver's cases")
    rd.add_argument("--limit", type=int, default=100, help="events per signal")
    rd.add_argument("--signal", action="append", help="signal name to pull (repeatable; default: every negative signal)")
    rd.set_defaults(fn=cmd_raindrop_intake)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
