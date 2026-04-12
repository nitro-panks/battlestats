# Runbook: Incident — Celery Zombie Worker & Cascading Systemd Failures

_Created: 2026-04-12_
_Status: **Remediated** — three-layer fix deployed, services hardened_

## Summary

On 2026-04-12, a cascade of two related failures caused extended production downtime:

1. **Zombie Celery background worker** — RabbitMQ's default `consumer_timeout` (1800000ms / 30 min) killed the AMQP channel for the enrichment batch task, which runs for 17-20 min per batch and holds an unacked message the entire time (due to `CELERY_TASK_ACKS_LATE = True`). The Celery main process stayed alive with no active consumer, so systemd's `Restart=always` never triggered. The `background` queue accumulated 1703 unprocessed messages.

2. **Cascading gunicorn outage** — When `advanced.config` was applied to disable `consumer_timeout`, RabbitMQ restarted. Gunicorn and all Celery workers had `Requires=rabbitmq-server.service` in their systemd units, which caused systemd to stop them when RabbitMQ went down. Gunicorn's restart attempt failed because RabbitMQ was briefly unavailable during its startup sequence. Result: all API endpoints returned 502 for ~25 minutes until manually detected.

## Timeline

| Time (UTC) | Event |
|---|---|
| ~04:48 | RabbitMQ `consumer_timeout` fires after an enrichment batch holds an unacked message for >30 min. Background worker's AMQP channel killed with `PRECONDITION_FAILED`. |
| 04:48–15:28 | Background worker is a zombie — process alive (systemd sees `active`), but 0 consumers registered on the `background` queue. Messages accumulate. |
| ~14:00 | First investigation: all 7 services show `active (running)`, but `rabbitmqctl list_queues` reveals background queue has 1703 messages, 0 consumers. |
| 15:28:10 | Fix attempt: `advanced.config` applied, RabbitMQ restarted. Systemd's `Requires=` dependency stops gunicorn and all Celery workers. |
| 15:28:18 | Gunicorn restart attempt fails: `Dependency failed for battlestats-gunicorn.service` — RabbitMQ not yet ready. |
| 15:29:44 | RabbitMQ finishes startup. Gunicorn remains dead. |
| 15:51 | Outage detected. Gunicorn manually restarted. Background worker resumed draining. |
| 15:53 | `Requires=` changed to `Wants=` on all 5 battlestats systemd units. |
| 16:00 | All services verified healthy, queues draining normally. |

## Root Cause Analysis

### Failure 1: RabbitMQ consumer_timeout vs CELERY_TASK_ACKS_LATE

**The interaction:**

```
CELERY_TASK_ACKS_LATE = True   (settings.py)
consumer_timeout = 1800000     (RabbitMQ 3.12 default, milliseconds)
```

- `CELERY_TASK_ACKS_LATE = True` means Celery does not acknowledge a message until the task function returns. This provides at-least-once delivery semantics — if a worker crashes mid-task, the message is redelivered.
- RabbitMQ 3.12 introduced `consumer_timeout` (default 30 min). If a consumer holds an unacked message longer than this, RabbitMQ closes the channel with `PRECONDITION_FAILED`.
- The `enrich_player_data_task` runs for 17-20 minutes per batch and self-chains. The `incremental_player_refresh_task` can run for 35-78 minutes per realm. Both exceed 30 minutes regularly.

**The failure mode:**

When RabbitMQ kills the channel, Celery logs the error but does not exit the main process. The worker enters an "unrecoverable error" state: the parent process is alive (systemd sees `active (running)`), but there are zero AMQP consumers. No new messages are consumed. `systemctl restart` would fix it, but systemd doesn't know anything is wrong because the process is still running.

**Why this wasn't caught earlier:**

The zombie state is invisible to `systemctl status` — all services show green. It's only detectable by checking consumer counts via `rabbitmqctl list_queues`.

### Failure 2: Systemd `Requires=` cascading stop

All five battlestats systemd units had:

```ini
[Unit]
After=network.target redis-server.service rabbitmq-server.service
Requires=redis-server.service rabbitmq-server.service
```

`Requires=` creates a hard dependency: if the required unit stops or restarts, systemd also stops the requiring unit. When RabbitMQ restarted (to pick up `advanced.config`), systemd stopped gunicorn and all Celery workers. Gunicorn's `Restart=always` tried to restart it, but the restart attempt was rejected because RabbitMQ was still in its startup sequence and the `Requires=` dependency check failed.

