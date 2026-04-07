---
title: Local Hindsight For Agentic SDLC
doc_type: runbook
status: active
last_updated: 2026-04-06
integration: battlestats local agentic memory
operator_surface:
  local_service: docker compose --profile agentic-memory up -d hindsight
  helper_script: ./scripts/start_local_hindsight.sh
  primary_urls:
    docker: http://hindsight:8888
    host: http://127.0.0.1:8899
---

# Runbook: Local Hindsight For Agentic SDLC

## Purpose

Run Hindsight locally so battlestats agentic workflows have a separate long-term memory lane during development without changing the production droplet shape.

This runbook is for local SDLC only. The production droplet remains a web-services host unless agentic memory is intentionally enabled there.

## Local Architecture

Use a dedicated Hindsight container on the local Docker network.

- Hindsight API inside Docker: `http://hindsight:8888`
- Hindsight API from the host: `http://127.0.0.1:8899`
- Hindsight UI from the host: `http://127.0.0.1:9999`

The battlestats backend and workers can talk to the `hindsight` service by name when they run inside Docker. Host-venv commands should use the published `8899` port instead.

## Prerequisites

- Docker is available locally.
- You have an LLM provider key exported for Hindsight, typically `HINDSIGHT_API_LLM_API_KEY`.
- `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `GEMINI_API_KEY` can also be used; the helper script maps the active provider key into `HINDSIGHT_API_LLM_API_KEY` automatically.
- The local server image is built with agentic dependencies. The repo now defaults local compose builds to `INSTALL_AGENTIC_DEPS=1` unless you override it.

## Start The Local Service

Simplest path:

```bash
./scripts/start_local_hindsight.sh
source ./scripts/use_local_hindsight_env.sh
```

Equivalent direct command:

```bash
docker compose --profile agentic-memory up -d hindsight
```

## Recommended Local Env

For Docker-based battlestats services:

```bash
ENABLE_AGENTIC_RUNTIME=1
BATTLESTATS_HINDSIGHT_ENABLED=1
BATTLESTATS_HINDSIGHT_API_URL=http://hindsight:8888
BATTLESTATS_HINDSIGHT_BUDGET=mid
BATTLESTATS_HINDSIGHT_MAX_TOKENS=4096
BATTLESTATS_HINDSIGHT_TAGS=project:battlestats,env:local,engine:langgraph
```

For host-venv commands:

```bash
ENABLE_AGENTIC_RUNTIME=1
BATTLESTATS_HINDSIGHT_ENABLED=1
BATTLESTATS_HINDSIGHT_API_URL=http://127.0.0.1:8899
BATTLESTATS_HINDSIGHT_BUDGET=mid
BATTLESTATS_HINDSIGHT_MAX_TOKENS=4096
BATTLESTATS_HINDSIGHT_TAGS=project:battlestats,env:local,engine:langgraph
```

## Recommended Workflow

1. Start `redis`, `rabbitmq`, the Django/worker services, and the `hindsight` profile.
2. Keep Hindsight enabled locally for LangGraph and hybrid runs.
3. Use `/trace` locally when you want trace and memory diagnostics.
4. Keep memory review conservative with `python manage.py review_agent_memory --backend file` or the configured LangGraph backend.

## Validation

Check the Hindsight container is up:

```bash
docker compose ps hindsight
curl -sS http://127.0.0.1:8899/
```

Smoke the battlestats adapter:

```bash
cd server
BATTLESTATS_HINDSIGHT_ENABLED=1 \
BATTLESTATS_HINDSIGHT_API_URL=http://127.0.0.1:8899 \
/home/august/code/archive/battlestats/.venv/bin/python - <<'PY'
from warships.agentic.hindsight import get_hindsight_config_summary, get_hindsight_store
summary = get_hindsight_config_summary()
store = get_hindsight_store()
print({
    'enabled': summary['enabled'],
    'api_url': summary['api_url'],
    'store_type': type(store).__name__ if store is not None else None,
})
PY
```

## Notes

- This setup is intentionally local-first and separate from the production droplet.
- If Hindsight is down, the rest of the battlestats web stack is still independent.
- Use tags that make later memory review easier rather than throwing every run into one undifferentiated bank.