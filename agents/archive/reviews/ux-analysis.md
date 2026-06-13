# UX Analysis

## Current Review: Tier and Type SVG Modernization Runbook

### UX Verdict

The runbook is directionally correct. The key UX constraint is that visual cleanup must not remove category comprehension while aligning the charts with the newer randoms section.

### UX Clarification

- "Remove the y-axis labels" should be interpreted as removing the rotated axis-title text only.
- Keep the category tick labels visible:
  - tier labels such as `11`, `10`, `9`
  - ship-type labels such as `Battleship`, `Cruiser`, `Destroyer`
- If those category labels become cramped after alignment changes, solve it with spacing and layout, not by removing the labels.

### UX Priorities

1. Preserve fast scanability from chart title to category labels to bars to summary detail.
2. Make the tier/type charts feel visually related to `RandomsSVG` without changing the user mental model.
3. Keep win-rate color meaning stable during hover and interaction.
4. Improve perceived polish through spacing, hierarchy, and consistency rather than more decoration.

### UX Acceptance Checks for This Tranche

- Users can still identify each tier/type row without relying on the removed rotated axis title.
- The stacked chart sequence feels visually consistent:
  - `Top Ships (Random Battles)`
  - `Performance by Tier`
  - `Performance by Ship Type`
- Hover summaries are readable and positioned predictably.
- No chart appears visually misaligned or detached from the rest of the player-detail page.
- Long labels remain readable and do not look clipped or crushed.

### UX Risks

- Removing too much axis copy could reduce orientation for first-time users.
- Margin alignment work could unintentionally punish readability for ship-type labels.
- Hover polish could accidentally over-emphasize style at the expense of semantic clarity.

## UX Perspective

Recent hierarchy updates are positive, but chart and interaction behavior still needs consistency and graceful degradation.

## UX Findings

- Interactive charts should always show meaningful fallback states.
- Member interactions are clickable but can benefit from stronger semantics.
- Visual encoding (color buckets) must be accurate to maintain user trust.

## Recommendations

1. Ensure chart states: loading/empty/error are consistently expressed.
2. Improve interaction semantics for assistive tech users.
3. Keep user-facing freshness/status language concise and stable.

## UX Acceptance Checks

- No blank chart region without explanation.
- Interactive elements are understandable and keyboard-usable.
- Color meanings map correctly to displayed ranges.
