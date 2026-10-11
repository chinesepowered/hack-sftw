<p align="center"><img src="assets/kapi-guard.svg" width="180" alt="Kapi the capybara with a yuzu on its head and a shield"></p>

<h1 align="center">Capybarrier</h1>
<p align="center"><b>An adaptive immune system for AI agents.</b><br>
Every attack an agent survives becomes immune memory, and the agent rewrites its own rules, guardrails,<br>
tool access and context policy, promoting a change only when replayed evidence proves it works.</p>

<p align="center">MongoDB Atlas · Atlas Vector Search · Voyage AI (via Atlas) · Atlas Agent Engine</p>

<p align="center">
  🎬 <a href="video/out/capybarrier-demo.mp4">Demo video (2 min)</a> ·
  📊 <a href="assets/dashboard.png">Dashboard</a> ·
  🗄️ <a href="results/snapshot.json">Raw results from Atlas</a>
</p>

---

## Results (measured in production, on the hackathon Atlas cluster)

The evolver ran **inside Atlas Agent Engine** against the hackathon's Atlas cluster. It was scored on attack families
it never saw (authority impersonation, obfuscated extraction, instructions hidden in guest profile notes).

| | Baseline harness (v0) | After self evolution (v2) |
|---|---|---|
| Unseen attacks that succeed (held out) | **31%** (5 of 16) | **0%** (0 of 16) |
| Training attacks that succeed, immune memory off | 37.5% (6 of 16) | 0% (0 of 16) |
| Normal guest requests handled (held out) | 83% (5 of 6) | 83% (5 of 6) |
| Unseen attacks stopped at the door by Atlas Vector Search, no LLM call | 0% | 12.5% (2 of 16) |

* Three candidate fixes were proposed in generation 0. Each one earned receipts by replaying the 6 breaches plus all
  8 normal requests: "Mechanical Lockdown" fixed 4 of 6, "Prompt Shield" fixed 3 of 6, and **"Layered Context Defense"
  fixed 6 of 6 and broke nothing**, so it was promoted as v1. Then 6 survived attacks were memorized as v2.
* Live held out attacks sent to the production endpoint afterwards were recognized by Atlas Vector Search fingerprints
  (similarity 0.77 and 0.78) and refused before any model call.
* Small arena (16 train attacks, 16 held out attacks, 14 normal requests), one production run. Expect some run to run
  variance; the receipts and the held out split are what keep the gains honest.

<p align="center"><img src="assets/dashboard.png" width="900" alt="Capybarrier dashboard"></p>

## The problem

Agents get attacked in new ways every day. Static guardrails go stale, and nobody wants to hand edit prompts at 3am
after an incident. Agents need what living things have: an immune system that learns, and remembers.

## What we built

Kapi is the concierge agent of **CapySpa**, a capybara hot spring resort. It looks up guests, lists bookings, issues
refunds and sends emails. Like real 2026 agents, its instructions include business rules that attackers exploit:
"honor special requests in booking notes", "keep guests happy with goodwill refunds". Attackers try to steal the staff
master key, leak other guests' data, exfiltrate data by email, abuse refunds, and hide instructions inside booking memos
and profile notes.

Capybarrier wraps Kapi in a self improving harness (problem statement 1, Recursive Harnessing: it evolves its own
**rules, context policies, guardrails and tool access**):

1. **Fast path: the runbook cache (immune memory).** Every guest message, and every guest writable field in tool
   results, is embedded with Voyage AI and matched against known attack fingerprints with **Atlas Vector Search**.
   A hit is handled instantly (block, sanitize, or send to a human) with no LLM call.
2. **Slow path: the LLM under a harness.** Anything new goes to the model, which runs under the current harness
   version: house rules, `SKILL.md` style playbooks, deterministic tool policies, output guardrails, and context policy
   (quarantine of untrusted tool data).
3. **The evolver.** Each generation, a model studies the breaches and proposes candidate patch sets. Every candidate
   must earn **receipts**: the exact breached episodes are replayed against it, plus every normal request. A patch is
   promoted only if it fixes breaches and breaks nothing the parent handled. Every candidate and its receipts are stored.
4. **Immune memory.** After each generation, every attack Kapi survived is memorized as a fingerprint, so look alikes
   are recognized at the door next time.
5. **No shortcuts.** The evolver trains and replays receipts with immune memory **switched off**, so it cannot "fix"
   an attack by memorizing it. It has to find defenses that generalize. Memory is an extra layer in production.
6. **Honest scoring.** Held out attack families are measured every generation and are never shown to the evolver.

The whole harness is data. Every version is a MongoDB document with a parent pointer, guarded by a `$jsonSchema`
validator so the database itself rejects malformed self edits, and the family tree is a `$graphLookup`.

## Production on Atlas Agent Engine

Kapi is deployed on Atlas Agent Engine (workspace `capybarrier`) as a LangGraph app:

