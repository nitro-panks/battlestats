# Designer Analysis

## Current Review: Tier and Type SVG Modernization Runbook

### Design Verdict

The runbook points in the right direction. The key visual requirement is to make `TierSVG` and `TypeSVG` feel like deliberate companions to `RandomsSVG`, not just older charts with a partial color update.

### Design Direction

- Use `RandomsSVG` design 1 as the visual anchor for:
  - axis tone
  - gridline restraint
  - bar hierarchy
  - detail text hierarchy
  - spacing rhythm
- Preserve the current bar-chart logic, but modernize the presentation so the charts feel authored as one family.

### Important Visual Elements to Carry Forward

- Muted axis and grid system:
  - axis/tick text in slate tones
  - low-contrast grid lines
  - no heavy framing
- Bar hierarchy:
  - cool pale battle-volume backbar
  - semantically colored wins bar with dark outline
  - subtle rounded corners
- Top-right detail treatment:
  - strong primary line
  - subdued metadata line
  - restrained use of accent color
- Consistent micro-typography:
  - 10px to 11px range
  - medium-weight category labels
  - no abrupt scale jumps between related charts

### Specific Design Requirements for This Tranche

- Remove the rotated y-axis title text from tier and type charts.
- Keep category tick labels readable and visually balanced.
- Align the plotting baseline with the randoms chart above so the stacked charts feel intentional.
- Avoid hover recolors that replace the semantic WR palette with an unrelated accent.
  - Prefer opacity or light emphasis changes.

### Design Risks

- The type chart can look cramped if alignment is solved only by shrinking the label area.
- The tier chart can still feel legacy if margins align but typography and detail treatment do not.
- Over-polishing hover states can make the chart feel noisier than the randoms reference.

### Design Acceptance Checks

- At a glance, the three chart blocks read as one visual system.
- The left plotting edge alignment is visibly cleaner in stacked layout.
- Category labels remain legible without the rotated axis title.
- Hover details feel cleaner and more intentional, not more decorative.
- The updated tier/type charts do not visually overpower the surrounding player-detail content.

## Visual Design Perspective

The current UI aligns better with minimal styling, but there are opportunities to improve consistency and readability.

## Findings

- Chart typography and labels vary in scale and spacing.
- State messages are present but not uniformly styled across components.
- Sidebar chart now fits better; consistency improvements remain for graph text/markers.

## Recommendations

1. Standardize micro-typography for chart labels and status text.
2. Use consistent muted text treatment for fallbacks.
3. Keep marker styling subtle to maintain minimal aesthetic.

## Design Guardrail

- Prioritize clarity and consistency over decorative effects.
