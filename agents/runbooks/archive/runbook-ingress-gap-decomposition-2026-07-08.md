# Runbook: Ingress 24h Capture-Gap Decomposition (gap_1d) + battles_json Re-enable

_Created: 2026-07-08 · Updated: 2026-07-11 (pick-up done, Step 1b decided)_
_Context: Next step in player-data-ingress optimization. The observation floor turned out not to be throughput-bound; this tranche shipped an instrument that decomposes the residual 24h capture gap and re-enabled the floor's displayed-stats rebuild. **The parked Step 1 decision was made on 2026-07-11: branch 1b (re-baseline the KPI, declare PvP capture complete).** See "Step 1 decision (RESOLVED)" below._
_QA: Backend suite 819 passed / 2 skipped (sqlite, `DJANGO_SECRET_KEY` set). Live-verified on prod 2026-07-08: gap_1d emitted at scale; first post-restart floor cycle rebuilt battles_json for 89/89 movers._

## Purpose

Captures the diagnosis, the two shipped changes (v2.22.3, merge `fb82bd2`), the monitoring window, and the decision framework for the follow-on step. Read this before touching floor limits, cadence, or capture scope: the headline coverage numbers understate how complete PvP capture already is, and the wrong reading leads to spending budget on a gap that is not a throughput problem.

## Diagnosis (what motivated this tranche)

Evidence gathered 2026-07-08 from the nightly benchmark series, prod env, and floor journal:

- **The floor drains its backlog daily.** Self-chain telemetry shows every realm reaching "Floor self-chain stop (remaining < 500)" several times per day, even while coexisting with the near-continuous clan crawl. Config stable for 14+ days: `LIMIT=12000`, `HOURS=8`, `CYCLE_MINUTES=180`, self-chain all realms, `FETCH_CONCURRENCY=4`.
- **PvP capture is effectively complete.** `mover_capture_rate` runs 1.1 to 1.4 across all realms (the floor records BattleEvents for more distinct players than the snapshot engine flags as PvP movers); `never_observed` is ~0.
- **The residual gap is compositional.** `coverage_ratio_vs_1d` ~77% overall (NA 58 to 67%, EU ~87%, ASIA ~76 to 83%). But `active_1d` derives from account-level `last_battle_time` (any game mode), while `Snapshot.battles` and BattleEvent extraction are PvP-only; `_fetch_ship_stats` / `_bulk_fetch_ship_stats` call `ships/stats/` with **no `extra=` param**, so co-op and Operations deltas are structurally invisible. Population mix explains the realm spread: only ~51% of NA daily actives are PvP movers, vs ~67% EU and ~74% ASIA. NA is co-op-heavy; it is not underserved by the floor.

First live run of the new instrument (2026-07-08 20:40 UTC; mid-day, so the date-granular `active_1d` denominator is inflated relative to the 04:30 series — compare only 04:30 snapshots to each other):

```
MOVER-CAPTURE: 63,200 of 58,499 daily movers captured (108.0%)
GAP-1D: 76,716 of 139,715 active-1d players produced no BattleEvent in 24h —
  65,296 active outside Random PvP (co-op/Operations),
  8,972 missed PvP movers (9 still uncaptured at 48h),
  2,448 unclassifiable (no snapshot pair).
```

Reading: 85% of the gap is non-PvP activity. The floor loses essentially nothing.

> **Correction (2026-07-11):** the "9 still uncaptured at 48h" figure above was an under-count — a mid-day one-off run taken minutes after the instrument deployed, before the trailing snapshot history had matured. The canonical 04:30Z series shows `pvp_mover_no_event_48h` steady at **~250–380/day**, not 9. That tail is **capture latency, not loss** (see "Step 1 decision (RESOLVED)"); the "floor loses essentially nothing" conclusion still holds, but the honest number is low-hundreds/day at >48h latency, not single digits.

## What shipped (v2.22.3)

