# Runbook: Daily Data Refresh Schedule

_Created: 2026-04-05_

## Purpose

Define the complete daily data refresh schedule for transitioning from backfill-mode enrichment to steady-state refresh once NA, EU, and Asia backfills are complete. Covers player data field mapping, player tiering, candidate queries, multi-realm scheduling, throughput budgets, crawler consolidation into DO Functions, and the transition plan.

This runbook is the continuation point for:

1. operationalizing DO Functions beyond backfill,
2. multi-realm enrichment scheduling,
3. steady-state candidate selection,
4. daily refresh completeness guarantees,
5. clan and player crawler consolidation.

## Current State

The enrichment pipeline (`enrichment/enrich-batch` DO Function) was built for **backfill** — it targets players with `battles_json IS NULL` and fills all fields from scratch. It runs as a DO Function with 2 parallel partitions, each processing 500-player batches in a loop for up to ~14 minutes per invocation.

Backfill status as of 2026-04-05:

- **NA**: In progress — 51,682 enriched, 27,444 remaining (65.3%)
- **EU**: In progress — 24,738 enriched, 114,836 remaining (17.7%)
- **Asia**: No eligible players in DB yet (no clan crawl has populated Asia realm data)

Three legacy Celery crawlers are **retired from Beat** (listed in `signals.py _RETIRED_SCHEDULE_NAMES`) and marked for DO Functions migration:

| Crawler | Status | Location | API calls/entity |
|---|---|---|---|
| Clan crawl | Retired | `server/warships/clan_crawl.py` | ~5-6 per member |
| Incremental player refresh | Retired | `server/warships/management/commands/incremental_player_refresh.py` | ~2-3 per player |
| Incremental ranked refresh | Retired | `server/warships/management/commands/incremental_ranked_data.py` | ~2 per player |

## Crawler Consolidation Strategy

### Incremental player refresh — subsumed by enrichment pipeline

The enrichment pipeline in steady-state mode replaces this entirely. The tier-aware candidate queries (Hot/Active/Warm) are a direct superset of the incremental player refresh's tiered selection. The enrichment pipeline touches more fields per player (battles_json, ranked, snapshot, explorer) while the incremental refresh only did core stats + conditional efficiency/achievements.

No separate DO Function needed.

### Incremental ranked refresh — subsumed by enrichment pipeline

The enrichment pipeline already fetches ranked account_info + ranked shipstats in parallel as part of every player enrichment. In steady-state, ranked data gets refreshed whenever a player re-enters the enrichment queue via staleness.

The only unique capability is **ranked discovery** — finding players who started playing ranked since their last enrichment. This is a small population (~75/cycle currently) that can be folded into the enrichment candidate query as a low-priority tier: players with `ranked_json IS NULL`, `pvp_battles >= 1000`, `last_battle_date` within 30 days.

No separate DO Function needed.

### Clan crawl — needs its own DO Function

The clan crawl serves a purpose the enrichment pipeline cannot: **discovering new clans, updating clan metadata, and tracking membership changes**. The enrichment pipeline operates on players already in the DB — clan sync is what puts them there.

**Current full crawl cost**: ~500K clans, ~3M member lookups, ~1.5M API calls, 4-6 hours on Celery.

**Optimized DO Function design**: Split into a lightweight clan-sync function that only handles metadata + membership, leaving per-player enrichment to the existing pipeline.

#### Clan sync function (`clan/clan-sync`)

Per-clan work:

1. `clans/info/{clan_id}` — fetch clan metadata (name, tag, members_count, leader)
2. `clans/info/{clan_id}` with `members_ids` field — fetch member ID list
3. Upsert Player rows with `clan_id` set (no per-player API calls)

Cost: ~2 API calls per clan. New/updated players discovered by clan sync enter the enrichment candidate pool automatically — if `battles_json IS NULL`, they become backfill candidates; if already enriched, their staleness timer handles the rest.

#### Incremental clan sync

Track `last_fetch` per clan. Only re-fetch clans where `last_fetch` is NULL or stale > 7 days. Most clan metadata changes rarely. New clans appear at ~100-200/day.

