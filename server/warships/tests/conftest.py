"""Shared pytest fixtures for the warships test suite.

Test isolation: the test cache backend is LocMemCache, which is process-global
and — unlike the database — is NOT rolled back between tests. Several suites
warm/publish to the cache (landing published-cache, the 1h current-season
detector, hot-entity pins, …). Without a reset, cache state leaks across test
classes and failures depend on collection order: the curated 4-file CI run hid
this by hand-ordering files; running the full directory (alphabetical) surfaced
order-dependent failures that all pass in isolation. Clear the cache around
every test so each starts from a clean slate.

(The other cross-test leak — landing durable snapshots committed by
``warm_landing_page_content``'s ThreadPoolExecutor on separate DB connections —
is cleaned per-class where the surface is read; see ``ApiContractTests`` /
``ApiThrottleTests`` setUp and ``_clear_landing_snapshots`` in test_landing.
It can't be centralized here without forcing DB access onto pure-unit tests.)
"""

import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _isolate_cache():
    cache.clear()
    yield
    cache.clear()
