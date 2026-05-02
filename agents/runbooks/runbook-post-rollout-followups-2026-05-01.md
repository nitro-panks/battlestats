# Runbook: post-rollout follow-ups (5 phases)

_Created: 2026-05-01_
_Context: After the battle-history rollout (Tranches 1+2 + cache fix), the dedicated `crawls` queue carve-out, the recent-players cache coalescing fix, and the dead-code cleanup pass, several follow-ups remain. This runbook captures them in priority order so the next operator (human or agent) sees the intended sequence and can pick up any phase independently._
_Status: phase-1-planned_

## Purpose

Five phases, ordered by value × urgency. Phase 1 is the highest-value follow-up (closes the warmer-fanout bug class once and for all) and ships from this runbook. Phases 2–5 are independent and can be picked up in any order.

| Phase | Title | Class | Risk | Effort | Owner today |
|---:|---|---|---|---|---|
| 1 | Lock-aware gate for the correlation-warm cold-cache dispatcher | code | low | ~30 min | shipping with this runbook |
| 2 | Soak-monitor the recent-players cache fix | read-only | none | ~5 min × 3 samples | scheduled / on-demand |
| 3 | Extend baseline coverage to EU + Asia | management cmd | low (WG-budget) | ~10 min × 2 realms | on-demand |
| 4 | Reconcile two runbook statuses | docs | none | ~5 min | next docs sweep |
| 5 | Storage growth check on `BattleObservation` | read-only | none | ~2 min | weekly until day-14 prune |
| 6 | Ranked battle-history rollout — see `runbook-ranked-battle-history-rollout-2026-05-02.md` | code (multi-phase) | low (additive, env-gated) | days, multi-phase | scoped separately |

---

## Phase 1 — Lock-aware gate for the correlation-warm cold-cache dispatcher

### Bug class

`task.delay()` fired from a request-driven cache-miss path with no dedup. Every cold-cache page load enqueues another full warmer task. Same shape as the `queue_landing_page_warm` bug fixed in commit `f0e51d8` on 2026-04-28 (4,581-message background-queue pileup).

### Confirmed instances (verified against `origin/main`)

- **`_dispatch_async_correlation_warm`** at `server/warships/data.py:140` → calls `_dispatch_async_refresh(warm_player_correlations_task, realm=realm)` from `fetch_player_tier_type_correlation` (`data.py:3400`, only non-Beat caller). **No lock check. No dedup.** Unbounded queue fanout under user traffic on the player-correlation cold-cache path.
- **`warm_player_distributions_task`** — out of scope for Phase 1. Search confirmed only one dispatcher for this task: the Beat schedule at `signals.py:151`. Beat dispatches by task name through the broker directly and would bypass any Python-side `queue_*` wrapper. The 500-message bursts seen on 2026-04-30 were Beat firings that accumulated while a clan crawl camped the worker slot for hours — root cause was the slot starvation (fixed in `8b139ce` by the dedicated `crawls` queue), not a missing dispatch gate. The task body's existing `cache.add(_distribution_warm_lock_key(...), timeout=DISTRIBUTION_WARM_LOCK_TIMEOUT)` at `tasks.py:805` correctly lock-skips Beat-fired dupes once they dequeue. No code change needed for distributions.

### Fix

Introduce two new dispatch wrappers in `server/warships/tasks.py` that mirror `queue_landing_page_warm` (`tasks.py:266-292`) exactly:

```python
def queue_warm_player_correlations(realm=DEFAULT_REALM):
    if cache.get(_correlation_warm_lock_key(realm)):
        return {"status": "skipped", "reason": "already-running"}
    dispatch_key = _correlation_warm_dispatch_key(realm)
    if not cache.add(dispatch_key, "queued",
                     timeout=CORRELATION_WARM_DISPATCH_TIMEOUT):
        return {"status": "skipped", "reason": "already-queued"}
    try:
        warm_player_correlations_task.delay(realm=realm)
        return {"status": "queued"}
    except Exception as error:
        cache.delete(dispatch_key)
        logger.warning(
            "Skipping correlation warm enqueue because broker dispatch failed: %s",
            error,
        )
        return {"status": "skipped", "reason": "enqueue-failed"}
```

Only the cold-cache user-traffic path needs gating; the existing `data.py:3400` callsite goes through `_dispatch_async_correlation_warm`, which we replace.

### Files to edit

