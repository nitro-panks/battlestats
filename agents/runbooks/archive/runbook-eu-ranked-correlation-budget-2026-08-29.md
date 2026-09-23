# EU ranked correlation: the budget the fan-out exposed (2026-08-29)

**Status:** shipped; **archived 2026-09-23** — its follow-up fired (eu ranked landed above 1000s and was killed at 1080s on 09-23) and the query was replaced, not re-budgeted: `runbook-ranked-correlation-materialized-record-2026-09-23.md`.
**Predecessors:** `runbook-ops-alert-remediation-2026-08-28.md` (the fan-out),
`runbook-correlation-warm-budget-and-per-realm-alerting-2026-08-26.md` (the 780s
sizing and the realm-scoped locks).

This closes the 08-28 follow-up that read *"EU correlation duration is still
unmeasured. After the fan-out lands, the three per-metric durations for EU are
the first honest measurement of what was censored at 900s."* They are now
measured, and one of the three does not fit.

## The alert

```
[battlestats] ops ALERT: warm_player_correlations_task missing on eu; 2 gunicorn timeouts
```

Two conditions, and **neither one names the actual defect.**

| Code | Verdict |
|---|---|
| `celery_task_realm_failing:warships.tasks.warm_player_correlations_task:eu` | True of the window, already self-clearing, not the problem. |
| `gunicorn_worker_timeouts` (2, limit 2) | Deploy artifact. Watch, do not remedy. |

## Condition 1 is an artifact of the deploy, and will not recur

`snapshot_service_health.sh` builds the per-realm success axis by grepping
`Finished <task> realm=<r>` and prefixing `warships.tasks.`. The 24h window ended
11:01 UTC on 08-29 and straddled the v5.6.4 deploy at 04:46:

- `Finished warm_player_correlations_task realm=asia` — 08-28 16:58 → asia 1
- `Finished warm_player_correlations_task realm=na` — 08-29 00:54 → na 1
- eu's only pre-deploy attempt was 08-28 08:46, which raised
  `SoftTimeLimitExceeded` at 09:01 and is outside the window anyway → eu 0

Post-deploy the parent is a **pure dispatcher**: it logs `Starting … realm=<r>`,
enqueues three messages, and returns in ~5ms without ever logging a `Finished`
line — by design, and pinned by
`test_the_dispatcher_emits_no_per_realm_success_line`. So from 08-30 the parent
reads 0/0/0, and `celery_task_realm_failing` cannot trip on it at all: the rule
needs a realm succeeding while another does not.

**The per-realm correlation signal has MOVED, not vanished.** It now lives on the
three subtask names — `warm_player_wr_survival_correlation_task`,
`warm_player_ranked_wr_battles_correlation_task`,
`warm_player_clan_battle_wr_battles_correlation_task` — each of which logs its
own `Finished … realm=<r>`. Read those rows in the digest, not the parent's.
No code changed for this condition.

## Condition 2 is a deploy artifact

Both `[CRITICAL] WORKER TIMEOUT` lines are at **05:01:23 and 05:01:36** — 13
seconds apart, under a gunicorn parent that started at the 04:46 deploy, inside
the 05:00–05:11 cold-cache correlation storm. Two workers dying in the same
quarter-minute is a shared-resource stall, not a slow handler. The digest named
`/api/fetch/clan_data/1000101335:active` and
`/api/fetch/clan_members/1000101335`; that is an attribution heuristic reporting
whoever happened to be browsing, and chasing that clan would have wasted the
investigation. It fired at exactly the boundary (2 of 2). Watch tomorrow.

## The real defect: eu `ranked_wr_battles` does not fit 780s

The 08-26 sizing measured 389–500s per realm — but on a sample where the three
metrics still shared one budget, so it understated the heaviest of them. Split
out by the fan-out, the eu ranked aggregation alone is:

| realm | ranked_wr_battles | wr_survival | clan_battle_wr_battles | tracked pop (ranked) |
|---|---|---|---|---|
| na | 600s | 1.9s | 22s | 57,541 |
| asia | 597s | 1.5s | 33s | 71,908 |
| **eu** | **708s / 757s (successes under the old limit)** | 2.2s | 48s | **102,558** |

