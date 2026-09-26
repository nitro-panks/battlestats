# Runbook: Correlation Warm Budgets and Per-Realm Alerting

_Created: 2026-08-26_
_Context: Follow-on tranche from `runbook-startup-warm-fanout-2026-08-26.md`. Verifying that fix surfaced three defects it did not cause: an unrouted correlation task, correlation warmers whose budget is smaller than their measured work, and an ops digest that cannot see a per-realm failure._
_QA: Every timing is read from the production journal 2026-08-25..26. The failure-rate table is read from the live snapshot `2026-08-26_1100Z.json`. Read-only throughout._

## QA Notes

_Reviewed 2026-08-27 against `/home/august/code/battlestats`. 41 assertions checked, 9 corrected._

_**§6, N5 and N6 were added AFTER this review**, from production evidence that arrived the same morning. Their citations were verified inline (`server/warships/tasks.py:2995-2996`) but they have not been through a full QA pass._

### Resolved
- **Roughly every `tasks.py` line citation in this runbook was stale** -> actual: implementing this runbook's own plan added ~22 lines to `server/warships/tasks.py`, moving nearly everything past line 38. `RECAPTURE_TASK_OPTS` 52->74, `RESOURCE_TASK_LOCK_TIMEOUT` 84->106, `CORRELATION_WARM_LOCK_TIMEOUT` 106->128, `CLAN_TIER_DIST_WARM_TASK_OPTS` 1988->2030, `_task_lock_key` 752->361, the `_run_locked_task` skip-return 757->514, the ranked task 1458->1480, the clan-battle task 1477->1507, the combined task 1896->1935, the realm-scoped lock sites 1466/1485->1493/1520, and all six D3 target rows. -> every citation re-resolved by symbol. **The general lesson: a runbook that cites line numbers is falsified by its own implementation, so citations must be re-resolved after the code lands, not before.**
- **N1 said "dispatcher to the three per-metric tasks"** -> actual: only **two** exist, `warm_player_ranked_wr_battles_correlation_task` (`server/warships/tasks.py:1480`) and `warm_player_clan_battle_wr_battles_correlation_task` (`:1507`). All three *data-layer* sub-warmers exist (`server/warships/data.py:3116`, `:3459`, `:3646`) but wr-survival has no task wrapper. -> N1 now requires creating `warm_player_wr_survival_correlation_task` first.
- **The "~1350s cold combined run" was stated as fact** -> actual: it extrapolates the ranked sub-warm's 389-500s to all three. `warm_player_clan_battle_wr_battles_correlation_task` logged **zero runs in 72h** (no Beat lane; on-view dispatch only) and wr-survival has no standalone task, so neither has ever been timed. -> restated as an extrapolation, with a note that N1 rests on the observed 900s kill rather than on this figure.
- **N4's "the limit only truncates an already ordered list" was flagged unverified** -> actual: **verified** — the query ends `.order_by("-battles")[:limit]` (`server/warships/data.py`). -> caveat removed. But QA also found the proposed fix incomplete: slicing the warmed `limit=25` payload serves only `limit <= 25`, while the view clamps to `[5, 50]` (`server/warships/data.py:6589`). -> N4 now specifies warming at the clamp ceiling (50) and slicing down.
- **§5 cache-key claim carried no citations** -> actual: confirmed at `server/warships/data.py:6600` (fresh key) and `:6601` (published key), clamp at `:6589`, and the warmer's hardcoded `limit=25` at `server/warships/tasks.py:1704`. -> citations added.

### Unverified
- The per-ship cost of `compute_ship_pop_avg_damage` (§4). The task's docstring says "SECONDS on popular ships" and one run died at 540s, but neither the per-ship cost nor the size of a post-midnight `missing` set was measured. N3 should size the chunk against a measurement rather than a guess.
- Whether the two `/api/realm/asia/top-ships/?limit=20` hits (§5) came from the frontend or a manual probe. The user-agent is truncated to `c` in the journal line and reads as `curl`. It does not change the defect, only how often it is exercised in practice.
- Step 2's **per-metric** 780s budget. The failed sample was the combined task only; the per-metric lanes are daily per realm and need ~48h for one sample each.

