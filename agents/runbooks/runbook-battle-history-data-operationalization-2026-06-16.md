# Runbook: Battle-History Data — What We Capture vs. What We Surface (Operationalization)

_Created: 2026-06-16_
_Context: A runway/pruning investigation into the battle-history tables turned up a more interesting finding — we already **capture, delta, and roll up** a rich set of per-ship combat fields (gunnery / torpedo / secondary accuracy, spotting, objective play), but the feature layer reads only 7 of them. This runbook records the measured DB composition, the full data-flow + field inventory, the prune-vs-operationalize analysis, and the chosen direction._
_Status: **DECISION — keep the 30-day window; operationalize the untapped fields with new user-facing features.** No pruning/year-removal work is being done. The runway concern is documented here for completeness but is **not** the chosen path._

## TL;DR for a future session

- The battle-history pipeline is **already fully widened**. Nothing is discarded between the WG API and `PlayerDailyShipStats`.
- We surface only `battles, wins, losses, frags, damage, xp, planes_killed`. Everything else — **hit ratios, torpedo accuracy, secondary, spotting, capture play, survival** — is captured and rolled up to per-day-per-ship aggregates and used by **zero** features (client grep for accuracy/torpedo/spotting = 0 hits).
- **`BattleObservation` holds no spare signal to mine** — its `ships_stats_json` is a pure diff-buffer, already harvested into `BattleEvent` + `PDSS`. So "operationalize instead of prune" is a *false trade-off for `BattleObservation`*; the data you'd build on lives in `PDSS` (the cheap 1.5 GB aggregate) and in the *latest* observation's career totals.
- **Chosen direction:** keep current windows (≤30d), build features on the untapped fields. The highest-coverage win is a **career combat profile** derived from a single latest observation per player (full coverage, no time-series, no retention dependency).

## Measured DB composition (prod, 2026-06-16)

Reproduce with the queries in "Verification recipe" below. Database total: **22 GB**.

| Table | Total size | Composition | Rows | Aged-out |
|---|---|---|---|---|
| `warships_battleobservation` | **7.3 GB** (33% of DB) | **TOAST 6.8 GB (93%)** = live `ships_stats_json`; heap ~314 MB; indexes 221 MB | 1,900,758 | 582,594 rows >30d, but JSON already **nulled** (compaction KEEP=1) → ~zero reclaim |
| `warships_playerdailyshipstats` | 1.48 GB | derived from `BattleEvent` | 3,335,793 | 764,715 >30d (23%) ≈ ~340 MB |
| `warships_battleevent` | 1.26 GB | the only non-derivable source | 3,390,144 | 480,288 >35d (14%) ≈ ~180 MB |
| `warships_snapshot` | 0.44 GB | — | — | — |

Key facts:
- **Bloat is negligible** (51,793 dead tuples on `BattleObservation`; last autovacuum recent) — the sizes are true live footprint, not VACUUM debt.
- Compaction is **enabled**: `BATTLE_OBSERVATION_COMPACT_ENABLED=1`, `BATTLE_OBSERVATION_COMPACT_KEEP=1` (`prune_battle_observations_task`, `tasks.py:2297`). Only the **latest** observation per player retains JSON; older rows are nulled skeletons.
- Only **338,468** observation rows carry JSON (the KEEP=1 retained set), ~20 KB TOAST each. For scale: 46,803 distinct players observed in the last 2 days.
- So the 6.8 GB elephant is **current-snapshot working data inside the read horizon**, not history — age-based archival never touches it.

### Runway note (NOT the chosen path, recorded for completeness)

