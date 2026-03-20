# QA Review: Detail Cache Stale-While-Revalidate

_Reviewed: 2026-03-19_

## Scope Reviewed

- [agents/runbooks/spec-detail-cache-stale-while-revalidate-2026-03-19.md](/home/august/code/archive/battlestats/agents/runbooks/spec-detail-cache-stale-while-revalidate-2026-03-19.md)

## QA Verdict

Approved with one constraint: cold misses for brand-new entities remain out of scope for this tranche.

The spec is appropriately focused on the real performance problem, which is repeat-read latency for already-known players and clans. It avoids overreaching into a broader API redesign while still pushing the hot paths onto stale-while-revalidate behavior.

## Findings

### Finding 1: Repeat-read performance is the right target

Severity: high

The site’s worst user-visible latency comes from requests that already have enough local state to render something but still do synchronous refresh work. Moving those reads to serve cached data first will help immediately without requiring frontend contract changes.

### Finding 2: Base-data refreshes must own dependent cache republishing

Severity: high

If battle or snapshot refresh stays decoupled from dependent cache rebuilds, read paths will either keep blocking or risk racing a derived refresh against missing base data. The spec is correct to require refresh cascades.

### Finding 3: Clan roster requests must tolerate partial local truth

Severity: medium

The roster endpoint needs to return whatever local rows it already has, even if the clan membership count says more members should exist. Returning partial truth quickly is better than blocking the page on a full roster refresh.

### Finding 4: First-ever lookups should stay explicit non-goals

Severity: medium

Changing first lookup behavior is a product decision because it may require pending states or partial responses in detail routes. Keeping that out of scope is the right choice for this tranche.

## QA Recommendations

1. Add regression tests that prove no synchronous refresh helper is called from the remaining known-entity hot paths.
2. Prefer returning `[]` over placeholder synthetic chart rows for cold derived caches.
3. Keep the hot entity warmer comprehensive so top landing entities actually benefit from the stale-while-revalidate model.

## QA Position

Proceed.

The spec is tight enough to implement now, and the remaining work is concrete and testable.
