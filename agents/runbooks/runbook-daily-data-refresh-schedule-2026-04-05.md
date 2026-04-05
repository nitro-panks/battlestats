# Runbook: Daily Data Refresh Schedule

_Created: 2026-04-05_

## Purpose

Define the complete daily data refresh schedule for transitioning from backfill-mode enrichment to steady-state refresh once NA, EU, and Asia backfills are complete. Covers player data field mapping, player tiering, candidate queries, multi-realm scheduling, throughput budgets, and downstream task dependencies.

This runbook is the continuation point for:

1. operationalizing DO Functions beyond backfill,
2. multi-realm enrichment scheduling,
3. steady-state candidate selection,
4. daily refresh completeness guarantees.

## Current State

The enrichment pipeline (`enrichment/enrich-batch` DO Function) was built for **backfill** — it targets players with `battles_json IS NULL` and fills all fields from scratch. It runs as a DO Function with 2 parallel partitions, each processing 500-player batches in a loop for up to ~14 minutes per invocation.

Backfill status as of 2026-04-05:

- **NA**: In progress (cron running every 15 min at 03:00 UTC)
- **EU**: Queued (not yet started)
- **Asia**: Queued (not yet started)

Once backfill completes for all three realms, the pipeline must transition to staleness-based refresh to keep already-enriched players current.

## Player Data Field to Refresh Cycle Mapping

| Field(s) | WG API source | Staleness threshold | Current refresh trigger |
|---|---|---|---|
| `pvp_battles`, `pvp_ratio`, `name`, `clan_id` | account/info | 15 min | Request-driven (`update_player_data` in views.py) |
| `battles_json`, `tiers_json`, `type_json`, `randoms_json` | ships/stats | 24 hr | DO Function enrichment batch |
| `ranked_json` | ranked account_info + ranked shipstats | 24 hr | DO Function enrichment batch |
| `efficiency_json` | Computed from battles_json | 48 hr | Celery on-demand (`update_player_efficiency_data_task`) |
| `snapshot` / `activity` | Computed from account stats | 24 hr | DO Function enrichment batch (via `update_snapshot_data`) |
| `explorer_summary` | Computed from battles + ranked | 24 hr | DO Function enrichment batch (via `refresh_player_explorer_summary`) |
| `achievements_json` | player achievements API | 7 days | Not yet in enrichment pipeline (future) |
| CB seasons (clan-level) | clan battles API | 7 days | Celery `warm_clan_battle_summaries_task` (every 30 min) |
| Efficiency rank tier | Computed from population | 48 hr | Celery `refresh_efficiency_rank_snapshot_task` |

### What the enrichment batch touches per player

Each `_enrich_player_parallel()` call updates:

1. `battles_json`, `tiers_json`, `type_json`, `randoms_json` (from ships/stats API)
2. `ranked_json` (from ranked account_info + ranked shipstats API)
3. snapshot + activity (via `update_snapshot_data`)
4. `explorer_summary` (via `refresh_player_explorer_summary`)

Net cost: ~3 WG API calls per player (2 parallel + 1 sequential).

### What is NOT in the enrichment batch

1. `efficiency_json` — separate Celery task, depends on battles_json being current
2. `achievements_json` — no enrichment support yet
3. Core player stats (`pvp_battles`, `pvp_ratio`, etc.) — request-driven only

## Player Tier Definitions

| Tier | Definition | Estimated size (per realm) | Refresh target |
|---|---|---|---|
| **Hot** | Visited on site in last 14 days (`EntityVisitDaily`) | ~200-500 | Daily, first in queue |
| **Active** | `last_battle_date` within 30 days | ~15K-40K | Daily, after Hot |
| **Warm** | `last_battle_date` within 90 days, but >30 days ago | ~20K-50K | Every 3 days |
| **Cold** | `last_battle_date` > 90 days ago | ~80K-120K | Weekly or skip |

Hot players are the highest priority because they are the ones site visitors are actually looking at. Active players are next because their stats are changing. Warm and Cold players change rarely and can tolerate longer staleness windows.

## Steady-State Candidate Query Design

### Backfill mode (current)

```python
Player.objects.filter(
    realm=realm, is_hidden=False,
    pvp_battles__gte=500, pvp_ratio__gte=48.0,
    battles_json__isnull=True,
).order_by(F("pvp_ratio").desc(nulls_last=True))
```

### Steady-state mode (target)

