# Runbook: EU Player Population by Best Ranking

**Created**: 2026-04-02
**Status**: Planned
**Depends on**: `spec-multi-realm-eu-support.md`, `spec-production-data-refresh-strategy.md`, `runbook-api-surface.md`

## Goal

Populate data for the full EU player corpus over time, while loading the players most likely to receive immediate traffic first.

The priority rule is:

1. Best-ranked EU players first
2. then the rest of the active visible EU population
3. then the remaining long-tail EU players in controlled batches

Ranked battle data is a required part of this plan for the Best-priority cohort and the broader active cohort. EU Best loading is not complete unless the target cohort also has current `ranked_json` and refreshed ranked summary fields in `PlayerExplorerSummary`.

This runbook is for operational backfill and warm sequencing. It does not change the public API contract.

## Durable Crawler Of Record

The durable crawler for player collection should be the existing incremental refresh lane, not an ad hoc shell loop.

Current durable building blocks already in the repo:

1. `server/warships/management/commands/incremental_player_refresh.py`
2. `server/warships/tasks.py` `incremental_player_refresh_task`
3. `server/warships/management/commands/incremental_ranked_data.py`
4. `server/warships/tasks.py` `incremental_ranked_data_task`
5. `server/warships/signals.py` periodic schedules for both player refresh and ranked refresh per realm

These lanes are durable because they already provide:

1. JSON checkpoint state files under `logs/`
2. resumable progress via `pending_player_ids`, `next_index`, and retry tracking
3. Redis lock exclusion against the legacy clan crawl
4. bounded per-run limits and error budgets
5. realm-aware scheduling through Celery Beat

### What the durable player crawler already does

`incremental_player_refresh` currently covers:

1. core player stats through `fetch_players_bulk(...)` and `save_player(...)`
2. achievements when stale
3. efficiency data when stale
4. clan-battle summary refresh when stale

### What it does not yet cover fully

For full EU player-detail completeness, the durable player crawler still needs to own or dispatch:

1. `battles_json`
2. `tiers_json`
3. `type_json`
4. `randoms_json`
5. `activity_json`
6. ranked data, or at minimum a guaranteed handoff to the durable ranked lane

Status update:

1. the code path has now been extended so the durable player crawler calls the same realm-aware detail hydration helper used by the explicit warm path
2. ranked remains a separate durable lane via `incremental_ranked_data`, but the player crawler now owns the rest of the derived detail payload sequence needed for player pages

That means the right architecture is:

1. use `incremental_player_refresh` as the durable backbone for all EU players over time
2. use `incremental_ranked_data` as the durable ranked companion lane
3. use Best-priority bootstrap waves only to front-load the high-traffic cohort while the durable lanes catch up

## Why Best Ordering Needs Two Passes

The final landing `Best` ranking is built in `server/warships/landing.py` by `_build_best_landing_players(limit, realm=...)`.

That ranking depends on derived player payloads that are often sparse in a newly loaded realm:

1. `battles_json` is needed to compute `high_tier_pvp_battles` and `high_tier_pvp_ratio`
2. `ranked_json` and `PlayerExplorerSummary` carry `latest_ranked_battles` and `highest_ranked_league_recent`, which are direct inputs into the Best competitive score
3. players with fewer than `500` tier 5-10 PvP battles are excluded from the final Best list

Because of that dependency, a cold EU corpus cannot be ranked purely by the final Best formula on day zero. The safe approach is:

1. seed a large candidate pool by the same pre-order used by the Best builder
2. hydrate those candidates
3. rerank by the final Best formula
4. execute warming waves in that final Best order

## Current Constraint

Before running a large EU player warm, patch the remaining realm propagation gap in `server/warships/data.py`.

Current unsafe path:

1. `warm_player_entity_caches(..., realm='eu')` fetches EU players correctly
2. but it still calls `update_battle_data(player.player_id)` and `update_snapshot_data(player.player_id)` without passing `realm`
3. the same default-realm hole exists in the clan member sync path for newly created players

