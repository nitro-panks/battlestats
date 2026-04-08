# Runbook: Deprecate ABS And Clan-CB Best Sub-Sorts

**Status:** Implemented in code and tests — deploy-time artifact cleanup still required
**Owner:** august
**Surfaces:** Landing page → Best Players strip, Best Clans strip
**Intent:** Remove ABS from players and clans, remove the clan-only CB best sub-sort, and fully retire the related backend generation, warming, and stored artifacts.

## Goal

Deprecate the `abs` best sub-sort completely and remove the clan-only `cb` best sub-sort in the same tranche.

That means all of the following must be true after implementation:

- ABS no longer appears anywhere in the landing UI.
- clan CB no longer appears anywhere in the landing UI.
- the backend no longer accepts or builds `sort=abs` payloads.
- the backend no longer accepts or builds clan `sort=cb` payloads.
- no scheduled warmer, lazy builder, or snapshot materializer continues collecting ABS data.
- no clan-CB best-sort warmer or best-sort cache path continues collecting clan `sort=cb` data.
- no ABS-specific or clan-CB-specific cached payloads or snapshot rows remain at rest.
- active docs and tests no longer describe ABS or clan CB best sorting as supported.

This tranche is scoped to landing best-sort behavior only.

- player `cb` remains supported
- clan-battle data products, badges, and clan-battle surfaces outside landing best-sort remain supported

This runbook now records the implemented code/doc tranche and the remaining deploy-time cleanup steps.

## Implementation Update

Applied in the current workspace:

- removed player `abs` from the landing UI, backend sort tuples, snapshot materialization, and landing warmers
- removed clan `abs` and clan `cb` from the landing UI, backend sort tuples, and best-clan ranking support
- removed ABS-specific player/clan chart branches
- replaced ABS and clan-CB-positive tests with rejection coverage and reduced warm-surface assertions
- reconciled the active player/clan best-sort specs and snapshot-materialization docs to the reduced supported sort lists

Still required outside this workspace:

- delete production `LandingPlayerBestSnapshot(sort='abs')` rows
- invalidate or delete the retired ABS and clan-CB cache families on the target environment
- run the post-deploy forced landing warm after cleanup

## Current Footprint

### Frontend

- `client/app/components/PlayerSearch.tsx`
  - player Best sub-sort row includes `ABS`
  - clan Best sub-sort row includes `ABS`
  - clan chart props and tooltip copy still assume a clan sort set wider than the intentionally reduced UI
  - player and clan formula tooltips include ABS copy
- `client/app/components/LandingPlayerSVG.tsx`
  - `PlayerBestSort` includes `'abs'`
  - ABS uses a dedicated log-scale x-axis branch
- `client/app/components/LandingClanSVG.tsx`
  - clan chart rendering handles `sort === 'abs'`
  - prop typing still allows `sort === 'cb'`
  - ABS uses a dedicated log-scale x-axis branch
- `client/app/components/__tests__/PlayerSearch.test.tsx`
  - any ABS button, sort-order, or tooltip assertions must be removed or rewritten
  - clan tests still model backend-owned `cb` as an active clan best sub-sort

### Backend

- `server/warships/landing.py`
  - `LANDING_PLAYER_BEST_SORTS` includes `'abs'`
  - `LANDING_CLAN_BEST_SORTS` includes `'abs'` and `'cb'`
  - `LANDING_PLAYER_BEST_ABS_MIN_PVP_BATTLES = 100`
  - player normalizer accepts `abs`
  - clan normalizer accepts `abs` and `cb`
  - `_build_best_abs_landing_players(...)` exists
  - Best-player snapshot materialization dispatch supports `sort='abs'`
  - landing warmers currently build and count `players_best_abs`, `clans_best_abs`, and `clans_best_cb`
  - cache keys currently permit persisted ABS families such as:
    - `landing:players:v13:n{namespace}:best:abs:{limit}`
    - `landing:players:v13:n{namespace}:published:best:abs:{limit}`
    - `landing:clans:best:v2:abs`
    - `landing:clans:best:v2:abs:published`
    - `landing:clans:best:v2:cb`
    - `landing:clans:best:v2:cb:published`
