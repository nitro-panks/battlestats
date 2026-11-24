# Runbook: Startup Cache Warm — A Packed Task That Never Once Completed

_Created: 2026-08-26_
_Context: The ops digest (F4, shipped the same day) fired `celery_task_failing:warships.tasks.startup_warm_caches_task` — 3 `SoftTimeLimitExceeded`, 0 successes in 24h. The 7-day journal shows 4 dispatches, 4 kills, **zero completions ever**._
_QA: Every timing below is read from the production worker journal on 2026-08-26. Read-only throughout; the fix was developed and tested locally._

## Purpose

`startup_warm_caches_task` ran three realms × four warmers — twelve serial
operations — inline under a single 540s soft limit. It has never finished. This
runbook records why the packed form was structurally impossible rather than
marginally slow, why raising the limit is the wrong lever, and what the fan-out
changes.

Read this before touching `startup_warm_caches_task`, and before assuming an
alert on it means a *new* regression. It only fires on gunicorn start, so it
alerts on deploy days and is silent otherwise — the breakage long predates the
alert.

## Findings

### 1. The task never completes — 100% failure rate

Whole retained journal for `battlestats-celery-background` (7 days):

| lifecycle event | count |
|---|---|
| `received` | 4 |
| `Soft time limit (540s) exceeded` | 4 |
| `succeeded` | **0** |

All four kills land at exactly 540s. Runs at 2026-08-24T22:12, 08-25T16:30,
08-26T05:51 and 08-26T06:07 — i.e. one per gunicorn restart, which is the only
thing that dispatches it (`server/gunicorn.conf.py` `when_ready`).

### 2. One realm alone exceeds the whole budget

Instrumented breakdown of the 2026-08-26T06:07 run, from the command's own
`[startup-warmer]` log lines:

| step | elapsed |
|---|---|
| `[asia]` hot entity caches | **268s** |
| `[asia]` bulk load | 5s |
| `[asia]` distributions | 58s |
| `[asia]` correlations | **≥209s, killed mid-flight** |
| `[eu]`, `[na]` | **never started** |

asia consumed the entire 540s on its own. The best run in the whole journal
(08-25T16:41) reached `[eu] Warming hot entity caches...` 13 seconds before it
was killed. **`na` — the default realm, and the one most visitors land on — has
never been warmed by this path.** The loop walked `sorted(VALID_REALMS)`, so
`na` was last in line every single time.

### 3. Raising the limit is the wrong lever

The overshoot is not marginal. Twelve operations at asia's measured rate needs
~1600s+, so the limit would have to roughly triple — and it would pin one of
only three `background` worker slots for 25+ minutes on every deploy, doing work
Celery Beat already performs on its own cadence (see §5). The task's whole
premise is "warm quickly after a restart"; a 25-minute serial crawl is not that.

### 4. A second defect rode along: the inline path bypassed every lock

`startup_warm_all_caches` calls the bare `warm_*` **functions** in
`warships/data.py`. The four per-realm Celery tasks that do the same work each
take a per-realm lock (`_hot_entity_cache_warm_lock_key`, etc.). Calling the
functions directly bypasses those locks entirely, so a startup warm could run on
top of a Beat warm of the same realm — duplicated aggregations against a 2-vCPU
managed Postgres, with nothing to detect it.

### 5. Nothing here was solely load-bearing, which is why it stayed hidden

All four warmers already have their own Beat lanes, striped per realm
(`server/warships/signals.py`): `hot-entity-cache-warmer-<realm>`,
`bulk-entity-cache-loader-<realm>` (12h), `player-distribution-warmer-<realm>`
and `player-correlation-warmer-<realm>` (both `1440` minutes). The deploy script
does **not** flush Redis, so a gunicorn restart leaves the caches warm anyway.

The enrichment kickstart at the tail of the task never ran either — but Beat's
`player-enrichment-kickstart` covers the same ground independently. So the
failure cost duplicated work and 9 minutes of a worker slot per deploy, not a
user-visible outage. That is exactly why it survived undetected until the digest
gained a Celery axis.

