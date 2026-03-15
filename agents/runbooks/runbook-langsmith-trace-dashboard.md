# Runbook: LangSmith Trace Dashboard

## Purpose

Operate and validate the battlestats `/trace` dashboard that summarizes LangSmith tracing state and recent local agent workflow runs.

## Preconditions

- Backend and frontend dependencies are installed.
- Agent workflow logging is enabled through the existing routed, LangGraph, or CrewAI commands.
- If live LangSmith trace URLs are desired, set:
  - `LANGSMITH_TRACING_V2=true`
  - `LANGSMITH_API_KEY=...`
  - optionally `BATTLESTATS_LANGSMITH_PROJECT=...`

## What The Dashboard Uses

- LangSmith environment configuration from the server process.
- Local run logs under `server/logs/agentic/`.
- Aggregated diagnostics and learning signals derived from those run payloads.
- Ranked heatmap tuning metadata derived from the current `ranked_wr_battles` backend correlation config.

When the stored run came from the enriched LangGraph workflow, recent-run summaries can now also reflect doctrine-aware review outcomes such as:

- design review pass or fail
- API review pass or fail
- retrieved battlestats guidance references through the run summary text

## Execution Steps

1. Generate or refresh workflow data.
   - Example:
   - `cd server && python scripts/run_agent_workflow.py "evaluate the trace dashboard spec" --engine hybrid --json`
2. Start the local stack or the relevant frontend/backend services.
3. Open `/trace` in the client.
4. Confirm the connection-status section reflects the server environment.
5. Confirm recent runs show the latest workflow execution.
6. For a recent LangGraph run, confirm the summary reflects doctrine-aware gates when relevant.
7. Confirm the learning section includes the ranked heatmap chart-tuning note and points at the ranked heatmap granularity runbook.
8. If trace URLs are present, open one and verify it resolves to the expected LangSmith run.

## Validation Commands

### Backend tests

`cd server && LANGGRAPH_CHECKPOINT_POSTGRES_URL='' DB_ENGINE=sqlite3 DJANGO_SETTINGS_MODULE=battlestats.settings DJANGO_SECRET_KEY=test-secret PYTHONPATH=$PWD /home/august/code/archive/battlestats/.venv/bin/python -m pytest warships/tests/test_agentic_dashboard.py warships/tests/test_views.py -q`

### Frontend build

`cd client && npm run build`

### Workflow generation

`cd server && python scripts/run_agent_workflow.py "review the trace dashboard spec and summarize operational risks" --engine hybrid --json`

## Manual Checks

- `/trace` renders without crashing when no LangSmith credentials are configured.
- The project name matches the resolved environment value.
- API key presence is shown as present/absent, never as the secret value.
- Recent runs show engine, status, task, and summary.
- LangGraph summaries can now mention doctrine loading, guidance retrieval, and design or API review outcomes when those gates are triggered.
- Learning cards show recurring issues and common commands when logs exist.
- The chart tuning note reflects the current ranked heatmap config, including the cache version and the linked runbook path.
- Layout remains readable on a narrow viewport.

## Useful Companion Docs

- `agents/runbooks/runbook-langgraph-opinionated-workflow.md`
- `agents/langgraph-usage-note.md`
- `agents/work-items/langgraph-opinionated-workflow-roadmap.md`

## Rollback

1. Remove the `/trace` frontend route and any navigation link to it.
2. Remove the backend `api/agentic/traces/` endpoint.
3. Leave LangSmith tracing in the agent workflow code if it is still useful elsewhere.

## Residual Limitations

- The dashboard reflects local log-backed history, not the complete LangSmith project history.
- Trace URLs appear only for runs that were actually executed with trace metadata available.
