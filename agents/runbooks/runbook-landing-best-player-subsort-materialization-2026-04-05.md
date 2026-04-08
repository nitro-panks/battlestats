# Runbook: Landing Best-Player Sub-sort Materialization

_Created: 2026-04-05_

## Purpose

Capture the current state of the landing-page Best-player sub-sort work, including the Ranked medal-order contract, the production cache incidents that followed, the cold-path performance investigation, and the shift to DB-backed daily materialization for all Best-player sub-sorts.

This document is the continuation point for any follow-up work on:

1. landing Best-player correctness,
2. landing Best-player cache behavior,
3. landing Best-player production deploy/warm flow,
4. landing Best-player daily snapshot materialization.

## Executive Summary

The landing page Best-player surface now exposes five backend-controlled sub-sorts behind the existing `/api/landing/players/?mode=best&sort=...` contract:

1. `overall`
2. `ranked`
3. `efficiency`
4. `wr`
5. `cb`

Historical note:

- `abs` was added briefly on 2026-04-07 and retired in the follow-up ABS/clan-CB deprecation tranche. Snapshot materialization now covers only the five active sorts above.

The Ranked sub-sort was initially tuned as a heuristic competitive score, but the intended product meaning was later clarified to be a medal table:

1. more Gold finishes first,
2. ties break by aggregate ranked win rate,
3. then more Silver finishes,
4. then more Bronze finishes,
5. then freshness, recent volume, and player score as later tie-breakers.

That contract is now implemented in code and validated in tests.

Production correctness then exposed two landing-player cache bugs:

1. dirty player-cache state still allowed stale published payloads to be served,
2. published player-cache keys were not namespaced, so stale non-default `limit` payloads could survive deploys and invalidations.

Those bugs were fixed and verified live.

After correctness was restored, profiling showed that cold request-time rebuilds of the Ranked Best-player list were too expensive for production. A cold Ranked `limit=25` rebuild against the production-backed corpus took about `49.4s` and could hit gunicorn timeout. The exact Ranked medal order also could not be preserved safely by a shallow candidate cutoff.

The chosen architecture is therefore:

1. materialize the top-25 payload once per `realm + sort`,
2. store it in the database,
3. serve Best-player requests by slicing that stored payload,
4. refresh it once per day,
5. keep the public API contract unchanged.

## CB Sort Update

As of 2026-04-05, the `cb` Best-player sub-sort no longer uses the earlier capped-volume composite.

The current `cb` ranking intent is:

1. use a confidence-adjusted CB win-rate score derived from `clan_battle_overall_win_rate` and `clan_battle_total_battles`
2. reward players who sustain strong CB results over much larger samples
3. keep `clan_battle_seasons_participated` as a secondary durability signal
4. keep the existing `is_clan_battle_enjoyer(...)` eligibility floor for the first rollout

The practical effect is that two players with the same raw CB WR no longer collapse toward the same rank once both clear an arbitrary volume cap. Larger battle samples continue to matter.

## User Intent History

The work evolved through these explicit user requests:

1. explain how the Ranked Best-player sub-sort worked,
2. factor Ranked leagues into the score so Bronze could not beat Silver and Silver could not beat Gold,
3. tune the within-band weights,
4. make a broader Ranked tuning pass,
5. release, deploy, and verify,
6. explain why the live top five did not reflect Gold-medal leadership,
7. redefine Ranked ordering as Gold medals first, then WR, then Silver, then Bronze,
8. verify the Ranked list was cached and performant,
9. improve both warm behavior and cold-path performance,
10. stop recomputing these lists on request and store them in the DB once per day,
11. apply that same daily-storage design to all Best-player sub-sort lists.

## Chronological Implementation Record

### 1. Best-player sub-sort feature shipped

Best-player sub-sorts were already implemented and deployed before the latest architecture pivot.

### 2. Separate server CI hard-gate fix completed

An unrelated but blocking server CI issue on `main` was fixed by adding `pyyaml==6.0.3` to:

1. `server/requirements.txt`
2. `server/Pipfile`

That fix shipped in commit `c01a496`, and GitHub Actions run `23991482257` completed successfully.

### 3. Ranked heuristic iterations

The Ranked Best-player sub-sort went through several iterations before the medal-table clarification:

