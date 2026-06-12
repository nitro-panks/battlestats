# Runbook — Crawls Queue Depth Alarm (`queue:crawls depth>5`) — 2026-06-12

**Status:** IMPLEMENTED (2026-06-12) — **Options A + B applied.**
- **A:** `scripts/healthcheck.sh` crawls threshold raised `5 → 15` with an updated comment.
- **B:** per-realm enqueue dedup so the queue idles near zero — the daily Beat cron now fires a
  lightweight `dispatch_clan_crawl_task` (on `default`) that enqueues `crawl_all_clans_task` only if
  one isn't already running/queued for the realm; a Redis pending flag (cleared at task start)
  enforces "at most one per realm in flight", and the watchdog clears a stale flag if the broker
  dropped the queued message. Options C/D (cadence change) remain optional and are not implemented.

**TL;DR:** The chronic `healthcheck.sh` failure `FAIL queue:crawls depth=N > 5` is a **false alarm
from a too-tight threshold**, not a stuck worker or a regression. Post-fix steady-state for the
crawls queue is **6–8 messages**; the threshold is **5**. The standing 6–8 are duplicate
`crawl_all_clans_task` enqueues (daily per-realm cron + 5-min watchdog) parked behind the one
legitimately long-running crawl pass on the single `-c 1` worker. The worker is healthy and
actively crawling. The real cost is **alarm fatigue**: because crawls fails on every 10-min run,
`healthcheck.sh` always exits 1, which buries genuine failures.

## Symptom

- `scripts/healthcheck.sh` logs `FAIL queue:crawls depth=7 > 5` (or 6/8) on essentially every run.
- **288 crawls FAILs in the Jun 10–12 window** — one per 10-min healthcheck, i.e. continuous.
- Because any failed check makes `healthcheck.sh` exit 1, the script is *always* red, even when
  every web/API endpoint is healthy.

## Observed state (2026-06-12, captured during the investigation)

```
$ rabbitmqctl list_queues name messages messages_ready messages_unacknowledged consumers
crawls   7   6   1   1
```

- **1 unacked / 1 consumer:** the crawls worker is alive and running one crawl. Worker log advances
  steadily (`Processed NNNN clans … saved …`, ~25 items / 30s), heartbeat fresh. Systemd:
  `battlestats-celery-crawls.service` active, `-c 1 --prefetch-multiplier=1
  --max-tasks-per-child=1 --time-limit=1209600` (14-day limit), `acks_late`.
- **6 ready (the backlog):** peeking the queue (non-destructively) shows **all six are
  `warships.tasks.crawl_all_clans_task`**, `eta=None`, `retries=0` — i.e. plain duplicate crawl
  dispatches, not retries and not a poisoned/zombie message.

## Root cause

Two layered causes; the first is the operative one for the alarm.

### 1. Threshold miscalibration (the alarm itself)

`scripts/healthcheck.sh` (`check_queue_depth "crawls" 5`, ~L247) asserts depth ≤ 5 with the comment
"Steady-state … above 5 means the crawl is queueing instead of running — investigate." That
assumption is **stale**. Post-fix (see history) the *normal* steady-state is **6–8** — the threshold
sits just under it, so the check fails forever.

Recent distribution (Jun 10–12): `depth=7` ×125, `depth=6` ×93, `depth=8` ×69. No spikes.

### 2. Un-deduplicated crawl enqueues (why the standing backlog is ~6, not 0)

`crawl_all_clans_task` is enqueued from two periodic families (`warships/signals.py`):

| Periodic task | Cadence | Effect |
|---|---|---|
| `daily-clan-crawl-{realm}` (cron) | once/day **per realm** → ~3/day | Enqueues `crawl_all_clans_task` regardless of whether a pass is already running. |
| `clan-crawl-watchdog-{realm}` → `ensure_crawl_all_clans_running_task` | every `CLAN_CRAWL_WATCHDOG_MINUTES` (5) | Re-dispatches `crawl_all_clans_task` **only** on a stale lock (`tasks.py:1438`); a fresh heartbeat → no-op. |