- `server/warships/data.py`
  - `BEST_CLAN_SORTS` includes `'abs'` and `'cb'`
  - `BEST_CLAN_ABS_MIN_MEMBERS` and `BEST_CLAN_ABS_MIN_TOTAL_BATTLES` exist
  - `score_best_clans(..., sort='abs')` has a dedicated ABS branch
  - `score_best_clans(..., sort='cb')` has a dedicated clan-CB ranking branch
- `server/warships/tests/test_landing.py`
  - ABS normalizer acceptance tests exist for players and clans
  - player ABS snapshot behavior is explicitly tested
  - clan ABS ranking behavior is explicitly tested
  - clan CB ranking behavior is explicitly tested
  - landing warmup expectations include `players_best_abs`, `clans_best_abs`, and `clans_best_cb`

### Stored Artifacts At Rest

- Postgres snapshot rows in `LandingPlayerBestSnapshot` can exist with `sort = 'abs'`.
- Redis can contain ABS player cache variants, ABS player published fallbacks, ABS clan best caches, ABS clan published fallbacks, clan-CB best caches, and clan-CB published fallbacks.
- production warm-landing operations currently rebuild ABS and clan-CB best clan entries after deploy unless code is changed first.

### Documentation

- `agents/runbooks/archive/runbook-abs-best-sort-2026-04-07.md`
- `agents/runbooks/spec-best-player-subfilters.md`
- `agents/runbooks/spec-best-clan-subfilters.md`
- `agents/runbooks/runbook-landing-best-player-subsort-materialization-2026-04-05.md`
- `agents/runbooks/README.md`
- `agents/doc_registry.json`

## Required Implementation Changes

### 1. Remove ABS And Clan-CB From The Interface

Frontend implementation tranche:

- remove `'abs'` from player and clan Best sub-sort type unions
- remove `'cb'` from the clan Best sub-sort type union
- remove the ABS button from the Best player sort bar
- remove the ABS button from the Best clan sort bar
- keep the clan UI intentionally reduced rather than reintroducing clan CB
- remove ABS-specific formula constants and tooltip sections
- remove clan-CB-specific clan formula constants and tooltip sections
- update tooltip copy so the remaining sorts no longer reference ABS or clan CB as active choices
- remove ABS-only chart logic from `LandingPlayerSVG.tsx` and `LandingClanSVG.tsx`
  - after deprecation, the temporary ABS log-scale path should disappear with the sort
- remove clan `cb` prop typing/branching from `LandingClanSVG.tsx`

Expected visible result:

- Best Players returns to `Overall | Ranked | Efficiency | WR | CB`
- Best Clans returns to `Overall | WR`
- no landing tooltip mentions ABS or clan CB as active sub-sorts

### 2. Remove ABS From Backend Acceptance And Generation

Backend implementation tranche:

- remove `'abs'` from `LANDING_PLAYER_BEST_SORTS`
- remove `'abs'` and `'cb'` from `LANDING_CLAN_BEST_SORTS`
- remove `LANDING_PLAYER_BEST_ABS_MIN_PVP_BATTLES`
- remove `_build_best_abs_landing_players(...)`
- remove the ABS branch from Best-player snapshot materialization
- remove `'abs'` and `'cb'` from `BEST_CLAN_SORTS`
- remove `BEST_CLAN_ABS_MIN_MEMBERS`
- remove `BEST_CLAN_ABS_MIN_TOTAL_BATTLES`
- remove the ABS branch from `score_best_clans(...)`
- remove the clan-CB branch from `score_best_clans(...)`
- tighten normalizer error strings back to the reduced sort lists

Expected API result:

- `/api/landing/players/?mode=best&sort=abs...` should no longer be a supported contract
- `/api/landing/clans/?mode=best&sort=abs...` should no longer be a supported contract
- `/api/landing/clans/?mode=best&sort=cb...` should no longer be a supported contract

Because the product intent is full deprecation rather than graceful hidden support, backend acceptance should be removed rather than silently preserved.

Player `sort=cb` remains supported and is explicitly out of scope for this removal.

### 3. Stop ABS And Clan-CB Collection And Warming

This is the critical "no collection occurring" part.

Implementation must ensure:

- `warm_landing_page_content(...)` no longer requests `players_best_abs`
- `warm_landing_page_content(...)` no longer requests `clans_best_abs`
- `warm_landing_page_content(...)` no longer requests `clans_best_cb`
- any post-deploy or scheduled warm path stops enumerating ABS and clan-CB best sorts entirely
- no management-command guidance or command surfaces continue materializing `sort=abs` snapshots

Implementation must also cover the concrete collection hooks that currently derive from the active sort lists:

- the periodic player snapshot materializer registration in `server/warships/signals.py`
- the snapshot Celery task in `server/warships/tasks.py`
- the standalone snapshot materialization command in `server/warships/management/commands/materialize_landing_player_best_snapshots.py`
- the post-deploy verify/snapshots command in `server/warships/management/commands/run_post_deploy_operations.py`
- the deploy-time auto-materialization path in `server/deploy/deploy_to_droplet.sh`

For the standalone snapshot materialization command, implementation must remove ABS from the accepted `--sort` choices and from any help text that still implies ABS is a valid best-player snapshot mode.

Observable success criteria:

- warm-landing result maps no longer include `players_best_abs`
- warm-landing result maps no longer include `clans_best_abs`
- warm-landing result maps no longer include `clans_best_cb`
- runtime logs no longer emit `sort=abs` or clan `sort=cb` landing warm/build lines after caches age out

Note: not every best-entity cache helper needs removal work here. Some broader entity warmers already enumerate only shipped non-ABS player sorts and do not currently collect ABS.

### 4. Remove ABS And Clan-CB Artifacts At Rest

This is the critical "nothing at rest" part.

#### Postgres cleanup

Delete all ABS player snapshot rows:

```sql
DELETE FROM warships_landingplayerbestsnapshot WHERE sort = 'abs';
```

There is no clan snapshot table for Best clans, so clan-CB retirement is purely a cache-artifact cleanup, not a relational-row cleanup.

#### Redis/cache cleanup

Clear the ABS landing cache families after the code deploy so old payloads are not served from previously warmed keys.

Families to remove or invalidate include:

- `landing:players:v13:n*:*best:abs:*`
- `landing:players:v13:n*:*published:best:abs:*`
- `landing:clans:best:v2:abs`
- `landing:clans:best:v2:abs:meta`
- `landing:clans:best:v2:abs:published`
- `landing:clans:best:v2:abs:published:meta`
- `landing:clans:best:v2:cb`
- `landing:clans:best:v2:cb:meta`
- `landing:clans:best:v2:cb:published`
- `landing:clans:best:v2:cb:published:meta`

Pragmatic implementation options:

- bump the landing player namespace and invalidate clan best caches after the code change
- or explicitly delete the ABS and clan-CB key families on the droplet

After cleanup, run a normal forced landing warm so only supported sorts repopulate.

### 5. Remove ABS And Clan-CB Test Coverage And Replace It With Negative Coverage

Current ABS-positive tests should be removed or rewritten.

Required replacement coverage:

- player normalizer rejects `abs`
- clan normalizer rejects `abs`
- clan normalizer rejects `cb`
- landing warmup expectations no longer include ABS or clan-CB surfaces
- frontend landing sort UI tests no longer expect ABS buttons
- frontend landing sort UI tests no longer expect clan-CB best-sort controls
- any API-facing tests that enumerate supported sort values are updated to the reduced lists