1. hard Bronze/Silver/Gold league bands,
2. tuned within-band weights,
3. recent WR, freshness, and low-sample damping.

Touched files during those iterations included:

1. `server/warships/landing.py`
2. `server/warships/tests/test_views.py`
3. `client/app/components/PlayerSearch.tsx`

Focused SQLite test slices passed during those tranches.

### 4. Heuristic Ranked version released

The broader heuristic Ranked version shipped as:

1. `0edc42d fix: improve ranked landing player sorting`
2. release commit `ccc42b9 chore: bump version to 1.6.14`
3. tag `v1.6.14`

Backend and client deployed successfully, and the homepage and Ranked endpoint returned `200`.

### 5. Medal-table requirement discovered from live data

When the live top five were checked, the endpoint returned:

1. `Noob_CoralSea`
2. `bfk_ferlyfe`
3. `FlakFiend`
4. `Evrien`
5. `Airman0386`

The live data investigation showed `Noob_CoralSea` had only `3` Gold finishes, while multiple players had `28` Gold finishes. That established that the intended meaning of Gold medals was historical first-place finishes, not a heuristic proxy.

### 6. Ranked medal-history redesign implemented

The Ranked Best-player sub-sort was rewritten to use `Player.ranked_json` medal history with this order:

1. Gold count descending,
2. aggregate ranked WR descending,
3. Silver count descending,
4. Bronze count descending,
5. freshness / volume / player score only as later tie-breakers.

Files updated:

1. `server/warships/landing.py`
2. `server/warships/tests/test_views.py`
3. `client/app/components/PlayerSearch.tsx`

Focused regressions were added for:

1. medal precedence,
2. WR tie-breaks among Gold leaders,
3. Silver after Gold/WR ties.

### 7. Medal-history release deployed but live order stayed stale

The medal-history Ranked version shipped as:

1. `2442bb2 fix: rank landing best ranked players by medal history`
2. release commit `31931db chore: bump version to 1.6.15`
3. tag `v1.6.15`

Backend and client deployed, but the public endpoint still returned the stale `Noob_CoralSea` ordering.

### 8. Production correctness investigation

Direct Django-shell computation against the production-backed DB returned the correct medal-ordered top group:

1. `DGPitbull3`
2. `jkc12`
3. `Aline_Ranger`
4. `Kage_Acheron`
5. `_Knotty_Beaver_`

The live HTTP payload was still stale, proving the issue was cache and release activation, not ranking logic.

### 9. Dirty-key player-cache bug fixed

The first production cache issue was that player landing payloads could still serve stale published results even after the player dirty key had been set.

That shipped as:

1. `fd38e88 fix: rebuild dirty landing player caches immediately`
2. release commit `4c6249e chore: bump version to 1.6.16`

Backend deploy revealed an additional operational issue: `/opt/battlestats-server/current` did not reliably advance to the latest release. Manual activation was required.

### 10. Release boot failure due to log permissions

After manual activation of the new backend release, gunicorn failed because `server/logs/django.log` was not writable by the release user. This was repaired manually by creating and fixing ownership for the release log directory, then restarting services.

### 11. Published-key namespace bug fixed

The second cache issue was that published landing-player payload keys were not tied to the player-cache namespace, so stale non-default `limit` payloads could survive deploy and invalidation.

That shipped as:

1. `dc1524f fix: namespace published landing player caches`
2. release commit `8326d9e chore: bump version to 1.6.17`

The backend deploy again failed to move `current` correctly, so the new release had to be manually activated. Landing-player caches were invalidated and the Ranked `limit=5` cache was rewarmed.

Final public verification then matched the intended medal order:

1. `DGPitbull3`
2. `jkc12`
3. `Aline_Ranger`
4. `Kage_Acheron`
5. `_Knotty_Beaver_`

### 12. Cache and performance audit

Once production correctness was fixed, the Ranked Best-player cache path was profiled.

Warm behavior was acceptable:

1. `limit=5` Django-side cached reads were about `0.023s`,
2. repeated live HTTP `limit=5` reads were about `0.18s`,
3. warm `limit=25` HTTP reads were about `0.20s`.

Cold behavior was not acceptable:

1. a cold `force_refresh=True` direct rebuild for Ranked `limit=25` took about `49.4s`,
2. a live gunicorn request could time out after about `30s` and return `500`,
3. the expensive part was `_build_best_ranked_landing_players`, which loaded and decoded a very large eligible cohort.

