# Feature Spec: Player Detail Clan Battle Shield Last-Known-State Hydration

_Drafted: 2026-03-17_

## Goal

Change the player-detail header shield so it renders immediately from the last known local state, then reconciles against the existing clan-battle fetch without withholding the icon until the async request completes.

The intended user-facing outcome is:

1. the player detail header can show a shield on first paint when Battlestats already knows the player is a clan-battle enjoyer,
2. the shield does not flicker in late solely because the child clan-battle component finished loading,
3. the client still reconciles against the authoritative fetch and updates only when the fetched summary materially changes the icon state.

## Current State

### Current player-detail header icon tray

The player-detail header currently renders these icon classes in `client/app/components/PlayerDetail.tsx`:

1. hidden mask icon,
2. clan leader crown,
3. PvE robot,
4. sleepy bed,
5. ranked star,
6. efficiency rank sigma,
7. clan battle shield.

### Current hydration pattern by icon

Immediate on initial player payload:

1. hidden mask icon
   - source: `player.is_hidden`
   - hydration: none beyond the initial player response
2. clan leader crown
   - source: `player.is_clan_leader`
   - hydration: none beyond the initial player response
3. PvE robot
   - source: local derivation from `total_battles` and `pvp_battles`
   - hydration: none beyond the initial player response
4. sleepy bed
   - source: local derivation from `days_since_last_battle`
   - hydration: none beyond the initial player response
5. ranked star
   - source: `highest_ranked_league` or fallback to `ranked_json`
   - hydration: none beyond the initial player response
6. efficiency rank sigma
   - source: `efficiency_rank_tier` and `has_efficiency_rank_icon`
   - hydration: none beyond the initial player response

Async after mount:

7. clan battle shield
   - current source: `clanBattleSummary` set by `PlayerClanBattleSeasons`
   - current fetch path: `GET /api/fetch/player_clan_battle_seasons/<player_id>/`
   - current behavior: the header starts with `clanBattleSummary = null`, so the shield is absent on first paint and only appears after the child component fetch completes and reports summary data back through `onSummaryChange`

### Why the current shield behavior is weaker than the other header icons

The shield is the only current header icon that is gated on a child component fetch rather than on the initial player detail payload.

That creates three UX problems:

1. first-paint inconsistency: the header looks incomplete compared with every other icon,
2. perceived flicker: the shield can pop in late even when Battlestats already had enough historical data to render it immediately,
3. unnecessary coupling: the header icon depends on a lower section finishing its own data lifecycle.

## Constraint Summary

From current repo doctrine and behavior:

1. prefer additive API changes over replacing current payloads,
2. reuse existing fetch paths and shared validation patterns,
3. avoid new browser-triggered WG API fan-out,
4. favor non-blocking background hydration over synchronous blocking,
5. preserve current visual language unless the task explicitly changes it.

This means the fix should not block player detail on the clan-battle seasons fetch and should not add a second browser fetch just for the shield.

## Proposed Product Behavior

### Primary behavior

On player detail:

1. render the shield immediately from a cached last-known summary delivered in the initial player payload,
2. continue to fetch clan-battle seasons through the existing `PlayerClanBattleSeasons` component,
3. reconcile the fetched summary against the cached summary,
4. update the shield only if the fetched result changes either:
   - qualification state, or
   - displayed win-rate color / tooltip content.

### Visual behavior

If a cached clan-battle summary is present and qualifies the player as a clan-battle enjoyer, the header should render the shield immediately.

Preferred rendering rules:

1. if cached overall win rate is known, use the existing win-rate color scale immediately,
2. if only a cached boolean qualification flag exists but no reliable cached win rate exists, allow a neutral fallback shield color such as the current Battlestats blue as a temporary display state,
3. once the fetched summary arrives, replace the fallback color only if the reconciled state differs.

The blue-shield example is acceptable as a fallback state, but the stronger product behavior is to carry enough cached summary to render the same color semantics immediately whenever possible.

## Recommended Delivery Shape

### Backend

Additive player-detail payload fields should be introduced on the player serializer for cached clan-battle header state.

Recommended payload fields:

1. `clan_battle_header_eligible`
2. `clan_battle_header_total_battles`
3. `clan_battle_header_seasons_played`
4. `clan_battle_header_overall_win_rate`
5. `clan_battle_header_updated_at`

These fields should be derived from already stored/local clan-battle summary state rather than requiring synchronous WG fetch on player-detail read.

