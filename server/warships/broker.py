"""Enqueueing Celery work from a request thread, without the broker holding it.

The load-bearing rule in CLAUDE.md is that no ``/api/fetch/*`` endpoint may
block the request thread. A ``task.delay()`` looks instantaneous and is not: it
publishes over AMQP and waits for the broker's reply frame on a socket with no
timeout. On 2026-09-08 two gunicorn workers died 9s apart, both parked in
``exchange_declare`` -> ``drain_events``, each returning a 500 with an empty
body.

Two properties make that impossible here:

* the publish runs on its own connection, never a pooled one that may already
  be wedged (or, before 2026-09-09, inherited across the gunicorn fork); and
* the connection carries a *Python-level* socket timeout. py-amqp's
  ``read_timeout`` reaches only ``SO_RCVTIMEO``, which CPython ignores on a
  blocking socket: verified against a broker wedged mid-``exchange.declare``,
  where a publish carrying ``read_timeout=5`` still hung past 45s, while
  ``settimeout(2)`` returned in 6.06s.

A failed enqueue is not an error the caller has to handle: every dispatch here
is a lazy refresh whose absence costs freshness, not correctness. The response
goes out regardless.
"""
import logging
import os

from battlestats.celery import app as celery_app

# Per-read budget, not a total: kombu retries the read three times before giving
# up, so a wedged broker costs ~3x this value (measured 15.07s at 5s, 6.06s at
# 2s). 2s keeps the worst case under nginx's 20s proxy_read_timeout and well
# under gunicorn's 25s worker timeout.
BROKER_PUBLISH_TIMEOUT_SECONDS = float(
    os.getenv('BROKER_PUBLISH_TIMEOUT_SECONDS', '2'))


def bounded_broker_connection():
    """A broker connection whose reads cannot outlive the request thread."""
    connection = celery_app.connection_for_write(
        connect_timeout=BROKER_PUBLISH_TIMEOUT_SECONDS)
    try:
        connection.connect()
        sock = getattr(
            getattr(connection.connection, 'transport', None), 'sock', None)
        if sock is not None:
            sock.settimeout(BROKER_PUBLISH_TIMEOUT_SECONDS)
    except Exception:
        connection.release()
        raise
    return connection


def publish_task(task, *args, **kwargs) -> bool:
    """Enqueue ``task``, returning whether the message reached the broker.

    Never raises: a broker that is down or wedged degrades freshness, and the
    caller is mid-response.
    """
    try:
        with bounded_broker_connection() as connection:
            task.apply_async(
                args=args, kwargs=kwargs, connection=connection, retry=False)
    except Exception as error:  # noqa: BLE001 - fire-and-forget by design
        logging.warning(
            'Skipping async task enqueue for %s due to broker error: %s',
            getattr(task, 'name', repr(task)),
            error,
        )
        return False
    return True
