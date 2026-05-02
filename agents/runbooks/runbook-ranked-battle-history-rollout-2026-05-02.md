# Runbook: Ranked battle-history rollout (Phase 7 of the broader rollout)

_Created: 2026-05-02_
_Context: The randoms battle-history rollout (`runbook-battle-history-rollout-2026-04-28.md`) explicitly filed ranked-battles as Phase 7 / out-of-scope. The randoms pipeline is now stable in production (17K+ events captured across 2,400+ players as of 2026-05-01) and the orchestration is proven. This runbook scopes the parallel ranked rollout — same diff-and-aggregate shape against the WG `seasons/shipstats/` endpoint, with a `mode` discriminator on the existing capture/event/rollup tables._
_Status: phase-4-shipped (pending live verification) — 2026-05-02. Phases 1–3 deployed: ranked capture on for NA, BattleObservation.ranked_ships_stats_json populated, BattleEvent + PlayerDailyShipStats partitioned by `mode` + `season_id`, rollup writer + rebuild keyed on the partition columns, period tier guarded to mode=random. Phase 4 extends GET /api/player/<name>/battle-history with `?mode=random|ranked|combined` (default `random` for back-compat), filters PlayerDailyShipStats accordingly for the daily layer, suppresses lifetime-delta fields for ranked/combined since the baseline (Player.battles_json / Player.pvp_*) is randoms-only, and namespaces the cache key on mode. 6 new endpoint tests + 4 RankedRollupWriteTests cover the partitioning + payload contract. Lean release gate green (241/241). Phase 5 (frontend mode pill) shipping next._

## Purpose

Capture ranked-battle activity per-player per-ship per-season as a side-effect of the WG calls the site already makes (or with a single additional WG call piggybacked on the same chokepoint), surface it on the existing `BattleHistoryCard` via a mode toggle, and align the storage shape so the downstream weekly/monthly/yearly rollups absorb ranked transparently.

## Premise

Ranked is much lower-volume than randoms: most active players play 0 ranked/day, and the season cadence is discrete (currently ~10 weeks per season, with off-cycle weeks where nobody plays). Storage envelope and API-budget cost both scale 5–10× smaller than randoms. The orchestration is already built — `_fetch_ranked_ship_stats_for_player` (`server/warships/api/ships.py:191`) wraps the WG `seasons/shipstats/` endpoint, and `_apply_event_to_daily_summary` (`server/warships/incremental_battles.py`) is the rollup chokepoint.

The Phase 7 note in the original rollout runbook prescribed:
> "Same diff-and-aggregate pattern as randoms, but each event needs a `mode='ranked'` tag and `BattleObservation` needs a parallel `ranked_ships_stats_json` to hold the prior totals."

This runbook honors that design.

## Storage shape

Extend the existing models rather than parallel them — keeps the read path simple, the rollup chokepoint single, and the `BattleHistoryCard` payload shape consistent.

### `BattleObservation` (modify)

| Existing | New |
|---|---|
| `ships_stats_json` (random `ships/stats/` payload) | `ranked_ships_stats_json` (the per-season per-ship payload from `seasons/shipstats/`) |
| `pvp_*` aggregate cols | (unchanged — random aggregates only; ranked totals live inside the JSON) |

### `BattleEvent` (modify)

| Existing | New |
|---|---|
| (no mode discriminator — implicit randoms) | `mode = CharField(choices=[('random','Random'),('ranked','Ranked')], default='random', db_index=True)` |
| (no season discriminator — N/A for randoms) | `season_id = IntegerField(null=True, blank=True, db_index=True)` — populated for `mode='ranked'`, NULL for `mode='random'` |
| existing per-ship delta fields | reused as-is — same battles_delta / wins_delta / damage_delta / etc. semantics, just attributed to the ranked season |

### `PlayerDailyShipStats` (modify)

| Existing | New |
|---|---|
| `(player, date, ship_id)` unique key | extend to `(player, date, ship_id, mode)` — adds `mode` to the key |
| existing aggregate counters | reused as-is per `mode` partition |

The `Player{Weekly,Monthly,Yearly}ShipStats` rollup tables (Phase 6) need the same `mode` partitioning. Consider folding into the extending migration so a future operator doesn't have to re-walk all four tiers.