| File | Change |
|---|---|
| `server/warships/tasks.py` | Add `CORRELATION_WARM_DISPATCH_TIMEOUT = 30` (matches landing); add `_correlation_warm_dispatch_key(realm)` helper; add `queue_warm_player_correlations(realm)` wrapper mirroring `queue_landing_page_warm:266-292` exactly. |
| `server/warships/data.py:140-142` | Replace `_dispatch_async_correlation_warm` body with `from warships.tasks import queue_warm_player_correlations; queue_warm_player_correlations(realm=realm)`. The function name stays so the single downstream caller (`data.py:3400`) requires no change. |
| `server/warships/tests/test_landing.py` | Add `QueueWarmPlayerCorrelationsGateTests` with 4 cases mirroring the existing `QueueLandingPageWarmGateTests`: queued / already-running / already-queued / enqueue-failed. |

Beat dispatches by task name → broker directly; the wrapper does not gate Beat. Beat-dispatched dupes are already lock-skipped at the task body's `cache.add(lock_key)` call (`tasks.py:864`). No change needed there.

### Verification (post-deploy)

1. **Lean release gate:** `cd server && python -m pytest --nomigrations warships/tests/test_views.py warships/tests/test_landing.py warships/tests/test_realm_isolation.py warships/tests/test_data_product_contracts.py -x --tb=short` → green, with the two new test classes included.
2. **Sample the queue:** the kombu-loop diagnostic from earlier session work (basic_get + reject-requeue) should show zero `warm_player_correlations_task` dupes accumulating during a normal page-load burst (vs the 500-dupe samples seen 2026-04-30).
3. **Watch celery logs:** `journalctl -u battlestats-celery-background --since '5 minutes ago' | grep -i 'Skipping correlation warm enqueue'` — this message should appear during sustained traffic when the gate is engaging (vs the prior unbounded `.delay()` calls that left no log trace at the dispatch site).
4. **Observe `background` queue depth:** under steady-state user traffic, depth should stay below 50. Currently sees periodic spikes to 800–2,100 driven by the unguarded warmer dispatch.

### Rollback

Revert the commit. The wrappers are pure new code; deleting them and reverting `data.py:140-142` to call `_dispatch_async_refresh` directly restores the prior behavior. Cache keys self-expire.

---

## Phase 2 — Soak-monitor the recent-players cache fix

Commit `d381551` shipped 2026-05-01. Confirms the 5-minute cooldown coalescing is holding under sustained capture load.

### Three sample observations across a 30-min window

```bash
ssh root@battlestats.online "redis-cli TTL ':1:na:landing:recent_players:dirty:v2'"
```

Expected outcomes:
- **Positive integer ≤ 300** → in cooldown window (cache rebuilt within last 5 min, dirty flag has TTL counting down). Healthy.
- **`-2`** → key gone (cooldown expired and no recent invalidation since). Healthy.
- **`-1`** → no expiration. **Regression** — the fix isn't holding; debug `invalidate_landing_recent_player_cache` flow.

### Cache-stability check

```bash
curl -sf 'https://battlestats.online/api/landing/recent/?realm=na' | jq '.[0:5]|map(.name)'
sleep 30
curl -sf 'https://battlestats.online/api/landing/recent/?realm=na' | jq '.[0:5]|map(.name)'
```

Identical first-5 across the two reads = cache is holding through the cooldown. Different first-5 = cache rebuilt between the reads, indicating either (a) actual capture activity changed the data or (b) the cooldown isn't gating reads properly.

If three samples across 30 min all show `ttl=-1`, file a regression follow-up.

---

## Phase 3 — Extend baseline coverage to EU + Asia

NA active-7d coverage is 99.95%. EU and Asia are unmeasured. The `establish_battle_history_baseline` management command (committed `c245fba`) takes a `--realm` arg.

### Pre-flight (dry-run)

```bash
ssh root@battlestats.online "cd /opt/battlestats-server/current/server && \
  set -a && source /etc/battlestats-server.env && source /etc/battlestats-server.secrets.env && set +a && \
  /opt/battlestats-server/venv/bin/python manage.py establish_battle_history_baseline \
    --realm eu --days 7 --dry-run"
```

Repeat for `--realm asia`. Reports the candidate count without spending WG calls.

### Execute

```bash
ssh root@battlestats.online "cd /opt/battlestats-server/current/server && \
  set -a && source /etc/battlestats-server.env && source /etc/battlestats-server.secrets.env && set +a && \
  /opt/battlestats-server/venv/bin/python manage.py establish_battle_history_baseline \
    --realm eu --days 7 --delay 0.5"
```