A full pass takes **~12–18h** (measured 2026-06-12: a fresh NA pass at 00:38 was ~25% through
35,640 items by ~05:00). NOTE: the `tasks.py:1363` "~14 days" comment is **stale** — it predates the
R2 `core_only` optimization (`tasks.py:1330-1336`) that gutted ~85% of the per-clan WG cost. Even at
~12–18h, the queue has **one** worker (`-c 1`, prefetch 1, `acks_late`); while it's busy on a pass
(one unacked message), every new daily-cron enqueue cannot be delivered and **stands in the queue**.
Result: a small standing backlog of duplicate `crawl_all_clans_task` messages (a few days' worth).

**The duplicates are harmless.** `crawl_all_clans_task` is guarded:
- per-realm Redis lock — `cache.add(lock_key, …)` fails → `return {"status":"skipped","reason":"already-running"}` (`tasks.py:1354-1357`);
- cross-realm mutex `MAX_CONCURRENT_REALM_CRAWLS` (`tasks.py:1342-1352`);
- run-scoped resume marker so a redelivered/duplicate dispatch *continues* a pass rather than restarting from clan 0 (`tasks.py:1359-1413`).

So a duplicate either skips fast or resumes the in-progress pass — it never double-crawls or corrupts state.

## Severity: LOW (monitoring noise)

- **No user impact.** All web/API endpoints healthy; the crawl is progressing normally.
- **Not a regression and not runaway** — see history.
- **Actual harm = alarm fatigue.** Chronic crawls FAIL keeps `healthcheck.sh` permanently exit-1,
  so a real incident is easy to miss. Example already buried in the log: a transient burst at
  `2026-06-10T21:20Z` (multiple `api:* HTTP 502` + `queue:background/hydration unreachable`,
  self-resolved within one cycle) sat in the same red stream as the crawls noise.

## History (why this is benign now)

The alarm has fired since **2026-05-11**. Early-May depths climbed to **205–345**, incrementing
**+6 / 10 min** — that signature is the **pre-fix watchdog re-dispatch storm**: a stuck crawl held
its lock 24/7, the watchdog kept seeing a "stale" lock and re-dispatched all three realms every
5 min. That bug was fixed on **2026-06-05** (run-scoped resume marker + floor/crawl coexistence;
see `runbook` references below and the *crawl-lock-starves-floor* note). Post-fix, peak depth has
been **6–8** (Jun 10 max 7, Jun 11 max 8, Jun 12 max 7) — flat, bounded, healthy. The current
alarm is purely the leftover threshold being below the new normal.

## Remediation options (NOT applied — choose in a follow-up)

- **A — Raise the crawls threshold (DONE 2026-06-12).** In `scripts/healthcheck.sh`,
  `check_queue_depth "crawls"` was raised from `5` to **15** with the comment updated to state the
  real post-fix steady-state (6–8) and that the tripwire still catches a *runaway* (the pre-fix storm
  reached the hundreds, so 15 trips well before that). Clears the chronic false alarm while keeping a
  genuine runaway detector. No production behavior change (the script runs locally via cron and only
  SSHes in to read queue depth).
- **B — Deduplicate enqueues at the source (DONE 2026-06-12).** Implemented as a **per-realm**
  pending flag: at most one `crawl_all_clans_task` per realm is ever in flight (queued or running),
  so depth is bounded at #realms (~3 — one running + the others queued for their turn under
  `MAX_CONCURRENT_REALM_CRAWLS=1`, which are *next-in-line* tasks, not duplicates), and typically 1.
  Continuity is preserved (the next realm is already queued when the running pass finishes — no gap).
  Implementation:
  - `_clan_crawl_pending_key(realm)` + `_enqueue_clan_crawl_if_absent(realm)` (`warships/tasks.py`):
    `cache.add` set-if-absent gate; skips if the realm's lock is held (running) or pending is set.
  - New `dispatch_clan_crawl_task(realm)` (routed to `default`); the `daily-clan-crawl-{realm}` Beat
    schedule now points at it instead of `crawl_all_clans_task` (`warships/signals.py`,
    `settings.py CELERY_TASK_ROUTES`).
  - `crawl_all_clans_task` clears the pending flag at the very top (before the cross-realm-mutex and
    already-running early returns) so a skip path can't wedge the realm.
  - `ensure_crawl_all_clans_running_task` routes its stale-lock resume through the same gate and
    clears a stale pending flag when **no** crawl is running on any realm (lost-message recovery),
    making the `CLAN_CRAWL_PENDING_TTL` (4d) backstop safe.
  - Tests: `ClanCrawlEnqueueDedupTests` in `test_clan_crawl.py` + routing assertion in
    `test_task_routing.py`.
  - NOTE: the healthcheck threshold (Option A) was left at 15, not lowered — it stays a runaway
    tripwire with headroom for transient deploy churn; the queue now idles at ~0–1 (≤3 worst case).
