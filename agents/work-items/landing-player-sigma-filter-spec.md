# Feature Spec: Landing Active Players Sigma Filter

_Drafted: 2026-03-17_

## Goal

Add a new `Sigma` filter button to the landing page `Active Players` list so users can switch that list to the top 40 players by Battlestats efficiency rating.

The intended product outcome is:

1. landing users can move from the current `Random` and `Best` lists to a dedicated efficiency-ranked list,
2. the ranking is based on the existing published efficiency contract rather than a new client-side score,
3. the change stays bounded to the `Active Players` list and does not reopen the broader explorer or player-detail ranking model.

## Current State

### Current landing player modes

The landing page `Active Players` section in [client/app/components/PlayerSearch.tsx](client/app/components/PlayerSearch.tsx) currently supports two modes:

1. `Random`
2. `Best`

Those buttons drive the `mode` query passed to `/api/landing/players/`.

### Current best-mode behavior

The existing `best` mode is a win-rate-oriented surface, not an efficiency surface.

Current backend behavior in [server/warships/landing.py](server/warships/landing.py):

1. eligible players must be visible, recently active, and above the minimum PvP battle floor,
2. ordering is based on high-tier win rate where enough high-tier history exists,
3. overall PvP win rate is used as a fallback when high-tier history is missing.

That means `best` is a PvP-strength shortcut, not a Battlestats efficiency shortcut.

### Current efficiency state on landing

Landing rows now already receive the published efficiency fields and can render the sigma icon when appropriate:

1. `efficiency_rank_percentile`
2. `efficiency_rank_tier`
3. `has_efficiency_rank_icon`
4. `efficiency_rank_population_size`
5. `efficiency_rank_updated_at`

Current landing-row icon behavior remains dense-surface scoped:

1. rows show the inline sigma only for resolved `E` rows,
2. non-`E` rows can still carry published efficiency fields without rendering the icon,
3. hidden or unpublished rows remain suppressed.

## Why This Needs A Separate Spec

This feature adds a new ranking surface, not just another icon.

The open questions that need to be settled before implementation are:

1. what `efficiency rating` means operationally on landing,
2. which players qualify for the new `Sigma` list,
3. how ties and stale or unpublished rows should behave,
4. whether this should reuse the current landing endpoint or create a new one.

## Constraint Summary

From the current repo doctrine and shipped landing behavior:

1. prefer additive endpoint evolution over a second landing-player endpoint,
2. reuse the published efficiency contract instead of introducing client-local score math,
3. avoid new browser fetches or hydration lanes for landing filters,
4. keep the `Active Players` section as a bounded top-40 surface,
5. validate backend ordering and frontend mode switching together.

## Recommended Product Behavior

### Surface scope

This tranche should affect only the landing page `Active Players` section.

In scope:

1. add a `Sigma` button beside `Random` and `Best`,
2. fetch `mode=sigma` from the existing `/api/landing/players/` endpoint,
3. render the returned 40-player list in the existing landing row component.

Out of scope:

1. `Recently Viewed` players,
2. player explorer sorting or filters,
3. player-detail header ranking behavior,
4. changes to how the inline sigma icon itself is displayed on dense rows.

### Button label and placement

The new button should be named exactly `Sigma`.

Recommended UI behavior:

1. place it in the existing `Active Players` mode toggle group,
2. use the same button styling and `aria-pressed` semantics as `Random` and `Best`,
3. keep `Random` as the default landing mode unless product explicitly asks to change the initial default later.

### Meaning of `efficiency rating`

For this landing feature, `efficiency rating` should mean the already-published Battlestats efficiency rank percentile.

The ranking source should therefore be:

1. `PlayerExplorerSummary.efficiency_rank_percentile`

Not recommended for this feature:

1. introducing a new landing-only score,
2. recomputing badge-strength math in the landing serializer,
3. sorting by `player_score` as a proxy for efficiency.

Reasoning:

1. the percentile is already the repo’s public Battlestats efficiency contract,
2. it is already freshness-gated for public use,
3. it avoids a second meaning of `sigma` on the same landing page.

## Backend Contract Recommendation

### Endpoint shape

Extend the existing landing players mode contract in [server/warships/landing.py](server/warships/landing.py):

1. add `sigma` to `LANDING_PLAYER_MODES`,
2. support `GET /api/landing/players/?mode=sigma&limit=40`,
3. keep the existing endpoint and response shape.

No new endpoint should be created for this tranche.

### Eligibility rule

The `sigma` list should include only players who have a fresh, publicly published efficiency rank.

That means the list should exclude:

1. hidden players,
2. players without a fresh published efficiency snapshot,
3. players whose `efficiency_rank_percentile` is `null`,
4. players filtered out by the existing landing-player visibility constraints for active-player lists.

Recommended active-player constraints to preserve:

1. visible account,
2. recent activity window aligned with other landing-player modes,
3. minimum PvP sample floor aligned with landing-player modes unless implementation evidence suggests sigma needs its own stricter threshold.

### Ordering rule

The `sigma` list should order players by published efficiency percentile descending.

Recommended tie-break order:

1. `efficiency_rank_percentile` descending,
2. `explorer_summary__player_score` descending,
3. `pvp_ratio` descending,
4. `name` ascending.

Reasoning:

1. percentile is the primary user-facing efficiency contract,
2. `player_score` is an existing stable Battlestats summary signal that can break ties without inventing new semantics,
3. deterministic secondary ordering avoids row jitter in cached payloads.

### Limit behavior

The `Sigma` filter should return the top 40 qualifying players, subject to the existing landing-player limit behavior.

Recommended contract:

1. preserve the current `limit` parameter,
2. cap returned rows to the requested limit up to the existing endpoint maximum,
3. default to 40 for the landing surface.

## Frontend Recommendation

### Mode wiring

Update [client/app/components/PlayerSearch.tsx](client/app/components/PlayerSearch.tsx) so the local mode union becomes:

1. `random`
2. `best`
3. `sigma`

Then:

1. add a `Sigma` toggle button,
2. let the existing `fetchLandingPlayers(mode)` call request the new mode,
3. keep the current row renderer instead of creating a sigma-specific row component.

### Row presentation

The `Sigma` filter changes which players are listed, not how the row component works.

Recommended row behavior:

1. reuse the existing landing player row renderer,
2. preserve the current icon tray behavior,
3. continue showing the inline sigma icon only for resolved `E` rows unless a separate product decision expands dense-row icon visibility.

This means some players in the `Sigma` list may rank highly by percentile without showing a visible sigma icon if they are not in tier `E`.

That is acceptable for this tranche because:

1. the filter is about ranking the list by efficiency,
2. the row icon policy is a separate dense-surface design choice,
3. the tooltip contract already exists for rows that do render the icon.

## Options Considered

### Option A: sort Sigma by published percentile

Pros:

1. directly matches the existing public efficiency contract,
2. no new score semantics,
3. easiest to explain and test.

Cons:

1. percentile ties are possible and need deterministic secondary ordering.

Verdict:

Recommended.

### Option B: sort Sigma by underlying normalized badge-strength score

Pros:

1. potentially finer ordering than percentile.

Cons:

1. exposes a less-public internal ranking metric,
2. increases contract surface,
3. creates a new product meaning of `Sigma` that current landing docs do not describe.

Verdict:

Not recommended for the first tranche.

### Option C: treat Sigma as `Expert-only` instead of top percentile ordering

Pros:

1. simpler eligibility rule,
2. visually aligned with the current row icon visibility policy.

Cons:

1. does not match the user request for the top 40 by efficiency rating,
2. collapses ranking into a tier gate rather than a sorted leaderboard,
3. can return fewer than 40 rows depending on population.

Verdict:

Not recommended.

## Suggested Implementation Sequence

### Phase 1: Backend Mode

1. extend landing mode normalization to accept `sigma`,
2. add a sigma-mode builder ordered by published efficiency percentile,
3. keep the existing landing row serializer and payload shape.

### Phase 2: Frontend Filter

1. add the `Sigma` button to the `Active Players` toggle group,
2. extend the local mode union and fetch wiring,
3. reuse the existing landing row renderer for results.

### Phase 3: Validation

1. backend tests for sigma-mode ordering, eligibility, and limit behavior,
2. frontend tests for button rendering, fetch mode switching, and sigma-list display,
3. manual landing-page verification with at least one `E` player and one non-`E` published efficiency player.

## Acceptance Criteria

1. The landing page `Active Players` section shows a third filter button named `Sigma`.
2. Clicking `Sigma` requests the existing landing players endpoint with `mode=sigma`.
3. The sigma list returns at most 40 players ordered by published efficiency percentile descending.
4. Hidden or unpublished players are not included in the sigma list.
5. The feature does not add a new browser fetch path beyond the current landing players request.
6. Existing `Random` and `Best` behavior remains unchanged.

## Validation Plan

Focused backend validation should cover:

1. sigma mode is accepted by landing mode normalization,
2. sigma mode excludes hidden and unpublished rows,
3. sigma mode orders by percentile descending with deterministic tie-breaks,
4. sigma mode respects the requested limit.

Focused client validation should cover:

1. the `Sigma` button renders in the `Active Players` control group,
2. clicking it triggers the `mode=sigma` landing request,
3. the returned sigma rows render in the existing player row UI,
4. switching back to `Random` or `Best` still works.

## Non-Goals

1. do not change the underlying efficiency percentile model,
2. do not add a new public endpoint for sigma-filtered players,
3. do not change the dense-row sigma icon visibility rule in this tranche,
4. do not add explorer-level filtering or sorting as part of this landing feature.