**Step 0: gap_1d instrument** (`086efc1`) in `server/warships/management/commands/benchmark_observation_floor.py`:
- Per realm + total, classifies every active-1d player with no BattleEvent in the trailing window into: `pvp_mover` (snapshot pair shows cumulative PvP battles rose; sub-count `pvp_mover_no_event_48h` = no event in the trailing 48h either, i.e. genuinely uncaptured), `non_pvp_active` (account clock moved, PvP battles flat), `no_snapshot_pair` (unclassifiable). `null` until two snapshot days exist.
- Emitted in the nightly 04:30 UTC JSON snapshots automatically (no cron change needed; the cron calls manage.py from the current release) and as a `GAP-1D:` line in human output.
- Tests: 5 new cases in `test_benchmark_observation_floor.py`. Docs: Benchmarks section of `runbook-bulk-battle-observation-capture-2026-06-06.md`; the `/observation` skill knows the field and its routing rule.

**Step 2: battles_json refresh re-enabled** (`0c34103`):
- `FLOOR_REFRESH_BATTLES_JSON_ENABLED=1`, now **pinned in `server/deploy/deploy_to_droplet.sh`** (kv block). It had been hand-set `=0` on the droplet during the backlog catch-up phase; that hand edit was one deploy away from being silently wiped, and the catch-up phase is over. The floor now rebuilds displayed `battles_json` + `battles_updated_at` from the same `ships/stats` response at zero extra WG cost.
- Also reconciled: `ops-env-reference.md` had a stale `BATTLE_OBSERVATION_FLOOR_GATE_SKIP_COOLDOWN_HOURS prod=0` claim; live env and deploy pin say 8.

Deployed 2026-07-08 ~20:28 UTC (backend release `20260708162724`, client `20260708162902`); floor worker restarted with the flag; first cycle: `movers=89 battles_json_rebuilds=89 battles_json_total_ms=29092` (~330ms/rebuild), healthcheck clean, footer 2.22.3.

## Pick-up results (2026-07-11) — all checks passed

Ran the procedure below on 2026-07-11. Findings:

- **Re-enable healthy.** `bulk floor done` shows `battles_json_rebuilds ≈ movers` on every cycle (e.g. 1537/1537, 5136/5136, 2808/2808); one 729→723. Flag held (pinned in `deploy_to_droplet.sh`).
- **Throughput / load healthy.** Self-chain reaches `stop (remaining < 500)` on na/eu/asia repeatedly; managed-PG `load15 = 1.33`, well under the 2.3 alarm (the 3.00 load1 was a momentary spike). Cycle times in-band.
- **Decomposition holds.** Three clean 04:30Z snapshots (07-09/10/11): `non_pvp_active` dominant at **80–84%** of the gap; `pvp_mover_no_event_48h` steady at **281 / 252 / 376** (not the 9 from the 07-08 one-off — see the Correction above).
- **`pvp_mover_no_event_48h` is latency, not loss (sampled live).** Of 25 flagged players: **0 never-observed**; ~19 last polled by the floor >48h ago (backfill on next observation; only per-battle timeline resolution across the gap is lost); ~5 baseline/broken-prior diffs that can't emit an event. The bucket is also time-of-day inflated (mid-day reproduction 885, 788 of them EU mid-session; same-day 04:30Z EU was 37).

**Decision: branch 1b** (below). Because the tail is latency and `non_pvp_active` dominates, there is no throughput deficit to tune.

## Pick-up procedure (on or after 2026-07-11) — retained for re-runs

1. **Health of the re-enable (Step 2 watch, ~2 days):**
   ```bash
   ssh root@battlestats.online 'journalctl -u battlestats-celery-floor --since "24 hours ago" --no-pager | grep "bulk floor done" | tail -20'
   ```
   Expect `battles_json_rebuilds` ≈ `movers` and `cycle_ms` not materially above the pre-flip band (asia cycles up to ~2.6M ms were already normal). Confirm self-chain still reaches "stop (remaining < 500)" on each realm; confirm managed-PG load15 stays under the 2.3 alarm. If capture regresses: revert the pin to `=0` in `deploy_to_droplet.sh` and redeploy (or sed the droplet env + restart `battlestats-celery-floor` as an immediate stopgap, then fix the pin).
2. **Read 3+ clean nightly gap_1d snapshots** (only 04:30Z files; compare like with like):
   ```bash
   ssh root@battlestats.online 'for f in $(ls -1t /opt/battlestats-server/shared/benchmarks/observation-floor/*_0430Z.json | head -4); do echo "== $(basename $f)"; python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(\"total\", d[\"totals\"][\"gap_1d\"]); [print(r, d[\"realms\"][r][\"gap_1d\"]) for r in sorted(d[\"realms\"])]" "$f"; done'
   ```
   Or run `/observation`; the skill now reports and interprets gap_1d.
