# Runbook: Battle-Observation Floor Throughput Tuning (2026-06-13)

_Created: 2026-06-13_
_Context: An /observation readout showed thin active-player coverage (cov/7d ~5%, fresh<24h ~8% on a slow window). Investigation found the binding constraint is the shared `background` Celery pool, not the floor's own config — an enrichment self-chain was spinning ~37s on perma-skip candidates and stealing worker slots/WG budget from the floor._
_QA: advisor-reviewed; live evidence captured from `journalctl -u battlestats-celery-background` on 2026-06-13 06:5x–07:0x UTC._

## Purpose

Remove a wasteful unbounded retry loop on the shared `background` Celery pool, then (separately, on evidence) consider levers that free that pool for battle-observation floor coverage. **Phase 1 stands on its own merits — it kills a doctrine-violating self-chain spin burning WG calls + CPU every ~37s — independent of any coverage outcome.** The hypothesis that this lifts floor coverage (cov/7d, fresh<24h) is to be **measured**, not assumed: cov/7d ~4.8% is within day-to-day noise against a ~38% ceiling, and enrichment is single-flight (1 of 3 `background` slots), so "starves the floor" is plausible but unmeasured.

This is a **phased, reversible, independently-sequenced** plan. Each phase lands and is measured before the next is decided:
- **Phase 1** (executed) — bound the enrichment spin. Pure win, no DB cost.
- **Phase 2** (deferred — evidence + attribution) — floor `RANKED_DAILY`. Plausible but the ranked fetches observed were *enrichment's*, not the floor's, so there is no direct evidence the floor's per-slot ranked baseline is costly. Decide after one clean post-Phase-1 `/observation` snapshot, and land it **on its own restart** so its effect is attributable.
- **Phase 3** (deferred — DB-gated) — cadence/concurrency. Adds DB writes; gated on managed-PG `load15` headroom, which `doctl` on the droplet can't read.

Read this with `runbook-bulk-battle-observation-capture-2026-06-06.md` (floor design + benchmarks) and `runbook-hot-players-engagement-queue-2026-06-10.md` (the other `background`-pool sweep family).

## Two background-pool relief changes landed 2026-06-13 — attribution note

**Phase 1 was not the only `background`-pool change that day.** On the same date the
hot-players **Tier-3 freshness sweep** (`refresh_hot_player_freshness_task`, re-activated in
`feat(hot-players): re-activate Tier 3 freshness sweep`) was **gated to once/24h** via
`HOT_PLAYERS_FRESH_AFTER_MINUTES=1440` in prod. Until that gate it was the heaviest hot-family
consumer — scheduled every ~12 min striped per realm, each pass calling
`update_battle_data(force_refresh=True)` (WG fetch + write). Gating it to once/24h removed a large,
recurring `background`-pool draw simultaneously with Phase 1's enrichment-spin fix.

**Consequence for measurement:** the first clean post-Phase-1 `/observation` snapshot reflects the
**combined** relief of both changes, not Phase 1 alone. That is fine for the question "is the floor
freer now?" but means a coverage lift **cannot be attributed to Phase 1 in isolation** — which is
exactly why Phase 2 must land on its own restart (below). The enrichment background worker restarted
**2026-06-13 16:50 UTC**; the latest benchmark at evaluation time (`2026-06-13_0430Z`) predates that
by ~12h, so its entire 24h window is **pre-both-changes**. The first daily snapshot whose window is
fully post-restart is **`2026-06-15_0430Z`**.

## The `background` pool has more tenants than enrichment + floor

> **UPDATE 2026-06-15:** the Tier-3 `refresh_hot_player_freshness_task` was **deleted** (not just
> gated) — the hot family is now **two** sweeps (brain + capture). The write-heavy 12-min freshness
> tenant described below is gone, which permanently removes that `background`-pool draw.

The hot-players runbook makes the co-tenancy concrete — the family was **three** sweeps at the time
of writing (now two):

- `maintain_hot_players_task` — DB-only daily (the "brain"), no WG, negligible.
- `capture_hot_player_observations_task` — per-realm striped, **skip-if-fresh against the floor**, so
  mostly non-redundant with the floor, but still occupies a `background` slot for the hot-but-inactive
  set and writes a `Snapshot` per hot player/day.
