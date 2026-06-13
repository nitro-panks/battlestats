# Project Manager Analysis

## Current Review: Tier and Type SVG Modernization Runbook

### PM Verdict

The runbook is appropriately scoped and should be executed as a narrow player-detail consistency tranche, not as a general chart redesign effort.

### Scope Decision

- In scope:
  - visual modernization of `TierSVG` and `TypeSVG`
  - alignment to `RandomsSVG` design language
  - removal of rotated y-axis title labels
  - canvas/y-axis alignment with the randoms chart above
  - hover/detail polish that preserves semantic WR coloring
- Out of scope:
  - API/schema changes
  - chart-family replacement
  - broad player-detail redesign
  - unrelated polish to other charts

### Execution Order

1. UX confirms the interpretation of "remove y-axis labels" as removing axis-title text while preserving category tick labels.
2. Engineer implements visual alignment and styling updates in `TierSVG.tsx` and `TypeSVG.tsx`.
3. Engineer validates stacked page alignment against `RandomsSVG`, not just local component output.
4. QA verifies visual consistency, hover semantics, and responsive behavior.

### Acceptance Criteria

- Tier and type charts read as members of the same design system as `RandomsSVG`.
- Rotated y-axis titles are removed from both charts.
- Category labels remain readable and unclipped.
- Left plotting edge/y-axis aligns visually with the randoms chart above.
- Hover states remain informative and do not destroy WR color meaning.
- Frontend build stays green.

### Risks and Mitigations

- Risk: type labels become cramped after alignment.
  - Mitigation: allow width/margin rebalance instead of forcing the legacy canvas size.
- Risk: engineer matches constants but not actual on-page alignment.
  - Mitigation: require page-level screenshot/manual validation as part of completion.
- Risk: hover treatment regresses semantic readability.
  - Mitigation: forbid non-semantic hover recoloring in implementation review.

### PM Orchestration Notes

- Treat this as a single vertical slice under player-detail chart consistency.
- Do not combine it with ranked/randoms/chart-data work in the same execution pass.
- Require a before/after visual comparison for sign-off.
- Gate completion on both `npm run build` and manual page validation.

## Product Delivery View

Current work has delivered significant reliability gains; present opportunity is quality hardening and UX polish with minimal scope increase.

## Scope Assessment

- In scope now: chart correctness, clarity, resilience, and consistency.
- Out of scope now: full design system rewrite, major feature additions, platform migration.

## Prioritized Suggestions

1. Correct known chart logic defects affecting user trust (color classification bug).
2. Harden async fetch/render lifecycle to reduce intermittent UI issues.
3. Improve accessibility semantics for interactive member/graph elements.

## Success Metrics

- Zero frontend type errors in touched components.
- No chart rendering failures in normal user flow.
- Improved UX clarity for status/error states.

## Suggested Milestone

- Milestone A (this pass): correctness + resilience + accessibility baseline for clan/random charts.