```python
# Tier 1: Hot — visited in last 14 days, stale >24hr
visited_ids = EntityVisitDaily.objects.filter(
    entity_type='player', realm=realm,
    date__gte=now - timedelta(days=14),
).values_list('entity_id', flat=True).distinct()

hot = Player.objects.filter(
    player_id__in=visited_ids, realm=realm,
    battles_updated_at__lt=now - timedelta(hours=24),
).order_by('battles_updated_at')

# Tier 2: Active — battled in last 30 days, stale >24hr
active = Player.objects.filter(
    realm=realm, is_hidden=False,
    last_battle_date__gte=now - timedelta(days=30),
    battles_updated_at__lt=now - timedelta(hours=24),
).order_by('battles_updated_at')

# Tier 3: Warm — battled 30-90 days ago, stale >3 days
warm = Player.objects.filter(
    realm=realm, is_hidden=False,
    last_battle_date__gte=now - timedelta(days=90),
    last_battle_date__lt=now - timedelta(days=30),
    battles_updated_at__lt=now - timedelta(days=3),
).order_by('battles_updated_at')
```

The candidate function processes tiers in priority order: exhaust Hot candidates first, then Active, then Warm. Cold players are excluded from automated refresh.

### Hybrid mode (transition)

During the transition period, the enrichment function should:

1. Check if backfill candidates remain (`battles_json IS NULL` with >1000 eligible).
2. If yes, process backfill candidates.
3. If no (or pool exhausted mid-batch), fall through to steady-state staleness query.

This requires a `mode` parameter on `_candidates()` and `enrich_players()`: `backfill`, `refresh`, or `hybrid` (default during transition).

## Multi-Realm Scheduling

Stagger enrichment windows by realm using the existing `REALM_CRAWL_CRON_HOURS` offset pattern from `server/warships/signals.py`:

| Realm | UTC window | Local context | Cron hours |
|---|---|---|---|
| **EU** | 00:00 - 05:45 | Early morning CET (off-peak) | 0-5 |
| **NA** | 06:00 - 11:45 | Early morning ET (off-peak) | 6-11 |
| **Asia** | 12:00 - 17:45 | Evening JST (off-peak) | 12-17 |

Each realm gets a 5h45m window. The remaining 6h15m (18:00-23:59 UTC) is buffer for retries, maintenance, and overlap avoidance.

### Droplet cron entries (target)

```cron
# EU enrichment — 2 partitions, every 15 min during EU window
0,15,30,45 0-5 * * * ENRICH_REALMS=eu ENRICH_NUM_PARTITIONS=2 /usr/local/bin/invoke-enrichment.sh

# NA enrichment — 2 partitions, every 15 min during NA window
0,15,30,45 6-11 * * * ENRICH_REALMS=na ENRICH_NUM_PARTITIONS=2 /usr/local/bin/invoke-enrichment.sh

# Asia enrichment — 2 partitions, every 15 min during Asia window
0,15,30,45 12-17 * * * ENRICH_REALMS=asia ENRICH_NUM_PARTITIONS=2 /usr/local/bin/invoke-enrichment.sh
```

### Current cron (backfill phase)

```cron
# NA-only backfill — single entry at 03:00 UTC
0,15,30,45 3 * * * /usr/local/bin/invoke-enrichment.sh
```

This will expand to per-realm entries as each realm's backfill is initiated.

## Throughput Budget

| Metric | Value |
|---|---|
| WG API rate limit | ~10 req/s per app_id |
| API calls per player | ~3 (ships/stats + rank_info parallel, then ranked shipstats) |
| Inter-player delay | 0.05s |
| Effective throughput (1 partition) | ~3.3 players/s, ~200/min |
| Effective throughput (2 partitions) | ~6.6 players/s, ~400/min, ~7.6 API req/s |
| Per 15-min invocation (2 partitions) | ~5,600 players |
| Per 6-hour realm window (2 partitions) | ~11,500 players (23 invocations) |
| Daily total (3 realms) | ~34,500 players |

### Coverage assessment

- Hot tier (~500/realm): Fully covered within first 2 invocations (~2 min)
- Active tier (~15K-40K/realm): Fully covered within the 6-hour window for most realms
- Warm tier: Cycles through over ~3 days naturally (covered by Active overflow)
- If Active pools are larger than expected, consider increasing to 3 partitions (~10.5 API req/s, still under limit)

## Downstream Task Dependencies

These Celery Beat tasks depend on enrichment data being fresh. Their schedules are already registered in `server/warships/signals.py`.

### Must run after enrichment window