eu is the tail because its ranked population is ~1.8× na's. The other two
metrics are nowhere near the limit.

**Those eu figures were censored, and the first uncensored pass proved it.** 708s
and 757s are the runs that fit under 780s; the ones that did not fit were killed
and reported nothing. The first eu pass on the new budget — 08-29 16:06:35, nine
minutes after the worker restarted onto the new release — **succeeded in 851s**.
That is above the old soft limit: under 780s it would have been killed, so this
single run is the decisive test of the change. It also means the honest margin is
**1.27× against the 1080s soft limit, not 1.43×**; treat the table above as a
lower bound on eu, not a measurement of it.

On 08-29 eu ranked soft-limited **six times** — 05:21, 09:00, 13:07, 14:07,
14:39, 14:54 — before completing at 15:08 in 708s. Each killed attempt spent
13 minutes of a 3-slot background pool producing nothing, and the on-view
dispatch path re-queued it every time the fresh key stayed cold: roughly **80
minutes of background occupancy for zero output.** Raising the budget here
*reduces* pool contention rather than adding to it. That is the argument that
makes this defensible against the standing note that the background pool is
contended.

**The digest structurally cannot see this.** `celery_task_realm_failing` is a
"0 successes in 24h" rule. eu had six failures *and one success*, so the correct
code never fired and will not fire on any day eu completes even once. This was
found by hand in the worker journal. A regression will be invisible the same way
— verify by the absence of `SoftTimeLimitExceeded`, never by tomorrow's mail.
Fixing the detector was deliberately **not** bundled here: it is a second change
that would muddy attribution on the budget change.

## The fix, and the constraint that shaped it

```
CORRELATION_METRIC_WARM_TASK_OPTS   780s soft / 840s hard  →  1080s / 1200s
CORRELATION_METRIC_WARM_LOCK_TIMEOUT                        →  1320s (new)
PLAYER_{RANKED,CLAN_BATTLE}_WR_BATTLES_CORRELATION_REFRESH_DISPATCH_TIMEOUT
                                     900s                   →  1200s
```

**The blocking constraint was the lock, not the limit.** `_run_locked_task`'s TTL
is `RESOURCE_TASK_LOCK_TIMEOUT` (900s), shared with ~20 other callers. Raising
the soft limit past it without touching the lock would let the lock lapse
mid-run, and the on-view path at `tasks.py:queue_player_ranked_wr_battles_
correlation_refresh` could start a **second identical 20-minute aggregation** on
the same 3-slot pool — the duplicate-warm class the realm-scoped locks were
introduced on 08-26 to remove. **Soft limit and lock TTL move together, or not at
all.**

So `_run_locked_task` gained an optional `lock_timeout` (defaulting to `None` →
`RESOURCE_TASK_LOCK_TIMEOUT`, leaving every other caller untouched), and the
three correlation metric tasks pass `CORRELATION_METRIC_WARM_LOCK_TIMEOUT`
explicitly. Invariant preserved: **1080 < 1200 ≤ 1320.**

The dispatch dedup keys moved with it for the same reason: they are cleared in
the task's `finally`, so their TTL is only a safety net — but a net shorter than
the run it guards lets a second enqueue land mid-aggregation.

na and asia gain headroom they do not need *today* — but "comfortable slack" would
be generous: na ranked at 600s had only ~1.3x margin against the old 780s limit
and was next in line as its population grows. Widening all three is accepted:
there is no per-realm budget knob, and inventing one for a single tail realm is
more machinery than the problem earns.

### Tests

`warships/tests/test_correlation_warm_budgets.py`, 17 passing:

- `test_each_metric_task_passes_the_longer_lock_ttl` — the load-bearing one; a
  task that forgets the explicit `lock_timeout=` reopens the duplicate-warm hole.
- `test_run_locked_task_defaults_to_the_resource_ttl` — the new parameter did not
  move the default for the other callers.
