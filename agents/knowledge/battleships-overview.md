# Battlestats Overview

Last verified: 2026-03-14

## Why This Matters

Future agent sessions need one high-density bootstrap file that explains the live battlestats system without rereading the full repo, all runbooks, and every prior investigation. This file is the first-read context primer. It should be updated whenever system behavior, maintenance workflow, or API semantics materially change.

## Current Conclusion

- battlestats is a Docker-first Django + DRF + Celery + Next.js application for World of Warships player and clan analytics.
- the repo intentionally separates unreliable upstream WG API behavior from stable internal normalized payloads.
- ranked history is now stored locally as the full non-empty season record, not a truncated tail.
- request-time reads should prefer best-effort local data and degrade gracefully when async infrastructure is unavailable.
- durable project knowledge belongs in `agents/knowledge/`; operational implementation guidance belongs in `agents/runbooks/`; machine-readable contracts belong in `agents/contracts/`.

## System Map

- root shape:
  - `client/`: Next.js 15.5.10 app router frontend, React 18, TypeScript, Tailwind, D3, Font Awesome.
  - `server/`: Django backend, DRF APIs, Celery worker/beat, WG ingestion logic, agentic tooling.
  - `agents/`: persona markdown, runbooks, reviews, work items, knowledge base, contracts.
- runtime stack via `docker-compose.yml`:
  - Next client on `localhost:3001`
  - Django/Gunicorn on `localhost:8888`
  - Postgres, RabbitMQ, Redis, Celery worker, Celery beat
- key backend app: `server/warships/`
- key frontend app: `client/app/components/`

## Source-Of-Truth Hierarchy

- upstream HTTP behavior and mismatches:
  - `agents/knowledge/*.md`
  - `agents/contracts/upstream/*.yaml`
- internal normalized payload semantics:
  - `agents/contracts/data-products/*.odcs.yaml`
  - serializers in `server/warships/serializers.py`
- implementation and maintenance workflow:
  - `agents/runbooks/*.md`
- repo bootstrap and operator commands:
  - `README.md`

## Core Architectural Rules

- do not treat raw WG API responses as the app contract boundary.
- centralize WG requests through `warships.api.client.make_api_request()` with retries/shared headers.
- derive stable internal payloads from local `Player`, `Clan`, `Snapshot`, and cached JSON fields.
- prefer runtime-derived flags when the source fields already exist and the logic is cheap.
- do not let async broker failures turn otherwise successful read endpoints into HTTP 500s.
- keep contracts and tests aligned with serializer/API output, not just docs.

## Data Model Reality

### Player

- important scalar fields:
  - `player_id`, `name`, `is_hidden`
  - `last_battle_date`, `days_since_last_battle`, `creation_date`
  - `pvp_battles`, `pvp_wins`, `pvp_losses`, `pvp_ratio`, `pvp_survival_rate`
  - `last_lookup`, `last_fetch`, `ranked_updated_at`
- important cached JSON fields:
  - `activity_json`: recent activity rows; current trusted recent-activity source
  - `battles_json`: per-ship base dataset
  - `randoms_json`: top-ships/randoms chart data
  - `tiers_json`, `type_json`
  - `ranked_json`: ranked season summaries; now full non-empty history
- derived/denormalized summary row:
  - `PlayerExplorerSummary`
  - stores `kill_ratio`, `player_score`, explorer-friendly aggregates

### Clan

- important fields:
  - `clan_id`, `name`, `tag`, `members_count`
  - `leader_id`, `leader_name`
  - `last_fetch`, `last_lookup`
- clan member payload is derived at response time and can include:
  - `is_leader`
  - `is_pve_player`
  - `is_ranked_player`
  - `highest_ranked_league`
  - `activity_bucket`

### Snapshot

- snapshot-based derivation is the current trustworthy path for recent activity and trend logic.
- do not rely on WG `account/statsbydate` as a production-quality recent PvP source.

## Upstream WG API Truths