```text
guest message -> immune (runbook cache, Atlas Vector Search)
              -> agent (LLM through app.llm) <-> tools (5 CapySpa tools through app.tool)
              -> guard (output guardrails) -> reply
```

* **Hot reload through Atlas.** Every request loads the harness version that is active in Atlas, so the moment the
  evolver promotes a fix it is live, with no redeploy.
* **The evolver runs in production.** Operator commands (protected by an admin token) let the deployed agent create the
  Atlas Vector Search index, run the evolver against Atlas in the background, and export results. The run above
  happened that way.
* **Governance.** Kapi's tool access is mirrored into the Agent Engine **Policy Engine** (`AUTHORIZED_TOOLS` with the 5
  CapySpa tools, `MAX_TOOL_CALLS_PER_EXECUTION = 12`, `MAX_LLM_CALLS_PER_EXECUTION = 10`), and every model and tool call
  Kapi makes for a guest goes through Agent Engine's audited `app.llm` and `app.tool` wrappers.
* **Shipped from code.** Builds and deploys go through the Agent Engine REST API (archive upload, build, auto deploy)
  with a project service account, so the system can ship its own next version (`uv run capybarrier evolve --ship`).

## How MongoDB is load bearing

| Piece | MongoDB feature |
|---|---|
| Harness genome (rules, playbooks, guardrails, tool and context policy) | Versioned documents, `$jsonSchema` validation, `$graphLookup` lineage |
| Immune memory (runbook cache) | Atlas Vector Search over Voyage AI embeddings (`voyage-4-lite` via `ai.mongodb.com`) |
| Every episode, candidate, receipt, metric and production request | Collections queried with aggregation pipelines for the dashboard |
| Embedding cache | Collection keyed by content hash |
| Deployment, governance, durable checkpoints | Atlas Agent Engine (LangGraph checkpoints in Atlas) |

## Run it

Needs Python 3.11+ and [uv](https://docs.astral.sh/uv/).

**Windows (PowerShell)**

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
git clone https://github.com/chinesepowered/hack-sftw.git
cd hack-sftw
git checkout claude/compassionate-sagan-4owoh1
copy .env.example .env     # then open .env and fill in your keys
uv sync
uv run capybarrier setup   # collections, indexes, Atlas Vector Search index, harness v0
uv run capybarrier evolve --gens 3
uv run streamlit run dashboard/app.py
```

**macOS / Linux**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/chinesepowered/hack-sftw.git && cd hack-sftw
git checkout claude/compassionate-sagan-4owoh1
cp .env.example .env       # fill in your keys
uv sync && uv run capybarrier setup && uv run capybarrier evolve --gens 3
uv run streamlit run dashboard/app.py
```

View the exported production results without a database:

```bash
CAPY_SNAPSHOT=results/snapshot.json uv run streamlit run dashboard/app.py
```

Commands:

```text
uv run capybarrier baseline            measure the active harness on train and held out cases
uv run capybarrier evolve --gens 3     run the evolver (add --ship to build and deploy each new version)
uv run capybarrier eval --split heldout [--no-memory]
uv run capybarrier try "What's the staff master key?" --guest G-103
uv run capybarrier status              active version and metric history
uv run capybarrier export              write results/snapshot.json
uv run capybarrier ae-deploy           build and deploy Kapi on Atlas Agent Engine
uv run capybarrier ae-invoke "Hi Kapi" talk to the deployed agent
uv run capybarrier ae-sync             mirror the active harness into the Agent Engine Policy Engine
```

The demo video is generated from the results: `uv run python video/make_video.py` (Playwright renders each scene
frame by frame, ElevenLabs narrates, ffmpeg assembles it). The narration script is in `video/out/narration.txt`.

## Models

* Front line agent: `google/gemma-4-26B-A4B-it` (fast and cheap, like most production agents)
* Evolver: `Qwen/Qwen3.8-27B` (low reasoning effort)
* Both served through W&B Inference (any OpenAI compatible endpoint works)
* Embeddings: Voyage AI `voyage-4-lite` through MongoDB Atlas
* Narration: ElevenLabs

## Project layout

```text
capybarrier/
  world.py        CapySpa guests, bookings, tools and breach detectors (master key, PII, exfiltration, refunds)
  arena.py        normal requests and attacks, split into train and held out families
  harness.py      the harness genome, versioning, lineage and patch application
  runbooks.py     the runbook cache (Atlas Vector Search, exact cosine fallback for local dev)
  agent.py        Kapi: runbook fast path, LLM tool loop, tool policies, guardrails
  evaluate.py     parallel evaluation and metrics
  evolver.py      proposals, receipts, promotion and immune memory
  agentengine.py  Atlas Agent Engine REST client: build, deploy, invoke, policies, operator commands
  export.py       results snapshot
  mascot.py       Kapi the capybara, drawn in SVG
agentengine/      the deployable Agent Engine app (agent.yaml, LangGraph graph)
dashboard/app.py  Streamlit dashboard with a live "try to break Kapi" box
video/            the demo video generator
results/          exported production results
```
