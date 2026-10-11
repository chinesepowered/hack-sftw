"""Atlas Agent Engine client: deploy Kapi, invoke it, and push learned guardrails and policies.

Everything goes through the Agent Engine REST API with a project service account, so it runs
from CI or from the evolver itself: the agent can ship its own next version.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import time

import httpx

from . import config

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
AGENT_DIR = os.path.join(ROOT, "agentengine")
MANAGED_PREFIX = "capybarrier:"
_token_cache = {"token": None, "exp": 0.0}


class AgentEngineError(RuntimeError):
    pass


def configured() -> bool:
    return bool(config.AE_CLIENT_ID and config.AE_CLIENT_SECRET and config.AE_PROJECT_ID)


def token() -> str:
    if _token_cache["token"] and time.time() < _token_cache["exp"] - 120:
        return _token_cache["token"]
    resp = httpx.post(f"{config.AE_BASE_URL}/oauth/token", data={"grant_type": "client_credentials"},
                      auth=(config.AE_CLIENT_ID, config.AE_CLIENT_SECRET), timeout=30)
    resp.raise_for_status()
    body = resp.json()
    _token_cache.update(token=body["access_token"], exp=time.time() + float(body.get("expires_in", 3600)))
    return body["access_token"]


def _req(method: str, path: str, **kwargs) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token()}", **kwargs.pop("headers", {})}
    resp = httpx.request(method, f"{config.AE_BASE_URL}{path}", headers=headers, timeout=kwargs.pop("timeout", 120), **kwargs)
    if resp.status_code >= 400:
        raise AgentEngineError(f"{method} {path} -> {resp.status_code}: {resp.text[:400]}")
    return resp


def project_path() -> str:
    return f"/projects/{config.AE_PROJECT_ID}"


def workspace_id() -> str:
    if config.AE_WORKSPACE_ID:
        return config.AE_WORKSPACE_ID
    for ws in _req("GET", f"{project_path()}/workspaces").json().get("workspaces", []):
        if ws.get("workspace") == "capybarrier" or ws.get("name") == "capybarrier":
            return ws.get("app_id") or ws.get("id")
    body = {"workspace": "capybarrier", "source_type": "archive", "build_config": {"name": "capybarrier"}}
    ws = _req("POST", f"{project_path()}/workspaces", json=body).json()
    return ws.get("app_id") or ws.get("id")


def assemble(out_dir: str) -> str:
    """Stage agentengine/ plus the capybarrier package, lock dependencies, and tar it up."""
    stage = os.path.join(out_dir, "stage")
    shutil.rmtree(stage, ignore_errors=True)
    shutil.copytree(AGENT_DIR, stage, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".venv", "uv.lock"))
    shutil.copytree(os.path.join(ROOT, "capybarrier"), os.path.join(stage, "capybarrier"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if shutil.which("uv"):
        subprocess.run(["uv", "lock", "--quiet"], cwd=stage, check=False)
    archive = os.path.join(out_dir, "agent.tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        for name in sorted(os.listdir(stage)):
            tar.add(os.path.join(stage, name), arcname=name)
    return archive


def build(label: str, auto_deploy: bool = True, wait: bool = True, log=print) -> dict:
    ws = workspace_id()
    base = f"{project_path()}/workspaces/{ws}/builds"
    with tempfile.TemporaryDirectory() as tmp:
        archive = assemble(tmp)
        init = _req("POST", f"{base}/archive-init", json={
            "label": label[:120], "git_info": {"commit_sha": _git_sha(), "branch": "capybarrier", "dirty": False},
            "build_target": {"subdirectory": ""}, "auto_deploy": auto_deploy}).json()
        # curl sends the presigned URL byte for byte; some HTTP clients re-encode it and break the signature
        # the presigned URL signs `If-None-Match` (S3 create only semantics), so the upload must send it
        up = subprocess.run(["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "-X", "PUT", "--upload-file", archive,
                             "-H", "Content-Type: application/gzip", "-H", "If-None-Match: *", init["upload_url"]],
                            capture_output=True, text=True)
        if up.returncode != 0 or not up.stdout.strip().startswith("2"):
            raise AgentEngineError(f"upload failed: http {up.stdout.strip()} {up.stderr[:300]}")
    build_id = init["build_id"]
    try:
        _req("POST", f"{base}/{build_id}/start")
    except AgentEngineError as exc:
        if "BUILD_ALREADY_ACTIVE" not in str(exc):
            raise
    log(f"build {build_id} started ({label})")
    if not wait:
        return {"build_id": build_id, "status": "started"}
    started = time.time()
    status = {}
    while time.time() - started < 1800:
        status = _req("GET", f"{base}/{build_id}").json()
        if status.get("status") in ("succeeded", "failed", "cancelled"):
            break
        time.sleep(10)
    log(f"build {build_id}: {status.get('status')} {status.get('error_message') or ''}")
    return status


def deploy(build_id: str | None = None) -> dict:
    body = {"build_id": build_id} if build_id else {}
    return _req("POST", f"{project_path()}/workspaces/{workspace_id()}/deployments", json=body).json()


def current_deployment() -> dict:
    return _req("GET", f"{project_path()}/workspaces/{workspace_id()}/deployments/current").json()


def invoke(message: str, guest: str = "G-103", session: str | None = None) -> dict:
    headers = {"X-Session-ID": session} if session else {}
    resp = _req("POST", f"{project_path()}/workspaces/{workspace_id()}/invoke",
                json={"message": message, "guest": guest}, headers=headers, timeout=180)
    return resp.json()


def push_guardrails(h: dict, log=print) -> list[str]:
    """Mirror the harness's learned regex guardrails into Agent Engine content guardrails."""
    path = f"{project_path()}/guardrails"
    existing = _req("GET", path).json()
    items = existing.get("guardrails", existing if isinstance(existing, list) else [])
    for g in items:
        if str(g.get("name", "")).startswith(MANAGED_PREFIX):
            _req("DELETE", f"{path}/{g.get('id') or g.get('_id')}")
    created = []
    for g in h.get("guardrails", []):
        body = {
            "name": f"{MANAGED_PREFIX} {g.get('label') or g['pattern']}"[:100],
            "description": f"Learned by Capybarrier, harness v{h.get('version')}",
            "type": "output_validation",
            "action": "block" if g["action"] == "block" else "modify",
            "status": "active",
            "stage_filter": ["llm_output"],
            "workspace_ids": [workspace_id()],
            "config": {"match_patterns": [{"type": "regex", "value": g["pattern"]}],
                       **({"on_fail": "fix"} if g["action"] != "block" else {})},
        }
        resp = _req("POST", path, json=body).json()
        created.append(resp.get("id") or resp.get("name") or body["name"])
    log(f"pushed {len(created)} guardrails to Atlas Agent Engine")
    return created