Throughput at 10 req/s: ~5 clans/s. A 15-min invocation handles ~4,500 clans. With 2 partitions across a 1-hour slot = ~36K clans. Running only stale clans (>7d) keeps the working set small enough to cover all realms.

## Player Data Field to Refresh Cycle Mapping

| Field(s) | WG API source | Staleness threshold | Refresh trigger |
|---|---|---|---|
| `pvp_battles`, `pvp_ratio`, `name`, `clan_id` | account/info | 15 min | Request-driven (`update_player_data` in views.py) |
| `battles_json`, `tiers_json`, `type_json`, `randoms_json` | ships/stats | 24 hr | DO Function enrichment batch |
| `ranked_json` | ranked account_info + ranked shipstats | 24 hr | DO Function enrichment batch |
| `efficiency_json` | Computed from battles_json | 48 hr | Phase 4: fold into enrichment batch |
| `snapshot` / `activity` | Computed from account stats | 24 hr | DO Function enrichment batch (via `update_snapshot_data`) |
| `explorer_summary` | Computed from battles + ranked | 24 hr | DO Function enrichment batch (via `refresh_player_explorer_summary`) |
| `achievements_json` | player achievements API | 7 days | Phase 4: fold into enrichment batch |
| Clan metadata + membership | clans/info | 7 days | DO Function clan sync (Phase 3) |
| CB seasons (clan-level) | clan battles API | 7 days | Celery `warm_clan_battle_summaries_task` (every 30 min) |
| Efficiency rank tier | Computed from population | 48 hr | Celery `refresh_efficiency_rank_snapshot_task` (triggered post-enrichment) |

### What the enrichment batch touches per player

Each `_enrich_player_parallel()` call updates:

1. `battles_json`, `tiers_json`, `type_json`, `randoms_json` (from ships/stats API)
2. `ranked_json` (from ranked account_info + ranked shipstats API)
3. snapshot + activity (via `update_snapshot_data`)
4. `explorer_summary` (via `refresh_player_explorer_summary`)

Net cost: ~3 WG API calls per player (2 parallel + 1 sequential).

### What is NOT yet in the enrichment batch

1. `efficiency_json` — separate Celery task, depends on battles_json being current (Phase 4)
2. `achievements_json` — no enrichment support yet (Phase 4)
3. Core player stats (`pvp_battles`, `pvp_ratio`, etc.) — request-driven only

## Player Tier Definitions

| Tier | Definition | Estimated size (per realm) | Refresh target |
|---|---|---|---|
| **Hot** | Visited on site in last 14 days (`EntityVisitDaily`) | ~200-500 | Daily, first in queue |
| **Active** | `last_battle_date` within 30 days | ~15K-40K | Daily, after Hot |
| **Warm** | `last_battle_date` within 90 days, but >30 days ago | ~20K-50K | Every 3 days |
| **Cold** | `last_battle_date` > 90 days ago | ~80K-120K | Weekly or skip |

Hot players are the highest priority because they are the ones site visitors are actually looking at. Active players are next because their stats are changing. Warm and Cold players change rarely and can tolerate longer staleness windows.

### Ranked discovery tier (Phase 3)

An additional low-priority candidate pool for ranked discovery:

- `ranked_json IS NULL`, `pvp_battles >= 1000`, `last_battle_date` within 30 days
- Processed after Hot/Active/Warm tiers are exhausted
- Estimated ~200-500 per realm per day
- Negligible throughput cost

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

# Tier 4: Ranked discovery — no ranked data, likely plays ranked
ranked_discovery = Player.objects.filter(
    realm=realm, is_hidden=False,
    pvp_battles__gte=1000,
    last_battle_date__gte=now - timedelta(days=30),
).filter(Q(ranked_json__isnull=True) | Q(ranked_json=[])).order_by('battles_updated_at')
```

The candidate function processes tiers in priority order: exhaust Hot candidates first, then Active, then Warm, then Ranked Discovery. Cold players are excluded from automated refresh.

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

### Window allocation per realm

| Slot | Duration | Workload | API budget |
|---|---|---|---|
| First hour | 1h | Clan sync (stale clans >7d) | ~2 calls/clan, ~5 clans/s |
| Remaining 4h45m | 4h45m | Player enrichment (tiered) | ~3 calls/player, ~6.6 players/s |

Clan sync runs first because it discovers new players that the enrichment pipeline can pick up in the same window.

### Droplet cron entries (target)

```cron
# EU clan sync — first hour of EU window
0,15,30,45 0 * * * SYNC_REALMS=eu /usr/local/bin/invoke-clan-sync.sh

