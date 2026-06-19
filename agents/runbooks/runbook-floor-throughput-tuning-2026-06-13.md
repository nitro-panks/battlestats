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

**Precondition that makes the guard safe:** the spinning batch queued 33 < the 500 `ENRICH_BATCH_SIZE` cap, so the *total* reachable candidate pool ≈ 33 — stopping the chain strands no reachable backlog. Caveat (RESOLVED 2026-06-14): `_candidates` orders by `pvp_ratio DESC` with no cursor, so high-WR private-at-fetch rows could clog the front of the queue. The per-row cooldown root-fix shipped (`0ad8797`, migration `0070`, `ENRICH_SKIP_RETRY_AFTER_DAYS` default 3) — `_candidates()` now suppresses private-at-fetch rows for the cooldown window (`enrichment_skipped_at` stamp), so they no longer re-clog the front and the guard no longer depends on a cursor (a cursor is redundant). A steady ~33 cooldown-suppressed `PENDING` is expected, not a stall. **Behavior-change note:** an `errors`-only batch (`enriched==0 and empty==0 and errors>0`) now also stops the chain — intended, to avoid error-spin; Beat retries in 15 min.

**Test:** `test_enrichment_task.py` — new case: a pure-skip summary (`enriched:0, empty:0, skipped:33`) calls `_maybe_redispatch_enrichment(made_progress=False)`; a productive summary passes `made_progress=True`.

**Validate (prod, post-deploy):** `journalctl -u battlestats-celery-background --since "20 min ago" | grep -c "Enrichment pass complete"` should fall from ~140/90min toward a handful; look for `made no progress … not self-chaining`.

**Rollback:** revert the guard (one commit); behavior returns to unconditional self-chain.

### Phase 0a — Floor instrumentation (SHIPPED — observability prerequisite)

Code-only, no behavior change. The floor's per-cycle tallies were written via management-command
`self.stdout.write`, which Celery only forwards to `journalctl` unreliably (at WARNING, via
`worker_redirect_stdouts`), so the deferred phases below had no trustworthy wall-time evidence to
attribute against. Now mirrored to the module loggers, which land cleanly in
`journalctl -u battlestats-celery-background`:

- `ensure_daily_battle_observations.py` mirrors the bulk-random, ranked-per-player, and final
  summary tallies (completed/baseline/events/`gated_skipped`/wall-time) to `log.info` (logger
  `battle_observation_floor`) alongside the existing stdout lines.
- `record_observations_bulk` (`incremental_battles.py`) emits one per-cycle line
  (`bulk floor done realm=… movers=… battles_json_rebuilds=… battles_json_total_ms=… cycle_ms=…`)
  via the module `logger` — isolating how much of the sweep's wall-time the per-mover
  `apply_battles_json` rebuild (the 06-14 `battles_json` refresh) consumes. Backed by a
  process-local timer reset/read inside the sweep (prefork-safe).

**Read it:** `journalctl -u battlestats-celery-background --since "30 min ago" -g 'bulk floor done'`.
This is the attribution source for the Phase 0b A/B (`FLOOR_REFRESH_BATTLES_JSON_ENABLED` 1→0) and
for deciding whether the rebuild is the throttle before touching Phase 2/3 levers. Covered by
`test_observations_bulk.py::BulkObservationInstrumentationTests` on the sqlite gate.

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

## Gate-skip cooldown + event-driven self-chain (implemented 2026-06-19)

**Corrected queue fact (supersedes the "starved on the background pool" framing above, for the
floor specifically).** The observation floor runs on the **`default`** queue / `battlestats-celery`
worker (per `settings.CELERY_TASK_ROUTES`; confirmed live 2026-06-19 — Beat dispatched
`observation-floor-{realm}` 12×/12h, the `background` worker received **0**, the floor ran on
`battlestats-celery`). The `background`-pool contention the earlier phases describe is real for the
**snapshot engine / enrichment / warmers**, but the floor does **not** share that pool — so an
earlier "floor slot-starved on background" read this session was a measurement error (grepping the
wrong worker). NOTE: `agents/diagrams/queue-data-flow.md` and `observation-floor-data-flow.md` still
mis-place the floor on `background`; flagged for a separate doc fix.