### 6. Exact precedent: `warm_realm_top_ships_task`, 2026-08-12

Same defect, same remedy, two weeks earlier — a warmer that walked 15 buckets
under one 540s limit and died after 2–5. See
`runbook-top-ships-warm-soft-limit-2026-08-12.md`, including the lesson from its
**second pass**: leaving *anything* heavy on the orchestrator's own budget makes
it a single point of failure for everything behind it. The orchestrator must
compute nothing at all.

## Implementation (2026-08-26)

`startup_warm_caches_task` is now a pure dispatcher. It fans out one subtask per
(realm, warmer) pair — 12 in total — and returns in milliseconds.

- Each subtask gets its own 540s budget, so one slow warmer costs one warmer
  rather than every warmer behind it.
- Dispatching through the tasks means the **per-realm locks are honoured**: a
  redundant startup warm now skips itself instead of duplicating a live Beat
  warm (§4).
- All four warmers are already routed to `background` in `CELERY_TASK_ROUTES`,
  so the fan-out does not move work onto the request-adjacent `default` pool.
  The `-c 3` cap bounds twelve DB-heavy tasks against the 2-vCPU Postgres.
- Dispatch is **warmer-outer, realm-inner, with `DEFAULT_REALM` first**: every
  realm gets its hot-entity warm before any realm's correlation warm, and `na`
  is no longer last in line (§2).
- `STARTUP_WARM_SPACING_SECONDS` (default 20, env-overridable) staggers arrival,
  mirroring `SHIPS_BUCKET_WARM_SPACING_SECONDS`. **Since 2026-11-23 it applies
  only to the two light warmers** (hot entities, bulk load). The distributions
  and correlations warmers run for minutes each and are spaced by
  `STARTUP_WARM_HEAVY_SPACING_SECONDS` (default 420), after the light ones, so
  realms follow each other; the last dispatch lands about 37 minutes after
  startup. At 20 s the three realms' correlation warmers overlapped and held the
  database at 0% idle for seven minutes:
  `runbook-db-cpu-alerts-steal-and-scan-bursts-2026-11-23.md`, F6.
- A failed dispatch is counted, logged and stepped over, so one broker hiccup
  cannot strand the eleven warmers behind it.
- The task keeps `queue='background'` (pinned by
  `test_task_routing.py::test_startup_cache_warm_task_declares_background_queue`).

`startup_warm_all_caches` is left in place as a manual foreground tool. It is
referenced only from runbooks, never from a deploy script. Its lock-bypass (§4)
now matters only when an operator runs it deliberately.

## Validation

Local: `warships/tests/test_startup_warm_fanout.py`, 5 tests, all four failing
against the old implementation for the right reasons before the change. Full
backend suite **1270 passed, 2 skipped** (1265 + 5 new).

The load-bearing test is `test_orchestrator_computes_nothing_at_all` — the
assertion that would have caught the original defect, and the one the top-ships
precedent had to learn twice.

**Production proof is the journal after the next gunicorn restart.** A unit test
asserting "dispatches 12 subtasks" passes with mocks and proves nothing.

```bash
ssh root@battlestats.online \
  'journalctl -u battlestats-celery-background --since "20 min ago" --no-pager -o short-iso \
     | grep -E "startup_warm_caches_task|Finished warm_|Finished bulk_load"'
```

Expect: `startup_warm_caches_task: {'status': 'completed', 'dispatched': 12, 'failed': 0}`
within seconds of the restart, then `Finished ...` lines naming **`eu` and `na`**,
not just `asia`.

**Expect `eu`'s correlation warm to still soft-limit.** Fan-out gives each warmer
its own 540s; it does not make correlations faster. Measured on the production
journal, 48h to 2026-08-26 — `warm_player_correlations_task` on its own Beat
lanes, which is exactly what the fan-out now dispatches:

| realm | Beat runs | finished | soft-limited |
|---|---|---|---|
| `na` | 2 | **2** | 0 |
| `asia` | 2 | 1 | 1 |
| `eu` | 2 | **0** | **2** |