- `account/info` is the authoritative cumulative player hydration source.
- `account/list` is discovery-only and must be post-filtered with local exact-name verification.
- `clans/accountinfo` absence is a valid no-membership state, not necessarily an error.
- `account/statsbydate` is documented but currently returns unusable `pvp: null` for tested active public accounts; treat it as non-reliable.
- `encyclopedia/info` is a trustworthy lightweight metadata dictionary for languages, ship types, nations, modification labels, ship-type icons, and game version.
- `encyclopedia/ships` is the main detailed ship encyclopedia catalog already used for ship metadata hydration and ship catalog sync.
- `encyclopedia/modules` is live and returns typed module metadata plus module-specific profile fragments.
- `encyclopedia/consumables` is broader than the name implies; verified live results can include `type: Skin` rows.
- `ships/badges` is a player-scoped ship-statistics endpoint returning `ship_id` plus `top_grade_class`, useful for mastery-style badge enrichment.
- current upstream contracts exist for:
  - `account/info`
  - `account/list`
  - `account/statsbydate`
  - `clans/accountinfo`
  - `encyclopedia/info`
  - `ships/badges`

## Public API Surface

- primary router endpoints:
  - `/api/player/<name>/`
  - `/api/clan/<clan_id>/`
  - `/api/ship/<id>/`
- key fetch endpoints:
  - `/api/fetch/player_summary/<player_id>/`
  - `/api/fetch/randoms_data/<player_id>/`
  - `/api/fetch/tier_data/<player_id>/`
  - `/api/fetch/type_data/<player_id>/`
  - `/api/fetch/activity_data/<player_id>/`
  - `/api/fetch/ranked_data/<player_id>/`
  - `/api/fetch/clan_data/<clan_id>:<filter>`
  - `/api/fetch/clan_members/<clan_id>/`
  - `/api/fetch/clan_battle_seasons/<clan_id>/`
  - `/api/fetch/player_distribution/{win_rate|survival_rate|battles_played}/`
  - `/api/fetch/player_correlation/{win_rate_survival|ranked_wr_battles|tier_type}/...`
  - `/api/players/explorer/`
  - `/api/landing/clans/`, `/api/landing/players/`, `/api/landing/recent/`, `/api/landing/player-suggestions/`
  - `/api/stats/`
- smoke coverage source: `agents/runbooks/runbook-api-surface.md`

## Frontend Surface Summary

- landing page:
  - clan/player discovery
  - landing clan table currently uses tighter spacing and no striped background fill
- player detail currently includes:
  - summary cards
  - player score / kill ratio exposure via API-backed summary
  - randoms chart
  - ranked WR vs battles heatmap, hidden entirely when player has no ranked games
  - ranked seasons table with full history, but viewport capped to 5 visible rows and scroll for the rest
  - tier and type charts
  - header icons for clan leader / PvE enjoyer / ranked enjoyer
- clan detail currently includes:
  - clan scatter/plot
  - clan member list with badges/icons and activity buckets
  - clan activity histogram
  - clan battle seasons summary

## Ranked System: Current Semantics

- request-time ranked endpoint: `/api/fetch/ranked_data/<player_id>/`
- storage field: `Player.ranked_json`
- ranked rows now contain season-level summaries with fields like:
  - `season_id`, `season_name`, `season_label`
  - `highest_league`, `highest_league_name`
  - `total_battles`, `total_wins`, `win_rate`
  - nullable `top_ship_name`
  - `best_sprint`, `sprints`
- ranked history retention rule:
  - store all non-empty seasons locally
  - do not truncate to last 10 seasons anymore
  - UI does not slice to 6 rows anymore; it shows full history inside a 5-row scroll viewport
- top ship enrichment:
  - obtained from WG ranked `seasons/shipstats/`
  - nullable, resilient when unavailable
  - fresh caches may be backfilled when older rows are missing `top_ship_name`
- highest ranked league semantics:
  - use best historical league across non-empty ranked seasons
  - ignore zero-battle ranked rows for league selection
- ranked enjoyer badge rule:
  - true only when aggregate ranked battles `> 100`
- correlation semantics:
  - ranked WR vs battles population excludes hidden players
  - excludes players below minimum total ranked battle threshold
  - should use stable local ranked rows, not accidental remote refreshes during tests/reads

## Ranked Maintenance Lanes

- full repair / broad sweep:
  - `python manage.py backfill_ranked_data --state-file logs/backfill_ranked_data_state.json`
  - durable checkpointing, retries failed players first, resumable
  - allowed to repair stale or incomplete ranked rows including missing top-ship enrichment
