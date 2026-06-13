# QA Review: Clan Ranked Hydration Runbook

_Reviewed: 2026-03-15_

## Scope Reviewed

- [agents/runbooks/runbook-clan-ranked-hydration.md](agents/runbooks/runbook-clan-ranked-hydration.md)
- [agents/work-items/clan-ranked-hydration-spec.md](agents/work-items/clan-ranked-hydration-spec.md)

## QA Verdict

The runbook matched the implemented shape closely enough to guide delivery, and the shipped change remains directionally accurate against the current codebase.

The implementation kept the clan-members endpoint as the single polling surface, added a dedicated ranked refresh task lane with duplicate-dispatch suppression, and introduced bounded per-roster admissions plus lightweight hydration headers as planned.

## What The Runbook Gets Right

1. It keeps the existing clan-members endpoint as the single client polling surface, which avoids the main N+1 browser-fetch regression.
2. It explicitly calls for a dedicated ranked refresh task with per-player locking, which matches the repo's current locking pattern in other task lanes.
3. It separates correctness checks from performance/load checks instead of assuming passing functional tests are enough.
4. It covers both clan detail and the player-detail embedded clan roster, which is necessary because both surfaces share `ClanMembers.tsx`.
5. It includes rollback sequencing that would let the team reduce load quickly without immediately deleting the whole change.
6. It now calls out per-roster admission budgeting and lightweight headers, which makes the load-shaping story more concrete and more testable.

## Accuracy Assessment

1. The runbook's client recommendation is accurate: `ClanMembers.tsx` is the right ownership point because both surfaces already depend on it.
2. The runbook's server recommendation is accurate: the current ranked refresh path is synchronous in `fetch_ranked_data`, so a dedicated Celery wrapper is the right missing layer.
3. The bounded polling recommendation is accurate and consistent with existing clan-hydration precedent in `PlayerSearch.tsx`.
4. The local and WG load concerns are real and correctly prioritized; repeated clan-page views could otherwise fan out into many redundant ranked refresh attempts.
5. The added budget-and-header guidance is accurate for this repo because it improves observability and reduces first-wave queue bursts without changing the JSON contract.

## Completeness Assessment

The runbook covers the required areas the request asked for:

1. client performance,
2. server performance,
3. memory,
4. bottlenecks,
5. local API load,
6. WG API load,
7. QA validation,
8. implementation and executed validation evidence.
9. bounded-admission validation guidance.

## Residual Gaps Or Follow-Ups

1. The runbook does not name a concrete instrumentation source for measuring WG call volume. That is acceptable for planning, but implementation should decide whether logs, counters, or test doubles are the source of truth during validation.
2. The polling budget is sensible, but QA should treat it as provisional until real clan-page behavior is observed on a populated fixture.
3. If the backend ends up adding per-clan queue budgets or cache markers, the runbook should be updated so QA can verify those specific guardrails explicitly.
4. If response headers become part of the QA workflow, browser-facing exposure requirements should be revisited only if the client ever needs to read them directly.

## Executed Validation Evidence

1. Focused backend tests for clan-members hydration metadata, queue gating, and ranked refresh locking passed.
2. A clean local `next build` passed after removing stale `.next` artifacts.
3. The site endpoint smoke task passed, including the clan members endpoint.
4. A broader Django suite found unrelated failures outside the ranked hydration lane in landing attrition, clan lookup timing, and player explorer score rounding.

## QA Recommendations After Implementation

1. Keep the current scope boundary intact; do not expand this lane into roster-level ranked detail rendering.
2. Resolve the unrelated broader-suite failures separately so future validation can report a clean full-suite result.
3. During later manual validation, inspect the browser network panel and backend logs together; either view alone is incomplete for judging load behavior.
4. Treat "no new per-member client fetches" as a continuing release gate.

## QA Exit Criteria Assessment

1. The implementation uses one roster request per poll cycle, not one ranked request per member.
2. The implementation stops polling once no rows remain pending or the bounded retry window is exhausted.
3. The implementation does not block clan-page rendering on ranked refresh completion.
4. The implementation now has explicit queue-budget and pending-header signals to support later observation of fresh-cache skips, lock hits, and deferred admissions.

## Final QA Position

Approved for the implemented clan-ranked hydration tranche.

Residual risk remains outside this change because the broader Django suite is not fully green, but the failing cases observed are not in the ranked hydration path.