### Migration safety

Two migrations:
- **`0057_battle_observation_ranked_payload.py`** — `AddField` for `ranked_ships_stats_json` (nullable JSONB). Cloud-DB-safe.
- **`0058_battle_event_mode_season_partitioning.py`** — `AddField` for `BattleEvent.mode` (default `'random'`) and `season_id` (nullable). Then drop the old unique constraint on `BattleEvent.(from_observation, to_observation, ship_id)` and replace with `(from_observation, to_observation, ship_id, mode, season_id)` so a single observation pair can carry both a random and a ranked event for the same ship in the same season. The unique-constraint swap is the only non-trivial DDL — Postgres rebuilds the index without a rewrite for `AddField` of nullable cols, but a constraint swap on a 1M+ row table needs a maintenance window.
- **`0059_player_daily_ship_stats_mode.py`** — `AddField` for `PlayerDailyShipStats.mode` (default `'random'`); drop+recreate the unique constraint with `mode` included. Same caution as above.

Defer the rollup-tier (`PlayerWeeklyShipStats` etc.) `mode` partitioning to a follow-up migration once the daily layer is stable.

## Phases

### Phase 1 — schema + capture

Adds the columns/constraints above, the `ranked_ships_stats_json` capture path, and the WG fetch wiring. No user-visible effect; gated by env flag.

**Files:**
- `server/warships/models.py` — extend `BattleObservation`, `BattleEvent`, `PlayerDailyShipStats`.
- `server/warships/migrations/0057*`, `0058*`, `0059*` — generated.
- `server/warships/incremental_battles.py` — extend `record_observation_from_payloads(player, *, player_data, ship_data, ranked_ship_data=None, source=...)` to accept the new ranked payload and write it to `BattleObservation.ranked_ships_stats_json`. Extend `compute_battle_events` to walk ranked ships per season as a separate diff lane and emit events with `mode='ranked'` + the right `season_id`.
- `server/warships/data.py` (~line 2450 capture hook in `update_battle_data`) — when `BATTLE_HISTORY_RANKED_CAPTURE_ENABLED=1`, fetch ranked ship stats via the existing `_fetch_ranked_ship_stats_for_player` and pass through to `record_observation_from_payloads`. Single extra WG call per refresh, gated.
- Tests: `RankedDiffTests`, `RankedRecordObservationTests` mirroring the existing randoms tests in `test_incremental_battles.py`.

### Phase 2 — capture flag (`BATTLE_HISTORY_RANKED_CAPTURE_ENABLED=1`)

Flip on production after Phase 1 deploys cleanly. Watch:
- `BattleObservation.ranked_ships_stats_json IS NOT NULL` row count climbs.
- `BattleEvent.mode='ranked'` rows appear when known-active ranked players play.
- WG API budget — extra `seasons/shipstats/` call per refresh adds ~1 call per active-tier player per crawl tick. Roughly +2K calls/hour at NA steady-state. Stays well under the application_id rate budget.

### Phase 3 — rollup writer mode-partitioning

**QA refinements (2026-05-02) — three corrections to the original design:**

1. **No separate `BATTLE_HISTORY_RANKED_ROLLUP_ENABLED` flag.** The existing `BATTLE_HISTORY_ROLLUP_ENABLED=1` is already on in production and gates all rollup writes. Adding a second flag for the ranked path adds operational complexity without buying anything — once the schema + writer support `mode`, the existing flag covers both modes uniformly.

2. **Extend the existing `rebuild_daily_ship_stats_for_date` rather than a new `rebuild_ranked_daily_ship_stats` command.** The current rebuild is randoms-only by accident: it deletes all rows for the date and re-aggregates `BattleEvent` keyed on `(player_id, ship_id)`. After mode-partitioning lands, the rebuild must key on `(player_id, ship_id, mode, season_id)` so ranked rows for different seasons stay distinct and don't collapse into the random row.

