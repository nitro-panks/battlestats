# QA Review: Landing Random Queue Mechanics

_Reviewed: 2026-03-19_

## Scope Reviewed

- [agents/runbooks/spec-landing-random-queue-mechanics-2026-03-19.md](/home/august/code/archive/battlestats/agents/runbooks/spec-landing-random-queue-mechanics-2026-03-19.md)
- [server/warships/landing.py](/home/august/code/archive/battlestats/server/warships/landing.py)
- [server/warships/tasks.py](/home/august/code/archive/battlestats/server/warships/tasks.py)
- [server/warships/views.py](/home/august/code/archive/battlestats/server/warships/views.py)
- [server/warships/tests/test_landing.py](/home/august/code/archive/battlestats/server/warships/tests/test_landing.py)

## QA Verdict

Approved for phased implementation, with players first.

The runbook is directionally sound and defines the right separation between request-time serving and background replenishment. The main requirement for a safe first implementation is that the player lane must not preserve the old one-hour response cache semantics, or the queue design collapses back into a static sampled payload.

## QA Findings

### Finding 1: Random-player payload caching must change for the queue to be real

Severity: high

If `GET /api/landing/players/?mode=random` still returns from the existing one-hour payload cache, the queue is effectively bypassed for most requests. The spec hints at this, but the implementation must make it explicit for Phase 1.

Why it matters:

1. a cached random payload would stop the queue head from advancing
2. background refill would become invisible to users until cache expiry
3. the endpoint would still look random-by-hour instead of queue-driven-by-visit

### Finding 2: Request-time queue pop needs atomic coordination

Severity: medium

Two concurrent landing requests cannot safely pop from the same queue with naive read-modify-write cache calls. The implementation needs a short queue lock around pop and refill operations, even if the queue is stored as a plain cached list rather than a Redis native list.

### Finding 3: Queue contents can drift from eligibility between refill and serve

Severity: medium

Players can become hidden or otherwise ineligible after being queued. The player-first implementation should resolve queued ids against current eligibility and tolerate underfilled responses instead of leaking stale rows or failing the request.

## QA Recommendations

1. Ship the player lane first and treat the clan queue as a separate tranche.
2. Make the random-player endpoint queue-backed and effectively uncached at the response-payload level.
3. Keep best, sigma, and recent lanes unchanged during the player-first pass.
4. Add regression tests for cold bootstrap, ordered pop, refill scheduling, and duplicate prevention.

## QA Position

Proceed with player-first implementation.

The runbook is strong enough to guide the first tranche as long as the implementation makes the random-player endpoint truly queue-driven and keeps concurrency safeguards explicit.
