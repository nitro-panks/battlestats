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

## Validation (overall)

- Phase 1: enrichment pass rate drops sharply; `enriched`/`empty` still progress when real backlog exists.
- Lean release gate green for the backend change.
- Next-day `/observation` readout: random `distinct_productive` and `fresh<24h` trend up (decompose numerator vs denominator per the skill's rules — don't credit a denominator shift).

## Follow-ups

- **Root-fix the stuck-candidate set (deeper than Phase 1).** The ~33 private-at-fetch `PENDING/battles_json IS NULL` rows should be excluded from `_candidates()` (a per-row cooldown after a skip, or marking them a terminal non-`PENDING` state) so they stop being re-selected at all. Ties into `agents/work-items/player-enrichment-map-2026-06-08.md` and memory `project_enrichment_misses_elite_empty_falseneg` (null `battles_json` on high-battle empties + scheduled reclassify). Phase 1 only bounds the spin; this removes its fuel.
- **Phase 3 execution** once managed-PG `load15` is verified.

## Related runbooks

- `runbook-bulk-battle-observation-capture-2026-06-06.md` — floor design, knobs, benchmarks.
- `runbook-hot-players-engagement-queue-2026-06-10.md` — the other `background`-pool sweep family.
- `runbook-db-cpu-saturation-2026-05-24.md` — prior enrichment fan-out (the 2026-05-27 ~1,190/hr churn); this runbook is a sibling capacity fix.