This is a known systemd footgun: `Requires=` is appropriate for units that truly cannot function without the dependency and should be stopped alongside it. For gunicorn (which doesn't use RabbitMQ directly), it's actively harmful.

## Remediation

### Layer 1: Disable RabbitMQ consumer_timeout

**File:** `/etc/rabbitmq/advanced.config` (droplet)
**File:** `server/deploy/deploy_to_droplet.sh` line 259 (repo)

```erlang
[{rabbit, [{consumer_timeout, undefined}]}].
```

RabbitMQ 3.12's new-style `.conf` format does not accept `false` or `undefined` for `consumer_timeout`. Setting it to `0` also fails. The only way to disable it is via `advanced.config` using Erlang term syntax.

**Why not fix `CELERY_TASK_ACKS_LATE` instead?** `acks_late` is intentional — it provides crash recovery for long-running enrichment and crawl tasks. Without it, a worker crash mid-enrichment loses the batch. The correct fix is to tell RabbitMQ to tolerate long-held messages, not to change the ack strategy.

**Commits:** `e6c6b3b`, `f5fe617`

### Layer 2: Celery consumer watchdog

**File:** `/usr/local/bin/battlestats-celery-watchdog.sh` (droplet)
**File:** `server/deploy/deploy_to_droplet.sh` line 514-558 (repo)

A systemd timer that runs every 5 minutes and checks each queue's consumer count via `rabbitmqctl list_queues`. If a queue has 0 consumers while its corresponding service is active, the watchdog restarts the service and logs the event.

```bash
check_worker() {
  local queue_name="$1" service_name="$2"
  systemctl is-active --quiet "${service_name}" || return 0
  local consumers
  consumers=$(rabbitmqctl -q list_queues name consumers 2>/dev/null \
    | awk -v q="${queue_name}" '$1 == q { print $2 }')
  if [[ "${consumers}" == "0" ]]; then
    logger -t battlestats-watchdog "ALERT: ${service_name} has 0 consumers on queue '${queue_name}' — restarting"
    systemctl restart "${service_name}"
  fi
}
check_worker default   battlestats-celery
check_worker hydration battlestats-celery-hydration
check_worker background battlestats-celery-background
```

Systemd units:
- `battlestats-celery-watchdog.service` — oneshot, runs the script
- `battlestats-celery-watchdog.timer` — fires every 5 minutes (2 min after boot, then every 5 min)

**Commit:** `e6c6b3b`

### Layer 3: Systemd `Wants=` instead of `Requires=`

**Files changed (droplet):**
- `/etc/systemd/system/battlestats-gunicorn.service`
- `/etc/systemd/system/battlestats-celery.service`
- `/etc/systemd/system/battlestats-celery-hydration.service`
- `/etc/systemd/system/battlestats-celery-background.service`
- `/etc/systemd/system/battlestats-beat.service`

**File changed (repo):** `server/deploy/deploy_to_droplet.sh` (3 occurrences)

```diff
-Requires=redis-server.service rabbitmq-server.service
+Wants=redis-server.service rabbitmq-server.service
```

`Wants=` preserves boot ordering via `After=` (gunicorn still starts after Redis and RabbitMQ) without the hard coupling. A transient RabbitMQ restart no longer cascades into stopping the entire application stack. Celery workers will reconnect to RabbitMQ automatically when it comes back — they don't need to be restarted.

**Commit:** `28557f5`

## Related Fix: Landing page clan badge OOM (same session)

**Commits:** `f5fe617` (v1.7.9)

The `_attach_clan_battle_activity_badges` function in `landing.py` was calling `get_clan_battle_activity_badge` synchronously for all 30 candidate clans during landing page render. On a cache miss, this triggered `refresh_clan_battle_seasons_cache`, which does a WG API fan-out (30 clans x ~30 members = ~900 API calls) inline on the gunicorn request thread. This exceeded gunicorn's 30-second timeout, causing SIGKILL and 502s on the random/best clan landing endpoints.

**Fix:** Added `cache_only: bool = False` parameter to `get_clan_battle_activity_badge` (`data.py:5396`). When `True`, cache misses return a default badge with `cache_miss=True` flag instead of firing synchronous WG API calls. `_attach_clan_battle_activity_badges` (`landing.py:53`) now uses `cache_only=True` and queues async refreshes via `queue_clan_battle_summary_refresh` for any cache misses.

**Files:**
- `server/warships/data.py:5391-5421` — `get_clan_battle_activity_badge` with `cache_only` param
- `server/warships/landing.py:53-85` — `_attach_clan_battle_activity_badges` using `cache_only=True` + async dispatch
- `server/warships/tests/test_landing.py` — updated test + new `test_landing_clan_badges_cache_miss_defers_to_async_refresh`

## Verification

### Check consumer counts (primary health signal)

```bash
ssh root@battlestats.online "rabbitmqctl -q list_queues name consumers messages"
```

Expected: each queue has ≥1 consumer.

### Check watchdog logs

```bash
ssh root@battlestats.online "journalctl -t battlestats-watchdog --no-pager -n 20"
```

Any `ALERT` entries indicate the watchdog caught and recovered a zombie worker.

### Check watchdog timer

```bash
ssh root@battlestats.online "systemctl status battlestats-celery-watchdog.timer --no-pager"
```

### Check service dependencies

```bash
ssh root@battlestats.online "grep -h 'Wants\|Requires' /etc/systemd/system/battlestats*.service"
```

Should show `Wants=` everywhere, no `Requires=`.

### Verify landing endpoints

```bash
for i in 1 2 3 4 5; do
  curl -s -o /dev/null -w "random players #$i: HTTP %{http_code} — %{time_total}s\n" \
    "https://battlestats.online/api/landing/players/?mode=random&realm=na"
done
```

All should return HTTP 200 in <500ms.

## Failure Modes Addressed

| Failure Mode | Detection Before | Detection After | Recovery Before | Recovery After |
|---|---|---|---|---|
| RabbitMQ kills channel on long task | None (invisible) | Prevented (consumer_timeout disabled) | Manual restart | N/A — doesn't happen |
| Celery zombie worker (0 consumers) | None | Watchdog every 5 min | Manual `systemctl restart` | Automatic restart by watchdog |
| RabbitMQ restart cascades to gunicorn | None (unexpected) | Prevented (`Wants=`) | Manual `systemctl start` | N/A — doesn't happen |
| Cold-cache clan badge blocks request | None (timeout → 502) | Prevented (`cache_only=True`) | Retry after timeout | Instant default badge + async refresh |

## Lessons Learned

1. **`systemctl status` lies about Celery health.** A Celery worker can be `active (running)` with zero AMQP consumers. The only reliable signal is `rabbitmqctl list_queues ... consumers`.

2. **`Requires=` in systemd is almost never what you want for application services.** It creates tight coupling that causes cascading failures during dependency restarts. Use `Wants=` + `After=` for soft ordering without the stop-cascade.

3. **RabbitMQ 3.12's `consumer_timeout` default is hostile to `acks_late` patterns.** Any Celery deployment using `CELERY_TASK_ACKS_LATE = True` with tasks that can run >30 minutes must explicitly disable this timeout.

4. **Landing page render must never block on upstream API calls.** The `cache_only` pattern — return stale/default data immediately and queue async refresh — is the correct approach for any data that touches external APIs on the hot request path.

5. **Infrastructure changes (RabbitMQ config, systemd dependencies) should be tested for cascading effects.** Restarting RabbitMQ to apply `advanced.config` would have been safe if `Wants=` had been in place. The fix for failure 1 directly caused failure 2.

## Related Runbooks

- `runbook-droplet-hardening-2026-04-09.md` — SSH, TLS, EPMD, and Umami hardening. The EPMD binding attempt on 2026-04-09 caused the same cascading failure pattern (gunicorn + Celery down when RabbitMQ restarted), which was the first occurrence of this exact bug.
- `runbook-celery-queue-strategy.md` — Queue topology and routing decisions
- `runbook-incident-rabbitmq-compromise-2026-04-04.md` — Prior RabbitMQ incident (crypto miner via exposed AMQP port)
- `runbook-landing-random-cold-queue-2026-04-07.md` — Root cause analysis of slow random landing strip (namespace bump issue, separate from the OOM addressed here)
- `runbook-backend-droplet-deploy.md` — Deploy procedures and post-deploy verification