- daily freshness lane:
  - `python manage.py incremental_ranked_data --state-file logs/incremental_ranked_data_state.json`
  - queue-based
  - known ranked + discovery candidates are interleaved
  - defaults in docker: `LIMIT=150`, `SKIP_FRESH_HOURS=24`, `KNOWN_LIMIT=300`, `DISCOVERY_LIMIT=75`
  - beat task name: `daily-ranked-incrementals`
  - default schedule: `10:30 UTC`
  - skips while clan crawl lock is active
- wrapper script:
  - `python scripts/incremental_ranked_data.py --status-only`

## Clan And Activity Semantics

- clan member endpoint includes `days_since_last_battle` and derived `activity_bucket` values for histogram/chart use.
- activity buckets currently include:
  - `active_7d`
  - `active_30d`
  - `cooling_90d`
  - `dormant_180d`
  - `inactive_180d_plus`
  - `unknown`
- clan PvE marker rule is runtime-derived:
  - `pve_battles = max(total_battles - pvp_battles, 0)`
  - marker only if `total_battles > 500` and `pve_battles > pvp_battles`
- clan leader marker rule:
  - prefer `leader_id`
  - fallback to case-insensitive `leader_name` matching when `leader_id` missing
- clan battle cache semantics:
  - summary cache key: `clan_battles:summary:v2:{clan_id}`
  - empty cached summaries for populated clans should be treated as stale and recomputed
  - clan battle seasons must sort by actual metadata dates, not raw season id ordering

## Player Evaluation Semantics

- keep evaluation decomposable; do not jump to opaque scoring without explicit inputs.
- current meaningful axes:
  - activeness
  - performance
  - engagement shape
  - breadth
  - longevity
  - competitive intensity
- `kill_ratio` is not literal K/D; it is a weighted, smoothed per-ship kill-rate aggregate.
- current `kill_ratio` weighting:
  - tiers `1-4` -> `0.15`
  - tiers `5-7` -> `0.65`
  - tiers `8-11` -> `1.0`
  - per-ship smoothing prior toward `0.7` over `12` battles
- `player_score` blends WR, weighted KDR/kill-rate, survival, battle volume, and recent activity into a detail/explorer summary number.
- dormant account rule exists for `player_score`: very inactive accounts are forced into a weak but non-zero band.
- playstyle verdicts use `compute_player_verdict()`.
- playstyle verdicts now use a `$WR \times survivability$` matrix:
  - `Sealord` above `65%` WR regardless of survivability
  - `Assassin` or `Kraken` from `60%` to `65%`
  - `Stalwart` or `Daredevil` from `56%` to `<60%`
  - `Warrior` or `Raider` from `54%` to `<56%`
  - `Survivor` or `Jetsam` from `52%` to `<54%`
  - `Flotsam` or `Drifter` from `50%` to `<52%`
  - `Pirate` or `Potato` from `45%` to `<50%`
  - `Hot Potato` or `Leroy Jenkins` below `45%`
  - low survivability means `pvp_survival_rate < 33.0`

## Caching And Read-Path Rules

- under `manage.py test`, cache is forced to `LocMemCache` to avoid polluting live Redis-based chart caches.
- ranked correlation cache key currently uses versioned suffix `ranked_wr_battles:v3`.
- clan battle summary cache key is versioned `v2`.
- hidden-profile refreshes should clear derived cached JSON on `Player` to avoid serving stale private data.
- request-time reads should use fresh local cache when available and avoid unnecessary upstream calls.

## Async/Background Behavior

- Celery broker outages should not make player-detail reads fail.
- safe task dispatch now logs broker failures as warnings instead of surfacing them as 500s on read paths.
- crawler and ranked incremental jobs use locks; incremental ranked must not run while the clan crawl lock is active.
- crawl watchdog can clear stale crawl locks and resume crawl scheduling.

## Agentic Subsystem

- two orchestration engines exist:
  - LangGraph for guarded implementation-heavy workflows
  - CrewAI for persona-shaped planning/synthesis
