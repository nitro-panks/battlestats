# Runbook: Battle History Phase 7 — Capture widening (gunnery, torpedoes, spotting, caps)

_Created: 2026-04-29_
_Context: Extends the battle-history capture pipeline (`runbook-battle-history-rollout-2026-04-28.md`) to record additional per-ship cumulative counters that already arrive in every `ships/stats/` response but are currently discarded by `incremental_battles._coerce_ship_snapshot`. Field inventory and definitions live in `agents/knowledge/wows-ships-stats-field-inventory.md`._
_Status: implemented; awaiting deploy (2026-04-30 — code + migration land on `feature/battle-history-phase7-data-widening`; release gate 294/294 green; migration `0056_battle_event_phase7_widening` ready to run on droplet)_

## Implementation reconciliation

Differences from the planned design that landed during implementation:

1. **Single combined migration instead of two.** `makemigrations` produced `0056_battle_event_phase7_widening.py` covering both `BattleEvent` (14 fields) and `PlayerDailyShipStats` (14 fields) in one operation. Original plan had separate `0056` and `0057` files. Atomic migration is arguably an upgrade — no half-applied state possible. Total 28 `AddField` operations, all `IntegerField(default=0)`, both tables small enough (<10 K rows) that the rewrite is sub-second.
2. **Period rollup tiers (`_PlayerPeriodShipStatsBase`) deliberately NOT widened.** The weekly/monthly/yearly tiers have no live data (period rollup writer is paused; pills are hidden on the BattleHistoryCard) so widening them now would only add 42 unused columns × 3 tables. Comment in `models.py` `PlayerDailyShipStats` notes this and ties widening of those tiers to whenever the period rollup writer is reactivated.
3. **`max(0, delta)` clamp considered and rejected.** The Phase-7 deltas use plain `current.attr - prev.attr` (no clamp), matching the existing `wins_delta` / `frags_delta` pattern. Negative deltas should be impossible for these counters (shots/hits/frags/cap_points only increase) but if WG ever publishes a regression, the daily rollup will record it as-is rather than silently smoothing — easier to spot in the Day-7 coverage probe.

## Purpose

Today the diff machinery in `incremental_battles.py` captures eight cumulative counters per ship (`battles`, `wins`, `losses`, `frags`, `damage_dealt`, `xp`, `planes_killed`, `survived_battles`). Wargaming's `ships/stats/` `pvp` block carries another ~15 cumulative counters in the same response. Capturing them costs **zero** additional WG API calls — they are already in every response we pull.

The fields proposed for this phase span four product families:

- **Gunnery** — main + secondary battery shots/hits/frags → accuracy and frag-source metrics.
- **Torpedoes** — torp shots/hits/frags → torp accuracy and torp-frag share.
- **Spotting** — `damage_scouting`, `ships_spotted` → vision-game contribution (the DD signature stat).
- **Caps** — `capture_points`, `dropped_capture_points`, `team_capture_points` → objective and defensive play.

The longitudinal value is the point of the phase: with months of these counters logged per player per ship per day, the playerbase can be ranked by stable, behaviorally-meaningful percentiles. That fuels a new class of identity icons (see "Future surfaces" below).

## Premise

This phase is **purely additive** to the capture and rollup pipelines that landed in Phases 2–6. It introduces no new WG calls, no new flags, and no new API surface in the first slice — it only widens what we already write. New surfaces (icons, accuracy charts, percentile ranks) ship as separate downstream phases once the capture has been on long enough to produce a meaningful population.

## Scope

### In scope

- Widen `ShipSnapshot` and `BattleObservation.ships_stats_json` to carry the new cumulative counters.
- Extend `compute_battle_events` to compute the corresponding `*_delta` fields on each `BattleEvent`.
- Extend `PlayerDailyShipStats` (and the weekly/monthly/yearly tiers when they ship) with the new aggregate columns.
- Add migrations and unit tests covering the new fields end-to-end.
- Update the existing `BATTLE_HISTORY_CAPTURE_ENABLED` and `BATTLE_HISTORY_ROLLUP_ENABLED` paths to write the new fields when on. **No new feature flag.** The fields fill from the moment Phase 7 is deployed; pre-Phase-7 observations simply lack them.

