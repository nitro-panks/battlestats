# QA Review: Cache Analysis Spec

_Reviewed: 2026-03-15_

## Scope Reviewed

- `agents/work-items/cache-analysis-spec.md`
- `server/battlestats/settings.py`
- `server/warships/data.py`
- `server/warships/landing.py`
- `server/warships/tasks.py`
- `server/warships/api/ships.py`
- `server/warships/api/client.py`
- `server/warships/views.py`
- `server/warships/tests/test_cache.py`

## QA Verdict

Approved as a planning and operations-analysis artifact.

The spec now accurately captures the major cache surfaces in battlestats, distinguishes hot interactive caches from aggregate analytics caches, and reflects the executed near-term fixes for crawler heartbeat ownership and landing cache normalization.

This review now covers the implemented near-term cache changes as well as the updated spec language describing the resulting behavior.

## What The Spec Gets Right

1. It correctly identifies Redis as the active runtime backend while noting that tests use locmem semantics instead.
2. It captures the real cache families that matter operationally: landing, analytics, clan-battle, ship metadata, response summaries, and task-coordination locks.
3. It does not overclaim freshness for the hourly analytics caches.
4. It recognizes that cache versioning is already in use for evolving payload shapes, especially in the ranked-correlation path.
5. It correctly treats task locks as cache-backed coordination rather than ordinary response caching.

## QA Findings

### Finding 1: Crawler heartbeat ownership is now materially safer

Severity: high

The crawler heartbeat is now passed explicitly from the task layer into the crawl implementation, and the task clears the heartbeat key on exit. That closes the previous gap where watchdog correctness depended on an implicit helper inside the crawler module.

Why it matters:

1. heartbeat freshness is now a declared task contract
2. watchdog reasoning no longer depends on hidden cache writes inside the crawler module
3. cleanup on task exit reduces stale coordination residue

### Finding 2: Expensive cache misses do not have explicit stampede protection

Severity: medium

The spec now calls this out appropriately. Several expensive `cache.get()` or `cache.get_or_set()` rebuild paths are acceptable at current scale, but they are not protected by cache-side locks. This is most relevant for clan-battle summary rebuilds and secondarily for landing and analytics payloads.

### Finding 3: Empty cache values carry more than one meaning in clan-battle flows

Severity: medium

An empty clan-battle response can mean either a true empty result or a just-enqueued refresh path. The response header helps for first-miss API responses, but the underlying cache contract is still ambiguous enough that the runbook should warn operators about it.

### Finding 4: Landing cache invalidation is now cleaner and cheaper

Severity: low

The landing cache family now uses versioned active keys consistently, and player-list invalidation has moved to namespace-versioning instead of fan-out delete-many. That is a meaningful maintainability improvement without changing the short interactive TTL.

## QA Recommendations

1. Keep the explicit crawler heartbeat callback covered by regression tests so future crawl refactors do not drift back toward implicit watchdog behavior.
2. Keep the analytics caches TTL-based unless there is a proven freshness requirement that justifies active invalidation or warmers.
3. Monitor landing cache churn, but the namespace-version pattern is now the correct default for the player-list family.
4. If clan-battle cache semantics become a frontend pain point, split true-empty from refresh-pending more explicitly in the API contract.

## QA Position

Approved for documentation and planning.

The spec is strong enough to guide operators and future cache work. The main remaining caveats are analytics staleness within the hour and the clan-battle empty-result ambiguity, not the landing cache or crawl heartbeat basics.
