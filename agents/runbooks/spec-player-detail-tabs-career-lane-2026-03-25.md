# Spec: Player Detail Secondary Tabs Migration

_Captured: 2026-03-25_

_Status: QA reviewed, implemented, and revised on 2026-03-25_

_Current shipped note: the visible tab label is `Clan Battles`; `Career` remains the conceptual lane name used in this runbook and some internal component naming._

## Goal

Complete the second tranche of the player-detail tab migration by moving three player-specific secondary sections out of the left column and into the tabbed insights surface:

1. `Clan Battle Seasons`
2. `Efficiency Badges`
3. `Performance by Tier`

Follow-up adjustment in the same tranche:

1. `Efficiency Badges` lives in its own tab
2. `Performance by Tier` moves into the `Profile` tab
3. `Career` narrows to clan-battle-only detail

## Why This Tranche Exists

The first tab tranche only moved the heavy lower right-column stack. The player page still kept three non-primary sections in the left column below the clan plot and clan-members list.

That left the route with two competing content patterns:

1. a tabbed single-active heavy lane on the right
2. an older stacked deferred lane on the left

The page remained more scattered than necessary, and the left column still carried player-specific sections that are not required for first understanding of the clan surface.

## Decision

Keep the left column focused on clan context only:

1. clan plot
2. clan members

Move the player-specific sections into focused tabs inside the existing insights surface:

1. `Clan Battle Seasons` into `Career`
2. `Efficiency Badges` into `Badges`
3. `Performance by Tier` into `Profile`

This keeps the previous tranche direction intact: one explicit user-controlled lane for optional heavier player analysis.

## UX Shape

### Left column after migration

1. clan heading or no-clan header
2. clan plot when present
3. clan members when present
4. no player-specific chart or badge sections below that point

### Tabs after migration

1. `Population`
2. `Ships`
3. `Ranked`
4. `Profile`
5. `Badges`
6. `Clan Battles` (career lane)

### Profile tab contents

1. `Tier vs Type Profile`
2. `Performance by Ship Type`
3. `Performance by Tier`

Reasoning:

1. the tier chart is another profile lens on ship mix and performance
2. it fits naturally beside the existing tier/type and ship-type profile views

### Badges tab contents

1. `Efficiency Badges`

Reasoning:

1. badges are compact but conceptually distinct from the profile charts
2. giving them a dedicated tab avoids mixing summary badges with heavier visual panels

### Career tab contents

Recommended order:

1. `Clan Battle Seasons` when the player has a clan context or stored clan-battle rows

Reasoning:

1. `Clan Battle Seasons` is the only remaining clan-adjacent secondary section
2. keeping `Career` narrow makes the tab purpose clearer

Current implementation label note:

1. the user-facing tab label is `Clan Battles`
2. the narrower career-lane intent remains unchanged

## Data Strategy

This tranche keeps the existing doctrine and cache-first rules.

### Clan battle seasons

Policy:

1. fetch only when `Career` becomes active
2. continue using the existing player-specific endpoint
3. allow the current cached player payload to drive the header shield until the tab is opened
4. when the tab is opened, allow the fetched summary to refine the header shield state through the existing callback path

### Efficiency badges

Policy:

1. no new request path is needed in this tranche
2. continue using the existing `efficiency_json` already present on the player payload
3. render inside the dedicated `Badges` tab only when that tab is active

### Performance by tier

Policy:

1. fetch only when `Profile` becomes active
2. continue using `tier_data/<player_id>/`
3. keep the panel on the longer-lived panel fetch TTL

## QA Review Outcome

Reviewed against the current implementation before coding.

Findings:

1. the left-column moved sections were still present and duplicated the intent of the tabbed surface
2. direct tests were missing for `PlayerClanBattleSeasons` and `TierSVG`
3. the earlier player-page clan-chart proof remained valid and should stay in place

Accepted implementation bar:

1. move the three sections behind focused tabs
2. remove them from the left column
3. preserve hidden-player behavior
4. add focused tests for the newly moved components and updated tab surface

## Validation

Required checks for this tranche:

1. `Career`, `Badges`, and `Profile` render the moved section headings only when active
2. left column no longer renders `Clan Battle Seasons`, `Efficiency Badges`, or `Performance by Tier`
3. `PlayerClanBattleSeasons` has direct test coverage
4. `TierSVG` has direct test coverage
5. existing player-detail and player-page clan-chart tests still pass

## Rollback

Rollback is straightforward:

1. restore the three sections to the left column
2. remove the `Career` and `Badges` tab changes from the insights surface
3. keep any independent test improvements and component-local fixes that remain valid
