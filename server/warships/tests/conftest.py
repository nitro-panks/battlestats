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