- ~~`refresh_hot_player_freshness_task` (Tier 3)~~ — **RETIRED 2026-06-15.** Was once/24h per hot
  player (gate above) but scheduled every ~12 min striped and write-heavy
  (`update_battle_data(force_refresh=True)`); deleted entirely.

Plus `snapshot_active_players_task` (the daily-snapshot engine), also `background` and write-heavy.
Live evidence 2026-06-13: a continuous run of `Updated snapshot data for player …` at ~3.5s/player
occupying a worker. **The pool's binding pressure is partly write contention against the 2-vCPU
managed PG, not only WG/CPU** — this matters for Phase 3 (below).

## Diagnosis (what's actually binding)

The floor is **not** capacity-bound by its own knobs:

- **Not WG rate** — global token-bucket limiter runs ~2–3 of 10 req/s.
- **Not app CPU** — app droplet `load average` ~1.1–1.3 on 2 vCPU.
- **Not `FLOOR_LIMIT`** — already 12000; the 7500→12000 bump did not move `obs_poll` (14.3k→13.9k). Cadence is already tightened (`CYCLE_MINUTES=180`, 8 slots/day) and `RANDOM_FIRST_ENABLED=1` is already live for all realms.

The real throttle is the **shared `background` pool (`-c 3`)**, consumed by enrichment + hot-player sweeps competing with the floor. Two concrete drains found:

1. **Enrichment self-chain spin (primary).** `enrich_player_data_task` → `_maybe_redispatch_enrichment()` (`tasks.py:1906`) re-dispatches whenever `_candidates()` returns ≥1 row. The candidate query (`enrich_player_data.py::_candidates`) selects `enrichment_status=PENDING, is_hidden=False, pvp_battles>=500, battles_json IS NULL, active`. A pool of ~33 rows (eu 25, na 8) matches the query but is **private-at-fetch** — every pass fetches them, classifies "skip", and mutates **no selection-relevant field**, so `_candidates()` re-returns the same 33 forever. **Evidence:** 146 enrichment passes in 90 min, 142 of them `enriched:0, empty:0, skipped:33` — a ~37s spin doing zero useful work while burning a worker slot + ranked WG fetches (`Bulk fetching ranked info` + per-player `Remote fetching ranked ship stats`). This is an unbounded retry loop (doctrine violation).

2. **Floor ranked baseline runs every slot.** With `RANKED_DAILY_ENABLED` unset (default off), the heavy per-player ranked path (3rd WG call) runs on all 8 daily slots even though ranked is niche/less time-sensitive — wall-clock the floor could spend on random coverage.

## Phased plan

### Phase 1 — Bound the enrichment self-chain (code; pure win, no DB cost)

**Change:** add a no-progress guard so a batch that changed zero state (every candidate skipped — `enriched==0 and empty==0`) does **not** self-chain. The 15-min Beat kickstart (`player-enrichment-kickstart`) remains the retry, dropping the spin from ~37s to ~15min (~24×) while real backlog (which produces `enriched`/`empty` > 0) still self-chains uninterrupted.

- `tasks.py::_maybe_redispatch_enrichment(made_progress=True)` — short-circuit with a log line when `made_progress` is False.
- Call site (`tasks.py:~2018`, the `finally` block): compute `made_progress = bool(summary.get("enriched") or summary.get("empty"))` (guard for `summary is None` on exception → keep retrying).

**Precondition that makes the guard safe:** the spinning batch queued 33 < the 500 `ENRICH_BATCH_SIZE` cap, so the *total* reachable candidate pool ≈ 33 — stopping the chain strands no reachable backlog. Caveat: `_candidates` orders by `pvp_ratio DESC` with no cursor, so high-WR private-at-fetch rows clog the front of the queue; if the pool ever grows past one batch, the guard relies on `enriched`/`empty` progress continuing to drive the chain (the follow-up root-fix removes the clog). **Behavior-change note:** an `errors`-only batch (`enriched==0 and empty==0 and errors>0`) now also stops the chain — intended, to avoid error-spin; Beat retries in 15 min.

**Test:** `test_enrichment_task.py` — new case: a pure-skip summary (`enriched:0, empty:0, skipped:33`) calls `_maybe_redispatch_enrichment(made_progress=False)`; a productive summary passes `made_progress=True`.

