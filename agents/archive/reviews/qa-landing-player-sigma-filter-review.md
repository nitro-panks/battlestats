# QA Review: Landing Active Players Sigma Filter Spec

_Reviewed: 2026-03-17_

## Scope Reviewed

- [agents/work-items/landing-player-sigma-filter-spec.md](agents/work-items/landing-player-sigma-filter-spec.md)
- [client/app/components/PlayerSearch.tsx](client/app/components/PlayerSearch.tsx)
- [server/warships/landing.py](server/warships/landing.py)
- [server/warships/tests/test_views.py](server/warships/tests/test_views.py)
- [client/app/components/**tests**/PlayerSearch.test.tsx](client/app/components/__tests__/PlayerSearch.test.tsx)

## QA Verdict

Approved for implementation.

The spec is well-bounded: it adds a third landing-player mode on the existing endpoint, uses the already-published efficiency contract, and avoids inventing a second efficiency score or a second landing fetch path.

## What QA Confirmed

1. The requested `Sigma` filter is scoped only to the landing page `Active Players` list.
2. Sorting by published `efficiency_rank_percentile` matches the repo’s current public efficiency contract.
3. Reusing `/api/landing/players/` with `mode=sigma` is the smallest safe extension of the current landing architecture.
4. The spec preserves the current dense-row icon rule, which avoids accidentally broadening row-level sigma visibility while adding the new list mode.
5. The proposed top-40 behavior fits the existing landing-player limit semantics.

## QA Focus Areas For Implementation

1. Freshness gating so sigma mode does not include stale explorer-summary rows with historical percentile data.
2. Hidden-player suppression so sigma mode does not leak hidden players into a public leaderboard.
3. Deterministic ordering when multiple rows share the same percentile.
4. Frontend mode switching so `Random`, `Best`, and `Sigma` can be toggled without breaking the existing list behavior.
5. Cache reuse and warming so the new mode does not introduce inconsistent landing latency compared with other active-player modes.

## Required QA Checks

1. `mode=sigma` is accepted by landing mode normalization and no longer returns a 400.
2. Sigma mode orders rows by published percentile descending.
3. Hidden and unpublished players are excluded from sigma results.
4. Requested limit behavior still caps sigma results correctly.
5. The client renders a `Sigma` button and switches the landing request to `mode=sigma` when clicked.
6. Existing `Random` and `Best` buttons continue to work after the new mode is added.

## Residual Risks

1. If the backend orders candidates before freshness-gating and slices too early, some qualifying rows could be dropped from the top 40 result.
2. If sigma mode relies on raw explorer-summary fields without the shared publication helper, stale or hidden rows could leak into results.
3. The landing row UI may show many high-ranked rows without visible sigma icons because the dense-row icon policy remains `E`-only; that is acceptable for this tranche but should not be mistaken for a data bug.

## QA Recommendations

1. Use the shared publication helper to determine final sigma eligibility, even if the initial queryset is narrowed by explorer-summary fields.
2. Add one backend test for sigma ordering and one for sigma exclusion rules instead of relying only on existing random/best coverage.
3. Add a client test that clicks `Sigma` and verifies the correct fetch mode rather than only snapshotting the initial random mode.

## Exit Criteria

1. The landing page exposes a `Sigma` button in the active-player toggle group.
2. The backend returns the top qualifying rows for `mode=sigma` in descending percentile order.
3. Hidden and unpublished rows are excluded.
4. Focused backend and client validation passes.

## Final QA Position

Approved for the planned landing sigma-filter tranche.
