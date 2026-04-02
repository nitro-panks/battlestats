# Spec: API Surface Performance Comparison After Migration Tuning

_Captured: 2026-04-02_

_Status: Production after-state snapshot and comparison against the earlier 2026-04-02 baseline_

## Scope

This comparison covers the migration-safe backend tuning that was deployed for the active EU clan/player population run, plus the client-side landing-page fallback that prevents the EU homepage from showing an empty Best clan section while tracked clan data is still warming up.

Reference baseline:

- `agents/runbooks/spec-api-surface-performance-baseline-2026-04-02.md`

## Landing Page Validation

Before taking the after-state snapshot, the live homepage was rendered in a real browser against production.

Observed on `https://battlestats.online/?realm=eu`:

- the landing page rendered active clan and player data normally
- EU random clans and players both displayed populated charts and tag grids
- EU Best clans no longer produced a blank chart-only section
- when Best clan ranking data is empty, the page now shows a clear warmup note and falls back to recent clans instead of leaving the surface empty

Live rendered text after the client deploy included:

```text
Best clan rankings are still warming up for this realm. Showing recent clans until enough tracked data is available.
```

That made the homepage safe to use as part of the after-state capture.

## What Changed

### Backend migration tuning

- `CLAN_CRAWL_RATE_LIMIT_DELAY=0.25`
- `CLAN_CRAWL_CORE_ONLY_RATE_LIMIT_DELAY=0.10`
- `MAX_CONCURRENT_REALM_CRAWLS=1`
- deploy/bootstrap now clear realm-scoped crawl locks before service restart
- Celery units are env-backed with explicit concurrency and `--max-memory-per-child`

### Frontend landing fallback

- the homepage now falls back to recent clans when Best clan mode has no eligible payload yet
- the fallback is explicit in the UI rather than silently rendering a blank state
- focused Jest coverage was added for that fallback behavior

## Production Host Comparison

### Memory

| Metric | Baseline | After | Delta |
| --- | ---: | ---: | ---: |
| RAM used | `3.6 GiB` | `1.4 GiB` | improved by about `2.2 GiB` |
| RAM available | `243 MiB` | `2.5 GiB` | improved by about `2.26 GiB` |
| Swap used | `501 MiB` | `1.4 GiB` | increased |

Interpretation:

- the box is no longer in the near-OOM posture seen in the baseline
- swap usage stayed elevated, but active RAM pressure dropped sharply
- this looks like reclaimed working-set pressure rather than a host that is still fighting for live memory

### Top RSS processes

Baseline standout:

- unidentified `/dev/shm/f43np` process at about `1.73 GiB RSS`

After snapshot top consumers:

- background Celery worker: about `250 MiB RSS`
- Next.js server processes: about `178 MiB` and `95 MiB`
- default Celery workers: about `150-156 MiB`
- gunicorn workers: about `19-30 MiB`

Interpretation:

- the earlier unidentified `/dev/shm` outlier was gone from the top RSS list
- the app process set is now the dominant resident memory consumer, which makes the host snapshot much easier to reason about

### Celery shape

Baseline live shape:

- default `3`
- hydration `4`
- background `2`

After-state env-backed shape:

- default `3`
- hydration `3`
- background `2`

Additional guardrails now visible in `/etc/battlestats-server.env`:

- `CELERY_DEFAULT_MAX_MEMORY_PER_CHILD_KB=393216`
- `CELERY_HYDRATION_MAX_MEMORY_PER_CHILD_KB=393216`
- `CELERY_BACKGROUND_MAX_MEMORY_PER_CHILD_KB=786432`

Interpretation:

- the deploy moved the queue layout from implicit unit-file state to explicit env-backed runtime control
- hydration concurrency dropped from the previously observed live `4` to `3`, which is consistent with the improved RAM headroom

## Public API Comparison

Method was unchanged from the baseline: 5 direct `curl` samples per endpoint against production.

### `GET /api/player/lil_boots/`

Baseline:

- median about `8.06 s`
- max `16.46 s`

After:

- samples: `1.32`, `0.87`, `0.43`, `0.53`, `0.96`
- median about `0.87 s`
- max `1.32 s`

Interpretation:

- this is the largest improvement in the probe set
- the severe tail from the baseline was not reproduced in the after-state sample

### `GET /api/landing/players/?mode=best&limit=25&realm=na`

Baseline:

- median about `0.21 s`
- max `14.78 s`

After:

- samples: `0.21`, `0.20`, `0.25`, `0.18`, `0.19`
- median about `0.20 s`
- max `0.25 s`

Interpretation:

- steady-state remained good
- the large cache-miss or contention outlier from the baseline was not reproduced

### `GET /api/landing/clans/?mode=best&limit=30&realm=na`

Baseline:

- median about `0.17 s`
- max `0.20 s`

After:

- samples: `0.19`, `0.18`, `0.20`, `0.19`, `0.17`
- median about `0.19 s`
- max `0.20 s`

Interpretation:

- effectively unchanged
- this endpoint remained stable and is still a good control surface

### `GET /api/fetch/player_distribution/win_rate/?realm=na`

Baseline:

- median about `0.21 s`
- max `0.33 s`

After:

- samples: `0.30`, `0.25`, `0.27`, `0.20`, `0.60`
- median about `0.27 s`
- max `0.60 s`

Interpretation:

- this probe was modestly slower in the after-state sample window
- even so, it remained sub-second and did not show the multi-second tail behavior seen earlier on player detail

## EU Migration Status After Deploy

Background worker logs after the backend deploy showed:

```text
Starting crawl_all_clans_task resume=True dry_run=False limit=None realm=eu core_only=True
Starting crawl (realm=eu, resume=True, dry_run=False, limit=None, core_only=True, request_delay=0.100s)
```

That confirms the migration resumed automatically in the faster `core_only` lane after the deploy.

Follow-up production probe showed:

```python
{
  'players': 386090,
  'clans': 42639,
  'visible_players': 367311,
  'efficiency_filled': 38,
  'latest_player_fetch': datetime.datetime(2026, 4, 2, 4, 46, 6, 535696),
  'latest_clan_fetch': datetime.datetime(2026, 4, 2, 4, 46, 4, 822163),
}
```

Compared with the immediate post-deploy probe taken earlier in the session:

- total EU players remained `386090`
- total EU clans remained `42639`
- visible EU players increased from `361166` to `367311`

Interpretation:

- the deploy did not break the migration path
- the crawl restart was confirmed in logs
- the short observation window did not yet show new top-line player or clan totals, so the strongest evidence of continuity is the resumed task log plus the increase in visible EU players

## Bottom Line

Before the after-state snapshot, the landing page was restored to a clearly usable state, including a non-empty EU Best clan experience. Compared with the earlier baseline, the production host is under far less active RAM pressure, the unidentified giant `/dev/shm` resident process is gone from the top RSS list, and the severe tail latency on the sampled player-detail and landing-player endpoints collapsed. The EU migration also resumed after deploy in the intended `core_only` fast path, so the performance work did not strand the migration.