# QA Review: Player Efficiency Badges Story Runbook

_Reviewed: 2026-03-15_

## Scope Reviewed

- [agents/runbooks/runbook-player-efficiency-badges-story.md](agents/runbooks/runbook-player-efficiency-badges-story.md)
- [agents/work-items/efficiency-badges-player-story-spec.md](agents/work-items/efficiency-badges-player-story-spec.md)

## QA Verdict

Approved as a planning artifact for a first implementation tranche on the player detail page.

The proposed scope is testable, uses existing stored data instead of introducing live WG dependencies, and keeps the feature aligned with the current player-page information hierarchy.

## QA Follow-Up On Implementation

Implementation was rechecked against the runbook on `2026-03-15`.

Observed result:

1. the player detail page now includes an `Efficiency Badges` section below randoms and above ranked content,
2. the section uses stored player payload data instead of page-load WG calls,
3. the fetch and crawl lanes now persist `top_grade_label` alongside the existing `badge_label` alias,
4. focused backend regression coverage and frontend build validation passed.

## What The Runbook Gets Right

1. It keeps the feature on the existing player detail page instead of creating a disconnected badge surface.
2. It uses stored `efficiency_json` data rather than adding page-load WG fetches.
3. It chooses a summary-plus-table pattern, which fits ordinal badge data better than forcing another chart.
4. It treats sparse or partial metadata as a first-class QA concern.
5. It sets a sensible bar for suppressing story copy when the underlying signal is too thin.

## QA Focus Areas

1. badge label correctness and ordering
2. truthful handling of sparse datasets
3. visible relationship between badge rows and current top-ship context
4. responsive readability of the summary strip and table
5. clarity and accessibility of the tooltip-based explanatory copy

## Required QA Checks

### Build and static checks

- `cd client && npm run build`

### Manual player-page verification

- visible player with several badge rows renders the new section
- visible player with no badge rows shows a clear empty state
- hidden player does not render broken badge UI
- section placement is below randoms and above ranked content
- table sorting preserves correct badge strength order
- mobile or narrow layout remains readable

### Data correctness checks

- `top_grade_class` maps correctly to `Expert`, `Grade I`, `Grade II`, `Grade III`
- rows with missing `ship_type` or `ship_tier` remain visible in the table
- incomplete rows are excluded from class and tier summary rollups
- top-ships overlap markers are only shown for actual overlaps

### Behavioral checks

- no new browser-triggered WG API calls appear when loading the player page
- tooltip headers explain the section intent without forcing extra inline body copy into the page flow
- tooltip copy does not claim broad class or tier strength from a single ship outlier

## Residual Risks

1. Client-side aggregation can drift from future backend logic if a second surface reuses the same feature differently.
2. Badge data is a peak-performance signal, so readers may overinterpret it as average ship performance if the surrounding copy is too strong.
3. Ship-name joins between badge rows and randoms rows can fail on naming mismatches, producing incomplete overlap cues.

## QA Recommendations

1. Treat the explanatory tooltip copy as a release gate, not just the rendering.
2. Use at least one real player with mixed classes and one with narrow badge concentration during manual verification.
3. If a second badge surface is later added, move summary aggregation to a shared backend or shared frontend utility and re-review the contract.

## Exit Criteria

1. The player page builds cleanly with the new section.
2. All required empty, sparse, and hidden states render without broken UI.
3. Badge labels and strength ordering are correct.
4. No new page-load WG API dependency is introduced.
5. Tooltip copy stays conservative and evidence-based.

## Executed Validation Evidence

1. Focused Django regression suite passed with `14` tests.
2. Production client build passed.
3. Site endpoint smoke task passed after the implementation.
4. Player-detail explanatory paragraphs were consolidated into shared tooltip headers to reduce visual clutter without dropping section guidance.

## Final QA Position

Approved for the implemented player-detail tranche.

Residual risk is limited to presentation nuance, not contract correctness. If this feature expands into explorer or clan surfaces later, the shared summary logic should be re-reviewed.