Because clan-CB is already intentionally absent from the live clan bar, the test tranche must reconcile current UI expectations with the reduced backend contract rather than accidentally reintroducing clan-CB into the interface.

### 6. Reconcile Documentation

Durable docs must match the new product state in the same tranche.

Required doc changes:

- keep the retired ABS implementation notes only in `agents/runbooks/archive/runbook-abs-best-sort-2026-04-07.md`
- update `agents/runbooks/spec-best-player-subfilters.md` to remove ABS from the supported order and contract
- update `agents/runbooks/spec-best-clan-subfilters.md` to remove ABS and clan CB from the supported order and contract
- update `agents/runbooks/runbook-landing-best-player-subsort-materialization-2026-04-05.md` to remove ABS from the active player snapshot description
- update `agents/runbooks/README.md` and `agents/doc_registry.json` so the deprecation doc becomes the active pointer during rollout

## Suggested Implementation Order

1. Remove ABS support and clan-CB support from backend clan/player sort tuples, builders, and warmers.
2. Remove ABS from the player controls and ABS-only chart behavior.
3. Remove ABS and clan-CB from the clan controls and clan-only sort behavior.
4. Update tests to the new supported-sort lists.
5. Deploy client first, or deploy client and backend in the same release window.
6. Deploy backend immediately after the client update.
7. Delete `LandingPlayerBestSnapshot(sort='abs')` rows.
8. Invalidate or delete ABS and clan-CB cache families.
9. Force-warm landing caches.
10. Archive the old ABS implementation runbook and reconcile the active specs.

This order avoids repopulating deleted ABS artifacts from still-running code while also avoiding a fresh-user window where the UI still offers ABS but the backend has already rejected it.

Long-lived browser tabs can still hold stale controls briefly after the backend cutover. That is acceptable for a deprecation tranche, but the runbook should avoid creating that mismatch for newly loaded pages.

## Verification Checklist After Implementation

### Interface

- landing page shows no ABS button for players
- landing page shows no ABS button for clans
- landing page shows no clan-CB best-sort button for clans
- no tooltip copy references ABS or clan-CB best sorting

### API

- supported player Best sorts are `overall`, `ranked`, `efficiency`, `wr`, `cb`
- supported clan Best sorts are `overall`, `wr`
- requests using `sort=abs` are rejected rather than returning live data
- clan requests using `sort=cb` are rejected rather than returning live data

### No Collection Occurring

- forced landing warm output contains no `players_best_abs`
- forced landing warm output contains no `clans_best_abs`
- forced landing warm output contains no `clans_best_cb`
- no new `LandingPlayerBestSnapshot(sort='abs')` rows appear after warm and traffic

### Nothing At Rest

- `SELECT COUNT(*) FROM warships_landingplayerbestsnapshot WHERE sort = 'abs';` returns `0`
- Redis no longer serves ABS or clan-CB landing payloads
- live endpoint probes cannot retrieve ABS or clan-CB payloads from stale published caches

## Deployment Notes

Use a client-first deploy, or ship client and backend in the same release window.

If the backend is deployed first, newly loaded pages can still offer ABS while the backend rejects it.
If the client is deployed first, the UI can stop advertising the retired sorts immediately while the backend remains temporarily over-capable.

The safe path is:

1. client deploy, or same-window client+backend release
2. backend deploy
3. delete `LandingPlayerBestSnapshot(sort='abs')` rows
4. invalidate or delete ABS and clan-CB cache families
5. forced landing warm

## Stop Condition For This Document

This runbook should move to `archive/` when all of the following are true:

- ABS is absent from the interface
- clan-CB is absent from the interface
- ABS and clan-CB are absent from supported backend sorts
- ABS and clan-CB warm/build paths are gone
- ABS snapshots and ABS/clan-CB caches are removed at rest
- active specs and indexes no longer describe ABS or clan-CB as supported