The existing `GET /api/fetch/player_clan_battle_seasons/<player_id>/` endpoint remains the authoritative reconciliation lane.

### Frontend

`PlayerDetail.tsx` should:

1. initialize header shield state from the cached player payload,
2. keep rendering that initial state during `PlayerClanBattleSeasons` loading,
3. accept the authoritative fetched summary from `onSummaryChange`,
4. replace header state only when the fetched summary differs materially from the cached summary.

`PlayerClanBattleSeasons.tsx` should continue to own the detailed seasons fetch and summary computation, but it should no longer be the only source of truth for first paint.

## Reconciliation Rules

### Initial state derivation

The header shield should be initialized from cached payload fields using the same qualification threshold already enforced in the client today:

1. total clan-battle battles >= 40
2. seasons played >= 2

### Reconciliation trigger

When the `PlayerClanBattleSeasons` fetch completes, the header should update only if one of these changes:

1. cached eligible -> fetched ineligible
2. cached ineligible -> fetched eligible
3. both eligible, but fetched overall win rate changes enough to alter:
   - tooltip text, or
   - visible shield color band

### Non-update cases

Do not update the header shield when:

1. cached and fetched state are both ineligible,
2. cached and fetched state are both eligible and the fetched win rate leaves the shield in the same displayed state,
3. the fetch fails and cached state exists.

### Failure behavior

If the seasons fetch fails:

1. keep the cached shield state if one exists,
2. do not clear the icon solely because reconciliation failed,
3. allow the seasons section itself to show its existing local error state.

## Data Source Options

### Option A: Derive cached shield state from player payload only

Pros:

1. preserves single initial player-detail fetch,
2. gives the header immediate render parity with the other icons,
3. matches current doctrine best.

Cons:

1. requires additive serializer and summary support.

Verdict:

Recommended.

### Option B: Add a dedicated header shield fetch

Pros:

1. localized implementation.

Cons:

1. adds another browser request,
2. weakens the current fetch discipline,
3. duplicates logic already present in the seasons lane.

Verdict:

Not recommended.

### Option C: Keep current behavior and only style the late arrival better

Pros:

1. smallest code diff.

Cons:

1. does not solve first-paint incompleteness,
2. keeps the header dependent on async child hydration.

Verdict:

Not sufficient.

## Suggested Implementation Sequence

### Phase 1: Cached Header State Contract

1. identify the existing local clan-battle summary source that can back player-detail reads without live WG fetch,
2. expose additive cached header summary fields through the player serializer,
3. add API-facing tests for the new player payload fields.

### Phase 2: PlayerDetail Initialization

1. initialize a local `clanBattleHeaderState` from the player payload,
2. render `HeaderClanBattleShield` from that state immediately,
3. keep the existing `PlayerClanBattleSeasons` fetch.

### Phase 3: Reconciliation Logic

1. compare fetched summary with cached header state,
2. update only on material change,
3. keep stale cached state on fetch failure.

### Phase 4: Validation

1. player-detail tests for immediate shield render from cached payload,
2. tests for no update when fetched summary is equivalent,
3. tests for update when eligibility or displayed color changes,
4. tests for preserving cached shield during fetch failure,
5. focused manual verification on at least one qualifying player and one non-qualifying player.

## Acceptance Criteria

1. A player with cached qualifying clan-battle summary data shows a shield immediately on first paint.
2. A player without cached qualifying state does not show a shield before reconciliation.
3. The existing clan-battle seasons fetch remains in place and still powers the detailed section.
4. The header shield updates only when reconciliation changes displayed state.
5. A failed seasons fetch does not clear a valid cached shield.
6. No new browser request is added solely for the shield.

## Validation Plan

Focused client validation should cover:

1. `PlayerDetail` immediate header render behavior,
2. reconciliation no-op behavior,
3. reconciliation update behavior,
4. failure fallback behavior.

Focused backend validation should cover:

1. player serializer payload shape,
2. cached clan-battle summary field derivation,
3. no contract drift for existing player detail consumers.

## Open Questions

1. What is the best existing local source for cached clan-battle summary on player detail: precomputed cache, denormalized player summary, or derived local store?
2. Do we want to expose a neutral fallback shield when only eligibility is known, or require cached win rate so color is always semantically meaningful?
3. Should `clan_battle_header_updated_at` be displayed anywhere or remain debug/diagnostic only?
