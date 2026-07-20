# Spec: Snapshot delta-gated writes (DB audit levers F3.2 + F4)

_Created: 2026-07-20. Implements follow-up #4 of `agents/runbooks/runbook-db-table-audit-2026-07-19.md`._

## Problem

The daily snapshot engine writes a `Snapshot` row for every active player every UTC day; measured live (2026-07-18/19), ~220K rows/day of which ~68% have `interval_battles = 0` — a permanent record of "this player played nothing today". The write path also churns: `update_snapshot_data` bulk-updates all ~29 window rows regardless of change (56M lifetime updates) and runs a per-player purge DELETE (2.3M lifetime calls, 490 min cumulative). Readers do not need the zero rows: `update_activity_data` already synthesizes `battles: 0` for missing dates.

## Decision

Gate the write in `update_snapshot_data` — the single choke point shared by all five callers (bulk engine, view-refresh task, enrichment, hot-players capture, hydrate path).

**Gate rule**: skip the entire Snapshot write machinery (purge DELETE, today-row upsert, interval recompute) iff:

1. `SNAPSHOT_DELTA_GATE_ENABLED` is `1` (code default `1`, set explicitly in the deploy script — code default and prod value aligned, avoiding the F5/F11 env-gate-trap shape), and
2. no row exists for today (a today-row means the write path already ran — always maintain it), and
3. a prior row exists (first-ever snapshot always writes, so tracking starts), and
4. cumulative `(battles, wins)` equal the latest prior row's values.

`update_activity_data` still runs on the skip path so `activity_json` keeps sliding daily — but **throttled to once per UTC day** for unchanged players (repeat passes produce the identical payload; rebuilding it each pass would re-create the Player/PES churn the gate exists to remove). `PlayerExplorerSummary` refresh semantics are otherwise unchanged.

**Engine convergence (checked set)**: the bulk engine runs every 30 min per realm and converged via "has today's row" idempotency. Under gating, non-movers never gain a today-row, so that exclusion alone would re-select the same recency-ordered top-3000 every run — re-polling it all day and never reaching deeper movers. The engine now also keeps a per-day cache-backed checked set (`snapshot_checked:{realm}:{date}`, 26h TTL). **Since 2026-07-20 (audit F9.1) the checked set is the SOLE idempotency mechanism** — both written and unchanged players are marked checked, and the `.exclude(snapshot__date=today)` NOT EXISTS anti-join was removed from the candidate query (it was 31-55 s/call on prod; the candidate scan is now a pure walk of the partial index `player_realm_lbd_active_idx`). Errored players are deliberately not marked (retried next run); hidden players fall out via `is_hidden`. On cache loss the engine degrades to one redundant bulk re-poll pass (`update_snapshot_data` handles an existing today-row idempotently).

**Write-path churn reduction** (applies gate on or off):

- `bulk_update` only rows whose interval values actually changed.
- The purge DELETE (legacy statsbydate zero-row sweep) runs only on the write path.

**Carry-forward seed (latent-bug fix, required for sparse correctness)**: the 28-day interval recompute seeded `previous = None` at the window edge, so the first stored row in the window always got `interval_battles = 0`. Dense zero-filler rows masked this; with sparse rows a returning mover would record interval 0 on the day they actually played. The recompute now seeds `previous_battles/wins` from the player's latest row *before* the window start.

**F4 fold-in**: drop `Snapshot.battle_type` (empty string in 100% of sampled rows) and `Snapshot.last_fetch` (written but read only by the account-merge scalar helper; no product reader). One migration; column drops are catalog-only.

**Bounded reader**: `update_activity_data` filtered the player's *entire* snapshot history to use 29 days of it; now bounded to the window (`date__gte = today − 28d`).

## KPI / benchmark reconciliation (`benchmark_observation_floor`)

- Its comment "interval_battles is NOT used: it's only populated on the per-player view path" was verified false (2026-07-20: 0 NULL intervals across 3 days of bulk rows). Movers are now classified by the latest-date row's `interval_battles > 0` — with the carry-forward seed this equals "battles rose since the player's previous stored row" under both dense and sparse writing, so `snapshot_movers` keeps its meaning (~72K/day baseline) across the transition.
- `snapshot_coverage_frac` ("active players with a row today / active-7d") is unknowable under gating (unchanged and unchecked are indistinguishable); it reports `null` when the gate is on.
- Gap-decomposition buckets: a today-row with `interval > 0` → `pvp_mover`, with `interval = 0` → `non_pvp_active`, no today-row → `no_snapshot_pair`. Under gating `no_snapshot_pair` absorbs the unchanged majority; documented in the command.

## Operational notes

- The bulk engine's `.exclude(snapshot__date=today)` idempotency weakens for non-movers: a same-day re-run re-polls them via bulk `account/info` (~1 WG call/100 players) and re-skips. The engine runs once daily per realm; re-run cost is a few hundred extra WG calls, accepted.
- The engine reports `Written` / `Unchanged-skipped` counters (from `update_snapshot_data`'s return value).
- Rollback: `SNAPSHOT_DELTA_GATE_ENABLED=0` restores dense writes immediately. No backfill needed in either direction — readers synthesize zeros for missing dates.
- Expected effect: ~150K fewer rows/day (~68% of the stream), no per-player 29-row same-value bulk_update, no per-player purge DELETE on the skip path; with the 90d downsampler armed (2026-07-20) the table plateaus well under the previous ~3.7 GB projection.
