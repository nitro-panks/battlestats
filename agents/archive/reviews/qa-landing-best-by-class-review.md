# QA Review: Landing Page Best by Class Filtering

**Reviewer:** QA Agent
**Status:** Incomplete Validation Criteria. Requires test plan amendments.

## Findings

- **Data Fidelity:** The `type_json` (e.g. Battleship) may be completely empty for newer or hidden players. The validation step requires asserting how empty classes map or drop out of ranking logic.
- **Cache Missing Headers:** The UI validation fails to account for a cache miss on the very first visit (e.g. `X-Clan-Plot-Pending: true`). We need to ensure selecting "Cruiser" shows an explicit loading skeleton or gracefully holds the active chart while fetching the new data.
- **Fallback Resilience:** Does clicking "Cruisers" fall back to "Overall" if the `landing:players:best:cruiser` cache is missing but overall is loaded? This is not explicitly answered in the spec.
- **UI State Duplication:** Changing the primary filter (e.g. from Best to Sigma) hides the sub-filter row correctly. However, does it reset the class state? If the user switches back to "Best", does it remember they were viewing "Submarines" or does it default back to "Overall"?

## Regression Scope

The spec must include Playwright smoke test (`e2e/player-detail-tabs.spec.ts` equivalents like `e2e/landing-best-by-class.spec.ts`) that asserts:

1. `Sub-Navigation Row` toggling.
2. SVG transition without unmounting the chart frame entirely.
3. Fallback state when `X-[Dataset]-Pending` triggers.

## Release Recommendation

**Hold.**

Require updates in the spec to explicitly define the backend Materialized View and frontend loading states (spinner vs skeleton vs pending headers) before execution.
