# Feature Spec: Streamlined Player PvE Icon Heuristic

_Drafted: 2026-03-17_

## Goal

Replace the current split PvE-icon logic with one consistent ruleset that marks players who have made a significant, visible investment in PvE without over-labeling high-volume PvP players who merely have a large absolute PvE count.

The desired product outcome is:

1. one shared PvE icon rule across player detail, clan members, and landing/search surfaces,
2. no absolute-battles-only exception that can label primarily PvP players as PvE players,
3. a heuristic that is simple enough to explain from stored player totals alone.

## Current State

There are currently two different PvE-icon rules in production code.

### Current player-detail header rule

In [client/app/components/PlayerDetail.tsx](client/app/components/PlayerDetail.tsx), the header robot uses local client derivation:

1. `pve_battles = max(total_battles - pvp_battles, 0)`
2. show the icon only if:
   - `total_battles > 500`
   - `pve_battles > pvp_battles`

This is effectively a majority-PvE rule.

### Current clan and landing rule

In [server/warships/data.py](server/warships/data.py), the shared helper is:

1. `pve_battles = max(total_battles - pvp_battles, 0)`
2. show the icon only if:
   - `total_battles > 500`
   - `pve_battles > 0.75 * pvp_battles`, or
   - `pve_battles >= 4000`

This rule is used by clan members and landing payloads.

## Why The Current Setup Is Unsatisfactory

The current setup has two separate problems.

### Problem 1: surface inconsistency

The same player can qualify on clan or landing surfaces but fail on player detail because those surfaces do not use the same rule.

That weakens trust in the icon and makes the behavior hard to reason about.

### Problem 2: the absolute `>= 4000 PvE battles` override is too permissive

An absolute-only override incorrectly labels some players who have a real PvE history but whose overall play profile is still mostly PvP.

The user-provided examples make that clear.

## Example Profiles

Using the current local player API, the relevant totals for the requested example players are:

| Player         | Total Battles | PvP Battles | Derived PvE Battles | PvE Share of Total | PvE / PvP | Desired Outcome |
| -------------- | ------------: | ----------: | ------------------: | -----------------: | --------: | --------------- |
| Mebuki         |        23,851 |      19,629 |               4,222 |              17.7% |     0.215 | No              |
| Hungria15      |         4,951 |         464 |               4,487 |              90.6% |     9.670 | Yes             |
| ShrimpDance    |        34,540 |      29,047 |               5,493 |              15.9% |     0.189 | No              |
| UnitedShipcare |         9,576 |       3,111 |               6,465 |              67.5% |     2.078 | Yes             |
| eisenhowers    |        14,344 |       9,549 |               4,795 |              33.4% |     0.502 | Yes             |

These examples show why the `>= 4000 PvE battles` exception should not stand on its own:

1. Mebuki and ShrimpDance both clear 4k PvE battles,
2. but their PvE share is only about 16% to 18% of total battles,
3. so the icon would overstate their playstyle if it treated high absolute PvE count as sufficient by itself.

At the same time, eisenhowers demonstrates that the new heuristic should not require a strict majority-PvE profile. A player can still be meaningfully PvE-focused without PvE exceeding PvP outright.

## Design Constraints

From current repo doctrine and product behavior:

1. prefer one shared backend rule over multiple client-local variations,
2. reuse existing stored totals instead of introducing new upstream fetches,
3. keep the rule explainable from current player fields,
4. preserve the existing robot icon treatment unless this task explicitly changes visuals,
5. avoid adding denormalized state unless the payload needs a shared derived flag.

## Data Shape Available Today

The current heuristic can already be derived from stored player fields:

1. `Player.total_battles`
2. `Player.pvp_battles`

Derived values:

1. `pve_battles = max(total_battles - pvp_battles, 0)`
2. `pve_share_total = pve_battles / total_battles`

These are enough for a better heuristic. No new WG payload or browser fetch is needed.

## Heuristic Options Considered

### Option A: keep the current clan helper everywhere

Rule:

1. `total_battles > 500`
2. `pve_battles > 0.75 * pvp_battles`, or
3. `pve_battles >= 4000`

Pros:

1. smallest implementation change,
2. already exists in shared backend code.

Cons:

1. still misclassifies Mebuki and ShrimpDance,
2. retains the absolute-count-only loophole,
3. does not match the requested fairness constraint.

Verdict:

Not acceptable.

### Option B: strict majority-PvE rule everywhere

Rule:

1. `total_battles > 500`
2. `pve_battles > pvp_battles`

Pros:

1. very easy to explain,
2. already matches current player-detail behavior,
3. correctly excludes Mebuki and ShrimpDance.

Cons:

1. too strict for players like eisenhowers,
2. turns the icon into a near-exclusive label for mostly-or-only-PvE accounts,
3. misses players with large, sustained PvE investment that still coexists with substantial PvP.