### Open Questions
1. ~~**Does N1 supersede D2's combined-task budget, or sit alongside it?**~~ **ANSWERED 2026-08-27: N1 supersedes it.** The combined task becomes a pure dispatcher, so it holds a slot for milliseconds and `PLAYER_CORRELATIONS_WARM_TASK_OPTS` becomes vestigial. **Deprecate the constant as part of N1** rather than leaving a misleading 900s value in the file — a budget that no longer bounds anything is worse than no budget, because the next reader will size against it. Delete it in the same commit that lands the dispatcher, not before: it is live and load-bearing until then.

## Purpose

Three defects, one theme: **per-realm striped work is failing on one realm and
nothing says so.** This runbook records the measurements, the design for each
fix, and the two items that are decisions rather than code.

Read this before changing any correlation warmer budget, and before assuming
the ops digest's "no alert" means "all realms healthy". It does not.

## Findings

### 1. `warm_player_clan_battle_wr_battles_correlation_task` is unrouted

`CELERY_TASK_ROUTES` (`server/battlestats/settings.py:310`) routes its sibling
`warm_player_ranked_wr_battles_correlation_task` to `background` (line 343) but
has **no entry** for `warm_player_clan_battle_wr_battles_correlation_task`
(`server/warships/tasks.py:1507`). With `CELERY_TASK_DEFAULT_QUEUE = 'default'`
(`settings.py:309`) it lands on `default` — the request-adjacent lane shared
with crawl dispatchers and watchdogs.

This is the identical defect class the project already fixed and pinned in
`test_ship_standings_warm_chain_routes_to_background`: three warmers were
unrouted, landed on `default`, and on 2026-07-13 a warm chain sat
received-but-unexecuted for 3.5h. `background` is the designed home for warmers.

**Risk:** a ~400s population aggregation competing with request-adjacent work.

### 2. The correlation warmers' budget is smaller than their measured work

`warm_player_ranked_wr_battles_correlation_task` warms **one** correlation and
nothing else. It carries `TASK_OPTS` — 540s soft / 600s hard
(`server/warships/tasks.py:24-28`). Measured on the production journal over 72h
to 2026-08-26:

| realm | succeeded | duration when it succeeded |
|---|---|---|
| `eu` | 1 / 8 | 468s |
| `asia` | 1 / 3 | 389s |
| `na` | 2 / 4 | 429s, 500s |

**389–500s of work against a 540s limit, on every realm.** This is not an `eu`
problem and not a packing problem — the budget is mis-sized for irreducible
work, and it tips over on roughly two thirds of runs everywhere.

**This is the opposite call from the startup warm fan-out, deliberately.**
There, twelve *separable* operations totalling ~1600s were packed into one
budget, so splitting was available and raising the limit would have pinned a
worker for 25 minutes. Here it is a single aggregation that legitimately costs
~450s. The codebase already sizes budgets to such work rather than splitting it:
`SHIP_PCT_WARM_TASK_OPTS` (30m/27m, `tasks.py:38`), `RECAPTURE_TASK_OPTS`
(16m/15m, `tasks.py:74`) and `CLAN_TIER_DIST_WARM_TASK_OPTS` (3h/2h45m,
`tasks.py:2030`) all exist for exactly this reason.

**The binding constraint is the lock TTL, not taste.** The documented invariant
(`tasks.py` `_reclassify_budget_seconds`, and the `RECAPTURE_TASK_OPTS` note) is:

```
soft_time_limit < time_limit <= lock TTL
```

- The two per-metric tasks lock through `_run_locked_task`, whose TTL is
  `RESOURCE_TASK_LOCK_TIMEOUT = 15 * 60` (900s, `tasks.py:106`).
- The combined `warm_player_correlations_task` (`tasks.py:1935`) locks with
  `CORRELATION_WARM_LOCK_TIMEOUT = 20 * 60` (1200s, `tasks.py:128`).

So the budgets must fit *under* those, or a slow run loses its lock mid-pass and
a second invocation starts on top of it — the failure mode already pinned by
`test_lock_outlives_the_hard_time_limit`.

### 3. The ops digest cannot see a per-realm failure

The digest's Celery axis (`server/scripts/daily_ops_email.py:608-633`) alerts
when a task has failures **and zero successes**, keyed on the **task name**. The
snapshot writer (`server/scripts/snapshot_service_health.sh:65-81`) tallies per
`(unit, task, exception)` from `raised unexpected` lines and counts successes
per task.

Every per-realm striped task therefore has a blind spot: one realm failing on
**every** run is masked by the other realms' successes. Live snapshot
`2026-08-26_1100Z.json`, 24h window:

| fail | ok | success rate | task |
|---|---|---|---|
| 10 | 3 | 23.1% | `warm_player_ranked_wr_battles_correlation_task` |
| 3 | 0 | 0% | `startup_warm_caches_task` (alerted; fixed in v5.6.1) |
| 1 | 0 | 0% | `roll_up_player_daily_ship_stats_task` (alerted; stale) |
| 1 | 2 | **66.7%** | `warm_player_correlations_task` |

That last row **is** the blind spot: `eu` fails every run, `na` and `asia`
succeed, and 2-of-3 reads as healthy.

**A second, independent masking mechanism (found in QA): a lock-skip counts as a
success.** `_run_locked_task` returns `{"status": "skipped", "reason":
"already-running"}` when the lock is held (`tasks.py:514`); Celery logs that as
`succeeded in ...`, and the writer counts every `succeeded in` line
(`snapshot_service_health.sh:69-72`). So `succeeded` means *did not raise*, not
*did work* — a task that only ever skips reads as perfectly healthy. The 3
successes in the table above are therefore an upper bound.

**A failure-rate threshold does not fix this, and I checked before assuming it
would.** Any threshold low enough to stay quiet on genuinely flaky warmers
(<50%) is also quiet on 66.7%, which is precisely the signature of one realm of
three failing every time. Rate is the wrong axis; realm is the right one.

**Realm cannot be recovered from the current journal format.** `Starting <task>
realm=X` is the task's own `logger.info` and carries **no task id**; only
Celery's `received`/`succeeded`/`Soft time limit` lines carry ids. Worse, the
soft-limit line is emitted by `MainProcess`, not the `ForkPoolWorker` that
logged `Starting`, so even worker-affinity pairing breaks for exactly the case
that matters. Pairing these positionally produced a confidently wrong reading
during the v5.6.1 verification; see that runbook's "Reading this journal
correctly".

### 4. The request-driven avg-damage warm packs an unbounded ship loop

`warm_ship_pop_avg_damage_task` (`server/warships/tasks.py:646`) soft-limited once
in the 24h to 2026-08-27 with **zero successes**, so it trips the digest's
zero-success rule. Received 00:37:31, killed 00:46:31 — the full 540s.

**Do not confuse it with its healthy sibling.** The *nightly bulk* warmer
`warm_all_ship_pop_avg_damage_task` (`:670`) completed on all three realms the
same night: eu 00:30:00→00:30:52 (52s), asia 00:55:32→00:56:40 (68s), na
02:40:39→02:41:33 (54s). The bulk warm is fine; the **request-driven lazy** warm
is what fails.

The mechanism is the same packing shape as §2 and as the startup warm: the task
walks `for ship_id in missing: compute_ship_pop_avg_damage(realm, ship_id)`
serially under one `TASK_OPTS` budget, with no bound on `len(missing)` and no
budget check between ships. Its own docstring notes the per-ship aggregate
"takes SECONDS on popular ships".

**Why it fires just after midnight.** The baseline cache is day-scoped, so all
~900 per-ship keys rotate cold at UTC midnight, and the bulk warmer is striped
per realm (00:30 eu, 00:55 asia, 02:40 na). A viewer landing in the gap between
midnight and their realm's bulk warm finds *every* ship cold, so one treemap
view queues a lazy warm over a large `missing` set. For `na` that gap is 2h40m.

### 5. `/api/realm/asia/top-ships/` 500s on any non-default `limit`

Both gunicorn worker timeouts in the 24h window are the same endpoint and the
same shape, on 2026-08-26:

```
17:23:01  GET /api/realm/asia/top-ships/?limit=20   500 0     <- empty body
17:23:07  GET /api/realm/asia/top-ships/            200 2530
17:23:34  GET /api/realm/asia/top-ships/?limit=20   500 0
17:23:56  GET /api/realm/asia/top-ships/            200 2530
```

The bare endpoint answers in the same second the parameterised one dies. The
cause is in the cache key: `compute_realm_top_ships` (`server/warships/data.py`)
builds **both** keys with the limit baked in —
`top-ships:{mode}:win{window_end}:{limit}` and
`top-ships:published:{mode}:{limit}` — while the warmer
`warm_top_ships_treemap_task` (`server/warships/tasks.py`) hardcodes
`limit=25`.