# EU enrichment — remaining hours of EU window
0,15,30,45 1-5 * * * ENRICH_REALMS=eu ENRICH_NUM_PARTITIONS=2 /usr/local/bin/invoke-enrichment.sh

# NA clan sync
0,15,30,45 6 * * * SYNC_REALMS=na /usr/local/bin/invoke-clan-sync.sh

# NA enrichment
0,15,30,45 7-11 * * * ENRICH_REALMS=na ENRICH_NUM_PARTITIONS=2 /usr/local/bin/invoke-enrichment.sh

# Asia clan sync
0,15,30,45 12 * * * SYNC_REALMS=asia /usr/local/bin/invoke-clan-sync.sh

# Asia enrichment
0,15,30,45 13-17 * * * ENRICH_REALMS=asia ENRICH_NUM_PARTITIONS=2 /usr/local/bin/invoke-enrichment.sh
```

### Current cron (backfill phase)

```cron
# NA+EU backfill — every 15 min, all hours
*/15 * * * * /usr/local/bin/invoke-enrichment.sh
```

This will transition to per-realm entries as backfills complete and steady-state begins.

## Throughput Budget

### Enrichment pipeline (per realm, 2 partitions)

| Metric | Value |
|---|---|
| WG API rate limit | ~10 req/s per app_id |
| API calls per player | ~3 (ships/stats + rank_info parallel, then ranked shipstats) |
| Inter-player delay | 0.05s |
| Effective throughput (2 partitions) | ~6.6 players/s, ~400/min, ~7.6 API req/s |
| Per 15-min invocation (2 partitions) | ~5,600 players |
| Per 4h45m enrichment slot (2 partitions) | ~76K players (19 invocations) |
| Daily total (3 realms) | ~228K players |

### Clan sync (per realm, 1 partition)

| Metric | Value |
|---|---|
| API calls per clan | ~2 (metadata + member list) |
| Effective throughput | ~5 clans/s |
| Per 15-min invocation | ~4,500 clans |
| Per 1h sync slot (4 invocations) | ~18K clans |
| Daily total (3 realms) | ~54K clans |

### Coverage assessment

- Hot tier (~500/realm): Fully covered within first invocation (~2 min)
- Active tier (~15K-40K/realm): Fully covered within the 4h45m enrichment slot
- Warm tier: Cycles through over ~3 days naturally (covered by Active overflow)
- Ranked discovery (~200-500/realm): Covered as overflow after main tiers
- Clan sync: ~18K stale clans per realm per day; full universe (~160K/realm) cycles through in ~9 days
- If Active pools are larger than expected, consider increasing to 3 partitions (~10.5 API req/s, still under limit)

### Phase 4 throughput impact

Adding efficiency_json and achievements_json to the enrichment loop increases per-player cost from ~3 to ~5 API calls:

| Metric | Phase 3 (current) | Phase 4 (expanded) |
|---|---|---|
| API calls per player | ~3 | ~5 |
| Throughput (2 partitions) | ~400/min | ~240/min |
| Per enrichment slot | ~76K | ~45K |
| Daily total (3 realms) | ~228K | ~135K |

At ~135K players/day with expanded scope, Hot + Active tiers are still fully covered. May need 3 partitions if Active pools trend toward the upper estimate (~40K/realm).

## Downstream Task Dependencies

These Celery Beat tasks depend on enrichment data being fresh. Their schedules are already registered in `server/warships/signals.py`.

### Must run after enrichment window

| Task | Current schedule | Realm stagger | Dependency |
|---|---|---|---|
| `landing-best-player-snapshot-materializer-{realm}` | Daily (EU 01:15, NA 07:15, Asia 13:15 UTC) | Yes | Reads `battles_json`, `ranked_json` from DB |
| `daily-clan-tier-dist-warmer-{realm}` | Daily (EU 02:30, NA 08:30, Asia 14:30 UTC) | Yes | Reads player tier data |
| `refresh_efficiency_rank_snapshot_task` | Triggered post-crawl | No | Reads efficiency badges from DB |

The Best-player snapshot materializer schedule will need adjustment once per-realm windows shift from backfill to steady-state. Target: run ~30 minutes after each realm's enrichment slot ends.

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
- Cron: Single entry firing every 15 min, processing both NA and EU
- No code changes needed
- **Status: In progress**

### Phase 2: Hybrid mode

- Trigger: When a realm's backfill pool drops below ~1000 remaining candidates
- Code change: Add `mode` parameter to `_candidates()` — `backfill` tries null-first, falls through to staleness query
- Add `_candidates_steady_state()` with tier-aware queries (Hot/Active/Warm + Ranked Discovery)
- Add `ENRICH_MODE` env var to DO Function config
- Deploy per-realm cron entries on droplet

### Phase 3: Full steady-state + clan sync

- All realms backfilled
- Candidate query is purely staleness-based with tier priority
- Per-realm cron windows active (clan sync hour + enrichment hours)
- Deploy new `clan/clan-sync` DO Function
  - Lightweight: clan metadata + member list only (~2 API calls/clan)
  - Incremental: only re-fetch clans stale >7 days
  - Discovers new players for the enrichment pipeline
  - Replaces the retired Celery clan crawl entirely
- Deploy `invoke-clan-sync.sh` invocation script
- Trigger `refresh_efficiency_rank_snapshot_task` at end of each realm's enrichment window
- Monitoring: alert if `enriched == 0` for >2 consecutive invocations in a window

### Phase 4: Expand enrichment scope

- Add `efficiency_json` refresh to enrichment loop (currently separate Celery task)
- Add `achievements_json` fetch (new WG API call)
- Increases per-player cost from ~3 to ~5 API calls
- May need 3 partitions to stay within rate limit budget (~10.5 API req/s)
- Retire remaining Celery crawler code paths (incremental player refresh, incremental ranked, clan crawl)

## DO Functions Architecture (Target)

### Functions

| Function | Purpose | Invocation | Runtime |
|---|---|---|---|
| `enrichment/enrich-batch` | Player enrichment (backfill + steady-state) | Cron every 15 min during realm enrichment slot | Up to 14 min, 1GB RAM |
| `clan/clan-sync` | Clan metadata + membership sync | Cron every 15 min during realm sync slot | Up to 14 min, 512MB RAM |
| `battlestats/db-test` | DB connectivity check | Manual | 30s, 256MB RAM |

### Invocation scripts

| Script | Launches |
|---|---|
| `invoke-enrichment.sh` | N partitions of `enrichment/enrich-batch` (existing) |
| `invoke-clan-sync.sh` | 1 partition of `clan/clan-sync` (new, Phase 3) |

### Environment variables

Existing (`enrichment/enrich-batch`):

- `ENRICH_REALMS` — comma-separated realm list
- `ENRICH_BATCH_SIZE` — players per batch (default 500)
- `ENRICH_NUM_PARTITIONS` — parallel partitions (default 2)
- `ENRICH_DELAY` — inter-player delay (default 0.05s)
- `ENRICH_TIMEOUT_S` — hard timeout budget (default 840s)

New for Phase 2:

- `ENRICH_MODE` — `backfill`, `refresh`, or `hybrid` (default `hybrid`)

New for Phase 3 (`clan/clan-sync`):

- `SYNC_REALMS` — comma-separated realm list
- `SYNC_BATCH_SIZE` — clans per batch
- `SYNC_STALE_DAYS` — re-fetch threshold (default 7)
- `SYNC_TIMEOUT_S` — hard timeout budget

## Code Changes Required

### Phase 2

| File | Change |
|---|---|
| `server/warships/management/commands/enrich_player_data.py` | Add `_candidates_steady_state()` with tier-aware queries (Hot/Active/Warm + Ranked Discovery), add `mode` param to `_candidates()` and `enrich_players()` |
| `functions/packages/enrichment/enrich-batch/__main__.py` | Add `ENRICH_MODE` env var support, pass to `enrich_players()` |
| `functions/project.yml` | Add `ENRICH_MODE` env var mapping |
| Droplet cron | Replace single entry with per-realm staggered entries |

### Phase 3

| File | Change |
|---|---|
| `server/warships/management/commands/clan_sync.py` | New command: lightweight clan metadata + membership sync with staleness check |
| `functions/packages/clan/clan-sync/__main__.py` | New DO Function wrapper (same pattern as `enrich-batch`) |
| `functions/packages/clan/clan-sync/build.sh` | Build script (copies server code) |
| `functions/project.yml` | Add `clan` package with `clan-sync` function |
| `functions/invoke-clan-sync.sh` | New invocation script |
| Droplet cron | Add per-realm clan sync entries in first hour of each window |

### Phase 4

| File | Change |
|---|---|
| `server/warships/management/commands/enrich_player_data.py` | Add efficiency_json + achievements_json to `_enrich_player_parallel()` |
| `server/warships/clan_crawl.py` | Archive — functionality replaced by clan sync + enrichment pipeline |
| `server/warships/management/commands/incremental_player_refresh.py` | Archive — replaced by enrichment pipeline steady-state |
| `server/warships/management/commands/incremental_ranked_data.py` | Archive — replaced by enrichment pipeline |

## Monitoring and Alerting

### Key metrics to track

1. `total_enriched` per activation — should be >0 in every window
2. `total_errors` per activation — should be <5% of batch size
3. Activation duration — should be <840s (under the 900s hard timeout)
4. Candidates remaining per realm — track backfill completion progress
5. Clan sync: `clans_updated` per activation, `new_players_discovered`

### How to check

```bash
# Recent activations
doctl serverless activations list --limit 10