3. **Confirm the decomposition holds:** `non_pvp_active` dominant (expect roughly 60 to 85% of the gap; NA highest), `pvp_mover_no_event_48h` negligible (single digits to low hundreds). If instead `pvp_mover_no_event_48h` is material and sustained, that (and only that) justifies floor tuning: `HOURS` 8→6 first, then cadence, then revisit `BATTLE_OBSERVATION_FLOOR_CRAWL_LIMIT=3000` if misses cluster in crawl-coexist windows. Do not change `CELERY_FLOOR_CONCURRENCY` without August's explicit approval.
4. **Make the Step 1 decision** (below) and open the follow-on work item.

## Step 1 decision (RESOLVED 2026-07-11): branch 1b

**Chosen: 1b — re-baseline the KPI, declare PvP capture complete.** The 24h goal is now defined over `snapshot_movers` (true PvP movers): sustain `mover_capture_rate ≥ 1.0` (live 1.1–1.4) with `never_observed` ~0. `pvp_mover_no_event_48h` is **not** a goal metric — it is a latency tail (~250–380/day, verified above), and the honest re-baseline language is "low-hundreds/day at >48h latency, not lost," **not** "≈ 0" (that was the too-optimistic 07-08 reading). Only a material, sustained RISE in it, or any rise in `never_observed`, is a throughput signal.

Landed as a docs/metrics change (branch `docs/gap-1d-rebaseline-1b`):
- `benchmark_observation_floor.py`: module docstring + `_GAP_KEYS` comment + classification comment corrected to "latency, not loss"; `MOVER-CAPTURE:` headline states the PvP-capture-complete goal; `GAP-1D:` line reworded from "still uncaptured at 48h" to ">48h latency — captured late/backfilled, not lost".
- `runbook-bulk-battle-observation-capture-2026-06-06.md`: gap_1d section carries the re-baseline + the 2026-07-11 latency/loss verification.
- `.claude/skills/observation/SKILL.md`: gap_1d metric row, routing cue, and readout template updated (latency framing + 04:30Z-vs-mid-day discipline).

**1a (NOT chosen, parked as a product decision).** Widen capture to co-op/Operations. No capture-completeness pressure sits behind it; pursue only if a product surface for PvE data is decided on. Details retained below.

### 1a. Widen capture to co-op/Operations (parked — product decision):
- Mechanism: add `extra=pve,pve_solo,oper_solo,oper_div,oper_div_hard` (verify exact block names against the WG API before building) to the floor's `ships/stats` fetch. Zero additional WG calls; the co-op players are already polled every cycle as gate-skipped non-movers, so their polls become productive.
- Main risk: payload size. BattleObservation JSON bloat caused the 2026-05-24 disk/CPU incident; extras must be compacted to per-ship counters at persist time, never stored raw. Scope as its own vertical slice: fetch param, compaction, BattleEvent mode tagging, change-gate interaction (account-level `last_battle_time` moves on co-op play, so the gate must not classify a co-op mover as "no change"), UI exposure question.
- Product question first: leaderboards and battle history are PvP surfaces today; PveEnjoyerIcon suggests the population matters. Decide what the data would actually feed before building.

## Related

- `runbook-bulk-battle-observation-capture-2026-06-06.md` (Benchmarks section: gap_1d field reference)
- `runbook-floor-battles-json-refresh-2026-06-14.md` (the re-enabled mechanism, safety properties, cost watch)
- `runbook-floor-throughput-tuning-2026-06-13.md` (floor tuning arc; binding-constraint history)
- `.claude/skills/observation/SKILL.md` (day-over-day readout, now gap_1d-aware)

**Archive when:** the 1b re-baseline commit (`docs/gap-1d-rebaseline-1b`) has merged to main. The Step 1 decision is made (1b) and the durable findings are folded into `runbook-bulk-battle-observation-capture-2026-06-06.md`; this runbook is retained only as the diagnosis record until that merge, then move to `archive/`.