So **only `limit=25` is ever warm.** The view clamps to `max(5, min(limit, 50))`,
so the other 45 values in that range miss the fresh key *and* the durable
`:published` fallback, and fall through to the live aggregation on the request
thread. That is a direct breach of the load-bearing rule in CLAUDE.md: no
request-thread blocking on a heavy DB aggregation. It blows the 25s gunicorn
timeout and returns a 500 with an empty body.

Note the parameter is unauthenticated and attacker-controlled: any caller can
pick a limit other than 25 and force the aggregation. This is mild denial-of-
service surface, not merely a latency bug, which is why it is worth fixing even
though the observed hits came from a `curl` user-agent rather than the frontend.

### 6. A truncated rollup reads as a SUCCESS to the digest

Observed 2026-08-27, the first run of the F2 rewrite (v5.6.0) to actually
execute. Verified against `server/warships/tasks.py:2995-2996`.

```
04:47:18  Starting roll_up... window=2026-08-24..2026-08-26 (3 days)
04:56:18  Soft time limit (540s) exceeded
04:56:18  TRUNCATED on 2026-08-24 by ProgrammingError: days_rebuilt=0 of 3
04:56:18  succeeded in 540.1s: {'status': 'partial', 'days_rebuilt': 0, ...}
```

**Two opposite results in one run.**

*The F2 handler works.* It caught `ProgrammingError` — not
`SoftTimeLimitExceeded` — which is exactly the substitution its comment predicts:
the soft limit fires inside `rebuild_daily_ship_stats_for_date`'s atomic block
and `atomic.__exit__` replaces the in-flight exception before the handler sees
it. Catching `DatabaseError` alongside the soft-limit class is what made this
survivable rather than a crash. That half is proven in production.

*The performance half is not.* `days_rebuilt=0 of 3` — it did not finish even the
first day in 540s, against F2's premise that the Postgres-side aggregation makes
a day cost ~2.34s.

**Heavy confounder, stated plainly:** this run started 17 minutes late and
executed against a saturated pool — three correlation warms holding all three
`-c 3` slots and a 10-deep queue, on a 2-vCPU managed Postgres, a state caused by
the 04:26 deploy. One run under those conditions does **not** establish that the
F2 rewrite is too slow. It needs a clean night.

**The finding that does not depend on the confounder:** the task now returns
`status: partial` and **succeeds**. Celery logs `succeeded in 540.1s`, so the
digest's Celery axis sees a success and stays silent — a rollup that rebuilt
**zero of three days** is indistinguishable from a healthy one. This is the same
class as the lock-skip flaw in §3: the digest measures *did not raise*, not *did
work*. Before F2 the failure was loud and wrong; now it is quiet and wrong.

## Decisions

### D1 — Route the clan-battle correlation task to `background`

One entry in `CELERY_TASK_ROUTES`, beside its sibling. Pinned by a test in
`server/warships/tests/test_task_routing.py` asserting **both** correlation
tasks route to `background`, so the pair cannot drift again.

### D2 — Size the correlation budgets to the measured work, under the lock TTL

Two new constants in `server/warships/tasks.py`, each respecting its own lock:

| constant | soft | hard | lock TTL | applies to |
|---|---|---|---|---|
| `CORRELATION_METRIC_WARM_TASK_OPTS` | 780s (13m) | 840s (14m) | 900s | `warm_player_ranked_wr_battles_correlation_task`, `warm_player_clan_battle_wr_battles_correlation_task` |
| `PLAYER_CORRELATIONS_WARM_TASK_OPTS` | 900s (15m) | 1020s (17m) | 1200s | `warm_player_correlations_task` |

780s gives ~1.56x headroom over the worst measured success (500s). The combined
task runs all three correlations serially, so it gets more.

**Not chosen:** fanning `warm_player_correlations_task` out into three
sub-warmers. That was my first instinct, from the fan-out shipped hours earlier.
The measurement in §2 refutes it — ranked alone needs ~450s, so a fan-out
relocates the failure instead of removing it. Recorded because the same wrong
instinct will recur.

> **SUPERSEDED 2026-08-28 — the fan-out shipped, and this note is why it took
> two days.** The objection above was sound *against the budget that existed
> when it was written*: with `TASK_OPTS`' 540s per-metric soft limit, splitting
> a 450s metric out really does relocate the failure. **D2 itself removed that
> premise** by raising the per-metric budget to 780s, ~1.56x the worst measured
> success — at which point each metric fits on its own and only the *combined*
> task does not. D2 gave the combined warmer 900s for three components budgeted
> at 780s each, i.e. 1.15x the budget of one of its own parts, and EU
> soft-limited on every run from 08-27.
>
> The lesson worth keeping is narrower than "fan-out was wrong": **a decision
> recorded against one set of constants does not survive a change to those
> constants made in the same commit.** Re-read a "not chosen" note against the
> numbers as they end up, not as they were when the note was drafted.
> Implementation: `agents/runbooks/runbook-ops-alert-remediation-2026-08-28.md`.