If runway ever becomes the priority again:
- The 30-day cold-store of `BattleEvent` (→ Parquet) + `PDSS` reclaims only **~0.5 GB one-time**, but it **bends the growth slope**: `BattleEvent` (~32 MB/day) + `PDSS` (~40 MB/day) ≈ **~72 MB/day of the ~105 MB/day no-retention floor** (see `project_db_growth_2026-06-15_runway` memory). Capping both at a rolling window makes them plateau.
- The real disk mass — 6.8 GB of `ships_stats_json` — is **not** addressed by age-based pruning. The lever there is shrinking the per-row payload (it's already a 22-field projection, KEEP=1) or moving the diff-buffer out of Postgres. Separate, more invasive work.
- **`BattleEvent` → `PDSS` coupling is a data-loss trap for any future prune:** the nightly rollup does `PDSS.filter(date=target).delete()` then recreates from `BattleEvent` (`incremental_battles.py:1255`). Any expunge of `BattleEvent` must refuse to touch dates newer than `month_window (30) + rollup_lookback (3, tasks.py:2148) + margin` → **~35 days**, or a rebuild/reconcile silently zeroes a day inside the month view. Reconcile audits 30 days (`tasks.py:2279`).

## Data flow & field inventory

```
WG ships/stats (pvp block)
  └─ incremental_battles.py parses per-ship counters       (ShipStats dataclass, incremental_battles.py:51-61, parse 120-130)
       └─ stored as ships_stats_json on BattleObservation  (flat 22-field projection, serialize 515-525)
            └─ diffed into BattleEvent deltas (Phase 7)     (models.py BattleEvent; delta map incremental_battles.py:447-457)
                 └─ rolled up into PlayerDailyShipStats      (per-day per-ship per-mode; rebuild 1255-1326)
```

`ships_stats_json` — **22 fields per ship** (confirmed from a live observation, 71 ships in payload):

```
battles, wins, losses, survived_battles,
damage_dealt, frags, xp,
capture_points, dropped_capture_points, team_capture_points,
damage_scouting, ships_spotted, planes_killed,
main_shots, main_hits, main_frags,
secondary_shots, secondary_hits, secondary_frags,
torpedo_shots, torpedo_hits, torpedo_frags,
ship_id
```

These are **career (lifetime) totals** per ship for the PvP mode. The delta pipeline diffs consecutive observations to get per-session/per-day deltas; the absolute values are career totals (usable directly for a career profile from one observation).

`BattleEvent` (`models.py`) carries a `*_delta` for **every** one of these (Phase 7 "widening" columns: `main_shots_delta … team_capture_points_delta`). `PlayerDailyShipStats` (`models.py`) carries the **same widened columns** as per-day aggregates (`main_shots, main_hits, …, capture_points, dropped_capture_points, team_capture_points, survived_battles`).

### What the feature layer actually reads

Across `views.py`, `data.py`, `landing.py`, **and the entire `client/`**, the only per-ship stats ever read are:

```
battles, wins, losses, frags, damage, xp, planes_killed
```

(`views.py:824/843/1121/1144` etc.; client grep for `hit_ratio|accuracy|main_hits|torpedo_hits|scouting|spotted` = **0 hits**.)

**Untapped (captured + rolled up, surfaced nowhere):**
`main_shots/hits/frags`, `secondary_shots/hits/frags`, `torpedo_shots/hits/frags`, `damage_scouting`, `ships_spotted`, `capture_points`, `dropped_capture_points`, `team_capture_points`, `survived_battles`.

## Current battle-history read surfaces (for context)

| Surface | Component | Reads | Window |
|---|---|---|---|
| Player profile → Battle History card | `BattleHistoryCard.tsx` | `BattleEvent` (24h) + `PDSS` (week/month/year) | up to 365d (year) |
| Landing → Top Ships treemap | `RealmTopShipsTreemapSVG.tsx` | `BattleEvent` (`data.compute_realm_top_ships`) | 14d trailing |
| Ship explorer + `/ship/<id>` board | `ShipLeaderboard.tsx` / `ShipRouteView.tsx` | `BattleEvent` → `ShipTopPlayerSnapshot` | 14d |
| Landing → "Recent" ordering | `landing.py:1644` | `PDSS` 7-day rollup | 7d |
| Profile badges / podium banner | `ShipTopPlayerBanner.tsx` | nightly snapshot | rolling |

Period config: `views.py:591` `BATTLE_HISTORY_MAX_DAYS=365`; window map `views.py:599-618` (`year` = 365 daily windows, the only >30d reader — see runway note).

**The year view is the only read path that touches data older than 30 days.** It was discussed for removal during the runway investigation; under the chosen operationalization direction it is **left in place** (keeping the 30-day window means status quo on windows, no removal needed).

## Operationalization menu (the chosen direction)

The data is the same set of derivable metrics in every case:
- **Main battery hit ratio** = `main_hits / main_shots`
- **Torpedo hit rate** = `torpedo_hits / torpedo_shots`
- **Secondary effectiveness** = `secondary_hits / secondary_shots`
- **Spotting / support** = `damage_scouting` + `ships_spotted` per battle
- **Objective play** = `capture_points` + `team_capture_points` − `dropped_capture_points`
- **Survival rate** = `survived_battles / battles`

| Feature idea | Source | Coverage | Cost |
|---|---|---|---|
| **Career combat profile / playstyle radar** (gunnery · torpedo · spotting · objective · survival) on the player page | lifetime totals in the **latest** `BattleObservation.ships_stats_json` | **Full** — every fetched player, no history, no retention dependency | Low–med |
| **New playstyle classification badges** (Sniper / Torpedo-boat / Scout / Objective-player) — fits existing `ship_badges` system | career ratios from latest observation | **Full** | Low |
| **Per-ship hit% / torpedo-hit% columns** in the existing Battle History table | `PDSS` widened columns | **Thin** (floor coverage only) | Low (table already rendered) |
| **Accuracy / spotting leaderboards** (realm or ship) | `BattleEvent` aggregate | **Thin** (14d window) | Med |

### The honest coverage constraint (why this data sat unused)

Per-day combat columns only exist where `BattleEvent`s exist = the **observation floor's coverage**, which is thin (~15–25% of active-7d, accumulating slowly; see `project_coverage_ceiling_daily_active`). So **recent/trend** combat features are sparse for most players.

**The high-coverage play sidesteps this:** lifetime accuracy/torpedo/spotting ratios are computable from a **single latest observation** per player — full coverage, no time-series, no retention needed. Lead with the **career combat profile**; treat per-day trend variants as a thin, opportunistic add.

### Implementer gotcha to verify first

The full-coverage `battles_json` (which powers the existing TierSVG/TypeSVG charts) appears **not** to carry the combat fields. So a career profile must read them from the latest observation's `ships_stats_json`, or `battles_json` must be widened at hydration. Confirm before building:
```bash
# does battles_json carry main_hits/main_shots/torpedo_*/damage_scouting per ship?
grep -nE "main_hits|main_shots|torpedo_hits|damage_scouting|ships_spotted" server/warships/data.py
```

## Verification recipe (how the numbers were measured)

Prod has **no Docker** — bare venv at `/opt/battlestats-server/venv`, code at `/opt/battlestats-server/current/server`. Run read-only measurements through Django's shell over piped stdin, guarded with `statement_timeout` (per `feedback_prod_db_long_query_safety`). **Do not** scan env files for DB creds — use the app's configured connection.

```bash
ssh root@battlestats.online 'cd /opt/battlestats-server/current/server && /opt/battlestats-server/venv/bin/python manage.py shell' <<'PY'
from django.db import connection
with connection.cursor() as c:
    c.execute("SET statement_timeout='25s'")
    for label, sql in [
      ("BO total",  "SELECT pg_size_pretty(pg_total_relation_size('warships_battleobservation'))"),
      ("BO TOAST",  "SELECT pg_size_pretty(pg_total_relation_size(reltoastrelid)) FROM pg_class WHERE relname='warships_battleobservation'"),
      ("BE size",   "SELECT pg_size_pretty(pg_total_relation_size('warships_battleevent'))"),
      ("PDSS size", "SELECT pg_size_pretty(pg_total_relation_size('warships_playerdailyshipstats'))"),
      ("BO rows w/ JSON", "SELECT count(*) FROM warships_battleobservation WHERE ships_stats_json IS NOT NULL"),
      ("BE >35d",   "SELECT count(*) FROM warships_battleevent WHERE detected_at < now() - interval '35 days'"),
      ("PDSS >30d", "SELECT count(*) FROM warships_playerdailyshipstats WHERE date < (now()::date - 30)"),
    ]:
        c.execute(sql); print(f"{label:18s} => {c.fetchone()[0]}")
PY
```

Inspect the live `ships_stats_json` schema:
```python
from warships.models import BattleObservation
obs = BattleObservation.objects.filter(ships_stats_json__isnull=False).order_by('-observed_at').first()
print(sorted(obs.ships_stats_json[0].keys()))   # 22 per-ship fields
```

## Code pointers

- `server/warships/incremental_battles.py:51-61` — `ShipStats` dataclass (the 22 captured fields)
- `server/warships/incremental_battles.py:120-130` — parse from WG `pvp` block
- `server/warships/incremental_battles.py:447-457` — delta field map (BattleEvent)
- `server/warships/incremental_battles.py:515-525` — serialize to `ships_stats_json`
- `server/warships/incremental_battles.py:1255-1326` — rebuild `PDSS` from `BattleEvent` (delete-then-recreate coupling)
- `server/warships/models.py` — `BattleObservation`, `BattleEvent` (Phase 7 columns), `PlayerDailyShipStats` (widened columns)
- `server/warships/views.py:591-618` — battle-history period/window config; `:769` 24h payload (BattleEvent), `:1048` calendar payload (PDSS)
- `server/warships/tasks.py:2148` rollup lookback (3d); `:2279` reconcile audit (30d); `:2297` `prune_battle_observations_task` (compaction KEEP=1)
- `server/warships/data.py` — `compute_realm_top_ships`, `compute_realm_ships_by_tier_type`, `snapshot_ship_top_players_task`
- `client/app/components/BattleHistoryCard.tsx`, `RealmTopShipsTreemapSVG.tsx`, `ShipLeaderboard.tsx`, `ShipRouteView.tsx`, `ShipTopPlayerBanner.tsx`

## Implementation status

- **2026-06-17 — ShipStats panel shipped (PR on `feat/ship-stats-component`).** First operationalization of these fields. Clicking a ship in the Battle History table (Activity tab) opens a per-ship panel comparing the player to the ship's 30-day population, bracketed by account-WR skill (All / Top 50% / Top 25%, ranked by `Player.pvp_ratio`, ≥200 pvp battles).
  - **Backend:** `GET /api/player/<name>/ship/<id>/combat-stats` → `compute_ship_combat_comparison` (`data.py`); population aggregated from `PlayerDailyShipStats` (random, 30d), cached per realm+ship.
  - **Reliability scoping confirmed the runbook's warning the hard way:** the Phase-7 widened per-day columns are populated on only **~6% of daily rows**, so their per-battle population averages are unusable. Surfaced metrics are limited to the complete core counters (win/damage/frags/xp) + accuracy **ratios** (hit% self-normalizes over rows-with-shots). User accuracy reads CAREER `ships_stats_json` (complete; 30d gunnery too sparse); core metrics read 30d PDSS (match the table).
  - **Deferred (needs the runbook's recommended source):** survival, spotting, scouting, capture play — require a **precomputed career-population aggregate** from `ships_stats_json` (per-ship cross-player, e.g. a nightly job), since no queryable per-ship career-population store exists. Until then those clusters are omitted.
- **2026-06-29 — ShipStats UX refresh (presentation only; `feat/shipstats-ux`).** Same payload/contract — the panel was re-fashioned from comparison bars into a compact left-aligned, centered table (`Average | Player | Delta`, cluster group rows, Outcomes group label dropped). Per-row emphasis follows the better reading (white/semibold) rather than the column; `/battle` units fold into the metric label, `%` stays inline; numeric columns hold a fixed min-width so they do not shift when switching skill brackets. No backend change. (A ceiling-scaled dumbbell variant was prototyped and reverted — its per-metric population ceiling needed the same deferred career-population aggregate as the survival/spotting cluster above.)
- **2026-06-30 — Accuracy "career" tag (v2.18.1, FE-only).** The Accuracy cluster header now renders `ACCURACY · career` (muted qualifier) to disclose that those rows' Player values are CAREER (`user_basis='career'`), not the 30-day window — the rest of the panel is 30d. Closes the long-standing confusion (a player can show a non-zero torpedo-hit% in a match where they fired none). FE assumes by cluster name ('Accuracy'); the payload still carries no per-metric `basis` field.

## Related

- `agents/runbooks/runbook-bulk-battle-observation-capture-2026-06-06.md` — the capture/floor design (write side)
- `agents/runbooks/runbook-floor-throughput-tuning-2026-06-13.md` — floor coverage limits
- Memory: `project_db_growth_2026-06-15_runway`, `project_coverage_ceiling_daily_active`, `feedback_prod_db_long_query_safety`
