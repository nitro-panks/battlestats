# Runbook — Landing featured-boards decommission (2026-06-22)

**Status:** dated-active · **Kind:** runbook · **Section:** feature-recovery · **Section:** frontend + backend (landing)

Removed the two landing-page **featured boards** — the "Players · Best" board and the "Active Clans ·
Best" board (each = a D3 scatterplot + a clickable name/tag grid + sort tabs + a formula tooltip) —
and stopped the Celery warmers that fed them. The landing page is now **search → top-ships treemap →
inline ship leaderboard**. Task functions, DRF endpoints, and routes are **kept idle** so the boards
can be revived from git + a registration revert.

Related: `runbook-player-fetch-orchestration-2026-06-21.md` (landing fetch layer),
`runbook-db-cpu-saturation-2026-05-24.md` (the republish-debounce these warmers fed),
`ops-env-reference.md` (the now-inert env vars).

## Why

Umami, trailing 30 days (1,440 landing sessions / 3,637 views of `/`):

- **`landing-player-click` = 0** — the Players scatterplot + its name grid drove zero navigation.
- **`landing-clan-click` = 1** — the Clans scatterplot + its tag grid drove one click, and that event
  is shared between the chart and the grid, so even that one may have been the grid.

Search is the real funnel (860 events / 439 sessions); the treemap discovery charts get real
engagement (~250 events). The featured boards were dead weight that also kept three warm-dispatch
paths and two daily/6-hourly Beat families running against the 2-vCPU managed-PG.

Both the scatterplot **and** its grid read the **identical** warmed cache payload, so the warmer
could only be stopped by removing the whole sections (charts + grids + tabs). Confirmed with the user
before implementing.

## What was removed

### Frontend (`client/`)
- `app/components/PlayerSearch.tsx` — both `{...}` featured sections; all state/effects/handlers that
  fed them (`clans`, `players`, `*BestSort`, `fetchLandingClans/Players`, `triggerBestLandingWarmup`,
  `refreshLandingBest`, the focus/visibility/interval refreshers, `handleSelectClan` /
  `handleSelectLandingPlayer`, and the local `PlayerNameGrid` / `ClanTagGrid` components). The
  `landing-clan-click` / `landing-player-click` / `landing-best-sort` trackEvent call sites went with
  them.
- **Deleted** `app/components/LandingPlayerSVG.tsx`, `app/components/LandingClanSVG.tsx`.
- **Pruned** the now-orphaned chartTheme helpers `wrColorByPercent`, `expandDomain`
  (`app/lib/chartTheme.ts`). `wrColorByRatio` + `formatCompactCount` stay (other charts use them).
- Rewrote `app/components/__tests__/PlayerSearch.test.tsx` to cover only the retained behaviour:
  landing renders treemap + ship leaderboard with no featured boards, q-param load + back, nav-search
  error, clan-hydration poll.

### Backend (`server/warships/`)
- `signals.py` — removed the two Beat registration loops; added the schedule names to
  `_RETIRED_SCHEDULE_NAMES` so `post_migrate` **purges the live `PeriodicTask` rows on deploy**:
  - `landing-page-warmer` + `landing-page-warmer-{na,eu,asia}` → `warm_landing_page_content_task`
  - `landing-best-player-snapshot-materializer-{na,eu,asia}` → `materialize_landing_player_best_snapshots_task`
- `landing.py` — `_queue_landing_republish()` short-circuited to a **no-op early return** (the third
  warm-dispatch path beyond Beat: it fired on player/clan writes **and** on published-fallback reads).
  Body kept intact below the return for revival.

## What was deliberately KEPT (idle, for revival)
- Task functions `warm_landing_page_content_task`, `materialize_landing_player_best_snapshots_task`
  (+ `warm_landing_page_content` / `materialize_landing_player_best_snapshots` in `landing.py`).
- DRF views + routes `landing_players` (`/api/landing/players`), `landing_clans`
  (`/api/landing/clans`), `landing_best_warmup` (`/api/landing/warm-best`). No frontend consumer
  remains; `mode=sigma|popular` were never consumed either. They serve correctly if hit but no longer
  self-warm.