**Deliberately not done:** raising `RESOURCE_TASK_LOCK_TIMEOUT`. It is shared by
many unrelated tasks; the budgets fit under the existing TTLs.

**BLOCKING CONSTRAINT found in QA — the per-metric lock is not realm-scoped.**
Both per-metric tasks call `_run_locked_task(<name>, "population", ...)`
(`tasks.py:1493`, `:1520`), and `_task_lock_key` (`tasks.py:361`) builds
`warships:tasks:<name>:<resource_id>:lock` from that literal `"population"` —
**the same key for every realm**. The ranked and clan-battle correlation warms
are therefore globally serialized, unlike the combined task, which keys on realm.

Raising the hard limit to 840s against the 900s TTL means one realm can hold that
lock for nearly the entire TTL, so the other two realms skip — and skips count as
successes (§3), making the digest *quieter* while coverage gets *worse*. This
budget change must not ship on its own. See Open Question 1.

### D3 — Attribute successes per realm, and alert on a realm that never succeeds

Invert the problem. Do **not** try to attribute *failures* to a realm — that
means parsing exception paths, and the `SoftTimeLimitExceeded` handler is
exactly where the 2026-08-26 F2 trap lived (an atomic unwind substitutes a
different exception before the handler runs). Attribute **successes** instead,
which is a pure success-path change with no exception-handling risk.

1. **`server/warships/tasks.py`** — emit the realm on the success path of each
   per-realm warmer. Success path only; no `except` clause is touched. Targets,
   enumerated in QA:

   | line | task | action |
   |---|---|---|
   | `:1927` | `warm_player_distributions_task` | amend existing `Finished` line |
   | `:1949` | `warm_player_correlations_task` | amend existing `Finished` line |
   | `:1982` | `warm_hot_entity_caches_task` | amend existing `Finished` line |
   | `:2003` | `bulk_load_entity_caches_task` | amend existing `Finished` line |
   | `:1499` | `warm_player_ranked_wr_battles_correlation_task` | **add** — it `return`s `_run_locked_task(...)` and logs no completion |
   | `:1526` | `warm_player_clan_battle_wr_battles_correlation_task` | **add** — same |

   `warm_recently_viewed_players_task` (`:2023`) is excluded: its Beat entry was
   removed 2026-06-20 (`signals.py`).

   A skip never reaches these lines, so per-realm success counts built from them
   correctly exclude lock-skips — which is exactly the flaw §3 records in the
   existing `succeeded` count.
2. **`server/scripts/snapshot_service_health.sh`** — collect
   `Finished <task> realm=<r>` into a `celery_realm_successes` array of
   `{task, realm, count}`. It must reuse the existing per-unit sweep rather than
   adding a third `journalctl` call inside the unit loop: the writer already
   makes 5 invocations textually (`:51,67,69,86,88`), two of them inside a loop
   over 6 units, and already costs ~90s CPU per run.
3. **`server/scripts/daily_ops_email.py`** — new condition
   `celery_task_realm_failing:<task>:<realm>`: a task that succeeded for at
   least one realm but has **zero** successes for another realm in the window.
   Requiring at least one success elsewhere is what keeps this from
   double-reporting a task the existing zero-success rule already caught.

**Known limitation, stated rather than hidden:** this detects a realm that never
succeeds. It cannot distinguish "failed" from "never dispatched" — both look
like zero successes. That is acceptable, and arguably correct: a striped task
that stopped being dispatched for one realm is also worth an alert.

**Enrolled 2026-09-26: `snapshot_ship_top_players_task`.** The EU run takes
279-502s against a 540s soft limit (NA/asia ~60s) and overran on 2026-09-24;
NA and asia successes hid it on the task-name axis. It now logs
`Finished snapshot_ship_top_players_task realm=<r>` on a completed run only.
Timing caveat: the asia stripe fires 10:30 UTC and the writer at 11:00, so an
asia run delayed more than ~30 min by queue contention reads as a zero for that
realm on that morning.

## Implementation plan