Until that is fixed and deployed, use explicit realm-aware hydrate steps instead of the generic warmer for EU bulk population.

## Ranking Source Of Truth

### Seed order

Use the Best candidate pre-order from `server/warships/landing.py`:

1. visible EU players only
2. `days_since_last_battle <= 180`
3. `pvp_battles > LANDING_PLAYER_BEST_MIN_PVP_BATTLES`
4. `last_battle_date is not null`
5. order by `explorer_summary__player_score DESC`, then `pvp_ratio DESC`, then `last_battle_date DESC`, then `name`
6. limit to `LANDING_PLAYER_BEST_CANDIDATE_LIMIT` which is currently `1200`

### Final Best order

After hydration, rerank by the final Best competitive logic from `_build_best_landing_players(limit, realm='eu')`.

Final sort keys:

1. `best_competitive_score DESC`
2. `high_tier_pvp_ratio DESC`
3. `player_score DESC`
4. `efficiency_rank_percentile DESC` with `shrunken_efficiency_strength` fallback
5. `name ASC`

This is the order that should drive the operational waves once the candidate pool has enough derived data to be trustworthy.

## Data To Populate Per Player

For each EU player in scope, populate these assets in order:

1. core player row via `update_player_data(player, force_refresh=True)`
2. ship stats via `update_battle_data(player_id, realm='eu')`
3. tier aggregates via `update_tiers_data(player_id, realm='eu')`
4. type aggregates via `update_type_data(player_id, realm='eu')`
5. randoms summary via `update_randoms_data(player_id, realm='eu')`
6. snapshot lane via `update_snapshot_data(player_id, realm='eu')`
7. activity JSON via `update_activity_data(player_id, realm='eu')`
8. ranked data via `update_ranked_data(player_id, realm='eu')`
9. explorer summary via `refresh_player_explorer_summary(player)`

Optional later lane:

1. achievements
2. efficiency badges

Those are useful for broader EU parity, but they are downstream of the required Best-loading set. Ranked data is required. Efficiency can tolerate a neutral fallback; ranked cannot be treated as an omitted lane in this plan.

## Coverage Strategy

This plan is not limited to the Best landing cohort.

The operational intent is:

1. use Best ordering to decide who gets hydrated first
2. use activity and recency to decide who gets hydrated next
3. keep extending coverage until the full EU player corpus has been processed over time

Best ordering is therefore a prioritization strategy, not the final scope boundary.

In operational terms:

1. the durable crawler drains the whole EU corpus over repeated runs
2. the Best-priority bootstrap determines which players get full detail hydration first
3. once the durable player crawler is extended to cover the full derived payload set, the one-off bootstrap should shrink back to a small operational accelerator instead of being the main collection path

## Wave Plan

### Phase 0: Patch the EU warm path

Before large-scale execution:

1. patch `warm_player_entity_caches(...)` to pass `realm` into `update_battle_data`, `update_snapshot_data`, `update_activity_data`, `update_tiers_data`, `update_type_data`, `update_randoms_data`, `update_ranked_data`, and `fetch_player_clan_battle_seasons`
2. patch the clan member sync path so newly created EU players do not default back to NA on immediate follow-on hydration
3. extend `incremental_player_refresh` so the durable player crawler can collect the derived player payloads needed for EU player-detail completeness, or explicitly dispatch those lanes in a bounded way
4. deploy backend

### Phase 1: Seed the EU Best candidate pool

Build the top `1200` EU seed candidates by the pre-order, not the final Best order.

Reason:

1. the final Best formula is only reliable once `battles_json` exists
2. this seed set mirrors the actual Best builder entry gate and keeps the operation bounded

### Phase 2: Prime the top shelf

Hydrate the first `100` seed candidates explicitly with realm-aware calls.

