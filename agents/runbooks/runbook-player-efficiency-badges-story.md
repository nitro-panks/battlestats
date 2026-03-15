# Runbook: Player Efficiency Badges Story

_Last updated: 2026-03-15_

## Purpose

Document how to expose Efficiency Badges on the player detail page in a way that helps readers understand what a player's best ships say about their class and tier strengths.

## Background

The backend now persists `ships/badges` data on `Player.efficiency_json`. Each stored row can include:

- `ship_id`
- `top_grade_class`
- `top_grade_label`
- `badge_label` as a backward-compatible alias
- `ship_name`
- `ship_tier`
- `ship_type`

The player page already has the right supporting surfaces:

- top ships by random battles,
- tier performance,
- ship-type performance,
- tier-vs-type profile,
- playstyle verdict.

This runbook adds a badge section that complements those surfaces instead of competing with them.

## Product Decision

For v1, use a `summary strip + ship table` pattern, not a standalone badge chart.

Reasoning:

- badge classes are ordinal and sparse,
- the page already contains multiple charts,
- users need exact ship rows to trust the story,
- a table can still support sorting and overlap cues with top ships.

## Planned UI Placement

Insert the new section on the player detail page:

1. after `Top Ships (Random Battles)`
2. before ranked sections

This placement keeps the badge story adjacent to the most relevant comparison surface: the randoms top-ships chart.

## Planned Data Shape

### Existing source

Reuse the existing player payload and extend the frontend type to include:

```ts
efficiency_json?: Array<{
    ship_id: number;
    top_grade_class: number;
    top_grade_label?: string | null;
   badge_label?: string | null;
    ship_name?: string | null;
    ship_tier?: number | null;
    ship_type?: string | null;
}> | null;
```

### Derived client-side fields for v1

Compute these from `efficiency_json`:

- `expertShips`
- `gradeIPlusShips`
- `bestClassByScore`
- `bestTierBandByScore`
- `badgeBreadthLabel`
- `metadataCoverage`

Recommended score mapping:

- `Expert = 4`
- `Grade I = 3`
- `Grade II = 2`
- `Grade III = 1`

This helper score intentionally inverts the API numbering, where `1` is strongest. The raw API and stored `top_grade_class` values must remain unchanged.

Exclude rows without `ship_type` or `ship_tier` from class and tier rollups. Keep them visible in the raw table.

## Implementation Steps

1. Extend the frontend player type in `client/app/components/PlayerDetail.tsx` and any upstream search state that stores the full player payload.
2. Add a small normalization helper for `efficiency_json` so null or malformed rows collapse safely to an empty array.
3. Create a new player-detail component for the section, for example `PlayerEfficiencyBadges.tsx`.
4. In that component, compute summary aggregates from normalized rows.
5. Join badge rows against randoms rows by `ship_name` when available to mark top-ships overlap in the table.
6. Render a summary strip with:
   - highest badge earned
   - strongest class
   - strongest tier band
   - breadth or concentration label
7. Render a sortable table beneath the summary strip.
8. Add empty-state handling for:
   - no badge rows
   - insufficient metadata for a story
   - hidden players
9. Insert the new component into the player detail layout directly below the randoms section.
10. Keep the explanatory copy concise and analytical, not achievement-like.
11. Prefer shared tooltip-based section descriptions over inline explanatory paragraphs so the player page stays dense without becoming text-heavy.

## Explanatory Copy Rules

Keep supporting copy concise, factual, and secondary to the data table and summary strip.

1. Section-level explanation should live in informational tooltips attached to section headers, not in repeated inline paragraphs.
2. Tooltip copy should explain what the section measures and how to read it, not make evaluative claims about the player.
3. Badge summaries should stay grounded in visible counts and rollups, not inferred broad narratives.
4. If future iterations reintroduce narrative copy, require at least `3` rows with usable `ship_type` or `ship_tier` metadata before making class or tier claims.

Example safe tooltip copy:

- `Efficiency badges mark a player's best qualifying ship performances in Tier V+ Random Battles.`
- `This section adds a peak-performance lens to the existing top-ships and tier or type views.`

## Validation

### Frontend checks

1. Run `cd client && npm run build`.
2. Confirm the player page still renders for:
   - a visible player with badge rows
   - a visible player with no badge rows
   - a hidden player
3. Confirm the new section appears between randoms and ranked sections.
4. Confirm the summary strip remains readable on narrow widths.
5. Confirm the table sorts by badge strength correctly.

### Data checks

1. Verify `Expert`, `Grade I`, `Grade II`, `Grade III` labels match the stored mapping.
2. Verify rows missing metadata still appear in the table.
3. Verify class and tier rollups exclude incomplete rows.
4. Verify overlap markers only appear when the row actually matches a current top-ship row.

### Product sanity checks

1. The section should read as an analysis aid, not as a detached trophy wall.
2. Tooltip copy and summary labels should remain restrained for sparse datasets.
3. The page should not trigger any new browser-initiated WG API traffic.

## Rollout Notes

This feature can ship without backend API changes because the stored player payload already carries the underlying badge rows.

## Execution Status

- Implemented the `Efficiency Badges` section on the player detail page below randoms and above ranked content.
- Reused stored `randoms_json` and `efficiency_json` from the player payload, with no new browser-triggered WG API calls.
- Updated stored badge rows to include `top_grade_label` alongside the existing `badge_label` alias so docs, UI, and fetch/crawl outputs align.
- Replaced inline player-detail section descriptions with shared informational tooltip headers so the page keeps context without adding more body copy.
- Added regression tests for badge hydration, clan-crawl persistence, and player-detail API exposure.
- Validated on `2026-03-15`:
  - focused Django suite: `14` tests passed
  - `cd client && npm run build`: passed
  - site smoke task: passed

If future reuse expands beyond the player page, consider adding a backend-derived summary payload to keep aggregation logic consistent across multiple surfaces.

## Rollback

If the section proves too noisy or misleading:

1. remove the player-detail component insertion,
2. keep backend badge hydration intact,
3. preserve the raw data for future use,
4. revisit whether the better next step is a smaller summary-only card or a different explorer surface.