- `LandingPlayerBestSnapshot` model + rows (the Best durable fallback).
- `score_best_clans()` — still used by the bulk entity loader (`bulk_load_player_cache` /
  `bulk_load_clan_cache`); out of scope.
- The search autocomplete endpoints `/api/landing/{player,clan}-suggestions` (HeaderSearch) — unrelated.

## Load-bearing verification gate

Because the task functions are **retained**, the `_RETIRED_SCHEDULE_NAMES` row-purge on `post_migrate`
is the **only** thing that stops Beat from dispatching them — there is no "task not found" error to
fall back on if it silently doesn't run. The backend deploy runs `manage.py migrate --noinput`
(`server/deploy/deploy_to_droplet.sh`), so the purge fires on deploy. The real pass/fail gate is:

1. **Tests** — `DB_ENGINE=sqlite3 python -m pytest warships/tests/test_periodic_schedule_topology.py
   warships/tests/test_landing.py --nomigrations` green (topology asserts the two families are now in
   the retired set; landing asserts `_queue_landing_republish` is a no-op and fallback still serves).
2. **Post-deploy (droplet)** — no `PeriodicTask` rows named `landing-page-warmer-*` or
   `landing-best-player-snapshot-materializer-*`:
   ```bash
   ssh root@battlestats.online "cd /opt/battlestats/current/server && \
     venv/bin/python manage.py shell -c \"from django_celery_beat.models import PeriodicTask; \
     print(list(PeriodicTask.objects.filter(name__startswith='landing-').values_list('name', flat=True)))\""
   # expect: [] (or only unrelated landing-* if any) — NOT the warmer/materializer names
   ```
3. **Beat log** — over one full cycle, no dispatch of `warm_landing_page_content_task` /
   `materialize_landing_player_best_snapshots_task`; viewing a player/clan no longer triggers a
   landing republish.
4. **Sanity** — landing shows no featured boards; search autocomplete + treemap still work.

## Revival recipe

1. Restore the FE sections + the two SVG components + the two chartTheme helpers from the pre-removal
   commit (this branch's parent).
2. `signals.py` — drop the two families from `_RETIRED_SCHEDULE_NAMES` and re-add the two registration
   loops (git revert the relevant hunks).
3. `landing.py` — delete the `_queue_landing_republish` early-return to restore warm-on-write +
   warm-on-fallback.
4. Re-point the topology test (`landing-page-warmer` back into `STRIPED_PER_REALM_FAMILIES` +
   `NA_LANE_FAMILIES`, out of `RETIRED_PER_REALM_FAMILIES`) and the `_queue_landing_republish` test.
5. Deploy backend (re-registers the schedules) then rebuild + deploy the client.

## Inert env vars (no longer scheduled; safe to leave set)
`LANDING_PAGE_WARM_MINUTES`, `LANDING_BEST_PLAYER_SNAPSHOT_HOUR`, `LANDING_REPUBLISH_COOLDOWN_SECONDS`.

## 3.0 — backend fully removed (decommission complete)

The 2026-06-22 pass kept the task functions, DRF endpoints, and routes **idle** for cheap
revival. In **3.0** the backend was **fully removed**, completing the decommission — the
"kept idle" / "Revival recipe" text above is now historical:

- **Endpoints deleted:** `landing_players`, `landing_clans`, `landing_best_warmup`,
  `landing_activity_attrition`, `analytics_top_entities`.
- **Scoring/builders deleted:** `score_best_clans()` and all Best/Popular landing builders.
- **Warmers deleted:** the Best/Popular landing warm-dispatch paths (including the
  `BULK_CACHE_BEST_PREWARM_ENABLED` best-prewarm branch of the 12h bulk loader).
- **Model dropped:** `LandingPlayerBestSnapshot` (table dropped via migration).

Reviving the boards now requires restoring these from git history, not just a registration
revert. `landing.py` still serves the live surfaces (ship treemap / tier-type list). This
runbook remains the durable record of the decommission.
