---
name: observation
description: Pull the latest observation-floor benchmark snapshots from the production droplet and give a day-over-day readout on battle-observation coverage, freshness, and capture cost. Use when the user says "/observation", "observation status", "observation readout", "how's coverage trending", "observation-floor benchmark", or asks how battle-observation / player-acquisition coverage is progressing over time. Read-only — never writes, never restarts anything.
---

# observation

Reads the durable benchmark snapshots written nightly by the droplet cron
(`/opt/battlestats-server/shared/bin/snapshot_observation_floor.sh`, 04:30 UTC →
`/opt/battlestats-server/shared/benchmarks/observation-floor/YYYY-MM-DD_HHMMZ.json`)
and renders a **day-over-day progress readout** of the battle-observation floor:
coverage, freshness, and capture cost.

**Scope — read this before interpreting anything.** These snapshots measure the
**battle-observation floor** only: the sweep that walks active-7d non-hidden
players (`ensure_daily_battle_observations_task`, `tasks.py` ~1537,
`Player.filter(realm, is_hidden=False, last_battle_date >= today-DAYS)`) and
records `BattleObservation` / `BattleEvent` rows. The floor is **NOT win-rate
gated** — it already sweeps every active player regardless of WR. This benchmark
is therefore **blind to the enrichment pipeline** (who gets `battles_json` /
explorer summaries built), which *is* gated by `ENRICH_MIN_WR` /
`ENRICH_MIN_PVP_BATTLES`. A change to those enrichment knobs will **not** move
these numbers; don't attribute observation-coverage movement to them. For
enrichment-pool progress (e.g. `skipped_low_wr` draining → `enriched`), this is
the wrong instrument — query `Player.enrichment_status` instead, or use
`enrichment-status` for live crawler health.

## When to invoke

- "/observation", "observation status", "observation readout"
- "how's coverage trending", "are we making progress on the floor", "observation-floor benchmark"
- After a floor-config change (e.g. `BATTLE_OBSERVATION_FLOOR_LIMIT`), to confirm the step moved the needle

Do **not** invoke for: enrichment-pool / WR-gate progress (query `enrichment_status`), live crawler/worker health (use `enrichment-status`), or general Celery queue depth (use `healthcheck.sh`). This skill reads *yesterday's snapshot*, not live state.

## Procedure

### 1. Pull recent snapshots

One SSH call; the files are ~1 KB each, so pull the last two weeks of them:

```bash
ssh root@battlestats.online '
DIR=/opt/battlestats-server/shared/benchmarks/observation-floor
echo "AVAILABLE=$(ls -1 "$DIR"/*.json 2>/dev/null | wc -l)"
for f in $(ls -1t "$DIR"/*.json 2>/dev/null | head -14); do
  echo "===== $(basename "$f") ====="
  cat "$f"
done
'
```

If SSH fails or `AVAILABLE=0`, surface the error verbatim and stop. If `AVAILABLE=1`, report the latest snapshot but say plainly there is no comparison point yet.

### 2. Select comparison points BY `captured_at`, never by file order

The cron fires daily at 04:30 UTC, but **off-cycle manual runs exist** (e.g. a `2034Z` file ~8h after the daily one). The capture/throughput metrics are over a **trailing 24h window**, so two snapshots only 8h apart share ~16h of the same window — diffing them is noise, not progress.

Parse `captured_at` from each snapshot and pick:

- **L** = latest snapshot (the readout's "now").
- **D-1** = the snapshot whose `captured_at` is closest to `L − 24h` (accept ~20–28h back; prefer the 04:30Z daily file). This is the day-over-day baseline.
- **D-7** = the snapshot closest to `L − 7d`, if one exists, for the weekly trend.

Never use "the second file in the list" as the baseline — that is the bug this step exists to prevent.

### 3. The config block lags the running worker — check the RIGHT worker

Each snapshot's `config` block is read from the **env file** at cron time, **not**
from what the running Celery worker has loaded. A floor knob (e.g.
`BATTLE_OBSERVATION_FLOOR_LIMIT`) only takes effect in capture when the worker that
runs the floor restarts — task code reads `os.getenv` but the process env is frozen
at process start. So a snapshot can show `LIMIT=12000` in its config while its
entire 24h data window was captured by a worker still running the old value.

**The floor runs on the `default` queue / `battlestats-celery` worker** (per
`settings.CELERY_TASK_ROUTES`; confirmed live 2026-06-19 — **NOT** `background`,
which runs enrichment / the snapshot engine / warmers). So a floor-config change
takes effect when **`battlestats-celery`** restarts. Check *that* worker — a
stale-doc trap this step exists to prevent: an earlier version checked
`battlestats-celery-background`, the wrong worker, which is what sent the 2026-06-19
investigation down a "slot-starvation" dead end.

```bash
ssh root@battlestats.online 'stat -c "env mtime: %y" /etc/battlestats-server.env; systemctl show battlestats-celery -p ActiveEnterTimestamp'
```

If `ActiveEnterTimestamp` is **before** the env mtime, the config-block value is
**not live** — say so, and treat the labeled snapshot as still running the old
config. The first clean reading under a new value is the first daily snapshot
captured fully *after* the worker restart.

**Floor knobs worth reading from the `config` block** (besides `LIMIT` / `HOURS` /
`CYCLE_MINUTES` and the bulk / change-gate / random-first / ranked-daily flags):
`BATTLE_OBSERVATION_FLOOR_GATE_SKIP_COOLDOWN_HOURS`,
`BATTLE_OBSERVATION_FLOOR_SELF_CHAIN_ENABLED`, and `…_SELF_CHAIN_REALMS` — the
gate-skip cooldown + event-driven self-chain (na pilot 2026-06-19). With self-chain
on for a realm, expect `gated_skipped` to fall cycle-over-cycle as the cooldown
drains the non-mover wall, and the floor to self-chain within a Beat cycle instead
of idling.

**Optional live cross-check — the floor's own instrumentation, on `battlestats-celery`:**
```bash
ssh root@battlestats.online 'journalctl -u battlestats-celery --since "6 hours ago" --no-pager | grep -E "bulk floor done|Floor self-chain" | tail'
```
`bulk floor done realm=… movers=… battles_json_total_ms=… cycle_ms=…` gives per-cycle
wall-time and the per-mover `battles_json`-rebuild share; `Floor self-chain stop` vs a
long run of `Floor self-chain re-dispatched` with no stop (= a spin → investigate).

### 4. Compute and interpret

For **totals** and **each realm** (na / eu / asia), compute L and the Δ vs D-1 (and the trend vs D-7 when available) for:

| field | meaning |
|---|---|
| `active_7d` | denominator — the linchpin; a coverage move can come from this shifting, not from capture |
| `distinct_productive` | distinct players who produced a `BattleEvent` in the window (the real numerator) |
| `coverage_ratio_vs_7d` | **headline** = `distinct_productive / active_7d` |
| `productive_rate` | `distinct_productive / distinct_observed` — of who we polled, how many had battled |
| `fresh_frac` | `fresh_within_24h / active_7d` — share of active players with an obs <24h old |
| `stale_over_24h` | active-7d players whose latest obs is >24h old. **Mostly the change-gate "non-mover wall" — by design, not a backlog:** a non-mover is gate-skipped *without* a fresh observation, so it stays stale until it plays again. A large/steady value is expected (live: ~181k); only read a *rising* one as cadence-falling-behind if `distinct_productive` is also dropping. |
| `obs_bulk_floor` / `obs_poll` | capture-cost split (cheap bulk floor vs per-player poll) |
| `never_observed` | should be ~0; a rise is a signal |
| `gap_1d` | 24h-gap decomposition (added 2026-07-08): active-1d players with **no BattleEvent** in the window, split into `pvp_mover` (snapshot PvP delta > 0; sub-count `pvp_mover_no_event_48h` = no event in 48h either — **capture LATENCY, not loss**: verified 2026-07-11 these players are already in the pipeline and backfill on the next observation; a low-hundreds/day tail is expected, only a sustained RISE is a throughput signal), `non_pvp_active` (account clock moved, PvP battles flat — co-op/Operations, structurally invisible to PvP-only extraction; expected to dominate, NA highest), and `no_snapshot_pair` (unclassifiable). `null` until two snapshot days exist. |

**Ceiling framing (corrects the command's own headline).** The snapshot's built-in HEADLINE says "drive both toward 100%." That is optimistic: `coverage_ratio_vs_7d`'s realistic ceiling is the **daily-active fraction** `active_1d / active_7d` (~25–45%, historically declining), because a player who didn't battle in the window *can't* produce an event. Report cov/7d **both raw and as a % of that ceiling** — the latter is the honest "how close to the achievable max" number. Don't bury the raw `distinct_productive / active_7d` counts under the editorializing.

**Decompose every coverage move.** If cov/7d moved, say *why*: did `distinct_productive` change (real capture shift) or did `active_7d` change (denominator shift)? They imply very different things.

**Other interpretation cues:**
- NA `productive_rate` runs well below EU/ASIA — known, not a regression on its own.
- A jump/drop right after a `config` change (compare the `config` block across snapshots — `LIMIT`, `HOURS`, gate flags) *may* explain a step change — but only if step 3 confirms the worker restarted to apply it. Call the config delta out explicitly.
- Rising `never_observed` while cov is flat → floor cadence falling behind the active set.
- `gap_1d` routing: a dominant `non_pvp_active` means the residual cov/1d gap is a capture-surface question (PvE/Operations invisible to PvP-only `ships/stats`), NOT a floor-throughput deficit — do not recommend cadence/limit raises off it. `pvp_mover_no_event_48h` is a latency tail (players backfill on the next observation), not a loss count; only a **material, sustained RISE** in it — or any rise in `never_observed` — justifies floor tuning. Also discount mid-day snapshots: this bucket is time-of-day inflated (in-flight EU/ASIA), so compare 04:30Z-to-04:30Z. (Rising `stale_over_24h` alone is **not** that signal — it's mostly the change-gate non-mover wall, see the metric note above; only treat it as falling-behind if `distinct_productive` drops too.)

### 5. Report

```
Observation-floor benchmark — battlestats.online
Latest: <L captured_at>   vs   <D-1 captured_at> (Δ24h)   [trend vs <D-7> over 7d]
Config: LIMIT=<…> HOURS=<…> cooldown=<…h> self_chain=<on:realms|off>  <flag if not-yet-live per step 3 — check battlestats-celery (default), not background>

                active7d   productive    cov/7d   (% of ceil)   prodRate   fresh<24h   stale>24h
  na            …          …  (Δ…)        …%       …%            …%         …           …
  eu            …          …  (Δ…)        …%       …%            …%         …           …
  asia          …          …  (Δ…)        …%       …%            …%         …           …
  TOTAL         …          …  (Δ…)        …%       …%            …%         …           …

Capture cost (total, 24h): bulk_floor <…>  /  poll <…>
never_observed: <…>

Read: <one line — what moved, numerator vs denominator, and how much is signal vs noise>
Gap-1d (totals): <N> no-event of <active_1d> — <non_pvp_active> non-PvP, <pvp_mover> movers (<pvp_mover_no_event_48h> at >48h latency, not lost), <no_snapshot_pair> unclassifiable
```

**Verdict discipline — do NOT cry regression off one snapshot.** Day-to-day
variance at a *fixed* config is large (observations have swung 53k↔120k, cov/7d
10%↔18% with no config change), driven by per-realm 6h striping, time-of-day,
clan-crawl coexistence, and off-cycle/partial windows. A single down day is
almost always noise or a transitional/pre-restart window — **not** a regression.
Only call something a real regression when it is **sustained across ≥2–3 clean
daily snapshots** under the same (live) config and the decomposition points to a
genuine capture drop (`distinct_productive` down while `active_7d` is flat). When
the move is within the historical variance band, say "within noise — need N more
clean days," not a verdict. Frame deliberate selection/config changes as
*expected transitions to re-baseline against*, not regressions.

## Scope and limits

- **Read-only.** SSHes, cats JSON, interprets. Never writes the DB, never restarts services, never re-runs the benchmark on the droplet (it serves the *snapshot*, not a fresh run).
- Reports the most recent **nightly snapshot**, not live state. For "right now," run `benchmark_observation_floor` live instead.
- **Observation floor only.** Not enrichment progress (separate WR-gated pipeline — see scope note up top), not live crawler health (`enrichment-status`).
- Background: `agents/runbooks/runbook-bulk-battle-observation-capture-2026-06-06.md` ("Benchmarks" section) — the active floor doc. (The original daily-01:15 floor design, `runbook-battle-observation-floor-2026-05-02.md`, is superseded and now in `archive/`.)