3. **Period rollup writer must be guarded to stay randoms-only.** `_aggregate_into_period_table` (`server/warships/incremental_battles.py:939`) reads `PlayerDailyShipStats` without filtering by mode. Once daily has ranked rows, the weekly/monthly/yearly tiers would over-count by mixing modes. Add `.filter(mode=PlayerDailyShipStats.MODE_RANDOM)` to keep period rollups strictly random until the period-tier mode-partitioning lands as a separate phase. The weekly/monthly/yearly UI pills are currently hidden (`7dc7e86`) so this deferral has no user-visible impact.

**Files to edit:**

| File | Change |
|---|---|
| `server/warships/models.py` (`PlayerDailyShipStats` class) | Add `MODE_RANDOM`/`MODE_RANKED`/`MODE_CHOICES` constants; add `mode` (default `'random'`, indexed) + `season_id` (nullable, indexed) fields; replace the existing single `unique_player_daily_ship_stats` constraint with two partial constraints (one per mode) mirroring the BattleEvent shape from migration 0057. |
| `server/warships/migrations/0058_*.py` | Generated additive migration. |
| `server/warships/incremental_battles.py:367-368` | Remove the `event.mode != BattleEvent.MODE_RANDOM` early-return guard added in Phase 1 (no longer needed). |
| `server/warships/incremental_battles.py:_apply_event_to_daily_summary` | Include `mode=event.mode, season_id=event.season_id` in the `get_or_create` lookup kwargs and the `defaults` dict. |
| `server/warships/incremental_battles.py:rebuild_daily_ship_stats_for_date` | Change row key from `(player_id, ship_id)` to `(player_id, ship_id, mode, season_id)`; include those fields in the row dict and bulk_create. |
| `server/warships/incremental_battles.py:_aggregate_into_period_table` | Add `.filter(mode=PlayerDailyShipStats.MODE_RANDOM)` to the daily query so period rollups stay randoms-only. |
| `server/warships/tests/test_incremental_battles.py` | Add `RankedRollupWriteTests`: random + ranked events for the same (player, date, ship_id) write to separate rows; multi-season ranked writes to separate rows per season; rebuild for a date with both modes preserves both partitions; period rollup query ignores ranked rows. |

### Phase 4 — read API

Extend `GET /api/player/<name>/battle-history` to accept `?mode=random|ranked|combined`. **Default is `random`** (not `combined` as originally drafted) to keep the existing payload contract byte-for-byte unchanged for callers that don't pass `mode` — the frontend pre-Phase-5 expects randoms-only and the lifetime-delta math relies on `Player.battles_json` / `Player.pvp_*` being randoms-only baselines.

- `mode=random` (default): filters `PlayerDailyShipStats.mode='random'`. Lifetime + delta fields populated as before.
- `mode=ranked`: filters `PlayerDailyShipStats.mode='ranked'` (sums across active seasons). Lifetime/delta fields are null since the baseline isn't ranked-aware.
- `mode=combined`: no mode filter (sums random + ranked rows). Lifetime/delta fields also null for the same reason.
- Period tables (weekly/monthly/yearly) are randoms-only by Phase 3 design — `mode=ranked` against a non-daily period returns empty.

Implementation: `_battle_history_period_table` is unchanged; the payload builder in `views.py` filters the daily query by `mode` and short-circuits lifetime math when `mode != 'random'`. Cache key gains a `:mode` segment so the three views are isolated. Invalid `mode` values fall back silently to `random`. Echoed in the response payload as `"mode": "<resolved>"`.

### Phase 5 — frontend

`BattleHistoryCard.tsx` adds a small mode pill (`Random | Ranked | Combined`, defaulting to Combined). Re-fetches on toggle. Reuses existing card layout — only the data binding changes.

### Phase 6 — backfill (optional)

Mirror `establish_battle_history_baseline` to seed observations for active-ranked-7d players who have ranked battles registered in `Player.ranked_json` but no `BattleObservation.ranked_ships_stats_json` yet. New command: `establish_ranked_baseline --realm na --days 7`. Rate-budget caveat from the randoms baseline-fill applies (~1% 407 retries on the next regular crawl).

## Operational watchpoints

