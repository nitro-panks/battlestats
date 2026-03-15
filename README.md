# battlestats

visit live site: [battlestats.io](https://battlestats.io)

this repo contains a D3/React/Django service designed to deliver interactive data charts (SVG) for a given player or naval clan.

## agentic bootstrap (langgraph)

starter LangGraph scaffolding now lives under `server/warships/agentic/`.

from `server/`, install dependencies (including `langgraph`) and run:

```bash
python manage.py run_agent_graph "add API caching around player detail fetch"
```

if your local Django bootstrap is not configured yet, run the standalone script instead:

```bash
python scripts/run_agent_graph.py "add API caching around player detail fetch"
```

optional JSON output:

```bash
python manage.py run_agent_graph "add API caching around player detail fetch" --json
```

the current graph is a guarded workflow with doctrine loading, curated guidance retrieval from battlestats runbooks and QA reviews, planning, design/API review gates, implementation notes, tool-boundary checks, verification gates, retry routing, and run summary.

the default battlestats doctrine now lives in `agents/knowledge/agentic-team-doctrine.json`. you can still layer per-run overrides with workflow context keys like `team_doctrine` and `team_style_snippets` when you want to test a stronger opinion during a specific run.

see `agents/runbooks/runbook-langgraph-opinionated-workflow.md` for the operator runbook covering doctrine sources, guidance retrieval, review-gate behavior, validation commands, and extension rules.

optional LangSmith tracing is also supported for the agent workflows. if you want trace URLs in workflow output and on the in-app trace dashboard, set:

```bash
export LANGSMITH_TRACING_V2=true
export LANGSMITH_API_KEY=your_langsmith_api_key
export BATTLESTATS_LANGSMITH_PROJECT=battlestats-agentic
```

when enabled, routed, LangGraph, and CrewAI workflow runs can include `langsmith_trace_url` in their JSON and CLI output.

## agentic bootstrap (crewai)

the repo now also includes a CrewAI adapter that turns the existing role markdown files under `agents/` into a runnable crew plan.

from `server/`, dry-run the crew without calling a live model:

```bash
python manage.py run_agent_crew "plan CrewAI integration" --dry-run --json
```

or use the standalone script:

```bash
python scripts/run_agent_crew.py "plan CrewAI integration" --dry-run --json
```

when a model is configured, CrewAI can run in hierarchical mode with the current persona set:

```bash
CREWAI_LLM=gpt-4o-mini python manage.py run_agent_crew \
	"design a ranked-player workflow rollout" \
	--process hierarchical
```

see `agents/runbooks/runbook-crewai-integration.md` for the integration plan, persona mapping, rollout guidance, and validation steps.

for automatic engine selection across LangGraph, CrewAI, and hybrid execution:

```bash
python scripts/run_agent_workflow.py "plan and implement CrewAI integration" --engine auto --json
```

the app now also includes a local trace dashboard at `/trace`. it is backed by `GET /api/agentic/traces/`, summarizes LangSmith configuration plus recent local workflow logs under `server/logs/agentic/`, and links to LangSmith only when a stored run already includes a trace URL. see `agents/runbooks/runbook-langsmith-trace-dashboard.md` for validation and operating notes.

when Postgres settings are available, the graph now uses durable Postgres checkpoints instead of in-memory-only state. you can pin a run to a durable thread with `--workflow-id`:

```bash
python scripts/run_agent_graph.py \
	"clan information does not hydrate on first player page load" \
	--workflow-id clan-hydration-debug \
	--json
```

to force an explicit checkpoint database URL, set `LANGGRAPH_CHECKPOINT_POSTGRES_URL`. if no Postgres checkpoint URL can be resolved, the workflow falls back to in-memory checkpoints.

you can provide workflow context (verification state, touched files, retries) with a JSON file:

```bash
python scripts/run_agent_graph.py \
	"clan information does not hydrate on first player page load" \
	--context-file scripts/agent_context.example.json \
	--json
```

the context file can now also include opinionated-workflow controls such as:

- `team_doctrine`
- `team_style_snippets`
- `max_design_review_retries`
- `max_api_review_retries`

## run with docker

### start the stack

from the repository root:

```bash
docker compose up -d
```

this starts:

- next.js client
- django/gunicorn server
- celery worker + beat scheduler
- rabbitmq
- postgresql (dockerized)

### first-time setup (`server/.env`)

before first run, make sure `server/.env` exists and contains at least:

```env
WG_APP_ID=your_wargaming_app_id
DB_PASSWORD=your_db_password
DB_ENGINE=postgresql_psycopg2
DB_NAME=battlestats
DB_USER=django
DB_HOST=db
DB_PORT=5432
DJANGO_ALLOWED_HOSTS=localhost
DJANGO_SECRET_KEY=your_django_secret_key
```

notes:

- `DB_HOST` must be `db` for Docker networking.
- Host-based `python manage.py ...` runs automatically remap `DB_HOST=db` to `127.0.0.1`, so the same `server/.env` works after activating the virtualenv.
- `DB_PORT` should be `5432`.
- `DB_NAME`/`DB_USER` should match compose defaults (`battlestats` / `django`).
- `DB_PASSWORD` must match the password used by the Postgres container.

### local access

- frontend app: <http://localhost:3001>
- django backend: <http://localhost:8888>
- rabbitmq management ui: <http://localhost:15672> (default user/pass: `guest` / `guest`)
- postgresql: `localhost:5432` (database: `battlestats`, user: `django`, password from `server/.env` -> `DB_PASSWORD`)

### stop the stack

```bash
docker compose down
```

to remove containers and volumes (including local postgres data):

```bash
docker compose down -v
```

## test suite

to run the full repo test pass from the repository root:

```bash
./run_test_suite.sh
```

this command:

- ensures the docker services are up
- runs the full Django test suite under `warships.tests`
- runs the Next.js production build in the Dockerized client environment
- warms the clan-battle smoke fixture cache for the verified Naumachia test clan
- runs the API smoke suite

if the script is not executable in your shell, run:

```bash
bash ./run_test_suite.sh
```

to backfill ranked history durably, use the resumable management command from `server/`:

```bash
python manage.py backfill_ranked_data --state-file logs/backfill_ranked_data_state.json
```

or use the helper runner script:

```bash
python scripts/backfill_ranked_data.py --state-file logs/backfill_ranked_data_state.json
python scripts/backfill_ranked_data.py --state-file logs/backfill_ranked_data_state.json --status-only
```

it writes an atomic JSON checkpoint after each player attempt, retries previously failed player IDs on the next run, and resumes from the last processed player ID if the job is interrupted. by default it targets visible players missing ranked data; add `--refresh-older-than-hours 168` to revisit stale rows or `--force` to sweep all players in scope.

for the ongoing ranked incremental refresh, use the queue-based command that keeps known ranked players fresh and samples likely discovery candidates without sweeping the entire player table every day:

```bash
python manage.py incremental_ranked_data --state-file logs/incremental_ranked_data_state.json
```

for manual runs outside Django command wiring, the wrapper script is available:

```bash
python scripts/incremental_ranked_data.py --limit 100 --status-only
```

the scheduled daily incremental task is created via `django-celery-beat` as `daily-ranked-incrementals`, defaulting to `10:30 UTC`, which is intentionally offset from the default `03:00 UTC` clan crawl. it also skips execution if the clan crawl lock is active.

the docker workflow now tunes the daily ranked incremental defaults to stay conservative on upstream request volume: `RANKED_INCREMENTAL_LIMIT=150`, `RANKED_INCREMENTAL_SKIP_FRESH_HOURS=24`, `RANKED_INCREMENTAL_KNOWN_LIMIT=300`, and `RANKED_INCREMENTAL_DISCOVERY_LIMIT=75`. the queue now interleaves discovery candidates among known-ranked refreshes, so `known-limit` and `discovery-limit` control the approximate mix within a run instead of discovery always waiting behind the full known backlog. adjust those environment variables on the `server` and `task-runner` services if you want a faster or broader sweep.

clan roster reads now also support bounded ranked hydration. `/api/fetch/clan_members/<clan_id>/` can queue ranked refresh work for stale or missing ranked rows and returns additive metadata plus lightweight response headers describing queued, deferred, pending, and max-in-flight counts. the shared `ClanMembers.tsx` client polls the same endpoint briefly so ranked stars can appear after initial paint without per-member browser fetches. see `agents/runbooks/runbook-clan-ranked-hydration.md` for the full flow.

if you need to repair already-cached impossible ranked rows from early seasons, use the resumable repair command from `server/`:

```bash
python manage.py repair_ranked_overcount --state-file logs/repair_ranked_overcount_state.json
```

use `--audit-only` first if you want to scan for affected players without rewriting caches. the command resumes from its JSON checkpoint and retries failed player ids on the next run. background and validation details are captured in `agents/work-items/thehindmost-ranked-season-overcount-report.md`.

Charts:

Player activity is summarized with a chart that shows battles within the last 30 days. Gray bars indicate total games played by date, and overlayed green bars indicate wins in that session. Mousing over a particular day will show the numbers for that day on the top of the chart.

![activity](server/images/activity.jpg)

Overall player stats are rendered with respect to color conventions for WoWs players, with purple -> blue -> green -> orange -> red indicating great to bad performance, respectively. Mousing over ships will show the player's all time performance metrics in that ship.

![activity](server/images/battles.jpg)

Naval clans are plotted with each player's w/l record against battles played. By default inactive are filtered out, though this is togglable via the radio buttons underneath. This chart provides most of the context needed to make quick determinations about the activity and quality of a team's players.

![activity](server/images/clan.jpg)
