[![CI](https://github.com/nitro-panks/battlestats/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/nitro-panks/battlestats/actions/workflows/ci.yml)

# battlestats

Live site: [battlestats.online](https://battlestats.online)

Battlestats is a World of Warships player and clan statistics platform.

- Frontend: Next.js 16 App Router + React 18 + D3 charts in `client/`
- Backend: Django 5 + DRF + Celery + PostgreSQL in `server/`
- Product realms: `na` and `eu`
- Agentic runtime: optional on the droplet, implemented in `server/warships/agentic/`

## Documentation Start Here

When a task says "review the docs first", use this read order:

1. `CLAUDE.md` for the current architecture, runtime, deployment, and repo operating rules.
2. `agents/knowledge/agentic-team-doctrine.json` for battlestats decision rules and pre-commit expectations.
3. `agents/README.md` for the task-oriented documentation map.
4. `agents/runbooks/README.md` for the active runbook index.

Do not scan every markdown file by default. Open `agents/reviews/`, `agents/work-items/`, or `agents/runbooks/archive/` only when an active doc points there.

## Current System

- The browser never calls the Wargaming API directly. The frontend only talks to `/api/*`, which Next.js rewrites to Django.
- The product is cache-first with background hydration. Reads should prefer cached or published payloads and queue refresh work rather than blocking on upstream fetches.
- Realm-aware behavior is part of the current architecture. Player and clan pages, landing endpoints, and crawl/warming flows must remain correct for both `na` and `eu`.
- Production background work is split across three Celery lanes: `default`, `hydration`, and `background`.
- The production droplet defaults to the non-agentic backend path. Base backend deps live in `server/requirements.txt`; optional LangGraph and CrewAI deps live in `server/requirements-agentic.txt` and are deployed only when `DEPLOY_AGENTIC_RUNTIME=1`.
- Optional SuperLocalMemory-backed local RAG over the `agents/` markdown corpus also lives in the agentic dependency lane. Enable it with `BATTLESTATS_SLM_ENABLED=1`. It runs in Mode A (math-only, zero LLM, local SQLite) and is droplet-safe. See `agents/runbooks/runbook-memory-layering-2026-04-10.md`.
- Homepage, hot entity, and distribution behavior relies on scheduled warming. Cold-path regressions usually show up as cache misses, stale locks, or queue pressure rather than missing UI wiring alone.

## Common Commands

### Full stack

```bash
docker compose up -d
./run_test_suite.sh
```

### Backend

```bash
cd server
python -m pytest warships/tests/test_views.py warships/tests/test_landing.py warships/tests/test_realm_isolation.py warships/tests/test_data_product_contracts.py -x --tb=short
python manage.py makemigrations && python manage.py migrate
```

### Frontend

```bash
cd client
npm run build
npm test -- --runInBand
```

### Deploy

```bash
./client/deploy/deploy_to_droplet.sh battlestats.online
./server/deploy/deploy_to_droplet.sh battlestats.online
DEPLOY_AGENTIC_RUNTIME=1 ./server/deploy/deploy_to_droplet.sh battlestats.online
```

## Documentation Map

- `CLAUDE.md`: authoritative repo working context for architecture, runtime, and deployment.
- `agents/README.md`: concise task-oriented docs map for coding agents.
- `agents/runbooks/README.md`: active runbooks only.
- `agents/knowledge/README.md`: durable verified findings that should survive beyond one task.
- `agents/contracts/README.md`: machine-readable upstream and internal data contracts.
- `agents/reviews/README.md`: historical review material, not default task-start context.
- `agents/work-items/README.md`: planning specs and tranche scaffolds, not current source of truth after a feature ships.
- `agents/runbooks/archive/README.md`: historical or superseded runbooks.
- `client/README.md`: client-specific commands and frontend testing notes.

## Local Runtime Notes

The default Docker workflow is cloud-db-first. It starts the app, worker, beat, RabbitMQ, and Redis without automatically starting a local Postgres container.

Switch the backend between the managed cloud database and the optional local Postgres service with:

```bash
./server/scripts/switch_db_target.sh cloud
./server/scripts/switch_db_target.sh local
```

Use `agents/runbooks/runbook-db-target-switching.md` for the operator guide.

If you want the local Postgres path, start it explicitly:

```bash
docker compose --profile local-db up -d db
```

Local service defaults:

- frontend: `http://localhost:3001`
- backend: `http://localhost:8888`
- RabbitMQ UI: `http://localhost:15672`

## Agentic Entry Points

### LangGraph

```bash
cd server
python scripts/run_agent_graph.py "fix clan hydration bug" --json
```

For local agentic memory with SuperLocalMemory, set the env vars below before running the workflow. The first call lazily indexes the `agents/` markdown corpus into a local SQLite database, and subsequent calls only ingest files whose mtime has changed.

```bash
ENABLE_AGENTIC_RUNTIME=1
BATTLESTATS_SLM_ENABLED=1
```

### CrewAI

```bash
cd server
python scripts/run_agent_crew.py "plan CrewAI integration" --dry-run --json
```

### Hybrid router

```bash
cd server
python scripts/run_agent_workflow.py "plan and implement a ranked player workflow" --engine hybrid --json
```

Use `agents/runbooks/runbook-agent-orchestrator-selection.md` and `agents/runbooks/runbook-langgraph-opinionated-workflow.md` for the current behavior and routing rules.

When `BATTLESTATS_SLM_ENABLED=1`, the LangGraph `_retrieve_guidance` node reranks the deterministic doctrine matches against a SuperLocalMemory recall over the `agents/` corpus. Optional knobs:

```bash
BATTLESTATS_SLM_MODE=A
BATTLESTATS_SLM_DB_PATH=server/logs/agentic/slm/corpus.db
BATTLESTATS_SLM_REINDEX_ON_BOOT=0
```