**The real finding (Phase 0a instrumentation, live 2026-06-19).** The floor is healthy on the
default worker (11 clean cycles/12h, `aborted=False`), but **asia is permanently `FLOOR_LIMIT`-bound**
(~12,000 candidates/cycle) with **~90–95% `gated_skipped` non-movers**. The change-gate skips a
non-mover *without writing an observation*, so it stays observation-stale and `_candidates()`
re-selects it every cycle — a permanent "non-mover wall" (≈ the 181k `stale_over_24h`). The per-mover
`battles_json` rebuild is ~16–48% of cycle wall-time (secondary; the per-mover `ships/stats` fetch
dominates).

**Why naive self-chain would spin.** `_maybe_redispatch_floor` re-dispatches while
`len(_candidates(...)) >= THRESHOLD`. The non-mover wall keeps `_candidates()` permanently far above
the threshold, so self-chain would re-dispatch forever (same shape as the enrichment self-chain spin
bounded above) — and because the floor is on the **default** worker, that spin would starve the
*other* default-queue tenants (dispatchers, lazy-refresh-on-view, watchdogs).

**The fix (flag-gated, default-off) — mirrors `enrichment_skipped_at`:**
- `Player.floor_gate_skipped_at` (migration `0075`, nullable → metadata-only on PG).
- `record_observations_bulk` stamps change-gated non-movers (one bulk `UPDATE` per ≤100-id chunk),
  **only when** `BATTLE_OBSERVATION_FLOOR_GATE_SKIP_COOLDOWN_HOURS > 0`.
- `_candidates()` excludes rows stamped within that window. A captured mover is excluded by the
  observation-staleness filter regardless, so the cooldown never delays a player who actually played
  beyond the window. The stamp cost self-limits: once the wall is cooled, later cycles see far fewer
  candidates → far fewer stamps.
- With the wall suppressed, `_candidates()` drains to genuine work, so the **existing**
  `BATTLE_OBSERVATION_FLOOR_SELF_CHAIN_ENABLED` self-chain terminates instead of spinning — no
  self-chain code change, just enable the flag once the cooldown is on.

**Rollout (staged).** Deploy with `…_COOLDOWN_HOURS` unset (default-off ⇒ behaviour-neutral). Then
enable on **na** first: set `BATTLE_OBSERVATION_FLOOR_GATE_SKIP_COOLDOWN_HOURS=2` (or 3), restart
`battlestats-celery` (the **default** worker, NOT `-background`), watch `gated_skipped` fall
cycle-over-cycle as the wall cools and `_candidates()` drains; then set
`BATTLE_OBSERVATION_FLOOR_SELF_CHAIN_ENABLED=1` and confirm the `Floor self-chain stop` log appears
(terminates, not spins) with managed-PG `load15` < 2. Expand to eu/asia. **Rollback:**
`…_COOLDOWN_HOURS=0` (filter widens, stamping stops; column harmless) + `…_SELF_CHAIN_ENABLED=0`; the
migration is additive.

**Cooldown sizing.** Shorter = the wall re-enters `_candidates()` sooner (tighter self-chain
duty-cycle, lower mover re-check latency); longer = more idle, longer worst-case re-check latency for
a player who returns mid-cooldown. Start 2–3h (≈ today's effective re-check latency on the
limit-bound realms). Tests: `GateSkipCooldownTests` in `test_observations_bulk.py`.

### Live validation — na pilot (2026-06-19, manual trigger ~18:02 UTC)