**Validate (prod, post-deploy):** `journalctl -u battlestats-celery-background --since "20 min ago" | grep -c "Enrichment pass complete"` should fall from ~140/90min toward a handful; look for `made no progress … not self-chaining`.

**Rollback:** revert the guard (one commit); behavior returns to unconditional self-chain.

### Phase 2 — Floor RANKED_DAILY (DEFERRED — evidence + attribution)

**Not landed in this tranche.** Plausible and doctrine-aligned (Random > Ranked), but the ranked fetches observed in logs were **enrichment's** (`enrich_player_data.py:142`), *not* the floor's ranked sweep, so there is no direct evidence the floor's per-slot ranked baseline is a meaningful cost right now. Decide only after one clean post-Phase-1 `/observation` snapshot, and land it on **its own** worker restart (never bundled with Phase 1) so its effect is attributable.

**Change (when taken):** set `BATTLE_OBSERVATION_FLOOR_RANKED_DAILY_ENABLED=1` in `/etc/battlestats-server.env`. The floor then runs the heavy ranked sweep only on the realm's earliest slot (`_is_ranked_daily_slot`) and passes `skip_ranked=True` on the others — freeing ~7/8 of the floor's ranked wall-clock for random coverage. `RANKED_SWEEP_LIMIT` stays default (5000). Takes effect on the next background-worker restart.

**Validate:** confirm `skip_ranked` on non-primary slots; watch the next `/observation` for random `distinct_productive` holding/rising while ranked spend drops.

**Rollback:** unset the env var, restart `battlestats-celery-background`.

### Phase 3 — Cadence/count (DEFERRED — gated on managed-PG headroom)

**Do NOT execute blind.** These add sustained DB writes against the 2-vCPU managed PG (`load15` saturates ~2):

- `BATTLE_OBSERVATION_FLOOR_SELF_CHAIN_ENABLED=1` — refill idle floor time between Beat slots.
- Background worker concurrency `-c 3 → 4`.

**Gate:** first verify managed-PG `system_load15` is comfortably < ~1.5 sustained. `doctl` on the droplet cannot enumerate the managed DB, so use the DO Prometheus scrape (creds from `/databases/{id}/metrics/credentials`, scrape `:9273/metrics`; recipe in memory `reference_do_db_cpu_metrics_endpoint.md`). Only after Phase 1 frees the pool — measure the post-Phase-1 baseline first; freeing the enrichment spin may itself lift floor throughput enough.

**`-c 3 → 4` is riskier than it looks — the pool is write-heavy.** As documented above, the
`background` pool already runs several *concurrent writers* (hot capture's `update_snapshot_data`,
Tier-3 freshness's `update_battle_data(force_refresh=True)`, the daily-snapshot engine, plus the
floor). A 4th slot adds another concurrent writer against the 2-vCPU managed PG, so check `load15`
**under a striping-overlap window** (when multiple realm sweeps coincide), not at an idle moment.

**Cheaper lever to try *before* Phase 3 — de-conflict striping.** The floor, hot capture, and the
snapshot engine are each independently per-realm striped. If their `base_minute`/offsets put a
write-heavy sweep in the **same realm-slot as the floor**, they contend for both the worker and the
DB. Re-spacing their `base_minute` in `signals.py` frees floor wall-clock at **zero added DB load** —
strictly preferable to buying a 4th worker. Audit `signals.py` for floor-vs-(hot/snapshot) slot
collisions first; only reach for concurrency/self-chain if striping is already clean and `load15`
has headroom.

## Validation (overall)

- Phase 1: enrichment pass rate drops sharply; `enriched`/`empty` still progress when real backlog exists.
- Lean release gate green for the backend change.
- Next-day `/observation` readout: random `distinct_productive` and `fresh<24h` trend up (decompose numerator vs denominator per the skill's rules — don't credit a denominator shift).

## Follow-ups