- **C — Reconsider the "daily" cadence.** A cron named *daily* that drives a ~14-day pass can never
  complete on schedule; it only manufactures duplicates. Lowering the cadence (e.g. weekly, or
  driven solely by the watchdog/self-chain) reduces dup generation regardless of A/B.
- **D — One-time drain (do NOT rely on this alone).** Purging the standing `crawl_all_clans_task`
  duplicates clears depth momentarily, but the daily cron refills it within a day. Cosmetic only.

**Recommendation:** ship **A** now to stop the alarm fatigue; consider **B** (and/or **C**) as the
durable fix so the queue genuinely idles near zero.

## Diagnostic recipe (re-verify)

```bash
# 1. Queue shape + consumer (alive worker = consumers≥1, unacked=running task)
ssh root@battlestats.online 'rabbitmqctl list_queues name messages messages_ready messages_unacknowledged consumers'

# 2. Worker health + live crawl progress
ssh root@battlestats.online 'systemctl status battlestats-celery-crawls --no-pager | head -20'

# 3. Identify the ready messages (non-destructive: basic_get then reject-requeue).
#    rabbitmqadmin cannot reach the mgmt port here; use the app's broker connection:
ssh root@battlestats.online 'cd /opt/battlestats-server/current/server && \
  /opt/battlestats-server/venv/bin/python manage.py shell' <<'PY'
import json; from collections import Counter; from celery import current_app as app
seen=[]
with app.connection_or_acquire() as conn:
    ch=conn.default_channel; msgs=[]
    for _ in range(50):
        m=ch.basic_get("crawls", no_ack=False)
        if m is None: break
        msgs.append(m)
    for m in msgs: seen.append((m.headers or {}).get("task"))
    for m in msgs: ch.basic_reject(m.delivery_tag, requeue=True)   # put them all back
print(Counter(seen))
PY

# 4. Alarm history / steady-state (local healthcheck log)
LOG=/home/august/code/archive/battlestats/logs/healthcheck/healthcheck.log
grep "queue:crawls" "$LOG" | grep -oE "depth=[0-9]+" | sort | uniq -c | sort -rn
```

Caveat: when peeking, **always reject-requeue** (or just close the connection without ack — unacked
messages auto-requeue on disconnect). Never `ack` a peeked crawl message; that would drop a real
crawl dispatch.

## References

- `scripts/healthcheck.sh` — `check_queue_depth`, crawls threshold (~L240-247).
- `warships/signals.py` — `daily-clan-crawl-{realm}` (~L535) and `clan-crawl-watchdog-{realm}` (~L560).
- `warships/tasks.py` — `crawl_all_clans_task` lock/mutex/resume (1325-1417), `ensure_crawl_all_clans_running_task` (1420-1443).
- CLAUDE.md → "Celery queues" (crawls = `-c 1`, multi-day crawl + watchdog; `CELERY_TASK_ACKS_LATE`).
- Related fix: the 2026-06-05 crawl-lock / floor-coexistence fix that ended the pre-fix depth storm
  (run-scoped resume marker) — *crawl-lock-starves-floor*.
- Kill switch: `ENABLE_CRAWLER_SCHEDULES` gates both periodic families.
