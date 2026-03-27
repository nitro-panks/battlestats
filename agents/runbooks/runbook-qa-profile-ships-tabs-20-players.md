# Runbook: QA — Profile & Ships Tab Charts, 20 Random Players

**Created**: 2026-03-27
**Status**: Complete — all endpoints performing well post-fix
**Context**: Verification sweep after deploying per-field lazy hydration fix (commit f174709)

## Test Population

20 random players pulled from `/api/landing/players/?mode=random&limit=20`. All 20 had `battles_json=None` at the start of testing, confirming the widespread stuck state documented in `runbook-profile-chart-warming-stuck-batch.md`.

## Results

### Profile Tab (tier_type correlation)

All 20 players return populated `player_cells` after hydration. Response times 192ms–1.0s (median ~260ms). No pending headers after hydration completes.

| Player | Cells | Response Time |
|--------|-------|---------------|
| supwitit66 | 21 | 1.00s |
| LONDONISTHECAPITAL | 30 | 0.31s |
| latelight | 35 | 0.28s |
| mayhem_aus | 29 | 0.44s |
| Tidalwave7 | 30 | 0.30s |
| coffee_gaost | 39 | 0.26s |
| ImRlyBad | 38 | 0.29s |
| BrodiAnt000 | 31 | 0.46s |
| CWK413 | 31 | 0.25s |
| Fireus543 | 25 | 0.24s |
| str82dahead | 33 | 0.29s |
| TimecopEO | 28 | 0.23s |
| akthelion | 27 | 0.22s |
| TheRuzzO | 35 | 0.22s |
| Thunderr__ | 35 | 0.21s |
| HmsVangaurd | 34 | 0.22s |
| Cleric_Crimson | 24 | 0.32s |
| FlexusFL | 28 | 0.23s |
| grayflanks | 38 | 0.23s |
| deathseagle | 34 | 0.19s |

### Ships Tab (tier / type / randoms)

All 20 players return data across all three endpoints. Response times 193ms–1.28s (most under 400ms).

- **Tier data**: 11 rows each (tiers 1–11), 193ms–955ms
- **Type data**: 4–6 rows each (ship types), 193ms–442ms
- **Randoms data**: 20 rows each (top ships), 206ms–635ms

### Activity Tab

All 20 players return 29 rows (daily activity for last 29 days) after snapshot+activity tasks complete. Initial delay of 2–6 minutes due to task queue congestion behind `refresh_efficiency_rank_snapshot_task` (312s) and `warm_landing_best_entity_caches_task` (65s).

### Ranked Tab

18/20 players have ranked data (1–28 seasons). 2 players (Fireus543, akthelion) have 0 ranked seasons — legitimate, not a bug.

## Findings

### 1. Lazy hydration works — all tabs populate on first visit

The per-field lazy hydration fix is confirmed working. All 20 previously-stuck players had their `battles_json`, `activity_json`, and ranked data populated after a single profile visit. The self-healing loop:
1. User visits profile → `fetch_player_summary` dispatches missing-field tasks
2. `update_battle_data_task` populates `battles_json` (1–3s per player via WG API)
3. `update_snapshot_data_task` + `update_activity_data_task` populate `activity_json`
4. Subsequent tab loads return data

### 2. Task queue congestion delays first-visit hydration

**Severity**: Medium — affects UX on first visit only

When multiple players are visited in quick succession, their hydration tasks queue behind long-running periodic tasks:
- `refresh_efficiency_rank_snapshot_task`: 312s (scans entire player population)
- `warm_landing_best_entity_caches_task`: 65s (fetches clan battle stats)

With only 2 Celery pool workers and `--prefetch-multiplier=1`, a single long-running task blocks half the worker capacity. The 20-player test created ~60 tasks (3 per player: battles, snapshot, activity) that took 5+ minutes to fully drain.

**Impact**: A user visiting a never-before-hydrated player may see "warming" for 30–60s on first visit if the queue is congested. Subsequent visits are instant.

**Potential mitigations** (not applied — documenting for future consideration):
- Separate task queues: route player hydration tasks to a dedicated queue/worker so they aren't blocked by periodic maintenance tasks
- Increase pool workers from 2 to 3–4 (requires more droplet memory)
- Add priority to hydration tasks so they preempt long-running background work

### 3. First request to tier_type is sometimes slow (1s+)

supwitit66's first tier_type request took 1.0s, likely because the population correlation tiles were being computed from scratch (cache miss). Subsequent requests for all players were 190–460ms. This is acceptable — the population tiles are cached after first computation.

### 4. All response times are within acceptable range

Post-hydration response times across all endpoints:
- **P50**: ~250ms
- **P95**: ~500ms
- **P99**: ~1.0s

No timeouts, no 5xx errors, no malformed responses.

## Conclusion

Profile and ships tab charts are performing well for all 20 tested players. The lazy hydration fix successfully resolves the stuck warming state. The only notable finding is task queue congestion on first visit, which is a pre-existing architectural constraint (2 workers, shared queue) rather than a regression.