- `test_on_view_dispatch_dedup_outlives_the_hard_limit`.
- `test_per_metric_budget_fits_under_its_lock_ttl` — comparand changed from
  `RESOURCE_TASK_LOCK_TIMEOUT` to the new constant.
- `test_the_dispatcher_budget_is_not_a_bound_on_the_metrics` — replaces
  `test_combined_budget_exceeds_the_per_metric_budget`, whose ordering became
  false when the per-metric soft limit passed the parent's 900s. Inverting it
  back would silently re-cap the metrics at the dispatcher's number.

## Verification

**Not tomorrow's mail** — the digest cannot see this condition. Read the journal:

```bash
ssh root@battlestats.online 'journalctl -u battlestats-celery-background \
  --since "24 hours ago" --no-pager \
  | grep -E "warm_player_ranked_wr_battles_correlation_task" \
  | grep -E "Finished|SoftTimeLimit"'
```

Expect `Finished warm_player_ranked_wr_battles_correlation_task realm=eu` on each
Beat fire and **no** `SoftTimeLimitExceeded`. A single success is not proof: eu
succeeded once on 08-29 too. The claim is that failures stop, so look at the
ratio across a full day.

**Verified 2026-08-29 16:20 UTC.** The first eu pass after the worker restart
succeeded in **851s** -- above the old 780s limit, so the change is proven by a
run that would previously have been killed. No `SoftTimeLimitExceeded` on any
realm since the deploy (na 434s/653s, asia 452s/385s). No manual dispatch was
needed; the note below is kept because it is the right procedure whenever the
evidence does *not* arrive on its own.

**Absence of failure is only evidence if a run occurred — check the clock before
the journal.** `CORRELATION_WARM_MINUTES=1440` with `base_minute=45` gives stride
480, so the stripe is 8h apart rotating realms: **na 00:45, eu 08:45, asia 16:45
UTC**. eu ranked therefore fires **once per 24h**, and the on-view path will not
fill the gap while the fresh key is still warm. Run the grep at any hour before
08:45 and it reads clean for the trivial reason that no eu pass has happened —
demonstrated twice on 08-29/08-30 before the lesson stuck, now non-obvious read 3
in `runbook-realm-schedule-striping-2026-08-15.md`. Earliest honest proof on a
given day is **~09:00 UTC**. To collapse an 18-hour wait
into a 13-minute one, dispatch the pass by hand — work the task does daily
anyway, and a production lever, so take it one at a time with an ack:

```bash
ssh root@battlestats.online 'cd /opt/battlestats-server/current/server && \
  /opt/battlestats-server/venv/bin/python manage.py shell -c \
  "from warships.tasks import warm_player_ranked_wr_battles_correlation_task as t; \
   print(t.delay(realm=\"eu\"))"'
```

## Follow-ups

- **1080s is ~1.27× the slowest observed success (851s), and every killed run is
  still censored** — the true eu tail remains unknown, and the first uncensored
  pass already came in 94s above the highest figure the old limit could report.
  That margin is thinner than it looked when this was written. If eu ranked
  starts landing above ~1000s, the answer is the query, not another budget raise.
- **The "0 successes in 24h" rule cannot see a task that fails most runs and
  succeeds once.** A ratio-based or duration-based condition would have caught
  eu ranked on 08-27. Scoped to the digest writer, deliberately not bundled here.
- **This change widens the skip window the 08-28 runbook flagged as a watch
  item.** An over-budget eu run used to be killed at 780s, releasing its lock;
  it now holds the lock to 1080s. A concurrent dispatch landing inside that wider
  window (the `startup_warm_caches_task` path that runbook names) returns
  `skipped`, which logs no `Finished … realm=` line, which the digest reads as a
  realm zero. So this fix can produce a **spurious**
  `celery_task_realm_failing:<metric>:<realm>`. Not a defect; the same detector
  surface this runbook spent a day disambiguating, so check for a `Skipping
  warm_player_… because another refresh is already running` line before believing
  one.

- **`gunicorn_error_paths` still invites misattribution** — carried forward
  unchanged from 08-28: reporting the timeout timestamps alongside the paths
  would let a reader see a 13-second cluster without reaching for the journal.