Execution priority:

1. top `25`
2. ranks `26-100`

After this phase:

1. rerun `_build_best_landing_players(limit=100, realm='eu')`
2. treat that reranked output as the authoritative top shelf
3. confirm the reranked top `100` has ranked coverage before promoting the wave
4. bulk-load those players into Redis

### Phase 3: Expand to the serious cohort

Hydrate seed ranks `101-500`, then rerank again using the final Best builder.

Execution priority:

1. hydrate `101-250`
2. hydrate `251-500`
3. bulk-load the reranked top `500`

### Phase 4: Finish the active Best candidate pool

Hydrate seed ranks `501-1200`, then rerank the full candidate pool.

Outcome target:

1. final EU Best landing rows are driven by real `battles_json` and ranked/activity-derived summaries across the whole candidate pool
2. the best-player public surface is no longer dominated by cold-start missing-data bias

### Phase 5: Expand beyond Best to the rest of active EU

After the top `1200` Best candidate pool has been hydrated and reranked:

1. continue with the remaining visible EU players who are active enough to matter operationally
2. prioritize by recent activity and existing summary strength, for example `days_since_last_battle ASC`, then `player_score DESC`, then `pvp_battles DESC`
3. keep ranked collection enabled for this active cohort, because ranked freshness materially affects player detail quality and future Best reranks
4. prefer doing this through the durable `incremental_player_refresh` queue, not manual loops

Recommended tranche order after Best:

1. visible EU players active within `30` days
2. visible EU players active within `31-90` days
3. visible EU players active within `91-180` days

### Phase 6: Sweep the long-tail EU corpus over time

After the active visible population is in good shape:

1. backfill the remaining EU long tail in bounded batches
2. prefer descending `player_score`, then descending `pvp_battles`, then newer `last_battle_date`
3. include dormant and lower-priority players so the full EU realm converges toward broad coverage over time
4. keep page-load refresh as the safety net for any player not yet fully hydrated

For this phase, core and derived player data still matter most. Ranked can be collected opportunistically if rate budget becomes tight, but the system should keep draining the ranked backlog through the existing incremental ranked lane.

This phase should be entirely crawler-driven. If manual shell batches are still required here, the durable crawler is not yet doing enough.

### Phase 7: Maintain freshness

Once the backfill is established:

1. run `warm_landing_page_content(..., realm='eu')`
2. run `bulk_load_player_cache(top_player_limit=<target>, realm='eu')`
3. schedule ranked upkeep for EU through `incremental_ranked_data_task(realm='eu')` so the reranked cohort does not drift stale between explicit warm cycles
4. let page-load refreshes handle long-tail dormant players
5. schedule a daily or twice-daily EU Best refresh for the reranked top cohort
6. keep the durable incremental player crawler running so the remaining EU corpus continues converging toward full coverage instead of stalling after the top cohorts are done

## Suggested Operational Commands

### 1. Build the seed cohort

Use a Django shell snippet that mirrors the Best builder pre-order and records the ordered EU player IDs.

```python
from django.db.models import F
from warships.landing import LANDING_PLAYER_BEST_CANDIDATE_LIMIT
from warships.models import Player

seed_ids = list(
    Player.objects.exclude(name='').filter(
        realm='eu',
        is_hidden=False,
        days_since_last_battle__lte=180,
        pvp_battles__gt=2500,
    ).exclude(
        last_battle_date__isnull=True,
    ).order_by(
        F('explorer_summary__player_score').desc(nulls_last=True),
        F('pvp_ratio').desc(nulls_last=True),
        F('last_battle_date').desc(nulls_last=True),
        'name',
    ).values_list('player_id', flat=True)[:LANDING_PLAYER_BEST_CANDIDATE_LIMIT]
)
```

### 2. Hydrate a wave explicitly

Use explicit realm-aware function calls only as a bootstrap path until the durable crawler owns the same detail lanes.

