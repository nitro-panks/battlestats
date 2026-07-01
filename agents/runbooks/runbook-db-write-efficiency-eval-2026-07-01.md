# Runbook: Ingest DB-Write Efficiency Evaluation — Is Bulk/COPY Warranted? (2026-07-01)

_Created: 2026-07-01_
_Context: Asked whether the observation floor + crawlers write player data efficiently, and whether collecting it and ingesting via a bulk/COPY operation would beat the current row-at-a-time writes._
_QA: advisor-reviewed; write paths traced directly in `incremental_battles.py`, `snapshot_active_players.py`, `enrich_player_data.py`, `clan_crawl.py`; DB-load claim cross-checked against `runbook-floor-throughput-tuning-2026-06-13.md` (Phase 0a DO-Prometheus evidence)._

## Purpose

Settle — with the code evidence — whether ingest writes should move to a bulk/COPY model.
Read this when someone proposes "batch the writes" or "COPY-load player data" for the
floor, snapshot engine, enrichment, or clan crawler. The short answer is **no**, and the
reasons are workload-shape reasons that will not change without a WG-API or schema change.

## Finding 1 — the writes are genuinely row-at-a-time

| Engine | Per-player write pattern | Round-trips | Transaction | Existing bulk use |
|---|---|---|---|---|
| Observation floor (`record_observation_from_payloads`, `incremental_battles.py:669`) | 1 `BattleObservation.create()`; per-event `BattleEvent.create()`; per-event PDSS `get_or_create`+F()`update`; 2 `Player` updates | ~4+3M (M=events) | per-player `atomic()` (`:762`) | none in hot path |
| Snapshot engine (`snapshot_active_players`) | `save_player(core_only=True)` + `update_snapshot_data(refresh_player=False)` + explorer upsert | ~8 | none | per-player `bulk_update` of 28-day intervals |
| Enrichment (`enrich_player_data.enrich_players`) | write-once `save(update_fields=...)` for battles/tiers/type/ranked + snapshot + explorer | ~10 | none | same per-player `bulk_update` |
| Clan crawler (`clan_crawl.run_clan_crawl`) | `save_clan` `update_or_create`; `save_player` `.save()`; departures bulk `UPDATE`; achievements `bulk_create` | ~4P+3C/pass (`core_only=True`) | `get_or_create_canonical_player`, `update_achievements_data` only | departures `.update()`, `PlayerAchievementStat.bulk_create` |

Per-player cache invalidation on the floor deletes ~160 Redis keys via `delete_many` in an
`on_commit` hook. No chunk-level batching across the 100-id WG chunk in any engine.

## Finding 2 — but writes are not the bottleneck, on two axes

**Throughput is WG-bound, not DB-bound.** WG `ships/stats/` is single-account-only
(rejects n≥2 → `INVALID_ACCOUNT_ID`); a global Redis token-bucket caps WG at ~9 req/s and
the floor deliberately draws ~1.5–2.4 req/s; the clan crawl is a single-slot, exclusive,
`c=1`, multi-day worker throttled by design. Data arrives as a trickle, one player every
fraction of a second. Faster writes cannot speed a pipeline whose clock is the WG fetch.

**DB load is warmer-dominated, not ingest-dominated.**
`runbook-floor-throughput-tuning-2026-06-13.md:56,:123` — confirmed via the DO Prometheus
managed-PG endpoint under Phase 0a instrumentation — states the binding constraint is the
shared 2-vCPU PG dominated by the analytical warmers + large-row `warships_player`
updates, **"not WG and not the floor's own writes."** At ~800–1000 queries per 100-player
chunk spread across seconds, ingest is low QPS against PG.

Warmer-set note (current 2026-07-01, post the 2026-06-22 landing-board decommission): the
dedicated best-clans/best-players *board* warmers (`landing-page-warmer-{realm}`,
`landing-best-player-snapshot-materializer-{realm}`) are **retired** — purged from Beat via
`_RETIRED_SCHEDULE_NAMES` (`signals.py:145-170`). The analytical warmers still running are
**`player-distribution-warmer` + `player-correlation-warmer` (daily; `signals.py:294`,
`:324`)** and the **`bulk-entity-cache-loader` (12h/realm; `signals.py:405-416`)**. The
latter still executes the heavy full-table `score_best_clans()` aggregation
(`data.py:5564`, called from `bulk_load_player_cache` `:5898` + `bulk_load_clan_cache`
`:5965`) — not to render a best-clans list (that UI is gone) but to choose which top
clans / best-clan members get detail payloads pre-warmed into Redis. So the June-13
runbook's "best-clans warmer" label is stale, but the `score_best_clans` scans it referred
to still run under the surviving loader.

## Finding 3 — the workload shape is the opposite of what COPY is for

- **Upsert-heavy, not append-heavy.** Player / Snapshot / PDSS / PlayerExplorerSummary are
  `update_or_create` / `get_or_create`. COPY is an append primitive; matching it needs
  COPY-to-temp + `INSERT … ON CONFLICT`, which only pays off at high rows-per-flush — a
  100-player chunk with ~1–5 events each does not produce that.
- **Per-player `atomic()` isolation is intentional.** The bulk floor explicitly does not
  wrap the chunk in one transaction: "one bad player must NOT roll back the chunk"
  (`incremental_battles.py:1267`). Cross-player bulk writes trade that away — a real
  regression when ingesting partially-bad WG payloads.

## Decision

Recommend **no bulk-ingest change** to these paths. It optimizes a documented-minor cost
and cannot improve WG-bound throughput.

If DB-load reduction is nonetheless pursued, the **only** safe ingest change is
*within-player* round-trip trimming (preserves per-player `atomic()` isolation, no COPY):

- `bulk_create` the per-player `BattleEvent` rows (M `INSERT`s → 1) —
  `incremental_battles.py:907-968`; `_apply_event_to_daily_summary` moves to operate on
  the returned created rows.
- Collapse the PDSS `get_or_create`+`update` (`incremental_battles.py:591-681`, 2M queries)
  into one `INSERT … ON CONFLICT (…) DO UPDATE SET battles = battles + EXCLUDED.battles, …`.

Net for a 3-event player: ~13 → ~6 queries, same `atomic()`, zero behavior change. Optional.

The real DB-load lever is the **analytical warmers** (cadence / `work_mem` / query shape) —
a separate investigation, not an ingest change.

## Validation (only if the micro-opt is pursued)

1. Baseline first: `connection.queries` count (or reuse Phase 0a floor instrumentation)
   around one floor cycle; record write-time vs cycle wall-time. Low single-digit % → do
   nothing.
2. Parity test in `server/warships/tests/`: identical `BattleEvent` / `PlayerDailyShipStats`
   rows before/after on a fixture player with multiple events across random + ranked, same
   day.
3. Backend release gate: `cd server && python -m pytest warships/tests/ --tb=short`.

## Related runbooks

- `runbook-floor-throughput-tuning-2026-06-13.md` — the binding-constraint evidence.
- `runbook-bulk-battle-observation-capture-2026-06-06.md` — the bulk `account/info` +
  change-gate capture path this evaluation covers.
- `runbook-landing-featured-boards-decommission-2026-06-22.md` — why the best-clans/
  best-players board warmers are retired (the warmer-set note above depends on this).
- `be-observation-floor-data-flow.md`, `be-player-enrichment-data-flow.md`,
  `be-queue-data-flow.md` (in `agents/diagrams/`) — the architecture diagrams.