Repeat for `--realm asia`. Each takes ~10 min wall clock at `--delay 0.5`. Same rate-budget caveat as the NA fill on 2026-04-29 — expect ~1% of candidates to hit `407 REQUEST_LIMIT_EXCEEDED` under contention with the active crawl; rate-limited candidates pick up baselines on the next regular crawl tick.

### Verify

```sql
-- post-fill, target ≥99% coverage (mirrors NA's 99.95%)
SELECT COUNT(*) FILTER (WHERE last_battle_date >= CURRENT_DATE - 7 AND NOT is_hidden) AS active_7d,
       COUNT(*) FILTER (WHERE last_battle_date >= CURRENT_DATE - 7 AND NOT is_hidden
                         AND id IN (SELECT player_id FROM warships_battleobservation)) AS with_baseline
  FROM warships_player WHERE realm = 'eu';
```

---

## Phase 4 — Reconcile two runbook statuses

### `agents/runbooks/runbook-clan-crawl-blocker-2026-04-30.md`

- Status: `planned` → `routed` (the dedicated `crawls` worker has been stable for 24h+ since deploy `20260430103732`).
- Add closing paragraph linking commits `8b139ce` (structural fix) and `779cdde` (enable-on-install).

### `agents/runbooks/runbook-recent-battled-sub-sort-2026-04-28.md`

- Status: `tranche-2-shipped` → `resolved` (cache-coalescing fix `d381551` closes the loop on the cache-bypass behavior).
- Add closing paragraph linking commit `d381551`.

Pure markdown edits. No test surface.

---

## Phase 5 — Storage growth check on `BattleObservation`

We're Day 4 of capture (since 2026-04-28). The rollout runbook (`runbook-battle-history-rollout-2026-04-28.md`) projects ≤7 GB/realm before Day-14 pruning enables on May 12.

```bash
ssh root@battlestats.online "cd /opt/battlestats-server/current/server && \
  set -a && source /etc/battlestats-server.env && source /etc/battlestats-server.secrets.env && set +a && \
  /opt/battlestats-server/venv/bin/python manage.py shell -c \"
from django.db import connection
with connection.cursor() as c:
    c.execute(\\\"SELECT pg_size_pretty(pg_total_relation_size('warships_battleobservation'))\\\")
    print('total:', c.fetchone()[0])
    c.execute(\\\"SELECT count(*), pg_size_pretty(pg_relation_size('warships_battleobservation')) FROM warships_battleobservation\\\")
    print('rows + heap:', c.fetchone())
\""
```

Run weekly until Day-14 prune lands. If size projects past 7 GB/realm before May 12, advance the prune-enable date or narrow the per-ship JSON shape early per `runbook-battle-history-rollout-2026-04-28.md`'s "Operational watchpoints" section.

---

## Doctrine pre-commit checklist

- **Documentation review:** this runbook is the documentation deliverable.
- **Doc-vs-code reconciliation:** Phase 1 verifies code references against `origin/main` HEAD before edits land.
- **Test coverage:** Phase 1 adds two new test classes mirroring the existing landing-warm gate tests.
- **Runbook archiving:** Phase 4 reconciles two existing runbooks. Archive THIS runbook only after all five phases close out.
- **Contract safety:** no API or payload changes in any phase.
- **Runbook reconciliation:** update **Status** between phases — `phase-1-planned` → `phase-1-shipped` → `phase-2-soaked` → `phase-3-extended` → `phase-4-reconciled` → `resolved`.

## References

- Canonical lock-aware gate pattern: `server/warships/tasks.py:266-292` (`queue_landing_page_warm`, commit `f0e51d8`).
- Recent-players cache fix: `server/warships/landing.py:771-800` (commit `d381551`).
- Dedicated crawls worker: `server/battlestats/settings.py:296-320` + `server/deploy/deploy_to_droplet.sh` (commits `8b139ce` + `779cdde`).
- Establish-baseline command: `server/warships/management/commands/establish_battle_history_baseline.py` (commit `c245fba`).
- Battle-history rollout (Tier 3 retention envelope): `agents/runbooks/runbook-battle-history-rollout-2026-04-28.md`.
- Pre-deploy worktree freshness gate: `scripts/check_local_tree.sh` (commit `46d6050`).