- **Root-fix the stuck-candidate set (deeper than Phase 1) — SHIPPED + VALIDATED IN PROD 2026-06-13.**
  Committed `0ad8797`, deployed (migration `0070` applied, `background` worker restarted 18:55 UTC).
  **Validated live:** pre-deploy `_candidates` returned eu 25 / na 8 = 33; one stamping pass logged the
  old `skipped:33` signature and set `enrichment_skipped_at` on all 33; immediately after, `_candidates`
  returned **0/0/0** and a second pass queued **0 players, `skipped:0`, completing in 0.36s** instead of
  the ~37s spin. The self-chain spin is **eliminated**, not merely bounded — the 15-min Beat kickstart
  now finds 0 candidates until the 3-day cooldown lapses. The ~33 private-at-fetch
  `PENDING/battles_json IS NULL` rows no longer re-clog `_candidates()`.

  **Decision: per-row cooldown, NOT a terminal state.** The "or terminal non-`PENDING` state" option
  was evaluated and **rejected as unsafe.** `reclassify_enrichment_status` is the authoritative state
  machine and it keys **purely on stored fields** — its `pending` bucket is exactly
  `is_hidden=False, battles_json IS NULL, pvp_battles>=MIN, days<=MAX, pvp_ratio>=MIN`. These rows
  have `is_hidden=False` (WG returns *null ship stats* for a private profile, but the account-level
  `is_hidden` flag isn't set), so any terminal `skipped_*` we wrote would be **bounced straight back
  to `pending`** by the next daily drift reclassify → re-clog. A terminal state would require teaching
  reclassify about the cooldown — out of scope (violates smallest-safe-slice).

  **The fix (implemented approach):** a dedicated `Player.enrichment_skipped_at` timestamp (migration
  `0070_player_enrichment_skipped_at`, a nullable add — metadata-only on PG), stamped only in
  the private-at-fetch skip branch (`_process_player_ship_data`, `ship_data_list is None`), with
  `_candidates()` excluding rows skipped within `ENRICH_SKIP_RETRY_AFTER_DAYS` (default **3** — shorter
  than EMPTY's 14 because private accounts un-hide relatively often, and the retry is now ~700× cheaper
  than the old 37s spin). Rows stay `PENDING` (correct — they *are* pending, just rate-limited), so the
  fix is orthogonal to reclassify and doesn't fight it. Mirrors the `ENRICHMENT_EMPTY_RETRY_AFTER_DAYS`
  precedent. **Transient failures are untouched:** the `"SKIP"` sentinel and chunk-level 5xx/timeout
  errors `continue` before reaching the stamp, so genuine transient retries stay immediate (covered by
  a regression test). Composes with Phase 1: after one stamping pass the pool drains to 0 candidates
  for the cooldown window, so even the 15-min Beat kickstart finds nothing — the spin is *eliminated*,
  not just bounded.

  **Operational note (so it doesn't read as a stall):** post-fix a steady **~33 `PENDING`** is
  expected — it is **cooldown-suppressed, not stuck**. The `enrichment-status` / "how's enrichment"
  health read should not flag a flat non-zero pending floor as a regression. Ties into
  `agents/work-items/player-enrichment-map-2026-06-08.md` and memory
  `project_enrichment_misses_elite_empty_falseneg` ("99% caught up" is false).

  **Validate (prod, post-deploy):** after the migration applies + the `background` worker restarts,
  the 15-min kickstart passes should show `skipped:0` (no longer `skipped:33`) — confirm with
  `journalctl -u battlestats-celery-background --since "30 min ago" | grep "Enrichment pass complete"`
  (the per-pass `skipped` count should fall to 0 once the 33 are stamped, with `candidates` also
  dropping toward 0). The ~33 stay `PENDING` (a `enrichment_status` count won't move); that's the
  cooldown holding them, not a stall. Re-eligibility after `ENRICH_SKIP_RETRY_AFTER_DAYS` (3d) is the
  expected retry. **Rollback:** unset/raise `ENRICH_SKIP_RETRY_AFTER_DAYS` (the filter widens; the
  column is harmless if left) — or revert the `_candidates()` filter + stamp commit; the migration is
  additive and need not be reversed.
- **Phase 3 execution** once managed-PG `load15` is verified — and only after the cheaper
  striping-collision audit above.

## Related runbooks

- `runbook-bulk-battle-observation-capture-2026-06-06.md` — floor design, knobs, benchmarks.
- `runbook-hot-players-engagement-queue-2026-06-10.md` — the other `background`-pool sweep family.
- `runbook-db-cpu-saturation-2026-05-24.md` — prior enrichment fan-out (the 2026-05-27 ~1,190/hr churn); this runbook is a sibling capacity fix.