def push_policies(h: dict, log=print) -> list[str]:
    """Mirror the harness's tool access into Agent Engine Policy Engine allowlists and budgets."""
    from .world import TOOL_NAMES

    allowed = [t for t in TOOL_NAMES if t not in h["tool_policy"].get("blocked_tools", [])]
    wanted = {"AUTHORIZED_TOOLS": allowed, "MAX_TOOL_CALLS_PER_EXECUTION": 12, "MAX_LLM_CALLS_PER_EXECUTION": 10}
    path = f"{project_path()}/policies"
    existing = _req("GET", path).json().get("policies", [])
    by_type = {p.get("policy_type") or p.get("type"): p for p in existing}
    done = []
    for ptype, value in wanted.items():
        body = {"policy_type": ptype, "value": value, "enabled": True,
                "description": f"Managed by Capybarrier, harness v{h.get('version')}"}
        if ptype in by_type:
            pid = by_type[ptype].get("id") or by_type[ptype].get("_id")
            _req("PUT", f"{path}/{pid}", json=body)
        else:
            _req("POST", path, json=body)
        done.append(ptype)
    log(f"pushed policies {done} to Atlas Agent Engine")
    return done


def sync(h: dict, log=print) -> None:
    """Best effort: mirror the active harness into Agent Engine governance. Never breaks evolution."""
    if not configured():
        return
    for fn in (push_policies, push_guardrails):
        try:
            fn(h, log=log)
        except Exception as exc:
            log(f"  agent engine sync skipped ({fn.__name__}): {exc}")


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return "0" * 40


def describe() -> str:
    return json.dumps({"project": config.AE_PROJECT_ID, "workspace": workspace_id()}, indent=2)