Verdict:

Too strict.

### Option C: combine absolute PvE investment with PvE share of total

Rule:

1. `total_battles > 500`
2. `pve_battles >= 1500`
3. `pve_share_total >= 0.30`

Pros:

1. excludes Mebuki and ShrimpDance,
2. includes Hungria15, UnitedShipcare, and eisenhowers,
3. removes the absolute-count-only loophole,
4. remains simple and explainable,
5. uses only currently stored totals.

Cons:

1. any fixed threshold near 30% will create some boundary cases,
2. lower-volume PvE-heavy players below 1500 derived PvE battles will not get the icon even if their mix is extreme.

Verdict:

Recommended starting point.

## Recommended Heuristic

Use one shared helper and one shared payload-derived boolean with this rule:

1. `pve_battles = max(total_battles - pvp_battles, 0)`
2. `pve_share_total = pve_battles / total_battles`
3. show the PvE icon only when all of the following are true:
   - `total_battles > 500`
   - `pve_battles >= 1500`
   - `pve_share_total >= 0.30`

### Why this is a fairer starting point

This rule requires both:

1. meaningful absolute PvE volume,
2. and a meaningfully PvE-shaped overall battle mix.

That addresses both failure modes:

1. high-volume PvP players with a side investment in PvE no longer get the icon,
2. but players with thousands of PvE battles and a clearly PvE-skewed profile still do.

### Example classification under the recommended rule

1. Mebuki: fails because 17.7% PvE share is too low.
2. Hungria15: passes on both absolute volume and share.
3. ShrimpDance: fails because 15.9% PvE share is too low.
4. UnitedShipcare: passes on both absolute volume and share.
5. eisenhowers: passes because 4,795 PvE battles and 33.4% PvE share represent meaningful sustained PvE investment.

## Product Contract Recommendation

To remove the current surface split, the PvE icon should become a single backend-derived flag published everywhere that needs it.

### Recommended payload shape

Add `is_pve_player` to the player-detail payload if it is not already present there.

Current state:

1. clan and landing rows already publish a derived `is_pve_player`,
2. player detail currently derives its own local rule.

Recommended state:

1. compute `is_pve_player` once in shared backend logic,
2. publish that same boolean to player detail,
3. make player detail consume the boolean instead of re-deriving locally.

Optional diagnostic additions for player detail, only if the team wants easier QA/debugging:

1. `pve_battles`
2. `pve_share_total`

These diagnostics are not required for the icon itself because the raw totals already exist in the player payload.

## Implementation Shape

### Phase 1: settle the rule in one helper

1. replace the current `is_pve_player(total_battles, pvp_battles)` rule in [server/warships/data.py](server/warships/data.py),
2. keep the helper as the single source of truth for derived PvE eligibility.

### Phase 2: unify the player-detail surface

1. expose `is_pve_player` on the player serializer additively,
2. update player detail to use that shared derived field,
3. remove the local majority-PvE-only calculation from the player header.

### Phase 3: validate all affected surfaces

1. clan members,
2. player detail header,
3. landing/search surfaces that show the robot icon.

## Acceptance Criteria

1. The PvE icon uses one consistent rule across all current surfaces.
2. Mebuki does not show the PvE icon.
3. Hungria15 shows the PvE icon.
4. ShrimpDance does not show the PvE icon.
5. UnitedShipcare shows the PvE icon.
6. eisenhowers shows the PvE icon.
7. No surface uses the old `>= 4000 PvE battles` override by itself.
8. Player detail no longer uses a client-local PvE rule different from clan or landing surfaces.

## Validation Plan

Focused backend validation should cover:

1. helper-level boundary tests around:
   - `pve_battles = 1499` vs `1500`
   - `pve_share_total = 0.299...` vs `0.30`
   - `total_battles = 500` vs `501`
2. player-detail API payload behavior,
3. clan-members payload behavior,
4. landing payload behavior if it shows the icon.

Focused client validation should cover:

1. player detail consumes the shared backend boolean,
2. clan members still render the robot from payload state,
3. no client surface reintroduces its own divergent PvE rule.

Manual validation should include the five named example players.

## Non-Goals

1. do not redesign the PvE robot icon itself,
2. do not introduce a full battle-mode taxonomy,
3. do not add upstream WG calls solely for PvE classification,
4. do not persist a database field if a shared derived helper and additive payload are sufficient.

## Open Questions

1. Is `1500` the right absolute floor, or should it be `1000` after broader field inspection?
2. Is `30%` the right mix threshold, or should it be `33%` if the team wants a slightly stricter interpretation of "meaningful PvE investment"?
3. Do we want to expose diagnostic derived fields on player detail, or is the shared boolean enough?
