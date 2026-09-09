# Runbook: a broker publish is a blocking network call (2026-09-09)

**Status:** active
**Owner:** platform
**Subject:** `server/warships/broker.py`, `when_ready` in `server/gunicorn.conf.py`
**Shipped:** v5.7.3
**Origin:** ops alert `gunicorn_worker_timeouts`, 2026-09-09 mail (2 timeouts in 24h, limit 2)

## The incident

Two gunicorn workers died 9 seconds apart on 2026-09-08 (14:26:08 and
14:26:17), serving `/api/fetch/clan_members/2000008048` and
`/api/player/Spooky_Carrier`. Each returned a **500 with an empty body**: the
worker was killed mid-request at the 25s timeout, so nothing was ever written.

Both tracebacks ended in the same place:

```
views.py:85 _delay_task_safely -> task.delay(**kwargs)
  celery/app/task.py apply_async -> app.send_task
    kombu maybe_declare -> entity.declare -> exchange_declare
      amqp abstract_channel.wait -> connection.drain_events -> transport.read_frame
```

The request thread was parked in a socket read waiting for RabbitMQ's
declare-ok frame. `task.delay()` reads like a fire-and-forget local call. It is
a synchronous network round-trip on a socket with **no timeout**.

## Root cause: the arbiter's socket is inherited by every worker

`when_ready` in `gunicorn.conf.py` dispatches `startup_warm_caches_task`.
Gunicorn calls `when_ready` in the **arbiter**, and only then forks workers, so
the AMQP socket that dispatch opens is inherited by every child. Several
processes then interleave frames on one file descriptor: a worker's publish
waits forever for a declare-ok that a sibling already consumed.

The proof is in the RabbitMQ log, and it is the single most useful check for
this class of failure:

```bash
grep -a "accepting AMQP" /var/log/rabbitmq/rabbit@battlestats-droplet.log | grep -aE "^<date> 14:"
```

**Zero accepts between 14:17:13 and 14:34:18**, while the workers hung at
14:26. They had opened no connection of their own; they were publishing on the
inherited fd. Had there been an accept in that window, the theory would have
been dead and the answer broker-side.

Corroborating detail: `when_ready` logged at 14:16:58 and the workers booted at
14:17:00-01. Fork strictly after.

## The two non-obvious findings

**1. `celery_app.close()` does not close the socket.** It sets `_pool = None`
and force-closes the *producer* pool; the connection pool's socket survives.
Verified against a live broker: one connection still `running` after boot with
no request served. Both pools have to be forced shut:

```python
celery_app.amqp.producer_pool.force_close_all()
celery_app.pool.force_close_all()
celery_app.close()
```

**2. py-amqp's `read_timeout` does not bound anything on CPython.** It reaches
only `SO_RCVTIMEO` via `setsockopt`; CPython's blocking-socket read path loops
on the resulting `EAGAIN` instead of surfacing it. Measured against a broker
wedged mid-`exchange.declare`: a publish carrying `read_timeout=5` still hung
past 45s. The **Python-level** `sock.settimeout()` is what actually bounds
`drain_events`, and `having_timeout(None)` preserves it.

Do not "fix" a hang by passing `broker_transport_options={'read_timeout': N}`.
It will look right and change nothing.

## What shipped

`warships/broker.py` owns the one way a request thread may reach the broker:
its own connection, never a pooled one, carrying a Python-level socket timeout.
Both request-thread dispatchers route through it — `_delay_task_safely`
(views) and `_dispatch_async_refresh` (data). A failed publish returns False,
is logged, and frees the views dedup key so the next request retries rather
than sitting out the 60s window.

`BROKER_PUBLISH_TIMEOUT_SECONDS` (default 2) is a **per-read** budget, not a
total: kombu retries the read three times, so a wedged broker costs ~3x it
(measured 15.07s at 5s, 6.06s at 2s). The multiplier is empirical; if you raise
the budget, re-measure rather than assuming 3x. 2s keeps the worst case under
nginx's 20s `proxy_read_timeout` and well under the 25s worker timeout.

`data.py:4862`'s bare `update_tiers_data_task.delay()` was deliberately left
alone: all three callers of `update_clan_tier_distribution` are warmers running
inside Celery workers, where blocking costs no user a response.

## Reproducing it

A black-hole listener is **not** enough — it only exercises the connect
timeout, and both the old and new code return in ~4s against one. You need a
broker that completes the handshake and then goes silent at the declare. A TCP
proxy that stops forwarding the moment it sees AMQP class 40 / method 10
(`\x00\x28\x00\x0a`) reproduces it exactly: pre-fix hangs past 45s, post-fix
returns in 6.06s with a warning.

## Verifying the fork fix in production

```bash
ssh root@battlestats.online 'ss -tnp state established "( dport = :5672 )" \
  | grep -oE "users:\(\(\"[a-z]+\",pid=[0-9]+" | sort | uniq -c'
```

Only `celery` processes may appear. A `gunicorn` pid holding an AMQP connection
means the leak is back.

## Test coverage

`server/warships/tests/test_broker_publish.py` — that the bound is
`settimeout` (not `read_timeout`), that a failed dispatch frees the dedup key,
and that `when_ready` closes both pools even when the warm dispatch raises.

`assert_dispatched_once_with` in `warships/tests/conftest.py` exists because
passing an explicit connection means `apply_async`, not `delay`: the call shape
is `apply_async(args=(...), kwargs={...}, connection=..., retry=False)`, and
only the task arguments are the contract under test.