### Out of scope (deferred to follow-on phases)

- Per-record bests (`max_damage_dealt`, `max_frags_battle`, etc.). Different storage and surface model — not deltable. Filed as **Phase 7b** (see "Future surfaces").
- Surfacing accuracy or cap stats on the `BattleHistoryCard` table. UI surface is **Phase 7c**.
- Population-percentile derivation and identity icons (e.g. top-quartile torp accuracy badge). These need ≥4 weeks of capture before percentiles stabilize, and are filed as **Phase 7d**.
- Ranked / clan-battle parallels (`mode='ranked'` etc.). Phase 7 of the *rollout* runbook (now renumbered Phase 8 to avoid collision — see "Numbering note" below).
- Operations / PvE coverage. Same vocabulary, different endpoint block; filed for later.

### Numbering note

The original rollout runbook (`runbook-battle-history-rollout-2026-04-28.md`) reserves "Phase 7" for ranked-mode capture. This runbook claims **Phase 7 (data-widening)** as a sibling expansion of the same pipeline, and shifts the original ranked phase to **Phase 8**. Both phases share the same orchestrator (`record_observation_from_payloads`), so renumbering does not alter dependencies. The rollout runbook should be amended at the time this phase merges to reflect the renumbering.

## Design

### Fields to add

Source fields are documented in `agents/knowledge/wows-ships-stats-field-inventory.md`. The first slice captures the following cumulative counters from each `ships/stats/` `pvp` block:

| Family | Source field | New `ShipSnapshot` attr | New `BattleEvent` delta column | New `PlayerDailyShipStats` column |
|---|---|---|---|---|
| Gunnery | `main_battery.shots` | `main_shots` | `main_shots_delta` | `main_shots` |
| Gunnery | `main_battery.hits` | `main_hits` | `main_hits_delta` | `main_hits` |
| Gunnery | `main_battery.frags` | `main_frags` | `main_frags_delta` | `main_frags` |
| Gunnery | `second_battery.shots` | `secondary_shots` | `secondary_shots_delta` | `secondary_shots` |
| Gunnery | `second_battery.hits` | `secondary_hits` | `secondary_hits_delta` | `secondary_hits` |
| Gunnery | `second_battery.frags` | `secondary_frags` | `secondary_frags_delta` | `secondary_frags` |
| Torpedoes | `torpedoes.shots` | `torpedo_shots` | `torpedo_shots_delta` | `torpedo_shots` |
| Torpedoes | `torpedoes.hits` | `torpedo_hits` | `torpedo_hits_delta` | `torpedo_hits` |
| Torpedoes | `torpedoes.frags` | `torpedo_frags` | `torpedo_frags_delta` | `torpedo_frags` |
| Spotting | `damage_scouting` | `damage_scouting` | `damage_scouting_delta` | `damage_scouting` |
| Spotting | `ships_spotted` | `ships_spotted` | `ships_spotted_delta` | `ships_spotted` |
| Caps | `capture_points` | `capture_points` | `capture_points_delta` | `capture_points` |
| Caps | `dropped_capture_points` | `dropped_capture_points` | `dropped_capture_points_delta` | `dropped_capture_points` |
| Caps | `team_capture_points` | `team_capture_points` | `team_capture_points_delta` | `team_capture_points` |

All new columns are non-negative integers. All deltas are computed against the most recent prior `BattleObservation` for the same player+ship, identical in shape to the existing `battles_delta` etc.

### File map (touch list)