### 13. Ranked candidate-pool shortcut rejected

The production-backed profile showed the Ranked eligible NA cohort was about `37,247` players.

Exact medal-order preservation was tested against shallow candidate pools ordered by Ranked seasons:

1. top `100` candidates missed `10` of the exact top `25`,
2. top `200` missed `7`,
3. top `300` missed `5`,
4. top `500` missed `4`,
5. top `800` missed `3`,
6. top `1200` missed `2`.

This made a shallow request-time shortlist unsafe for preserving the exact medal leaders.

### 14. Architecture pivot to DB-backed daily snapshots

The selected design became:

1. compute the exact top-25 once per `realm + sort`,
2. store that payload in the database,
3. serve smaller request limits by slicing the stored payload,
4. refresh the stored payload once per day,
5. apply the same architecture to all Best-player sub-sorts, not only Ranked.

## Current Architecture

### Source of truth

Best-player sub-sort payloads are now moving to `LandingPlayerBestSnapshot`, keyed by:

1. `realm`
2. `sort`

Stored fields:

1. `payload_json`
2. `generated_at`

There is a unique constraint on `realm + sort`.

### Request path

The landing Best-player request path now reads a stored snapshot first. If the snapshot is missing, the backend materializes it once and persists it before serving the response.

This preserves the public payload shape and endpoint contract:

1. `/api/landing/players/?mode=best&sort=overall`
2. `/api/landing/players/?mode=best&sort=ranked`
3. `/api/landing/players/?mode=best&sort=efficiency`
4. `/api/landing/players/?mode=best&sort=wr`
5. `/api/landing/players/?mode=best&sort=cb`

### Refresh path

The refresh path now includes:

1. a materialization helper in `server/warships/landing.py`,
2. a Celery task in `server/warships/tasks.py`,
3. a daily Beat schedule in `server/warships/signals.py`,
4. a manual management command in `server/warships/management/commands/materialize_landing_player_best_snapshots.py`.

## Files and Functions That Matter

### Backend logic

Primary files:

1. `server/warships/landing.py`
2. `server/warships/models.py`
3. `server/warships/tasks.py`
4. `server/warships/signals.py`
5. `server/warships/management/commands/materialize_landing_player_best_snapshots.py`

Key functions and helpers in `server/warships/landing.py`:

1. `_summarize_ranked_medal_history`
2. `_calculate_landing_ranked_sort_score`
3. `_finalize_best_player_payload`
4. `_build_best_ranked_landing_players`
5. `_build_best_overall_landing_players`
6. `_build_best_efficiency_landing_players`
7. `_build_best_wr_landing_players`
8. `_build_best_cb_landing_players`
9. `materialize_landing_player_best_snapshot`
10. `materialize_landing_player_best_snapshots`
11. `get_landing_players_payload_with_cache_metadata`

### Tests

Relevant test files:

1. `server/warships/tests/test_views.py`
2. `server/warships/tests/test_landing.py`

Ranked contract tests in `test_views.py` cover:

1. medal precedence,
2. Gold-tie WR ordering,
3. Silver ordering after Gold and WR ties.

Landing tests in `test_landing.py` now cover:

1. dirty player-cache rebuild behavior,
2. published-key namespace behavior,
3. snapshot-backed Best-player reads,
4. snapshot materialization order persistence.

### Frontend explanation

The user-facing Ranked explanation lives in `client/app/components/PlayerSearch.tsx` and should continue to describe the shipped medal-table contract rather than the earlier heuristic model.

## Validation Record

Validated during this work:

1. focused Ranked API regressions passed under SQLite during heuristic and medal-history iterations,
2. focused landing cache regressions passed during the invalidation and published-key fixes,
3. focused landing helper tests passed after snapshot materialization was added,
4. the final validation command that completed cleanly was:

`/home/august/code/archive/battlestats/.venv/bin/python manage.py test warships.tests.test_landing.LandingHelperTests --keepdb --noinput`

Important validation note:

1. a plain non-`--keepdb` run passed the test assertions but hit an existing Postgres teardown issue when dropping the open test database,
2. this is a test-environment teardown issue, not a failure of the snapshot logic itself.

## Production and Operational Findings

### Landing-player cache behavior