Ordered smallest-risk first; each step independently shippable and verifiable.

**Step 1 — D1, the routing fix.** Add the route; extend the routing test to
assert both correlation tasks land on `background`. Behaviour-free otherwise.

**Step 2 — D2, the budgets.** **Realm-scope the per-metric lock first** (Open Question 1, answered: pass `realm`
as `resource_id` at `tasks.py:1493` and `:1520`), then add the two constants,
apply them to the three task decorators. Tests must pin the lock invariant (`soft < hard <= lock TTL`)
for both new constants, mirroring `test_lock_outlives_the_hard_time_limit`. A
test that only asserts the numbers is worthless; the invariant is the contract.

**Step 3 — D3, per-realm alerting.** Success-path log line, writer field,
evaluator condition. The evaluator change needs unit tests in
`server/warships/tests/test_daily_ops_email.py`, which already has the fixture
shape for this (see its `celery_task_failing:` tests around line 746).

## Implementation (2026-08-26)

All three steps built; **not yet deployed** at time of writing.

**Step 1** — `warm_player_clan_battle_wr_battles_correlation_task` routed to
`background` in `server/battlestats/settings.py`, beside its ranked sibling.
Pinned as a pair by `CorrelationWarmRoutingTests`.

**Step 2** — `CORRELATION_METRIC_WARM_TASK_OPTS` (780s soft / 840s hard) and
`PLAYER_CORRELATIONS_WARM_TASK_OPTS` (900s / 1020s) added and applied to the
three tasks. The `"population"` lock scope became `realm` at both call sites, so
the per-metric warms no longer serialize across realms. Tests pin the lock
invariant rather than the numbers, plus a line-number guard that fails if a lock
scoped to the literal `"population"` ever returns.

**Step 3** — the realm now appears on the success path of six tasks: amended on
the four that already logged a completion, **added** on the two per-metric tasks
that logged none. The added line is explicitly gated on
`result.get("status") != "skipped"`, so a lock-skip is never counted as a warm —
the exact flaw §3 records in the existing `succeeded` count.
`snapshot_service_health.sh` emits `celery_realm_successes`;
`daily_ops_email.py` gains `celery_task_realm_failing:<task>:<realm>`.

**One QA constraint was violated and then fixed:** the first cut of the writer
added a *third* `journalctl` call inside the per-unit loop, which this runbook's
own QA notes forbid. It now folds both success tallies into a single sweep using
`-g 'succeeded in|Finished [a-z_0-9]+ realm='`, holding the writer at 5
invocations. Verified on prod: the alternation returns an identical
`succeeded in` count to the plain pattern (162 = 162 over 6h), so the existing
tally is unchanged.

A second defect was caught in review and fixed: the evaluator keyed on
`(task, realm)` while the writer tallies per `(unit, task, realm)`, and it
*assigned* rather than accumulated — so a task seen under two units let a zero
row erase a healthy count and invent an alert. Each task lands on one unit
today, which is precisely why it needed a fixture rather than trust.

Backend suite **1284 passed, 2 skipped** (1270 + 14 new). Writer passes `bash -n`,
and its parse was exercised against both real line shapes plus a skip line and a
realm-less line, which it correctly ignores.

## Validation

- Backend suite green (`DJANGO_SECRET_KEY=k DB_ENGINE=sqlite3 pytest
  warships/tests/ --nomigrations`).
- **Step 1** is proven by the route test, not by production.
- **Step 2** is proven in production: `warm_player_ranked_wr_battles_correlation_task`
  should stop soft-limiting. Its Beat lanes run daily per realm, so a fair read
  needs **~48h** after deploy. Expect success durations to stay 389–500s — the
  fix removes the kill, not the cost.
- **Expect one day-one false positive on Step 3 and do not chase it.** The
  window is 24h but `Finished ... realm=` lines only exist post-deploy, so the
  first run straddles the boundary: a realm whose lane fell *before* the deploy
  reads as zero successes while the others read as one. The "at least one
  succeeding realm" guard covers the all-zero case, not this partial one. It
  self-corrects within 24h.
- **Watch for the serialization that was removed.** The global `"population"`
  lock was accidentally preventing two realms from warming the same metric at
  once. That is gone by design. The exposure is narrower than it looks — the
  combined task already keyed on realm, and v5.6.1's fan-out ran concurrent
  cross-realm correlation work successfully (425s, 497s) — but the per-metric
  Beat lanes and the on-view dispatch sites (`tasks.py:987`, `:1010`) can now
  overlap too. Spacing will not help (20s of stagger against ~450s of work), so
  do not add any: watch `background` queue depth and Postgres load instead.
  Three of these holding all three slots for 780s is the signal.
