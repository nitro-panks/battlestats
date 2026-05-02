# Runbook: clan crawl camping `background` worker slots

_Created: 2026-04-30_
_Context: `crawl_all_clans_task` runs for days end-to-end and shares the `background` Celery queue (`-c 2`) with `incremental_player_refresh_task`, all warmers, and enrichment. Once a clan crawl starts, it occupies 50% of the available concurrency for its full multi-day runtime, blocking incrementals and letting warmer-fanout duplicates accumulate in the queue. Mitigation today is a manual revoke + lock clear — fired three times in the past 24 hours._
_Status: routed (2026-05-01 — structural fix shipped via commit `8b139ce` + enable-on-install patch `779cdde`; dedicated `crawls` worker has been stable for 24h+, no operational kills needed since 2026-04-30 13:47 UTC; the daily clan-crawl Beat tick now runs on `crawls@battlestats-droplet` without ever touching the `background` slot pool that incremental refreshes need)_

## Purpose

Capture the recurring-blocker pattern observed across three incidents on 2026-04-29 → 2026-04-30, document the operational kill-switch playbook the team has been running ad-hoc, and prescribe the structural fix (dedicated `crawls` queue + worker) so the daily Beat-driven clan crawl stops competing with everything else on the droplet's `-c 2` background slots.

## Incident chain

| When (UTC) | Realm | Trigger | Symptom | Resolution |
|---|---|---|---|---|
| 2026-04-29 ~21:00 | NA | manual `incremental_player_refresh_task` dispatch from operator | the crawl already on Slot 2 since ~20:00 UTC was at clan 350/35,440 (~1%); the manual incremental sat behind 1,962 warmer-republish dupes in the queue and never started | revoke `d1fe56e1-…` + clear `_clan_crawl_lock_key('na')` + `_clan_crawl_heartbeat_key('na')` + purge background queue + redispatch the refresh |
| 2026-04-30 ~03:00 | EU | daily Beat (`CLAN_CRAWL_SCHEDULE_HOUR=3` + EU offset) | EU crawl camped Slot 2; queue grew to 360 messages, all `warm_player_distributions_task` self-fanout dupes | revoke `cee69a50-…` + clear EU clan-crawl locks |
| 2026-04-30 ~13:47 | NA | Beat re-fired NA at the next 03:00+offset | NA crawl camped Slot 1; queue at 888 messages, mix of distribution + correlation warmer dupes; NA observations fell to 0 in last 5 min | revoke `dfc04be0-…` + clear NA clan-crawl locks |

Battle capture totals between revokes: started session at ~1,564 battles, sat at ~4,766 ten hours later, **11,549 by the third incident** — coverage ramped dramatically once incrementals + the manually-dispatched baseline filler were unblocked, demonstrating that this is the rate-limiting bottleneck for the rollout, not an upstream WG-API-budget constraint.

## Why the pattern is structural

`server/battlestats/settings.py:296-320` routes 16 task classes to the single `background` queue, including:

- `crawl_all_clans_task` — runs ~14 days end-to-end at steady state (35,440 clans × ~25 clans/15min × 2 realms with daily firings).
- `incremental_player_refresh_task` — 35–78 min per realm, fires every 3 h.
- A dozen warmers / refreshers — 10–55 min cadences each.

The `background` worker runs with `-c 2` (`server/deploy/.../battlestats-celery-background.service`). Math:

- Crawl camps 1 of 2 slots for days.
- Remaining slot has to absorb every incremental, warmer, and enrichment kickstart.
- Self-fanout warmer bugs (e.g. `warm_player_distributions_task`, `warm_player_correlations_task`, the previously-fixed `warm_landing_page_content_task`) inflate the queue with dupes that lock-skip cheaply but still occupy FIFO slots.

Result: every Beat-fired clan crawl starts a slow asphyxiation of the rest of the `background` workload until manually killed.

## Operational kill-switch (use until structural fix lands)

This is the playbook executed three times in 24 h. ~30 seconds wall clock per incident. Repeat after every Beat firing of `crawl_all_clans_task` until the structural fix is deployed.

### 1. Identify the active crawl task ID

```bash
ssh root@battlestats.online "cd /opt/battlestats-server/current/server && \
  set -a && source /etc/battlestats-server.env && source /etc/battlestats-server.secrets.env && set +a && \
  /opt/battlestats-server/venv/bin/celery -A battlestats inspect active --timeout 6 \
  2>&1 | python3 -c 'import sys, re
out = sys.stdin.read()
for m in re.finditer(r\"\\{\\047id\\047: \\047([^\\047]+)\\047[^}]*?\\047name\\047: \\047warships\\.tasks\\.crawl_all_clans_task\\047[^}]*?\\047kwargs\\047: (\\{[^}]+\\})\", out):
    print(f\"TASK_ID={m.group(1)} kwargs={m.group(2)}\")
'"
```