Enabled on **na only** (`…_GATE_SKIP_COOLDOWN_HOURS=2`, `…_SELF_CHAIN_ENABLED=1`,
`…_SELF_CHAIN_REALMS=na`; `battlestats-celery` restarted 17:11; persisted in `deploy_to_droplet.sh`
via #63). Manually dispatched one na floor cycle to drive it now instead of waiting for the 19:15
Beat slot.

**Cooldown works — draining, NOT spinning (the decisive check).** Direct prod read after the first
run:
```
na floor_gate_skipped_at within 2h : 12,279   (stamping fires)
na _candidates remaining (cooled out): 38,210  (the stamped 12,279 are EXCLUDED)
```
A spin would re-select the same stamped wall; instead `_candidates()` returns a *different* 38,210,
so the pool is genuinely draining. First run tally: `completed=108 baseline=10 events=178
gated_skipped=11871 cycle_ms=344s battles_json_total_ms=60s (~17%)` — limit-bound at 12000, then
`Floor self-chain re-dispatched (remaining>=500)` with `self_chained: True`.

**Big finding: na's true stale pool is ~50k** (12,279 stamped + 38,210 remaining). The single
12k-cap cycle was only ever reaching **~24% of na's stale set per cycle** — ~38k players untouched
every cycle. This is the coverage hole the self-chain closes; it drains the full pool over ~4–5
self-chained runs.

**Contention observation (matters for expansion).** The self-chained na runs share the `default`
`-c 3` pool with **other floor realms' scheduled cycles** — the asia 18:15 Beat cycle ran
concurrently, and each per-mover `ships/stats` fetch is serial, so the na burst slowed markedly
(first run 5.7 min; the self-chained run ran >20 min under the asia overlap). Default queue stayed
healthy (`0 ready` — no user-facing lazy-refresh starvation) during the burst, but the floor's own
realms contend with each other on `default` once more than one is active. (managed-PG `load15` not
captured — the DO metrics token lacks scope; use default-queue depth as the proxy until that's
fixed.)

**⚠️ Cooldown must outlast the per-realm drain burst — size before expanding.** Termination requires
the whole stale pool to drain to `< THRESHOLD` *before the earliest stamps expire* (the cooldown
window). na (~50k, ~6–17 min/run) drains inside 2h. **asia is always limit-bound, runs slower
(~25–30 min/run for ~12k), and likely has a larger pool — at a 2h cooldown asia's burst can exceed
2h, the earliest stamps expire mid-burst, those players re-enter `_candidates()`, and it never
converges (effective spin).** So do **NOT** blind-expand `…_SELF_CHAIN_REALMS` to eu/asia at 2h.
Before each realm: measure its pool (`len(_candidates("<realm>",7,8,100000))` via `manage.py
shell`) and either raise `…_GATE_SKIP_COOLDOWN_HOURS` to comfortably exceed `(pool/FLOOR_LIMIT) ×
run_minutes`, or raise `FLOOR_LIMIT` to drain in fewer runs (cheap — the extra budget is mostly
bulk gate-skips). Reconsider whether the floor's realms should self-chain concurrently on `default`
at all (a dedicated floor worker would remove the cross-realm contention — see Phase 4 isolation).

**Verify commands (operator):**
```bash
# draining vs spinning (the decisive read):
ssh root@battlestats.online 'cd /opt/battlestats-server/current/server && /opt/battlestats-server/venv/bin/python manage.py shell -c "
from warships.models import Player; from django.utils import timezone; from datetime import timedelta
from warships.management.commands.ensure_daily_battle_observations import _candidates
cut=timezone.now()-timedelta(hours=2)
print(\"stamped<2h:\", Player.objects.filter(realm=\"na\", floor_gate_skipped_at__gte=cut).count())
print(\"candidates remaining:\", len(_candidates(\"na\",7,8,100000)))"'
# terminate vs spin in the logs (floor is on the DEFAULT worker, battlestats-celery):
ssh root@battlestats.online 'journalctl -u battlestats-celery --since "2 hours ago" --no-pager | grep -E "floor bulk random realm=na|Floor self-chain"'
```
Healthy = successive runs' `gated_skipped` draws from a shrinking pool and a `Floor self-chain stop`
appears once `remaining < THRESHOLD`. Spin = many `re-dispatched` with the pool not shrinking.
**Termination on na was still in progress at the time of writing (slow under the asia overlap);
update this note with the confirmed `stop` once observed.**

## Iteration 2 — the actual fix: recency-first + dedicated worker (self-chain RETIRED, 2026-06-19)

The na pilot proved the floor is **under-capacity**, not mis-tuned: na has **~52k active-7d /
~35k active-1d** players but the floor captures ~12k/cycle serially (~140 movers/min). The stale
pool never drains, so the **self-chain + gate-skip cooldown (#62) are the WRONG tool** — they grind
an un-drainable backlog. **Retired** (`SELF_CHAIN_ENABLED=0` / `GATE_SKIP_COOLDOWN_HOURS=0` in the
deploy block; code kept, flag-gated off). The fix is **prioritization + isolation + concurrency**:

1. **Recency-first candidate ordering** (PR #66) — `_candidates` orders `-last_battle_date` first, so
   scarce capture capacity goes to the likeliest movers (not the stalest/crawl-inflated tail).
   Measured: ~108 movers/cycle → ~140/min.
2. **Defer the per-mover `battles_json` rebuild** — `FLOOR_REFRESH_BATTLES_JSON_ENABLED=0` (~16-48%
   per-mover saving) during the capture-max / backlog-catch-up phase.
3. **Dedicated floor worker** (PR #67) — own `floor` queue + `battlestats-celery-floor` `-c 3`. Off
   the user-facing `default` lane (isolation) + per-realm cycles run **concurrently** (~3×). Verified
   live: 2 realms concurrent, `default` 0-ready, no WG 407s.

**Per-mover threading (option C) was SKIPPED** — redundant once the dedicated worker's cross-realm
concurrency approaches the global WG token-bucket ceiling (~10 req/s; floor at 3 concurrent realms
≈ 6-9 req/s). WG is the true ceiling. **Worker isolation ≠ WG isolation:** the bucket is shared with
user-facing `hydration`, so if hydration backs up *sustainedly* under heavy organic traffic, cap floor
`-c`→2 (a one-off transient deploy-herd is not that signal).

### Leaderboard impact of deferring `battles_json` (verified 2026-06-19) — and the steady-state re-enable

Deferring the per-mover `battles_json` rebuild does **not** affect the ship leaderboards. The
`/ship/<id>` standings, the landing **treemap** (`compute_realm_top_ships`), the **tier-type list**,
and the profile **ship badges** are all built by **aggregating `BattleEvent`**
(`compute_ship_top_player_snapshot`) — and the floor **still writes `BattleObservation` + `BattleEvent`
regardless** of `FLOOR_REFRESH_BATTLES_JSON_ENABLED` (the rebuild is a separate post-capture step).
Recency-first actually *improves* these (more movers → more `BattleEvent`).

The only board that touches the deferred data is the **landing best-players ranking** (`landing.py`):
its score blends `high_tier_pvp_ratio` (derived from `battles_json`) + `efficiency_rank_percentile`
(from `PlayerExplorerSummary`). The lag there is **minor and bounded**: `PlayerExplorerSummary` is
refreshed by many *other* paths (enrichment, incremental refresh, clan crawl, on-view, backfill);
`battles_json` still refreshes on page-views / incremental / hot-player capture; the board's top
entries are popular/frequently-viewed players (refreshed via the view path); and the Best board is a
periodically-materialized cached snapshot. Deferring just reverts to the **pre-2026-06-14 status quo**.

**ACTION (don't forget): re-enable `FLOOR_REFRESH_BATTLES_JSON_ENABLED=1`** (in the deploy block +
live env) once past the capture-max / backlog phase, to restore active-but-unviewed players'
displayed-stats + landing-ranking freshness for free off the floor's existing `ships/stats` fetch.

## Related runbooks

- `runbook-bulk-battle-observation-capture-2026-06-06.md` — floor design, knobs, benchmarks.
- `runbook-hot-players-engagement-queue-2026-06-10.md` — the other `background`-pool sweep family.
- `runbook-db-cpu-saturation-2026-05-24.md` — prior enrichment fan-out (the 2026-05-27 ~1,190/hr churn); this runbook is a sibling capacity fix.