- **Step 2 needs ~48h, not one night.** The per-metric lanes run daily per
  realm, so a single night is one sample per realm.
- **Step 3** is proven by dry-running the digest **as the unprivileged user**,
  which is the only way that proves anything (running it as root was the trap
  recorded in the 2026-08-26 sweep's F4):
  ```bash
  sudo -u battlestats -E /usr/bin/python3 scripts/daily_ops_email.py --dry-run --no-llm
  ```
  from `/opt/battlestats-server/current/server`. Expect a
  `celery_task_realm_failing:...:eu` line for the correlation warmers until
  Step 2's effect lands.

## Production results (2026-08-27, v5.6.2)

Deployed 04:26 UTC, backend release `20260827002439`.

**Step 1 — routing.** Proven by test, nothing to observe in production.

**Step 3 — per-realm alerting: VERIFIED end to end.**
- Nine realm-tagged success lines emitted (`bulk_load_entity_caches_task`,
  `warm_hot_entity_caches_task`, `warm_player_distributions_task` × na/eu/asia).
- `celery_realm_successes` present and correctly populated in
  `shared/benchmarks/service-health/2026-08-27_0436Z.json`.
- Digest dry-run **as the `battlestats` user** reads the field cleanly and
  correctly raises no per-realm condition, because all three realms succeeded.
- Writer cost **80s**, against the ~90s baseline — folding the realm tally into
  the existing sweep held the budget.
- The predicted day-one false positive **did not occur**: the startup fan-out
  warms all three realms together, so no realm straddled the deploy boundary.

**Step 2 — budgets: PARTIAL FAILURE on the first sample. Not resolved.**

The three combined `warm_player_correlations_task` runs dispatched by the
post-deploy fan-out started 04:32:08, 04:32:17 and 04:37:26. Two were killed by
`Soft time limit (900s) exceeded` at **04:47:08 and 04:47:17** — the *new* limit,
at exactly 900s. Zero completed.

**Why the sizing was wrong, and it is instructive.** 900s was derived from the
only combined runs that had ever succeeded (425s and 497s). Those are
survivorship-biased: they succeeded *because* their sub-caches were already warm
from the per-metric Beat lanes. A genuinely cold combined run is plausibly around
**~1350s** — three correlations at roughly the ranked one's measured cost — which
no budget under the 1200s `CORRELATION_WARM_LOCK_TIMEOUT` could accommodate.

**That figure is an extrapolation, not a measurement, and QA flags it as such.**
Only the *ranked* sub-warm has ever been timed standalone (389-500s).
`warm_player_clan_battle_wr_battles_correlation_task` logged **zero runs** in 72h
— it has no Beat lane and fires only on view — and no standalone task exists for
wr-survival at all, so neither has ever been measured on its own. What IS
measured and sufficient to refute the 900s budget: two of three combined runs
were killed at exactly 900s. N1 does not depend on the ~1350s figure; it depends
on the kill, and on each sub-warm fitting the 780s per-metric budget. The test comment
`test_per_metric_budget_clears_the_measured_worst_case` names this exact hazard
("every killed run is censored at 540s, so the true tail is unknown") and the
sizing still walked into it.

**The per-metric budget (780s) is NOT implicated by this sample** — these were
the combined task. It remains unmeasured and still needs ~48h.

**Queue interaction observed.** The nightly `roll_up` was dispatched by Beat at
04:30:00 and did not start until **04:47:18** — a 17-minute wait behind three
correlation warms holding all three `-c 3` slots, on a queue that peaked at 10
messages. Deploying shortly before 04:30 UTC therefore delays the nightly
rollup. Note `soft_time_limit` is a ceiling, not a reservation — a slot frees the
moment the task returns — so this cost falls only on runs that would previously
have been *killed*, and those were already burning 540 slot-seconds for nothing.

## Next steps

Ordered by evidence strength. **Nothing below is implemented.**

**N1 — Re-fan-out the combined correlation task (supersedes part of D2).**
The 900s budget is refuted (see Production results). The combined task should
become a pure dispatcher to per-metric tasks after all.

**Only TWO per-metric tasks exist**, not three:
`warm_player_ranked_wr_battles_correlation_task` (`tasks.py:1480`) and
`warm_player_clan_battle_wr_battles_correlation_task` (`tasks.py:1507`). All
three data-layer sub-warmers exist — `warm_player_wr_survival_correlation`
(`data.py:3116`), `..._ranked_wr_battles_...` (`data.py:3459`),
`..._clan_battle_wr_battles_...` (`data.py:3646`) — but the wr-survival one has
no task wrapper. N1 must **create** `warm_player_wr_survival_correlation_task`,
route it to `background`, give it `CORRELATION_METRIC_WARM_TASK_OPTS`, and lock
it per realm, before the dispatcher has three targets to fan out to.

This is the design D2 explicitly rejected, and the reason it was rejected has
since been removed. The objection was that the ranked correlation alone needs
~450s against a 540s budget, so fan-out would merely relocate the failure — true
at the time. The per-metric budget is now **780s** (§D2) and the per-metric locks
are realm-scoped, so each sub-warm has ~1.7x the headroom it needs and no longer
contends across realms. Fan-out now removes the failure instead of moving it.

A dispatcher also escapes the 1200s lock ceiling entirely, which no single
budget can: ~1350s of cold work cannot fit under a 1200s TTL.

**Deprecate `PLAYER_CORRELATIONS_WARM_TASK_OPTS` in the same commit** (Open
Question 1, answered). Once the combined task computes nothing, its 900s budget
bounds nothing — and this runbook is itself the record of what happens when
someone sizes against a stale number. The constant is live until the dispatcher
lands, so remove it *with* that change, not ahead of it. The per-metric
`CORRELATION_METRIC_WARM_TASK_OPTS` stays: it is what the fan-out targets rely on.

**N2 — Confirm the per-metric budget over ~48h.** Untouched by the failed
sample. One night is a single sample per realm; do not call it either way sooner.

**N3 — Bound the avg-damage lazy warm (§4).** Chunk `missing` across dispatches
or check the budget between ships, so one post-midnight viewer cannot queue an
unbounded serial walk. Same remedy family as the startup fan-out.

**N4 — Warm the top-ships payload for every servable limit, or drop limit from
the cache key (§5).** The slice claim is **verified**: the query ends
`.order_by("-battles")[:limit]`, so the limit is a pure truncation of one ordered
list and the top-N of a longer warmed payload is exactly the warmed top-N.

But slicing the `limit=25` payload only serves requests for **limit <= 25**. The
view clamps to `[5, 50]` (`data.py:6589`), so 26-50 would still miss. The
complete fix is therefore to warm at the clamp ceiling (`limit=50`) and slice
down to any requested limit, which covers the whole servable range with one
warmed key per (realm, mode) instead of 46.

**N5 — Alert on a rollup that rebuilds nothing (§6).** The digest cannot see
`days_rebuilt=0` because the task succeeds. The benchmark family
`check_battle_history_rollup.sh` already exists in `server/scripts/`; check
whether it covers this before adding an axis. Do not fix by making the task
raise again — the whole point of F2 was that truncation is survivable; the gap is
in what the digest reads, not in the task's behaviour.

**N6 — Re-read the rollup on a clean night (§6).** Its 04:30 slot collided with a
deploy-triggered warm storm. Establish whether the F2 aggregation is genuinely
too slow before treating it as a performance defect.

## Follow-ups (not code)

1. **The `background` pool saturates on worker startup.** During the v5.6.1
   deploy the startup dispatcher waited **4.5 minutes** for one of three slots,
   queued behind `warm_all_clan_tier_distributions_task` grinding 22,252 asia
   clans. Worth deciding whether that task belongs on `background` at all, or
   whether the pool needs a fourth slot. Sizing: `ops-infra-resources.md`.
2. **Should the startup warm exist at all?** The deploy does not flush Redis and
   all four warmers have their own Beat lanes, so it earns its keep only after a
   genuine cold start. Inherited from a 2026-03-29 docker-compose assumption
   (`archive/runbook-startup-cache-warming.md`); never revisited.

## Related

- `runbook-startup-warm-fanout-2026-08-26.md` — the tranche this follows
- `runbook-top-ships-warm-soft-limit-2026-08-12.md` — where the fan-out remedy
  is right, and the lock-outlives-limit invariant
- `runbook-health-sweep-remediation-2026-08-26.md` — F3 originates here; F4
  built the digest axis this extends
- `runbook-celery-queue-strategy.md` — the queue map D1 restores
