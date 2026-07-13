# Runbook: Battle-History Treemaps + Ship Population Avg-Damage Baseline

_Created: 2026-07-13_
_Context: Player-page Battle History gained three mini-treemaps above the sparkline (type / ships-by-damage / tier); the damage map's coloring required a new per-ship population baseline that must never be computed on the request thread._
_QA: 164/164 backend battle-history tests (sqlite), 4/4 new frontend component tests, live-verified locally on Flareside (warm path) and Domino_s_Pizza (cold path, previously a gunicorn worker-kill)._

## Purpose

Documents the 3.1.0 battle-history treemap feature: what the three maps encode, the new `ship_pop_avg_damage` payload field and `X-Ship-Pop-Pending` header, and the lazy-warm architecture that keeps the per-ship population aggregate off the request thread. Read this when touching `BattleHistoryTreemaps.tsx`, the battle-history payload shape, or the ship-population warm task.

## Decisions

- **Three maps, one area rule.** `BattleHistoryTreemaps.tsx` renders above the sparkline in `BattleHistoryCard`, fed the resolved `by_ship` rows (purely presentational — the card owns the fetch), so the maps always mirror the selected Window/Mode and the table below. AREA = volume everywhere:
  - **By type** — DD/CA/BB/CV/SS sized by battles, colored by the type's aggregate WR (wins ÷ battles across its ships, `wrColor`).
  - **Ships by damage** — one tile per ship sized by **total window damage** (additive, so areas sum honestly; avg damage was rejected as a size metric — a 1-battle lucky game would dominate), colored on a **diverging scale** by player avg damage ÷ ship population average (red ≤0.6 · neutral gray 1.0 · green ≥1.5, Lab-interpolated, clamped). Sub-label is avg damage at 3 significant digits (`d3.format('.3~s')`), not WR. No baseline → neutral `#6f7683`. Clicking a ship tile toggles the same ShipStats panel as a table-row click.
  - **By tier** — tiles per tier sized by battles, colored by tier aggregate WR.
- **Baseline convention.** `ship_pop_avg_damage` is the realm-wide average damage over the trailing 30d of **random** battles (`SHIP_COMBAT_WINDOW_DAYS`, same population/window convention as the ShipStats panel), regardless of the view's mode — it is an expectation anchor, not a mode-scoped stat. Ships with under `SHIP_POP_AVG_MIN_BATTLES` (20) population battles carry `null` (a 0 sentinel in cache marks "computed, below floor" so it is never re-queued).
- **Never computed inline.** The first implementation ran one bulk `GROUP BY ship_id` population query on the request thread; a popular-T10 ship set (Domino_s_Pizza, 38 ships) took ~15s for 5 ships and **SIGKILLed the gunicorn worker** — the card errored and the page read as "no activity" while the (independently served) Vermont badge still showed. The shipped architecture mirrors the ship-list WR-percentile lazy warm:
  - `data.py`: `compute_ship_pop_avg_damage` (one small aggregate per ship, task-side only), `get_cached_ship_pop_avg_damage` (read-only bulk probe), cache key `ship_pop_avgdmg:v1:<realm>:<ship>:<day>` (26h TTL backstop on a day-scoped key).
  - `tasks.py`: `warm_ship_pop_avg_damage_task` (default lane, skip-if-cached) + `queue_ship_pop_avg_damage_warm` (per-ship `cache.add` dispatch dedup, 15 min).
  - `views.py`: `_attach_ship_pop_avg_damage` attaches from cache only, **per-request after the payload cache** (the payload cache never stores baselines, so warmed values appear without waiting out `BATTLE_HISTORY_CACHE_TTL`), queues the warm for misses, and returns pending → the response sets `X-Ship-Pop-Pending: true`.
  - `BattleHistoryCard.tsx`: on the pending header, re-polls up to 10× at 3s (`SHIP_POP_PENDING_*`, degradation-scaled, cacheBust rides the existing ranked-pending mechanism) — tiles colorize progressively as baselines land; stragglers stay neutral until a later visit.

## Implementation

- Frontend: `client/app/components/BattleHistoryTreemaps.tsx` (new), `BattleHistoryCard.tsx` (mount above sparkline, exported `BattleHistoryByShip` type, `ship_pop_avg_damage` field, ship-pop pending poll).
- Backend: `server/warships/data.py` (baseline compute/probe helpers next to the ShipStats population helpers), `server/warships/tasks.py` (warm task + queue helper), `server/warships/views.py` (cache-only attach + pending header in `battle_history`).
- Tests: `server/warships/tests/test_incremental_battles.py::BattleHistoryEndpointTests::test_by_ship_ship_pop_avg_damage_warm_then_hydrate` (cold → nulls + pending header, never inline; warm task → values; floor → null; cross-realm rows excluded), `client/app/components/__tests__/BattleHistoryTreemaps.test.tsx` (panel aggregation, diverging vs neutral fills, avg-dmg sub-label + tooltip, ship-tile click contract).

## Validation

- Cold request that previously killed the worker now returns in ~0.5s with `X-Ship-Pop-Pending: true`; celery `default` worker consumed the warm and 12/16 of the month-window ships had baselines within ~2 minutes locally (popular ships are shared across players and per-day cached, so realm coverage snowballs with browsing).
- Full backend battle-history suite green (164), new FE suite green (4), ESLint clean.

## Follow-ups

- The 10×3s client poll covers only the first ~30s of a large cold set; a player opened once and never revisited keeps neutral tiles for the stragglers. Acceptable now (baselines are day-cached and shared); revisit if Umami shows treemap engagement concentrated on cold profiles.
- Prod first-day behavior: every realm starts cold, so expect a burst of warm tasks on the default lane after deploy; per-ship dedup caps the fan-out. If the default lane shows contention, move the task to the `background` queue.