- hybrid routing exists via `run_agent_workflow` with `auto|langgraph|crewai|hybrid`
- key commands:
  - `python manage.py run_agent_graph "..." --json`
  - `python manage.py run_agent_crew "..." --dry-run --json`
  - `python scripts/run_agent_workflow.py "..." --engine hybrid --json`
- durable run logs live under `server/logs/agentic/{langgraph|crewai|hybrid}/`
- CrewAI is strongest as two-pass usage:
  - planning crew first
  - implementation/review crew second

## Environment And Local Runtime Notes

- Docker-first env lives in `server/.env`.
- host-side `python manage.py ...` runs remap `DB_HOST=db` to `127.0.0.1` automatically.
- Python venv path in this workspace is typically `/home/august/code/archive/battlestats/.venv`.
- `rg` is not available in this environment; prefer `grep` fallback when using shell search.
- client local docker runtime uses Node `20.19.1` and runs as the host user to avoid root-owned `.next` artifacts.

## High-Value Validation Commands

- full repo validation:
  - `./run_test_suite.sh`
- backend ranked/player-detail slice that recently passed:
  - `python manage.py test warships.tests.test_views.PlayerViewSetTests warships.tests.test_views.ApiContractTests warships.tests.test_data.RankedDataRefreshTests warships.tests.test_backfill_ranked_command warships.tests.test_incremental_ranked_command warships.tests.test_crawl_scheduler warships.tests.test_ranked_top_ship warships.tests.test_upstream_contracts warships.tests.test_data_product_contracts --keepdb`
- broader backend slice that recently passed:
  - `python manage.py test warships.tests.test_views warships.tests.test_data warships.tests.test_crawl_scheduler warships.tests.test_incremental_ranked_command --keepdb`
- client production build:
  - `cd client && npm run build`
- smoke endpoints:
  - `docker compose exec -T server python scripts/smoke_test_site_endpoints.py`

## Verified Fixtures And Navigation Anchors

- clan fixture:
  - `Naumachia` / `clan_id=1000055908`
- ranked populated fixtures:
  - `Shinn000` / `1000270433`
  - `Punkhunter25` / `1001243015`
- ranked empty fixtures:
  - `Kevik70` / `1001712582`
  - `DOOKJA` / `1021287127`
- player-detail fixtures:
  - `Maraxus1` randoms
  - `Secap` tier
  - `fourgate` type/activity
  - `CitizenS9` hidden-profile
  - `MRK_GG` no-clan

## Current Known Gotchas

- `clan_data` route shape is unusual: `/api/fetch/clan_data/<clan_id>:<filter>` has no trailing slash.
- list router endpoints for player/clan/ship are unpaginated and too slow for smoke; client does not rely on them.
- older ranked fixtures/tests may accidentally trigger remote ranked refresh if `ranked_updated_at` is absent or cached ranked rows lack expected shape.
- ship encyclopedia metadata can be missing for ship IDs present in battle rows; battle refresh uses fallback ship metadata rather than dropping rows.
- the WG API smoke script currently uses `encyclopedia/info` as the low-cost reachability probe for upstream availability.
- activity endpoint/chart exists and is implemented, but is not yet a first-class mounted player-detail section.

## Update Policy

- update this file when any of these change:
  - public API surface
  - ranked storage or maintenance semantics
  - major UI surface composition
  - upstream WG trust assumptions
  - contract/testing strategy
  - high-value validation commands
- for deep investigations or feature-specific operational detail, keep the canonical deep note in `agents/knowledge/` or `agents/runbooks/` and add only the durable summary here.

## Pointers

- upstream behavior: `agents/knowledge/wows-account-hydration-notes.md`, `agents/knowledge/wows-statsbydate-status.md`, `agents/knowledge/wows-encyclopedia-surface.md`, `agents/knowledge/wows-api-contract-strategy.md`
- ranked/player hardening: `agents/runbooks/runbook-player-detail-ranked-hardening.md`, `agents/runbooks/runbook-ranked-top-ship.md`
- API and smoke coverage: `agents/runbooks/runbook-api-surface.md`
- scoring/activity semantics: `agents/runbooks/runbook-player-activity-measurement.md`, `agents/runbooks/runbook-player-kill-ratio.md`
- agentic engine choice: `agents/runbooks/runbook-agent-orchestrator-selection.md`, `agents/runbooks/runbook-crewai-integration.md`