| File | Change |
|---|---|
| `server/warships/incremental_battles.py:36-79` | Extend `ShipSnapshot` dataclass with the 14 new attrs. Extend `_coerce_ship_snapshot` to read the nested objects defensively (`(ship_dict.get("main_battery") or {}).get("shots", 0)` pattern; missing nested keys → 0). |
| `server/warships/incremental_battles.py` (`compute_battle_events`) | For each new attr, compute `current - previous` and emit on the `BattleEvent` payload. Keep the existing `delta_battles <= 0` guard at the top of the loop unchanged — the new fields piggyback on the same diff. |
| `server/warships/incremental_battles.py` (`_serialize_ships_payload`) | Include the new attrs in the dict written to `BattleObservation.ships_stats_json` so historical observations reproduce the full delta vocabulary. |
| `server/warships/models.py` (`BattleEvent`) | Add 14 nullable `IntegerField`s with `default=0`, `db_index=False`. Indexed lookups by these fields are not anticipated in the first slice. |
| `server/warships/models.py` (`PlayerDailyShipStats`) | Add 14 `IntegerField`s with `default=0`. Same reasoning. |
| `server/warships/migrations/0056_battleevent_battery_torpedo_spotting_caps.py` | `AddField` × 14 for `BattleEvent`. Nullable + default=0 → safe under live writes. |
| `server/warships/migrations/0057_playerdailyshipstats_battery_torpedo_spotting_caps.py` | `AddField` × 14 for `PlayerDailyShipStats`. Same shape. |
| `server/warships/incremental_battles.py` (rollup writer, currently `apply_event_to_daily_rollup` or equivalent) | Sum the new deltas into the daily row alongside existing `battles_delta`/`damage_delta`/etc. |
| `server/warships/tests/test_incremental_battles.py` | Add cases: (1) snapshot coercion handles missing nested objects; (2) two-observation diff computes all 14 deltas correctly; (3) zero-battles observation produces zero deltas across the new fields; (4) rollup writer sums the new fields into the daily row. |
| `server/warships/tests/test_battle_history_api.py` (Phase-4 file) | Update payload contract assertions only if the API surface adds the new fields in this slice (decision: **defer**, see "API surface" below). |

### API surface

This slice does **not** modify `/api/player/<name>/battle-history` payload shape. The new columns are stored but not exposed. That choice keeps the contract surface tight and lets us validate capture correctness in production for ≥1 week before committing to a public field shape that downstream callers can lock in.

Phase 7c will be the API + UI exposure pass once we are confident in the captured data.

### Migration safety

- `AddField` with `default=0` on `BattleEvent` (~tens of thousands of rows / day at full coverage) and `PlayerDailyShipStats` (1/N the volume of events). Postgres rewrites the table in place under an `AccessExclusiveLock`. On the managed instance, both tables should still be small in absolute terms at the time this lands; if the count exceeds ~10 M rows by then, switch the migration strategy to `AddField` with `null=True, default=None` (no rewrite) and backfill in a separate migration.
- No index changes. No FK changes. No change to existing column defaults.
- Rollback: revert to migration `0055_player_last_random_battle_at`; the new columns are dropped. `BattleObservation.ships_stats_json` retains the wider payload — that is fine because the JSON is forward-compatible.

## Production rollout sequence

### Day 0 — code-only deploy

1. Land the migrations and code changes on a feature branch.
2. Curated release gate: `cd server && python -m pytest warships/tests/test_views.py warships/tests/test_landing.py warships/tests/test_realm_isolation.py warships/tests/test_data_product_contracts.py warships/tests/test_incremental_battles.py -x --tb=short`. Expect the 4 new cases to pass; everything else unchanged.
3. Frontend release gate: `cd client && npm test`. Expect zero diff (no UI changes in this slice).
4. Deploy backend: `./server/deploy/deploy_to_droplet.sh battlestats.online`. Migrations `0056` and `0057` run at deploy time. Watch the deploy log for migration completion within 30 s on each.
5. No frontend deploy needed (no UI surface change in this slice).

### Day 1 — observe capture

The new fields fill from the next observation onward. Verify on the droplet:

```bash
# Sample one or two recent BattleEvent rows and confirm the new delta columns are populated:
ssh root@battlestats.online "sudo -u postgres psql battlestats -c \\
  \"SELECT id, battles_delta, main_shots_delta, torpedo_shots_delta, capture_points_delta, ships_spotted_delta \
    FROM warships_battleevent ORDER BY id DESC LIMIT 5;\""
```

Expected: most recent rows show non-zero deltas in `main_shots_delta` (almost every battle fires the main battery), zero or non-zero in the others depending on ship type and play.

### Day 7 — coverage assessment

After ~7 days of capture, run a population-coverage query:

```sql
SELECT
  COUNT(*)                          AS daily_rows,
  AVG(main_shots > 0)::numeric(4,3) AS pct_with_main_shots,
  AVG(torpedo_shots > 0)::numeric(4,3) AS pct_with_torp_shots,
  AVG(damage_scouting > 0)::numeric(4,3) AS pct_with_scouting,
  AVG(capture_points > 0)::numeric(4,3) AS pct_with_caps
FROM warships_playerdailyshipstats
WHERE day >= NOW() - INTERVAL '7 days';
```

If the percentages match expectations (main-battery near 100 %, torpedoes around 25–40 % depending on DD/CA representation, scouting around 30 %, caps around 50 %), capture is healthy. Significantly lower percentages indicate a field-name mismatch in `_coerce_ship_snapshot` and warrant log inspection.

### Day 14+ — open Phase 7c (API + UI)

Once Day-7 coverage looks healthy and is stable for another week, open Phase 7c (the API + UI surface pass) as a separate runbook.

## Future surfaces (downstream phases this enables)

These are the **product motivations** for Phase 7. None ship in this runbook — they are filed here so the data shape captured in Phase 7 supports them without a second migration later.

### Phase 7c — `BattleHistoryCard` per-period accuracy + cap rows

Two new columns on the per-ship table inside the card:

- **Accuracy** column showing main-battery hit rate (`main_hits / main_shots`) and, for ships with torps, torpedo hit rate (`torpedo_hits / torpedo_shots`). Stacked WrCell-style with lifetime + delta.
- **Spotting / caps** mini-row beneath the per-ship row showing `damage_scouting`, `capture_points` won, `dropped_capture_points` (defensive). Hidden by default; toggled with an "expand stats" affordance on the card header.

### Phase 7d — Identity icons keyed to longitudinal percentiles

The motivating use cases:

- **Top-quartile torpedo player.** Compute per-ship-class percentiles of torpedo hit rate and total `torpedo_hits` over the trailing 30 days, restricted to players with `torpedo_shots >= N` (cold-start gate, e.g. N=200 over the window). Players in the top 25 % of both axes get a torpedo icon next to their name on the player surfaces (analogous to `RankedPlayerIcon`, `EfficiencyRankIcon`, etc.).
- **Vision player ("scout" icon).** Top-quartile `damage_scouting` per battle over the trailing 30 days, gated on `>= 50 battles`. Restricted to DDs and submarines initially (the ship classes whose identity centers on vision); CAs and BBs join once the percentile distribution is observed in production.
- **Cap player.** Top-quartile (`capture_points` + `dropped_capture_points`) per battle. Distinguishes "objective-aware" players from pure stat-padders.
- **Cap defender.** Subset of the above weighted toward `dropped_capture_points` — a player who spends battles wresting caps back rather than capping fresh ones. Smaller population; possibly a single combined "objectives" icon is sufficient.
- **Gunnery player ("sharpshooter" icon).** Top-quartile main-battery accuracy on a per-ship-class basis (BB and CA accuracy distributions are very different). Cold-start gate on `main_shots >= N`.

The percentile layer reuses the population-correlation infrastructure already in `data.py` (cf. `_elevated_work_mem` and the existing distribution warmers). Refresh cadence: nightly or every 6 h, well within the existing distribution-warmer envelope.

### Phase 7e — first-party "vs field" badges on the card

Once the Phase 7d population-percentile layer exists, surface "vs field" comparisons on each per-ship row of the card. For example, a small `↑ 78th pct` badge next to a torpedo accuracy column. This is the first-party equivalent of wows-numbers' expected-values dataset, restricted to the metrics our capture pipeline owns.

Cold-start gate: suppress the comparison until population sample size for that ship + metric exceeds a threshold (e.g. 50 distinct players with ≥30 battles in the trailing 30 days).

## Cadence guarantees (load contract)