| Task | Current schedule | Realm stagger | Dependency |
|---|---|---|---|
| `landing-best-player-snapshot-materializer-{realm}` | Daily (EU 01:15, NA 07:15, Asia 13:15 UTC) | Yes | Reads `battles_json`, `ranked_json` from DB |
| `daily-clan-tier-dist-warmer-{realm}` | Daily (EU 02:30, NA 08:30, Asia 14:30 UTC) | Yes | Reads player tier data |

The Best-player snapshot materializer runs ~1h15m into each realm's enrichment window. This is acceptable — it reads from the DB and picks up whatever was enriched in the prior day's window. The 49s cold-path cost (documented in `runbook-landing-best-player-subsort-materialization-2026-04-05.md`) is fine for a background Celery task.

### Independent of enrichment timing

| Task | Schedule | Notes |
|---|---|---|
| `landing-page-warmer-{realm}` | Every 120 min | Reads cached/published data |
| `hot-entity-cache-warmer-{realm}` | Every 30 min | Warms detail page caches |
| `player-distribution-warmer-{realm}` | Every 360 min | Full table scan, independent |
| `player-correlation-warmer-{realm}` | Every 360 min | Full table scan, independent |
| `bulk-entity-cache-loader-{realm}` | Every 12 hr | Loads top entities |
| `recently-viewed-player-warmer-{realm}` | Every 10 min | Cache gap filler |
| `clan-battle-summary-warmer` | Every 30 min | Configured clan IDs only |

## Transition Plan

### Phase 1: Backfill (current)

- Target: Complete `battles_json IS NULL` backfill for NA, EU, Asia
- Candidate query: `battles_json__isnull=True`, ordered by WR descending
- Cron: Single NA entry expanding to per-realm as each backfill starts
- No code changes needed
- **Status: In progress**

### Phase 2: Hybrid mode

- Trigger: When a realm's backfill pool drops below ~1000 remaining candidates
- Code change: Add `mode` parameter to `_candidates()` — `backfill` tries null-first, falls through to staleness query
- Add `_candidates_steady_state()` with tier-aware queries
- Add `ENRICH_MODE` env var to DO Function config
- Deploy per-realm cron entries on droplet

### Phase 3: Full steady-state

- All realms backfilled
- Candidate query is purely staleness-based with tier priority
- Per-realm cron windows active
- Monitoring: alert if `enriched == 0` for >2 consecutive invocations in a window

### Phase 4: Expand enrichment scope (future)

- Add `efficiency_json` refresh to enrichment loop (currently separate Celery task)
- Add `achievements_json` fetch (new WG API call)
- Increases per-player cost from ~3 to ~5 API calls
- May need 3 partitions to stay within rate limit budget

## Code Changes Required (Phase 2)

| File | Change |
|---|---|
| `server/warships/management/commands/enrich_player_data.py` | Add `_candidates_steady_state()`, add `mode` param to `_candidates()` and `enrich_players()` |
| `functions/packages/enrichment/enrich-batch/__main__.py` | Add `ENRICH_MODE` env var support, pass to `enrich_players()` |
| `functions/project.yml` | Add `ENRICH_MODE` env var mapping |
| Droplet cron | Replace single entry with per-realm staggered entries |

## Monitoring and Alerting

### Key metrics to track

1. `total_enriched` per activation — should be >0 in every window
2. `total_errors` per activation — should be <5% of batch size
3. Activation duration — should be <840s (under the 900s hard timeout)
4. Candidates remaining per realm — track backfill completion progress

### How to check

```bash
# Recent activations
doctl serverless activations list --limit 10

# Specific activation result
doctl serverless activations result <activation-id>

# Enrichment crawler status (droplet)
./server/scripts/check_enrichment_crawler.sh battlestats.online
```

## Files Referenced

- `server/warships/management/commands/enrich_player_data.py` — enrichment command, candidate queries
- `functions/packages/enrichment/enrich-batch/__main__.py` — DO Function wrapper
- `functions/invoke-enrichment.sh` — invocation script
- `functions/project.yml` — DO Function config and env vars
- `server/warships/signals.py` — Celery Beat schedule registration, `REALM_CRAWL_CRON_HOURS`
- `server/warships/tasks.py` — all Celery tasks including materialization
- `server/warships/models.py` — Player fields, `LandingPlayerBestSnapshot`, `EntityVisitDaily`
- `server/warships/landing.py` — Best-player materialization helpers
- `agents/runbooks/runbook-landing-best-player-subsort-materialization-2026-04-05.md` — subsort materialization architecture and history
