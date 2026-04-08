# Runbook: Deprecate ABS And Clan-CB Best Sub-Sorts

**Status:** Implemented in code and tests; production cleanup completed
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

This runbook now records the implemented code/doc tranche and the completed production cleanup.

## Implementation Update

Applied in the codebase:

- removed player `abs` from the landing UI, backend sort tuples, snapshot materialization, and landing warmers
- removed clan `abs` and clan `cb` from the landing UI, backend sort tuples, and best-clan ranking support
- removed ABS-specific player/clan chart branches
- replaced ABS and clan-CB-positive tests with rejection coverage and reduced warm-surface assertions
- reconciled the active player/clan best-sort specs and snapshot-materialization docs to the reduced supported sort lists
- corrected the clan landing API error contract so invalid clan best-sort requests now advertise only `overall, wr`

Production cleanup completed on `battlestats.online`:

- deleted production `LandingPlayerBestSnapshot(sort='abs')` rows
- deleted retired Redis key families for player ABS plus clan ABS and clan CB landing caches
- invalidated current landing player and clan caches after cleanup
- ran the forced landing warm for supported surfaces
- verified that production now rejects player `sort=abs`, clan `sort=abs`, and clan `sort=cb`

## Historical Implementation Summary

### Interface Changes Completed

- `client/app/components/PlayerSearch.tsx` now renders player Best sorts as `Overall | Ranked | Efficiency | WR | CB` and clan Best sorts as `Overall | WR`
- `client/app/components/LandingPlayerSVG.tsx` no longer supports `sort='abs'` or the ABS-only log-scale branch
- `client/app/components/LandingClanSVG.tsx` no longer supports clan `sort='abs'` or clan `sort='cb'`
- frontend tooltip copy no longer documents ABS or clan-CB as active landing choices

### Backend Changes Completed

- `server/warships/landing.py` now accepts player Best sorts `overall`, `ranked`, `efficiency`, `wr`, `cb`
- `server/warships/landing.py` now accepts clan Best sorts `overall`, `wr`
- ABS player snapshot generation was removed
- clan ABS and clan-CB best ranking branches were removed
- landing warmers no longer enumerate `players_best_abs`, `clans_best_abs`, or `clans_best_cb`
- `server/warships/management/commands/materialize_landing_player_best_snapshots.py` now inherits the reduced player sort choices from `LANDING_PLAYER_BEST_SORTS`
- `server/warships/management/commands/run_post_deploy_operations.py` now rebuilds and verifies only the active player Best sorts via `LANDING_PLAYER_BEST_SORTS`

### Test And Doc Reconciliation Completed

- landing tests now reject player `abs`, clan `abs`, and clan `cb`
- frontend landing tests no longer expect ABS or clan-CB controls
- the active player/clan subfilter specs and landing snapshot materialization runbook now reflect the reduced supported sort lists
- the retired ABS implementation runbook remains archived at `agents/runbooks/archive/runbook-abs-best-sort-2026-04-07.md`

## Production Cleanup Evidence

Verified on `battlestats.online`:

- `LandingPlayerBestSnapshot(sort='abs')` rows were deleted and post-cleanup verification returned `abs_snapshot_rows = 0`
- Redis scans over the retired player ABS and clan ABS/clan-CB key families returned `0` remaining matches after cleanup
- `run_post_deploy_operations invalidate --players --clans` completed after the retired key purge
- `run_post_deploy_operations warm-landing --force-refresh` completed and rebuilt only the supported surfaces
- direct backend probes now reject player `sort=abs`, clan `sort=abs`, and clan `sort=cb`

## Current Contract

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
- clan requests using `sort=abs` are rejected rather than returning live data
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

The original rollout guidance was client-first or same-window client+backend release to avoid a fresh-user window where the UI still offered retired sorts after backend removal.

That deploy and cleanup sequence has already been executed for the deprecation tranche. The only remaining follow-up at the time of this update is a normal backend redeploy if the tightened clan landing error-copy change in `server/warships/views.py` also needs to be visible in production.

## Stop Condition For This Document

This runbook should move to `archive/` when all of the following are true:

- ABS is absent from the interface
- clan-CB is absent from the interface
- ABS and clan-CB are absent from supported backend sorts
- ABS and clan-CB warm/build paths are gone
- ABS snapshots and ABS/clan-CB caches are removed at rest
- active specs and indexes no longer describe ABS or clan-CB as supported
- any small follow-up contract-copy fixes have been redeployed to production
