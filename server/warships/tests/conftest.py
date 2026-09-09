"""Shared pytest fixtures for the warships test suite.

Test isolation: the test cache backend is LocMemCache, which is process-global
and — unlike the database — is NOT rolled back between tests. Several suites
warm/publish to the cache (landing published-cache, the 1h current-season
detector, hot-entity pins, …). Without a reset, cache state leaks across test
classes and failures depend on collection order: the curated 4-file CI run hid
this by hand-ordering files; running the full directory (alphabetical) surfaced
order-dependent failures that all pass in isolation. Clear the cache around
every test so each starts from a clean slate.
"""

import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _isolate_cache():
    cache.clear()
    yield
    cache.clear()


def assert_dispatched_once_with(mock, *expected_args, **expected_kwargs):
    """Assert a request-thread dispatch happened once with these task arguments.

    Views and hydration enqueue through ``warships.broker.publish_task``, which
    publishes on its own time-bounded connection: the call shape is
    ``apply_async(args=(...), kwargs={...}, connection=..., retry=False)``. Only
    the task arguments are the contract under test, not the transport ones.
    """
    mock.assert_called_once()
    actual_args = tuple(mock.call_args.kwargs["args"])
    actual_kwargs = mock.call_args.kwargs["kwargs"]
    assert actual_args == expected_args, (
        f"dispatched args {actual_args!r}, expected {expected_args!r}")
    assert actual_kwargs == expected_kwargs, (
        f"dispatched {actual_kwargs!r}, expected {expected_kwargs!r}")
