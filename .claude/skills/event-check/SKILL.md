---
name: event-check
description: Check in on the battlestats event-processing system (Celery workers, RabbitMQ broker, Beat) using the Flower + RabbitMQ management APIs, then produce a metrics summary and a qualitative performance read. Use when the user says "event check", "how's the event system", "check the workers/queues", "is the pipeline healthy", "any backlog", or wants a health snapshot of the async/Celery side after a deploy or during an incident. Read-only — queries APIs and systemd; never restarts services.
---

# event-check

Runs `server/scripts/event_check.sh` against the production droplet and interprets the
output into a metrics summary + qualitative analysis. The check pulls from the
observability stack (`runbook-flower-observability-2026-04-02.md`): worker liveness +
task history via the **Flower API**, queue depth + broker rates via the **RabbitMQ
management API**, plus systemd service state, host load, and recent error logs — all in
one SSH call.

## When to invoke

- "event check", "how's the event system", "check the pipeline/workers/queues"
- "any backlog", "are tasks failing", "is Celery healthy"
- After a deploy, to confirm all worker lanes came back and are draining
- During an incident on the async side (enrichment stalls, floor not capturing, etc.)

Scope: this is the **event/Celery side**. For enrichment-crawler specifics use
`enrichment-status`; for the public web tier use `scripts/healthcheck.sh`.

## Procedure

### 1. Run the check

```bash
./server/scripts/event_check.sh battlestats.online
```

Single SSH call, completes in ~10–20s. If SSH fails, surface the error verbatim and
stop — there's nothing to interpret. (Run from a battlestats checkout; the script is at
`server/scripts/event_check.sh`.)

### 2. Interpret against known-good ranges

Reference sizing: app droplet **2 vCPU / 8 GB** (`ops-infra-resources.md`); managed PG
**2 vCPU**, `system_load15` saturates ~**2.0**.

**Services** — all of nginx, redis, rabbitmq, gunicorn, beat, the five celery lanes, and
flower should be `active`. `failed_units: none` expected. (`celery-watchdog` is a
timer, not in the list.) Any inactive worker lane or a non-empty `failed_units` is the
headline finding.

**Host** — `load15 > 2.3` sustained is DB/CPU pressure; cross-ref
`runbook-floor-throughput-tuning-2026-06-13.md` (back off floor self-chain / `-c 1`).
Swap creeping up = memory pressure.

**Queues** — the signal is *ready* (backlog) vs *ack/s* (drain rate):
- `ready≈0` across lanes → caught up, healthy.
- `ready` high **and** `ack/s` high → actively draining a backlog (fine, note the trend).
- `ready` high **and** `ack/s≈0` with `consumers>0` → **stalled** consumer (slow tasks,
  DB-bound, or a stuck task). Investigate.
- `consumers=0` on a queue whose service is `active` → **zombie worker**; the watchdog
  (`battlestats-celery-watchdog.timer`) should restart it within ~5 min — say so, and
  flag if it hasn't.
- `floor` and `background` legitimately carry the heaviest, slowest work; some backlog
  there is normal. `default`/`hydration` should stay near-empty (user-facing).

**Workers (Flower)** — `online: 5` expected (default/background/hydration/crawls/floor).
Fewer than 5 = a lane is down or not reporting.

**Recent tasks (Flower)** — `failure_rate` under ~2–3% is normal. Spikes, or `FAIL`
lines mentioning `REQUEST_LIMIT_EXCEEDED` / `407` → WG rate-limit pressure (the shared
token bucket; `ops-env-reference.md` "WG rate limiter"). `WorkerLost`/`SIGKILL` →
zombie/OOM, see `runbook-incident-celery-zombie-worker`. A near-empty sample right after
a deploy just means Flower started fresh — note it, don't alarm.

**Recent errors** — `none` ideal. Nonzero error-level counts: read them as a magnitude
signal and name the worst offender.

### 3. Produce the summary

Output two parts:

**a) Key metrics** — a compact table, e.g.:

| Metric | Value | Status |
|---|---|---|
| Worker lanes online | 5/5 | ✅ |
| Failed units | none | ✅ |
| Host load (1/5/15m) | … | ✅/⚠️ |
| Total queue backlog (ready) | … | ✅/⚠️ |
| Busiest queue | `floor` ready=… ack/s=… | … |
| Task failure rate (last 500) | …% | ✅/⚠️ |
| Error-level logs (1h) | … | ✅/⚠️ |
| Public serving (`/`) | 200 | ✅ |

**b) Qualitative analysis** — 2–4 sentences: open with a one-word verdict
(**Healthy / Watch / Degraded**), then what the numbers mean together (is it idle,
draining, or stalled?), any trend worth noting, and a recommended action **only if**
something's off. This is read-only: recommend, never restart. If everything's green, say
so plainly and stop — don't manufacture concerns.