So 11 of 12 subtasks should complete and `eu`'s correlation warm should not.
That is **the packing defect fixed with a pre-existing, separate defect still
open** — not this fix failing. Do not report the fan-out as fixed on the basis
of the `eu` correlation line, and do not report it as broken either.

Note this is a *different* task from F3's
`warm_player_ranked_wr_battles_correlation_task` (23 soft limits / 29 dispatches
in the same 48h), which the startup path does not dispatch at all. An earlier
draft of this runbook conflated the two; they are separate defects.

## Production verification (2026-08-26, v5.6.1)

Deployed as **v5.6.1**, backend release `20260826103819`, gunicorn restarted
14:39:54 UTC. Outcome — **the prediction above held exactly**:

| | |
|---|---|
| dispatcher result | `{'status': 'completed', 'dispatched': 12, 'failed': 0}` at **14:44:33** |
| subtasks that ran | **12 / 12**, all three realms |
| completed | **11** |
| soft-limited | **1** — `warm_player_correlations_task` on `eu` (14:57:35 → 15:06:35) |

`eu` and `na` were warmed by this path for the first time. The other two
correlation warms completed at **425s** and **497s** — inside the 540s budget
but not comfortably; they are the next candidates to tip over.

**The dispatcher waited 4.5 minutes for a worker slot.** It was dispatched at
14:39:55 and did not execute until 14:44:33: the `background` pool was saturated
within milliseconds of coming up (`3 unacknowledged, 1 ready`), the three slots
taken by `incremental_player_refresh_task`, a Beat `warm_hot_entity_caches_task`,
and `warm_all_clan_tier_distributions_task` grinding 22,252 asia clans. The old
packed version would have waited in the same line and then still failed. Worth
noting that the dispatch is not instant in practice even though the task is.

### Reading this journal correctly — two mis-attributions to avoid

1. **Pair by task id, not by log order.** Three correlation tasks run
   concurrently on three workers, so `Starting realm=X` and `Finished` lines
   interleave and *cannot* be paired positionally. Doing so during this
   verification produced a confident but wrong "eu finished in 72s" reading.
   Grep `warm_player_correlations_task\[<id>` and match `received` to
   `succeeded`.
2. **Do not identify a realm by its `tracked_population`.** The values are close
   enough between realms to invite exactly that error.

Also note a lock-skip logs `Starting ...` *before* it logs `Skipping ...` and
never logs `Finished`, so a skipped run looks like a lost one unless you grep
for `Skipping` too.

## Follow-ups

1. **`warm_player_correlations_task` fails on `eu` every run, and the digest
   cannot see it.** 0/2 in 48h (table above) — while `na` goes 2/2. The ops
   digest threshold is *"a failing task with zero successes in the window"*,
   keyed on the **task name**, so `na`'s successes mask `eu`'s total failure.
   Every per-realm striped task shares this blind spot; the digest's Celery axis
   would need a (task, realm) key to close it. Found while verifying this fix,
   not previously recorded. Degrades to the `:published` durable copy, so the
   symptom is silent staleness on the largest realm.
2. **F3 (deferred, pre-existing):** `warm_player_ranked_wr_battles_correlation_task`
   — a separate task — soft-limited 23 times in 29 dispatches over the same 48h.
   Not dispatched by the startup path. Still the largest single source of
   `background` soft-limit noise.
3. **Is the startup warm still worth its cost at all?** The deploy does not
   flush Redis and Beat covers all four warmers (§5), so this path earns its
   keep only after a genuine cold start (Redis restart or mass eviction). Worth
   an explicit decision rather than inheriting the 2026-03-29 docker-compose
   assumption from `archive/runbook-startup-cache-warming.md`.

## Related

- `runbook-top-ships-warm-soft-limit-2026-08-12.md` — the same defect and remedy
- `runbook-health-sweep-remediation-2026-08-26.md` — F4 (the digest's services
  axis) is what surfaced this; F3 is the open correlation item
- `archive/runbook-startup-cache-warming.md` — the original 2026-03-29 design
- `runbook-celery-queue-strategy.md` — the `background` `-c 3` pool this shares