### 2. Revoke + clear locks

```bash
ssh root@battlestats.online "cd /opt/battlestats-server/current/server && \
  set -a && source /etc/battlestats-server.env && source /etc/battlestats-server.secrets.env && set +a && \
  /opt/battlestats-server/venv/bin/python manage.py shell -c \"
from battlestats.celery import app
from django.core.cache import cache
from warships.tasks import _clan_crawl_lock_key, _clan_crawl_heartbeat_key
app.control.revoke('<TASK_ID>', terminate=True, signal='SIGTERM')
cache.delete(_clan_crawl_lock_key('<REALM>'))
cache.delete(_clan_crawl_heartbeat_key('<REALM>'))
print('revoked + locks cleared')
\""
```

Expected: SIGTERM hits the worker process; with `acks_late=True` and the explicit revoke, the message is dropped (not requeued). The slot frees within seconds. Beat won't re-fire that realm's crawl until the next daily cron.

### 3. Optional: purge stale warmer dupes from background

If the queue has accumulated obvious self-fanout dupes (sample with the Python+kombu loop documented in `runbook-landing-warm-self-fanout-2026-04-28.md` history if present, or just check `rabbitmqctl list_queues background messages`), a blanket `rabbitmqctl purge_queue background` is acceptable per the precedent set on 2026-04-27 — but **only when the operator can confirm no in-flight legitimate dispatches sit ahead of the dupes** (their own `incremental_player_refresh_task` would also be dropped). Re-dispatch any survivors after the purge.

## Structural fix: dedicated `crawls` queue

Move the long-running crawl off `background` so it can never camp a slot the rest of the system needs.

### Changes