- **No new WG API calls.** Phase 7 only widens what we already extract from existing `ships/stats/` responses. The 15-minute per-player capture throttle (`runbook-battle-history-rollout-2026-04-28.md`) continues to bound write volume.
- **No new Celery tasks.** Phase 7 piggybacks on the existing `update_battle_data` hook and the existing on-write rollup writer. The nightly sweeper (`roll_up_player_daily_ship_stats_task`) automatically backfills the new columns from `BattleEvent` deltas the same way it backfills existing columns.
- **Per-row size growth.** `BattleEvent` row size grows by ~14 × 4 bytes ≈ 56 bytes (Postgres int columns). At full coverage that is roughly +5–10 % on the table's running size — well within the storage envelope outlined in the rollout runbook.

## Operational watchpoints

- **Field-name typos.** WG documents `art_agro` / `torp_agro` historically and `art_agro` / `torpedo_agro` more recently in some live samples. This runbook intentionally **does not include `art_agro` / `torpedo_agro`** in the first slice for that reason — capture them in a follow-up after a live sample confirms the field name. Same caution applies to `distance` and `suppressions_count`.
- **Nested-key defensiveness.** `main_battery`, `second_battery`, and `torpedoes` can be `null` in the response for ships that have no such armament (some special CVs lack secondaries; lots of BBs lack torps). `_coerce_ship_snapshot` must treat missing or null nested objects as zeros, not raise. The unit tests in the file map cover this case.
- **Migration write-lock.** If `BattleEvent` exceeds ~10 M rows by the time this lands, the `AddField` × 14 migration becomes a long lock. Mitigate by switching to `null=True, default=None` (no rewrite) and adding a backfill migration. Decide at deploy planning time based on actual row counts.

## Kill switch

- **Disable capture of new fields** by reverting `_coerce_ship_snapshot` to read only the original 8 fields. The new columns persist with `default=0` for new rows and the wider data already captured in `BattleObservation.ships_stats_json` is not lost.
- **Cold kill** of all capture is the existing `BATTLE_HISTORY_CAPTURE_ENABLED=0` switch — unchanged from Phase 2.

## Validation

### Capture path

```bash
cd server
python -m pytest warships/tests/test_incremental_battles.py -x --tb=short
```

Expect the 4 new cases listed in the file map to pass.

### End-to-end on the droplet

After Day-1 deploy, run the SQL probe in "Day 1 — observe capture" above. Expect non-zero deltas on the recent rows.

### Coverage probe

After Day-7, run the population coverage query in "Day 7 — coverage assessment".

## Doctrine pre-commit checklist (per `agents/knowledge/agentic-team-doctrine.json`)

- **Documentation review:** Update `CLAUDE.md` "Architecture" → "Data models" line for `BattleEvent` and `PlayerDailyShipStats` to mention the widened field set. Update `agents/knowledge/wows-ships-stats-field-inventory.md` "Currently Captured" table when the slice merges (move the 14 fields out of "Discarded").
- **Doc-vs-code reconciliation:** Mark this runbook **shipped** at merge; update the `_Status:_` line.
- **Test coverage:** Per the file map (4 new test cases).
- **Runbook archiving:** Archive **this** runbook once Phase 7c (the API + UI surface pass) ships and the data-widening capture has been live for ≥30 days with steady coverage.
- **Contract safety:** No public API contract change in this slice. The `/api/player/<name>/battle-history` payload shape is unchanged.
- **Runbook reconciliation:** Amend `runbook-battle-history-rollout-2026-04-28.md` "Phase 7 (ranked)" to "Phase 8 (ranked)" at merge time, citing this runbook as the new owner of "Phase 7" naming.

## References

- Battle-history rollout runbook (capture pipeline + flags): `agents/runbooks/runbook-battle-history-rollout-2026-04-28.md`
- Recent-battled sub-sort runbook (sibling consumer of the same capture pipeline): `agents/runbooks/runbook-recent-battled-sub-sort-2026-04-28.md`
- WG `ships/stats/` field inventory: `agents/knowledge/wows-ships-stats-field-inventory.md`
- Diff machinery: `server/warships/incremental_battles.py:36-322`
- Aggregate model: `server/warships/models.py` (`PlayerDailyShipStats`)
- Population-correlation infrastructure (basis for Phase 7d percentile work): `server/warships/data.py` (search for `_elevated_work_mem` and the existing distribution warmers)