1. **Off-season weeks.** Ranked has dead weeks between seasons. During those, `seasons/shipstats/` returns sparse / empty payloads. The diff lane should produce zero events, not crash. Add a regression test for the empty-season case.
2. **Season transitions.** When a new season starts, every ranked-active player gets a new `season_id` row in their first observation. The diff against the prior observation (which was for the previous season) should NOT count those battles as the player ramping up — they're a new season's totals from zero. Cleanest handling: for any new `(ship_id, season_id)` pair appearing between two observations, treat it as a baseline (record an event with `battles_delta = current.battles, wins_delta = current.wins, ...` only if the prior observation was within the same season; otherwise NULL it as ambiguous since the player's season-1 totals are now hidden behind season-2's).
3. **Storage growth.** Per-player payload is smaller than randoms (most players have <10 ranked ships per season vs ~50+ random ships across all tiers), but accumulates over multiple seasons. The Day-14 prune on `BattleObservation` from the randoms rollout applies transparently — the `ranked_ships_stats_json` column gets pruned alongside `ships_stats_json`.
4. **Ranked-only players.** Some accounts play only ranked. The `BattleEvent.mode` discriminator means their daily rollup rows live entirely in the ranked partition. The "Recently Battled" landing surface (driven by `Player.last_random_battle_at`) does NOT pick them up — that column tracks random battles only. **Decision needed:** introduce `Player.last_ranked_battle_at` for parallel surfacing, or accept that the Active landing pill is randoms-only. Default: accept randoms-only for the Active pill; surface ranked-only players via the player-detail page only.

## Out of scope (deferred to later sub-phases)

- **`Player.last_ranked_battle_at` denormalization** + a sibling Active-Ranked landing pill. Ship only if ranked-only player segment becomes a stakeholder ask.
- **Per-season aggregate views** (e.g. "your last 5 seasons" leaderboard). Belongs to a future feature.
- **Mode-aware weekly/monthly/yearly rollup partitioning.** Defer until the daily layer has 30+ days of dual-mode data.
- **Co-op / scenario / operations battles.** Same out-of-scope-forever framing as the randoms rollout — no per-mode WG endpoint.

## References

- WG `seasons/shipstats/` wrapper: `server/warships/api/ships.py:191` (`_fetch_ranked_ship_stats_for_player`).
- Existing ranked usage in enrichment: `server/warships/management/commands/enrich_player_data.py:317`.
- Capture orchestrator: `server/warships/incremental_battles.py:322` (`record_observation_from_payloads`).
- Rollup chokepoint: `server/warships/incremental_battles.py:_apply_event_to_daily_summary`.
- Read API + Card: `server/warships/views.py:537-725`, `client/app/components/BattleHistoryCard.tsx`.
- Companion runbook (random-battles rollout): `agents/runbooks/runbook-battle-history-rollout-2026-04-28.md` (Phase 7 note at line 366).
- Companion runbook (post-rollout follow-ups): `agents/runbooks/runbook-post-rollout-followups-2026-05-01.md`.

## Doctrine pre-commit checklist

- **Documentation review:** this runbook is the documentation deliverable. Phases 1–5 each get a small CLAUDE.md "Background enrichment" section update for any new env vars (`BATTLE_HISTORY_RANKED_CAPTURE_ENABLED`, `BATTLE_HISTORY_RANKED_ROLLUP_ENABLED`, `BATTLE_HISTORY_RANKED_API_ENABLED`).
- **Doc-vs-code reconciliation:** N/A at runbook authoring; Phase 1 verifies code refs at edit time.
- **Test coverage:** each phase ships its tests with the code change. Phase 1 must include the off-season + season-transition cases flagged in Operational watchpoints.
- **Runbook archiving:** archive THIS runbook only after all phases ship and 14+ days of dual-mode data accumulate cleanly.
- **Contract safety:** Phase 4 extends `/api/player/<name>/battle-history` with a `?mode=` query param; default behavior stays backwards-compatible.
- **Runbook reconciliation:** update **Status** between phases — `planned` → `phase-1-shipped` → `capture-on` → `rollup-on` → `api-on` → `frontend-shipped` → `backfilled` → `resolved`.

## Next step

User reviews this runbook. On approval, Phase 1 (schema + capture wiring + tests) is the natural first slice — it's a non-user-visible change gated by the capture env flag, so it can ship to production without surfacing anything until the flag flips.
