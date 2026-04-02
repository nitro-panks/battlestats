[![CI](https://github.com/nitro-panks/battlestats/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/nitro-panks/battlestats/actions/workflows/ci.yml)

# battlestats

Live site: [battlestats.online](https://battlestats.online)

Battlestats is a World of Warships player and clan statistics platform.

- Frontend: Next.js App Router + React + D3 charts in `client/`
- Backend: Django + DRF + Celery + PostgreSQL in `server/`
- Realms: `na` and `eu`
- Agentic runtime: LangGraph + CrewAI in `server/warships/agentic/`

## Agent Start Here

When an agent is asked to review project documentation, the fastest useful read order is:

1. `CLAUDE.md` for the current architecture, runtime, deployment, and repo operating rules.
2. `agents/knowledge/agentic-team-doctrine.json` for battlestats decision rules and pre-commit expectations.
3. `agents/README.md` for the task-oriented documentation map.
4. `agents/runbooks/README.md` for the active runbook index.

Do not scan every runbook by default. Start from the active index and open only the docs relevant to the task.

## System Summary

- The browser never calls the Wargaming API directly. The frontend only talks to `/api/*`, which Next.js rewrites to Django.
- The product is cache-first with background hydration. Reads should prefer cached or published payloads and queue refresh work rather than blocking on upstream fetches.
- Realm-aware behavior is part of the current architecture. Player and clan pages, landing endpoints, and crawl/warming flows must remain correct for both `na` and `eu`.
- Production background work is split across three Celery lanes: `default`, `hydration`, and `background`.
- Homepage, hot entity, and distribution behavior relies on scheduled warming. Cold-path regressions usually show up as cache misses, stale locks, or queue pressure rather than missing UI wiring alone.

## Common Commands

### Full stack

```bash
docker compose up -d
```

### Backend

```bash
cd server
python -m pytest warships/tests/ -x --tb=short
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
```

## Documentation Map

- `CLAUDE.md`: authoritative repo working context for architecture, runtime, and deployment.
- `agents/README.md`: concise task-oriented docs map for coding agents.
- `agents/runbooks/README.md`: active runbooks only.
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
