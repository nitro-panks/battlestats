# battlestats

visit live site: [battlestats.online](https://battlestats.online)

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

the default stack is now cloud-db-first: it starts the app, worker, beat, rabbitmq, and redis without automatically starting a local Postgres container.

to switch the backend between the managed cloud database and the optional local Postgres service with one command:

```bash
./server/scripts/switch_db_target.sh cloud
./server/scripts/switch_db_target.sh local
```

see `agents/runbooks/runbook-db-target-switching.md` for the operator runbook.

if you want the old local-Postgres development path, start it explicitly:

```bash
docker compose --profile local-db up -d db
```

the server now warms the landing-page caches automatically a few seconds after startup, so the first browser hit after a `bounce` or fresh `docker compose up` does not have to build the landing payloads cold.

landing behavior to be aware of:

- public landing players use a `25`-row published cache surface
- public landing clans use a `30`-row published cache surface
- landing player and clan reads keep a durable published fallback copy, so if the TTL-bound cache key is missing after the first publish, the public route still serves the last published payload while a background republish is queued
- landing page load also queues an asynchronous best-detail warmup for the current top `25` Best players and top `25` Best clans; this is best-effort and depends on worker dispatch, but it does not block the page render

to trigger the same warm-up manually:

```bash
docker compose exec -T server python manage.py warm_landing_page_content
```

this starts:

- next.js client
- django/gunicorn server
- celery worker + beat scheduler
- rabbitmq
- redis

optional local-only service:

- postgresql (dockerized) via `--profile local-db`

the server and client images now also install `ripgrep` (`rg`), so fast workspace searches are available inside the project containers after rebuild.

### first-time setup (`server/.env` + `server/.env.secrets`)

before first run, make sure `server/.env` exists with the non-secret connection values:

```env
DB_ENGINE=postgresql_psycopg2
DB_NAME=defaultdb
DB_USER=doadmin
DB_HOST=your-managed-postgres-host
DB_PORT=25060
DB_SSLMODE=require
DB_SSLROOTCERT=ca-certificate.crt
DJANGO_ALLOWED_HOSTS=localhost
```

store secrets separately in `server/.env.secrets`:

```env
WG_APP_ID=your_wargaming_app_id
DB_PASSWORD=your_db_password
DJANGO_SECRET_KEY=your_django_secret_key
```

notes:

- host-side `python manage.py ...` and the Docker services now load both `server/.env` and `server/.env.secrets` automatically.
- keep `server/.env.secrets` out of version control; it is intended to be machine-local.
- when you want to use the optional local Postgres container instead, start `docker compose --profile local-db up -d db` and set `DB_HOST=db`, `DB_PORT=5432`, `DB_NAME=battlestats`, and `DB_USER=django`.
- when `DB_HOST=db` is used outside containers, host-side `python manage.py ...` runs still remap it to `127.0.0.1`.

optional startup warm-up knobs for the landing page:

```env
WARM_LANDING_PAGE_ON_STARTUP=1
LANDING_WARMUP_START_DELAY_SECONDS=5
```

- set `WARM_LANDING_PAGE_ON_STARTUP=0` if you want to disable the automatic landing-cache warm-up.
- `LANDING_WARMUP_START_DELAY_SECONDS` controls how long the server waits after migrations before launching the background warm command.

optional clan battle badge freshness knob:

```env
CLAN_BATTLE_BADGE_REFRESH_DAYS=14
```

- controls how old a durable clan-battle shield snapshot can be before slow producer lanes refresh it.
- `clan_members()` no longer queues shield refresh work on read; stale-or-null badge summaries are refreshed by slower background lanes such as incremental player refresh and hot-entity warming.
- legacy `CLAN_BATTLE_SUMMARY_STALE_DAYS` is still honored as a fallback if `CLAN_BATTLE_BADGE_REFRESH_DAYS` is unset.

optional client analytics knob:

```env
NEXT_PUBLIC_GA_MEASUREMENT_ID=your_ga4_measurement_id
```

- when set, the routed player and clan pages still send the first-party battlestats analytics POST and also emit a parallel GA4 `entity_detail_view` event.
- when omitted, the first-party analytics path stays active and GA4 emission is skipped.

optional client proxy knob when the Next.js app is not running inside the local Docker stack:

```env
BATTLESTATS_API_ORIGIN=http://localhost:8888
```

- the client now fetches relative `/api/...` paths and relies on a Next.js rewrite.
- keep the default `http://localhost:8888` for local development outside Docker.
- on a droplet, point it at the backend origin, for example `http://127.0.0.1:8888` when Django is on the same host.
- keep `BATTLESTATS_API_ORIGIN` slashless. a trailing slash can trigger avoidable localhost proxy redirects on custom API routes, especially for POST requests such as `/api/analytics/entity-view`.
- client-side shared API fetches now normalize same-origin `/api/.../` requests to slashless paths before issuing the request, but the local origin should still be configured without a trailing slash.

### local access

- frontend app: <http://localhost:3001>
- django backend: <http://localhost:8888>
- rabbitmq management ui: <http://localhost:15672> (default user/pass: `guest` / `guest`)
- local optional postgresql: `localhost:5432` only when started with `docker compose --profile local-db up -d db`

### route-based navigation

the client now supports direct detail routes for both players and clans:

- player detail: `/player/<encoded-player-name>`
- clan detail: `/clan/<clan_id>-<optional-slug>`

the landing-page search box, clan-member links, and clan selection controls now navigate through those routes instead of relying on in-page-only state. this makes player and clan detail pages linkable and reload-safe.

the header search input now only reflects explicit `q` query usage. simply navigating to a player detail route no longer backfills the viewed player name into the global search box.

player and clan detail headers now also expose a `Share` action that copies the current route URL, so the route-based pages are easy to hand off without manual URL copying.

those same routed detail views now emit first-party `entity_detail_view` analytics only after a real player or clan payload resolves successfully. the canonical ingest endpoint is `POST /api/analytics/entity-view/`, and ranked rollups are available from `GET /api/analytics/top-entities/`.

hidden accounts are now signaled consistently with a mask icon across search suggestions, landing-player rows, clan member rows, explorer rows, and player detail headers.

the clan activity SVG now ignores icon-only async member hydration changes when deciding whether to redraw, which removes the visible flicker that previously showed up on both clan detail and player detail pages.

the landing-page `Best` active-player mode now falls back to overall PvP win rate when high-tier history is sparse, instead of collapsing the list to a near-empty result set.

### player detail layout

the player detail page now keeps the left column focused on clan context:

- clan plot and clan members

the broader player analysis lives in the `Insights` tab surface on the right:

- `Population`: win-rate and battle-distribution charts
- `Ships`: top ships
- `Ranked`: ranked heatmap and ranked seasons
- `Profile`: tier-vs-type, ship-type, and tier performance charts
- `Badges`: efficiency badges
- `Clan Battles`: player clan battle seasons

once the main player detail payload finishes loading, inactive insights tabs warm their data in the background during idle time so the first explicit tab switch is faster without mounting hidden chart DOM.

### browser smoke tests

the client now includes Playwright browser smoke lanes for real-browser route safety checks.

from `client/`:

```bash
npm run test:e2e:install
npm run test:e2e:install:deps
npm run test:e2e
```

on Linux hosts that do not already have the required browser runtime libraries, prefer `npm run test:e2e:install:deps` for the first setup pass.

the current browser smoke coverage includes:

- the routed player detail page, proving that background insights warmup waits until the player payload resolves before issuing inactive-tab data requests
- the routed clan detail page, proving that an empty clan-plot response with `X-Clan-Plot-Pending: true` stays in a loading state and retries instead of flashing an empty-chart state

on the backend side, clan-plot reads are now effectively stale-while-revalidate once clan members are already present. if clan shell metadata is stale but the clan roster is complete enough to build a plot, `/api/fetch/clan_data/<clan_id>:active` should still return plot rows while queueing the background clan refresh, instead of returning `[]` with `X-Clan-Plot-Pending: true` forever.

for the client-specific command matrix, smoke targets, and coverage notes, use `client/README.md` and `agents/runbooks/runbook-client-test-hardening.md` as the durable sources.

the player-tab clan battle seasons and efficiency badge tables are tuned to show up to ten visible rows before scrolling, and the efficiency badge section uses a denser compact layout with inline badge totals in the header.

when a fresh published Battlestats efficiency-rank snapshot exists for an Expert-ranked player, the player header shows a compact sigma marker. non-Expert published tiers and stored badge-only fallback rows do not render a visible header sigma, which keeps the player-detail header aligned with the current `E`-only behavior used on the other player lists. this header marker remains distinct from the lower `Efficiency Badges` section, which still shows the underlying raw ship-level WG badge rows.

the shared clan roster on both clan detail and player detail now uses the single `/api/fetch/clan_members/<clan_id>/` hydration path for efficiency-rank state. when roster rows are stale, the client shows a compact `Updating Battlestats rank icons...` status while the backend warms player efficiency data and republishes the tracked rank snapshot; once the refresh completes, only Expert-ranked clan members render the inline sigma icon without switching to per-player browser requests.

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

for focused frontend regression coverage during local UI work, the client now also has a Jest + React Testing Library harness:

```bash
cd client && npm test -- --runInBand
cd client && npm run test:ci
```

the current targeted client coverage includes route loaders, route helper utilities, header search behavior, compact efficiency badge rendering/sorting, and clan-chart redraw signatures.

the focused clan/player roster efficiency-rank checks are:

```bash
cd client && npm test -- --runInBand app/components/__tests__/ClanMembers.test.tsx app/components/__tests__/ClanDetail.test.tsx app/components/__tests__/PlayerDetail.test.tsx
```

for a bare DigitalOcean droplet deployment of the client without adding CI/CD yet, use the bootstrap and deploy scripts under `client/deploy/`. the operator steps live in `agents/runbooks/runbook-client-droplet-deploy.md`.

for a matching bare DigitalOcean droplet deployment of the Django backend, use `server/deploy/`. the backend operator steps live in `agents/runbooks/runbook-backend-droplet-deploy.md`, and that flow reuses the existing cloud DB target from `server/.env.cloud` plus `server/.env.secrets.cloud`.

targeted analytics and routed-detail regressions are also available with:

```bash
cd client && npm test -- --runInBand app/components/__tests__/PlayerRouteView.test.tsx app/components/__tests__/ClanRouteView.test.tsx app/lib/__tests__/visitAnalytics.test.ts
```

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

to backfill player efficiency badges durably, use the resumable management command from `server/`:

```bash
python manage.py backfill_player_efficiency_badges --state-file logs/backfill_player_efficiency_badges_state.json
```

it uses the same checkpoint pattern as the ranked backfill, retries failed player IDs on the next run, and resumes from the last processed player ID after interruption. by default it targets visible players with PvP activity whose badge payload is missing or unstamped; add `--refresh-older-than-hours 168` to revisit stale rows, `--include-zero-pvp` to stamp zero-PvP accounts, or `--force` to sweep all players in scope.

to backfill player combat achievements, use the dedicated achievements command from `server/`:

```bash
python manage.py backfill_achievements_data --only-missing
```

this refreshes the raw `account/achievements/` payload onto each player and rebuilds curated `PlayerAchievementStat` rows from the Battlestats combat-achievement catalog. by default it targets visible players missing achievements data; add `--force`, `--player-id`, `--older-than-hours`, or `--include-hidden` to widen the scope.

the player detail header now renders a single sigma marker for efficiency only when the published player payload resolves to Expert (`E`). non-Expert published tiers and stored badge-only fallback rows remain available in payload data and badge tables, but they no longer produce a visible header sigma. this remains distinct from the lower `Efficiency Badges` section, which still shows the per-ship badge rows.

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