Warm landing-player cache behavior is now acceptable, but cold request-time Ranked rebuilds are not. The DB-backed snapshot architecture is intended to remove that cold-path cost from the request path.

### Backend deploy script remains suspect

The backend deploy script repeatedly copied a new release without reliably moving `/opt/battlestats-server/current` to the new release. Manual activation was required more than once.

This was the key operational follow-up for the next tranche.

### Release log permissions remain a hardening target

At least one backend release failed to start because the release log directory was not writable. That was repaired manually. The deploy path should eventually guarantee release-local log directory creation and ownership.

## Implementation Update: Deploy Hardening

The backend deploy path now hardens the two rollout failures seen during the Best-player cache and Ranked-order work.

Current deploy behavior:

1. each release wires `server/logs` to `${APP_ROOT}/shared/logs`,
2. the deploy ensures `${APP_ROOT}/shared/logs/django.log` exists and is owned by the app user before management commands and service restarts,
3. release activation now uses an atomic temporary symlink plus `mv -T` replacement for `${APP_ROOT}/current`,
4. the deploy verifies that `readlink -f ${APP_ROOT}/current` matches the intended release path,
5. after the new backend release is active, the deploy automatically runs `manage.py materialize_landing_player_best_snapshots` unless disabled by env var.

Optional deploy-time controls:

1. `AUTO_MATERIALIZE_LANDING_PLAYER_BEST_SNAPSHOTS=0`
2. `MATERIALIZE_LANDING_PLAYER_BEST_SNAPSHOT_REALMS=na,eu`
3. `MATERIALIZE_LANDING_PLAYER_BEST_SNAPSHOT_SORTS=ranked,wr`

### Current live correctness checkpoint

After the player dirty-key fix, the published-key namespace fix, manual backend release activation, and targeted cache invalidation, the live Ranked top five matched the intended medal-table order.

## Current Code Status

Implemented in the repo:

1. Ranked medal-order contract,
2. dirty player-cache rebuild behavior,
3. namespaced published player-cache keys,
4. `LandingPlayerBestSnapshot` model,
5. snapshot-backed Best-player read path,
6. daily snapshot materialization task,
7. daily Beat schedule,
8. manual materialization command,
9. focused snapshot tests,
10. spec update describing the new architecture.

Recent files changed in the current tranche:

1. `server/warships/models.py`
2. `server/warships/migrations/0039_landingplayerbestsnapshot.py`
3. `server/warships/landing.py`
4. `server/warships/tasks.py`
5. `server/warships/signals.py`
6. `server/warships/tests/test_landing.py`
7. `server/warships/management/commands/materialize_landing_player_best_snapshots.py`
8. `agents/work-items/landing-player-best-ranking-recalibration-spec.md`

## Commands and Operational Entry Points

### Focused validation

Use:

```bash
cd server
/home/august/code/archive/battlestats/.venv/bin/python manage.py test warships.tests.test_landing.LandingHelperTests --keepdb --noinput
```

### Manual snapshot materialization

Use:

```bash
cd server
/home/august/code/archive/battlestats/.venv/bin/python manage.py materialize_landing_player_best_snapshots
```

Optional scoping:

```bash
cd server
/home/august/code/archive/battlestats/.venv/bin/python manage.py materialize_landing_player_best_snapshots --realm na --sort ranked
```

### Existing deploy-related context

Recent deploy and verification work used:

1. `SKIP_CI_CHECK=1 ./server/deploy/deploy_to_droplet.sh battlestats.online`
2. `SKIP_CI_CHECK=1 ./client/deploy/deploy_to_droplet.sh battlestats.online`
3. `./scripts/run_release_gate.sh`

Note that deploy success in the script output did not always mean the backend `current` symlink had advanced.

## Open Follow-ups

The highest-value remaining follow-ups are:

1. verify that `warm_landing_page_content` and related warm paths are republishing from snapshots exactly as intended in production,
2. run a live post-deploy verification against the production Ranked endpoint after the snapshot-backed release ships,
3. decide whether `warm_landing_best_entity_caches` should proactively materialize snapshots before warming entity payloads.

## Current Recommended Next Action

If continuing this work, the next practical step is:

1. deploy the snapshot-backed backend,
2. run the migration,
3. materialize Best-player snapshots for all realms,
4. verify the public Ranked and other Best-player sub-sort endpoints are fast and correct.
