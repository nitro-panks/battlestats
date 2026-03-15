# Feature Request: Efficiency Badges Player Story

_Drafted: 2026-03-15_

## Goal

Turn ship-level Efficiency Badges into a player-story surface on the existing player detail page so a visitor can quickly answer questions like:

- what classes this player is strongest in,
- whether their best ships cluster in top tier or mid tier,
- whether their mastery profile is broad or concentrated,
- whether their badge strength agrees with their current top-ships and tier/type charts.

## Why This Matters

The repo now hydrates `ships/badges` into `Player.efficiency_json`, but that data is not yet visible in the product. The player page already has the right context for this story:

- top ships by random battles,
- performance by tier,
- performance by ship type,
- tier vs type profile,
- playstyle verdict.

Efficiency Badges add a second lens. Top ships show volume and win rate. Badges show peak qualifying performances on specific ships. Together they tell a more complete story about what a player actually excels at.

## Current Product Surface

Current player detail layout in the repo:

- `Top Ships (Random Battles)` chart for top-volume random ships.
- `Performance by Tier` chart.
- `Performance by Ship Type` chart.
- `Tier vs Type Profile` heatmap.
- `Playstyle` verdict card.

Current backend reality:

- `PlayerSerializer` already exposes model fields, so `efficiency_json` is available to the client payload once the TypeScript shape is extended.
- `efficiency_json` rows now include `ship_id`, `top_grade_class`, `top_grade_label`, `badge_label`, `ship_name`, `ship_tier`, and `ship_type` when local ship metadata is available.
- migration `warships.0025_player_efficiency_fields` has been applied.

## Recommended Product Shape

### Primary addition

Add a new player-detail section named `Efficiency Badges` directly below `Top Ships (Random Battles)` and above ranked sections.

This section should have two parts:

1. a compact summary strip that explains the player's badge story,
2. a sortable ship table for the underlying badge rows.

### Summary strip

The summary strip should answer four questions at a glance:

1. highest badge earned: `Expert`, `Grade I`, `Grade II`, or `Grade III`
2. strongest class: class with the best weighted badge score
3. strongest tier band: `V-VII`, `VIII`, `IX-X`, or similar grouped tier story
4. breadth vs concentration: whether badges are spread across many ships or concentrated in a few

Recommended summary metrics:

- `expert_ships`: count of badge rows with `top_grade_class = 1`
- `grade_i_plus_ships`: count of rows with class `1` or `2`
- `best_class_by_score`: badge-weighted score by ship type
- `best_tier_band_by_score`: badge-weighted score by tier band
- `badge_ship_coverage`: badge rows divided by all random-battle ships with enough metadata to compare

Suggested badge-weight mapping for summary math:

- `Expert = 4`
- `Grade I = 3`
- `Grade II = 2`
- `Grade III = 1`

This weighting is for internal aggregation only. The UI should still present official badge names, not numeric scores. It intentionally inverts the API numbering, where `top_grade_class = 1` is strongest.

### Ship table

Recommended columns:

- ship
- badge
- type
- tier
- appears in top ships: yes or no

Recommended default sort:

1. badge class ascending by strength,
2. ships that also appear in the randoms chart first,
3. battles desc when available through the top-ships join.

### Storytelling copy

The section should generate short, grounded copy rather than generic praise. Example patterns:

- `Strength concentrates in cruisers, especially Tier VIII-X.`
- `Most visible mastery sits in mid-tier destroyers rather than high-tier staples.`
- `Badges are broad across classes, but the sharpest peaks are in battleships.`

This copy should only be shown when enough rows exist to support the claim.

## Cross-Functional Input

### Engineering

Recommendation:

Use the existing player payload first instead of creating a new endpoint immediately.

Reasoning:

- `PlayerSerializer` already returns `efficiency_json`.
- the player detail route already fetches the player object.
- initial implementation can derive summary metrics client-side or through a shared helper with low complexity.

Engineering constraints:

- extend the frontend player type in `PlayerDetail.tsx` and upstream calling code to include `efficiency_json`
- normalize missing or partial rows defensively
- treat badge rows without ship metadata as valid but lower-fidelity rows
- avoid coupling this feature to a fresh WG API call on page render; it should rely on stored player data only

Escalation point:

If derived summary logic becomes too heavy or duplicated across surfaces, add a dedicated backend summary field or endpoint in a second tranche.

### UX

Recommendation:

Prefer a summary-plus-table pattern over a standalone chart for v1.

Reasoning:

- badges are ordinal categories, not continuous measures
- users will want to inspect exact ships, not just aggregates
- the player page already has several charts; adding another dense chart risks crowding and cognitive overload

UX requirements:

- explain badge meaning in one concise sentence
- keep official badge ordering obvious
- preserve scanability on mobile by allowing the table to collapse to stacked rows
- maintain continuity with current player-detail information hierarchy

### Design

Recommendation:

Render badges as small, deliberate rank chips instead of inventing trophy-heavy art.

Design direction:

- reuse the repo's blue player-detail palette as the base
- let badge chips carry distinction through border, fill, and label weight rather than loud saturation
- use a restrained accent hierarchy so `Expert` reads strongest, but all badge levels remain legible
- keep the section visually compatible with the existing cards and chart headings

Design caution:

Do not make the badge strip look like an achievement gallery disconnected from the statistical story. It belongs with analysis, not decoration.

### Data

Recommendation:

Treat badges as peak-signal metadata and avoid overclaiming stability.

Data guidance:

- badges represent best qualifying ship performances, not average ship strength
- derived story copy should be based on clusters and counts, not one exceptional ship
- rows missing `ship_type` or `ship_tier` should be excluded from class/tier rollups but still shown in the raw table
- if fewer than `3` badge rows have usable metadata, suppress story copy and show the raw list only

Recommended derived fields for v1:

- class score totals
- tier-band score totals
- strongest badge level counts
- top-ships overlap count
- metadata coverage count

## Proposed Scope

### In scope

1. Show `efficiency_json` on the player detail page.
2. Add one summary strip and one raw badge table.
3. Add lightweight derived storytelling copy.
4. Join badge rows against the existing top-ships data client-side when possible.
5. Show a clear empty state when no badge rows are present.

### Out of scope

1. Historical badge tracking over time.
2. Clan-wide badge aggregation.
3. Landing-page badge search or explorer filters.
4. New WG fetches triggered directly from the browser.
5. Treating badges as a replacement for win-rate or battle-volume charts.

## Empty And Edge States

Required states:

- no badges present
- hidden player
- rows present but incomplete ship metadata
- only one or two badge rows, insufficient for a meaningful story

Recommended copy for no-badge state:

`No Efficiency Badge data is stored for this player yet, or no qualifying ships have earned a badge.`

## Success Criteria

1. A user can identify the player's strongest class and tier concentration in under a few seconds.
2. Badge rows feel connected to existing top-ship and tier/type surfaces rather than bolted on.
3. The UI handles sparse badge data without broken or misleading narratives.
4. No new synchronous WG API traffic is introduced by opening a player page.

## Delivery Recommendation

Ship this in one tranche on the player detail page first. If the section proves useful, a later tranche can add badge-aware explorer filters or clan-level rollups.