# Specific activation result
doctl serverless activations result <activation-id>

# Enrichment crawler status (droplet)
./server/scripts/check_enrichment_crawler.sh battlestats.online

# DB-level progress check
ssh root@battlestats.online "cd /opt/battlestats-server/current/server && \
  source /opt/battlestats-server/venv/bin/activate && \
  export \$(grep -v '^#' /etc/battlestats-server.env | xargs) && \
  export \$(grep -v '^#' /etc/battlestats-server.secrets.env | xargs) && \
  DJANGO_SETTINGS_MODULE=battlestats.settings python manage.py shell -c '
from warships.models import Player
for realm in [\"na\", \"eu\", \"asia\"]:
    enriched = Player.objects.filter(realm=realm).exclude(battles_json__isnull=True).count()
    remaining = Player.objects.filter(realm=realm, is_hidden=False, pvp_battles__gte=500, pvp_ratio__gte=48.0, battles_json__isnull=True).exclude(name=\"\").count()
    print(f\"{realm.upper()}: {enriched:,} enriched, {remaining:,} remaining\")
'"
```

## Files Referenced

- `server/warships/management/commands/enrich_player_data.py` — enrichment command, candidate queries
- `server/warships/clan_crawl.py` — legacy clan crawl (to be replaced by clan sync in Phase 3)
- `server/warships/management/commands/incremental_player_refresh.py` — legacy player refresh (subsumed by enrichment)
- `server/warships/management/commands/incremental_ranked_data.py` — legacy ranked refresh (subsumed by enrichment)
- `functions/packages/enrichment/enrich-batch/__main__.py` — DO Function wrapper
- `functions/invoke-enrichment.sh` — invocation script
- `functions/project.yml` — DO Function config and env vars
- `server/warships/signals.py` — Celery Beat schedule registration, `REALM_CRAWL_CRON_HOURS`
- `server/warships/tasks.py` — all Celery tasks including materialization
- `server/warships/data.py` — efficiency rank snapshot computation (lines 932-1248)
- `server/warships/models.py` — Player fields, `LandingPlayerBestSnapshot`, `EntityVisitDaily`
- `server/warships/landing.py` — Best-player materialization helpers
- `agents/runbooks/runbook-landing-best-player-subsort-materialization-2026-04-05.md` — subsort materialization architecture