```python
from warships.data import (
    refresh_player_explorer_summary,
    update_activity_data,
    update_battle_data,
    update_player_data,
    update_randoms_data,
    update_ranked_data,
    update_snapshot_data,
    update_tiers_data,
    update_type_data,
)
from warships.models import Player

for player in Player.objects.filter(player_id__in=seed_ids[:25], realm='eu').select_related('explorer_summary', 'clan'):
    update_player_data(player, force_refresh=True)
    update_battle_data(player.player_id, realm='eu')
    update_tiers_data(player.player_id, realm='eu')
    update_type_data(player.player_id, realm='eu')
    update_randoms_data(player.player_id, realm='eu')
    update_snapshot_data(player.player_id, realm='eu')
    update_activity_data(player.player_id, realm='eu')
    update_ranked_data(player.player_id, realm='eu')
    player.refresh_from_db()
    refresh_player_explorer_summary(player)
```

Ranked collection rule:

1. do not mark a player wave complete unless `update_ranked_data(player_id, realm='eu')` has run successfully for the wave
2. after the wave, verify `Player.ranked_json` is non-null and `PlayerExplorerSummary.latest_ranked_battles` has been recomputed for the same cohort

### 2b. Run the durable crawler

The durable crawler should be the primary recurring collection mechanism for EU.

Manual command:

```bash
python manage.py incremental_player_refresh --realm eu
python manage.py incremental_ranked_data --realm eu
```

Task-backed path:

1. `incremental_player_refresh_task(realm='eu')`
2. `incremental_ranked_data_task(realm='eu')`

Expected behavior:

1. resume from checkpoint files instead of restarting from scratch
2. skip if the legacy clan crawl lock is active
3. keep making forward progress even if one run hits an error budget or times out

### 3. Rerank by the true Best formula

```python
from warships.landing import _build_best_landing_players

top_rows = _build_best_landing_players(limit=100, realm='eu')
top_ids = [row['player_id'] for row in top_rows]
```

### 4. Bulk-load the warmed cohort

```python
from warships.data import bulk_load_player_cache

bulk_load_player_cache(top_player_limit=100, clan_member_clans=0, realm='eu')
```

## Verification

After each phase, verify:

1. count of EU top-cohort players with non-null `battles_json`
2. count with non-null `tiers_json`, `type_json`, `randoms_json`, and `activity_json`
3. count with non-null and fresh `ranked_json`
4. count with populated `explorer_summary__latest_ranked_battles` and `explorer_summary__highest_ranked_league_recent`
5. public `/api/landing/players/?mode=best&limit=25&realm=eu` returns stable populated rows
6. public `/api/player/<name>/?realm=eu` returns `200` for at least three players in the reranked top `25`

Recommended stop conditions:

1. top `25` fully hydrated, including ranked, before moving on
2. top `100` fully hydrated, including ranked, before widening to `500`
3. final top `500` bulk-cached with ranked coverage before declaring the EU Best surface healthy
4. top `1200` Best candidates fully hydrated before shifting the main budget to non-Best EU players
5. active visible EU players broadly covered before spending significant budget on the long-tail dormant cohort

## Success Criteria

This plan is complete when:

1. the EU top `25` Best players are fully hydrated, ranked-populated, and cached
2. the EU top `100` Best players are fully hydrated, ranked-populated, and cached
3. the full `1200` Best candidate pool has enough derived data that `_build_best_landing_players(..., realm='eu')` is ranking on real high-tier and ranked inputs rather than missing-data fallbacks
4. the active visible EU population beyond the Best cohort is materially hydrated
5. the remaining EU long-tail population is being drained by a continuing low-priority backfill lane over time
6. ongoing EU refreshes can use the generic realm-safe warmer without manual workaround loops
7. ranked freshness for the EU Best cohort is maintained by the existing incremental ranked lane rather than ad hoc one-off warm commands