| File | Change |
|---|---|
| `server/battlestats/settings.py:297-298` | Reroute `warships.tasks.crawl_all_clans_task` and `warships.tasks.ensure_crawl_all_clans_running_task` from `'queue': 'background'` to `'queue': 'crawls'`. |
| `server/deploy/deploy_to_droplet.sh` | Add a new `battlestats-celery-crawls.service` systemd unit install. Match the existing `battlestats-celery-background.service` shape but use `-Q crawls -c 1 --time-limit=1209600 --max-tasks-per-child=1` (one slot, 14-day time limit, recycle the worker between crawls so the long-running process doesn't accumulate memory drift). Restart the unit at deploy. |
| `server/deploy/templates/battlestats-celery-crawls.service.template` (new) | systemd unit definition; copy `battlestats-celery-background.service.template` and substitute the queue + flags above. |
| `server/scripts/healthcheck.sh:210-233` | Extend `check_queue_depth` calls to include `crawls` with a small threshold (`5` — steady-state should be 0 or 1; anything higher means crawls are queueing instead of running). |
| `agents/runbooks/runbook-celery-queue-strategy.md` | Document the new queue + worker. Two-line update plus a paragraph in the "queue topology" section explaining why `crawls` was carved out. |
| `CLAUDE.md` "Celery queue architecture" (line 159+) | Add a fourth bullet for `crawls (-c 1)` describing the multi-day clan-crawl tenancy. |

### Why this shape, not the alternatives

- **Bumping `background` to `-c 4` or higher** doesn't fix anything. The crawl still occupies a slot for days; the same starvation reappears at higher scale, with extra memory pressure on the droplet (each Celery worker process is ~150–300 MB RES).
- **Restructuring `crawl_all_clans_task` to chunk + self-chain** (like enrichment does) is the platonically-cleanest fix but a much bigger change touching the crawl orchestrator. Worth filing as a Phase-2 follow-up; not the right scope for an immediate stability patch.
- **Routing only the warmers off `background`** ignores the actual root cause. The warmers cohabit fine — they're seconds-to-minutes. The clan crawl is the singular multi-day camper.

### Migration safety

- Config-only on the application side; no schema or model change.
- Beat schedules don't reference queues — they only reference task names. Re-routing is transparent to Beat.
- The new systemd unit can be installed and started without restarting the existing `battlestats-celery-background` worker. Zero-downtime.
- Backout: revert the routing entries and remove/disable the new unit. The `crawls` queue itself is harmless to leave in place if reverting in a hurry — RabbitMQ keeps empty queues at trivial cost.

## Verification

1. **Lean release gate** (smoke that settings.py edit doesn't break import):
   ```bash
   cd server && python -m pytest --nomigrations \
     warships/tests/test_views.py warships/tests/test_landing.py \
     warships/tests/test_realm_isolation.py warships/tests/test_data_product_contracts.py \
     -x --tb=short
   ```
2. **Deploy:** `./server/deploy/deploy_to_droplet.sh battlestats.online`. Expect the new `battlestats-celery-crawls.service` to be created, enabled, and started.
3. **Post-deploy droplet checks:**
   ```bash
   ssh root@battlestats.online "systemctl is-active battlestats-celery-crawls"
   # expected: active

   ssh root@battlestats.online "rabbitmqctl list_queues -q name messages consumers --no-table-headers | grep crawls"
   # expected: 'crawls 0 1'  (queue exists, one consumer)
   ```
4. **Smoke a small dry-run crawl on the new worker:**
   ```bash
   ssh root@battlestats.online "cd /opt/battlestats-server/current/server && \
     set -a && source /etc/battlestats-server.env && source /etc/battlestats-server.secrets.env && set +a && \
     /opt/battlestats-server/venv/bin/python manage.py shell -c \"
   from warships.tasks import crawl_all_clans_task
   r = crawl_all_clans_task.apply_async(kwargs={'realm':'na','dry_run':True,'limit':5}, queue='crawls')
   print(f'dispatched {r.id} to crawls')
   \""

   ssh root@battlestats.online "cd /opt/battlestats-server/current/server && \
     set -a && source /etc/battlestats-server.env && source /etc/battlestats-server.secrets.env && set +a && \
     /opt/battlestats-server/venv/bin/celery -A battlestats inspect active --timeout 6 | grep crawl_all_clans"
   # expected: hostname is crawls@battlestats-droplet, not background@
   ```
5. **Next Beat firing** (03:00 UTC + per-realm offset): confirm the crawl runs on `crawls@battlestats-droplet`, `background` queue stays steady at near-zero during the run, and `incremental_player_refresh_task-na` fires on its 3 h schedule and completes without waiting on the crawl.
6. **24 h soak:** healthcheck cron stays green; `crawls` queue depth never exceeds the threshold.

## Doctrine pre-commit checklist

- **Documentation review:** CLAUDE.md "Celery queue architecture" + `runbook-celery-queue-strategy.md` updated per the changes table.
- **Doc-vs-code reconciliation:** new queue is registered in both code (settings.py routing, healthcheck thresholds) and docs.
- **Test coverage:** no new test needed (config-only; no logic change). Verification section covers the live-check.
- **Runbook archiving:** N/A — this runbook describes a new behavior; archive only after the fix is deployed and stable for 14 days, OR after the Phase-2 follow-up (chunked crawl) supersedes it.
- **Contract safety:** no API or payload change.
- **Runbook reconciliation:** update **Status** between phases: `planned` → `mitigation-only` → `routed` → `resolved` once the structural fix has soaked.

## Out of scope (filed for follow-ups)

- **Phase 2: chunked + self-chained `crawl_all_clans_task`.** The dedicated queue makes the multi-day crawl harmless to the rest of the system, but the crawl shape itself is still inelegant — a single 14-day task with a 14-day time-limit is fragile to worker restarts and process death. Restructuring as `enrich_player_data_task`-style self-chaining batches with checkpoint resumption would be more robust. Land when there's appetite.
- **Lock-aware `queue_warm_player_distributions` / `queue_warm_player_correlations` gates.** Same self-fanout shape as the landing warmer had pre-`f0e51d8`. The dedicated `crawls` queue solves the camping problem; this remaining bug just bloats the `background` queue with ~50ms-skip noise. Follow-up: port the `queue_landing_page_warm` lock-aware gate (`server/warships/tasks.py:266`, commit `f0e51d8`) to those two dispatchers.
- **Healthcheck row-count for the recurrence indicator.** The clearest signal of "clan crawl is camping again" is `background` queue depth > 100 with `incremental_player_refresh_task` not running. Worth a derived alert, but only if the structural fix doesn't land soon.

## References

- Operational kill executions: this session's chat history (2026-04-29 ~21:00, 2026-04-30 ~03:00, 2026-04-30 ~13:47).
- Routing config: `server/battlestats/settings.py:296-320`.
- Crawl orchestrator: `server/warships/tasks.py:1054` (`crawl_all_clans_task`).
- Lock helpers: `server/warships/tasks.py:64,68` (`_clan_crawl_lock_key`, `_clan_crawl_heartbeat_key`).
- Existing queue strategy doc: `agents/runbooks/runbook-celery-queue-strategy.md`.
- Companion fix runbook (same self-fanout pattern, different task): `agents/runbooks/archive/runbook-deploy-oom-startup-warmers.md` and the landing-warm fix in commit `f0e51d8`.
- Daily clan-crawl Beat schedule: `server/warships/signals.py` (search `clan-crawl` schedule registration); cron driven by `CLAN_CRAWL_SCHEDULE_HOUR` / `_MINUTE` env vars.
