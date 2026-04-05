# Runbook: Best Player CB Ranking

_Created: 2026-04-05_

_Status: Implemented locally 2026-04-05; pending deploy-time snapshot regeneration_

## Purpose

Hand off the Best players -> CB sort ranking change to the agentic personas for implementation.

The goal is to make clan-battle ranking more equitable by rewarding players who sustain strong CB win rates over much larger battle samples, instead of flattening the volume contribution too early.

This runbook covers the dedicated landing-player CB sort only:

1. `/api/landing/players/?mode=best&sort=cb`
2. CB snapshot materialization for Best-player payloads
3. landing-player cache invalidation and warming after the scoring change

This runbook does not change:

1. the overall Best-player score
2. player/profile header eligibility or CB badges
3. clan-level Best-clan ranking
4. raw CB data fetching or persistence contracts

## Source Of Truth

Use these docs together:

1. [agents/runbooks/spec-best-player-subfilters.md](agents/runbooks/spec-best-player-subfilters.md)
2. [agents/runbooks/runbook-landing-best-player-subsort-materialization-2026-04-05.md](agents/runbooks/runbook-landing-best-player-subsort-materialization-2026-04-05.md)
3. [agents/runbooks/runbook-api-surface.md](agents/runbooks/runbook-api-surface.md)

Use these code paths as the implementation anchors:

1. `server/warships/landing.py` for the CB ranking helper, CB list builder, snapshot materialization, and landing-player cache invalidation
2. `server/warships/data.py` for CB eligibility and the persisted explorer-summary CB fields
3. `server/warships/models.py` for `PlayerExplorerSummary` and `LandingPlayerBestSnapshot`

## Desired Outcome

At the end of this tranche, the repo should have:

1. a defensible CB ranking heuristic that incorporates confidence from CB battle count
2. unchanged CB eligibility scope for the first rollout
3. preserved landing API contract for `mode=best&sort=cb`
4. focused regression tests proving large-sample durability matters
5. a documented propagation sequence for snapshot regeneration and player-cache invalidation

## Implementation Status

Implemented in code on 2026-04-05:

1. the dedicated `mode=best&sort=cb` score now uses a Wilson lower-bound style confidence adjustment over CB WR and CB battles
2. `clan_battle_seasons_participated` remains a secondary durability signal
3. the overall Best-player score remains unchanged
4. CB snapshot materialization remains the serving path
5. focused backend tests now cover same-WR different-volume ordering and tiny-sample outlier suppression

Still required after deploy:

1. regenerate `LandingPlayerBestSnapshot` rows for `sort='cb'`
2. invalidate landing-player caches per realm
3. warm `players_best_cb` and related entity caches

## Current Problem

The current player CB ranking in `server/warships/landing.py` uses:

```text
cb_sort_score =
  0.55 * normalized_cb_wr
  + 0.25 * normalized_cb_volume
  + 0.20 * normalized_cb_season_depth
```

This is directionally correct, but the volume term saturates too early. Once two players are both well above the volume cap, the model no longer meaningfully distinguishes between a player who sustained the same WR over roughly `2,000` battles and another who sustained it over roughly `20,000` battles.

That makes the sort closer to a WR-led ranking than intended for very large CB samples.

## Recommended Heuristic

### Primary recommendation

Rank the CB list by a credibility-adjusted CB WR instead of the current capped composite.

Preferred first implementation:

1. compute a Wilson lower-bound style score from `clan_battle_overall_win_rate` and `clan_battle_total_battles`
2. keep `clan_battle_seasons_participated` as a secondary durability signal or tie-breaker
3. keep the existing `is_clan_battle_enjoyer(...)` gate for the first slice

Why this is preferred:

1. it directly rewards stronger evidence, not just raw observed WR
2. it naturally differentiates `60%` over `20,000` battles from `60%` over `2,000`
3. it avoids adding schema or payload changes in the first tranche
4. it is easy to explain and test

### Acceptable fallback

If Wilson scoring feels too opaque for the first merge, use an empirical-Bayes shrinkage model centered on a neutral baseline such as `50%`.

That still satisfies the product intent, but Wilson is the preferred default because it is easier to defend as a confidence-aware ranking.

## Data Inputs

Use only already-persisted CB summary fields for the first implementation:

1. `PlayerExplorerSummary.clan_battle_total_battles`
2. `PlayerExplorerSummary.clan_battle_seasons_participated`
3. `PlayerExplorerSummary.clan_battle_overall_win_rate`
4. `PlayerExplorerSummary.clan_battle_summary_updated_at`

Keep the current CB eligibility gate:

1. `is_clan_battle_enjoyer(total_battles, seasons_participated)`

Do not add a migration or new persisted ranking field in this tranche.

## Agent Routing

- Project Coordinator owns routing, doc reconciliation, and final handoff hygiene.
- Project Manager owns scope lock, non-goals, acceptance criteria, and rollout boundaries.
- Architect owns the scoring-model review, cache propagation shape, and rollback plan.
- Engineer-Web-Dev owns implementation in `server/warships/landing.py`, focused tests, and any required doc touch-ups in code comments only if necessary.
- QA owns regression design and release-readiness judgment for ranking correctness and propagation.
- Safety reviews blast radius and confirms the change does not widen hidden-profile exposure, invalidate unrelated caches, or introduce unbounded refresh behavior.
- UX and Designer are not primary owners for this tranche because there is no intended UI shape change. Pull them in only if tooltip or explanatory copy changes are introduced.

## Task 1: Lock Scope And Acceptance Criteria

### Goal

Keep this as a ranking-function change on the dedicated CB sort, not a broad competitive-surface refactor.

### Required decisions

1. change only `mode=best&sort=cb`
2. do not change `_normalize_best_clan_score(...)` in the overall Best-player path
3. do not change CB eligibility thresholds in `is_clan_battle_enjoyer(...)`
4. do not add migrations in the first tranche

### Acceptance criteria

1. the public route `/api/landing/players/?mode=best&sort=cb` remains unchanged
2. players with the same raw CB WR are ranked higher when that WR is supported by a meaningfully larger CB sample
3. snapshot materialization and landing warmers continue to serve the CB list through the existing cache-first path
4. focused backend tests cover the new confidence behavior and cache propagation

## Task 2: Replace The CB Ranking Helper

### Goal

Implement a confidence-aware CB score in `server/warships/landing.py`.

### Files To Update

1. `server/warships/landing.py`

### Required implementation shape

1. replace or refactor `_calculate_landing_cb_sort_score(...)`
2. use `clan_battle_overall_win_rate` and `clan_battle_total_battles` as the primary scoring inputs
3. keep `clan_battle_seasons_participated` as a secondary durability signal rather than the main rank driver
4. keep deterministic tie-breakers so output ordering is stable

### Recommended ordering

```text
1. credibility_adjusted_cb_wr DESC
2. raw_cb_wr DESC
3. clan_battle_total_battles DESC
4. clan_battle_seasons_participated DESC
5. name ASC
```

### Guardrails

1. do not trigger new upstream WG calls in the landing path
2. do not read per-season player CB payloads during landing builds
3. do not add unbounded computation to request-time builders beyond the existing snapshot build path

## Task 3: Keep Snapshot Materialization As The Serving Path

### Goal

Preserve the current Best-player snapshot architecture while changing only the CB ordering semantics.

### Files To Verify Or Update

1. `server/warships/landing.py`
2. `server/warships/tasks.py`
3. `server/warships/signals.py`

### Required behavior

1. `materialize_landing_player_best_snapshot('cb', realm=...)` persists the new CB order in `LandingPlayerBestSnapshot`
2. `materialize_landing_player_best_snapshots(...)` continues to include `cb`
3. the daily `landing-best-player-snapshot-materializer-{realm}` schedule remains valid without new task topology

## Task 4: Test The New Ranking And Propagation

### Goal

Prove both ranking correctness and unchanged cache/snapshot behavior.

### Files To Update

1. `server/warships/tests/test_views.py`
2. `server/warships/tests/test_landing.py`

### Required test additions

1. a CB sort test where two players have the same raw CB WR and the much larger sample ranks first
2. a CB sort test where a smaller-sample higher WR does not automatically beat a much larger-sample near-peer WR if the confidence-adjusted score says otherwise
3. a snapshot test proving `materialize_landing_player_best_snapshot('cb')` preserves the new ordering
4. existing landing-cache tests still prove namespace bumping and published-cache behavior remain correct for player Best payloads

### Suggested focused validation

1. `cd server && python -m pytest warships/tests/test_views.py -k "landing_players_best_cb_sort or landing_players_best" -x --tb=short`
2. `cd server && python -m pytest warships/tests/test_landing.py -k "materialize_landing_player_best_snapshot or warm_landing_page_content or invalidate_landing_player_caches" -x --tb=short`

## Task 5: Document Propagation And Rollout

### Goal

Make the rollout steps explicit so production ordering changes are intentional and reproducible.

### Required docs to update

1. this runbook
2. `agents/runbooks/runbook-landing-best-player-subsort-materialization-2026-04-05.md`
3. `agents/runbooks/spec-best-player-subfilters.md`

### Propagation steps after deploy

Per realm:

1. regenerate the `LandingPlayerBestSnapshot` row for `sort='cb'`
2. call `invalidate_landing_player_caches(include_recent=True, realm=...)`
3. run `warm_landing_page_content(...)` so `players_best_cb` is rebuilt immediately
4. run `warm_landing_best_entity_caches()` so newly surfaced CB leaders are hot in entity caches
5. optionally run the bulk entity cache loader if the always-hot player cohort should reflect the new CB leaders immediately

### Caches and tables affected

1. `LandingPlayerBestSnapshot` rows keyed by `realm + sort='cb'`
2. `landing:players:v13:namespace`
3. `landing:players:dirty:v1`
4. `landing:players:v13:n{namespace}:best:cb:{limit}` and metadata companions
5. `landing:players:v13:n{namespace}:published:best:cb:{limit}` and metadata companions

### Caches not required to flush for this tranche

1. `clan_battles:player:{account_id}`
2. `clan_battles:summary:v2:{clan_id}`

Those caches remain valid because the first slice changes ranking only, not the underlying persisted CB summary data.

## Rollback Plan

If the new ranking produces unacceptable live ordering or unforeseen warm-path cost:

1. revert the CB ranking helper in `server/warships/landing.py`
2. regenerate the `cb` best-player snapshots
3. invalidate landing-player caches again so the old ordering is republished

No schema rollback is required in this tranche.

## Implementation Notes For Personas

### Project Coordinator

1. route the work in this order: Project Manager -> Architect -> Engineer -> QA -> Safety
2. keep doc updates in the same implementation tranche
3. make sure final output calls out the exact propagation commands or shell snippets used for each realm

### Project Manager

1. keep non-goals explicit so the tranche does not spill into overall Best-player ranking or clan-level ranking
2. require acceptance criteria that compare same-WR different-volume players

### Architect

1. confirm the chosen scoring model is monotonic and defensible
2. confirm the request path still serves materialized snapshots instead of recomputing broad CB candidate sets on every request
3. confirm no extra upstream or cross-surface invalidation is required

### Engineer-Web-Dev

1. keep the code change local to the CB sort path
2. prefer small helpers over broad refactors
3. extend existing CB sort and snapshot tests instead of creating a new test harness

### QA

1. challenge the ranking with adversarial fixtures, especially same-WR / different-volume and small-sample outlier cases
2. verify post-invalidation repeated endpoint reads return stable ordering

### Safety

1. verify hidden or incomplete players are not surfaced by relaxed conditions
2. verify the change does not expand cache invalidation beyond landing-player surfaces
3. verify no new request-time polling or async fan-out was introduced

## Definition Of Done

This runbook is complete when all of the following are true:

1. `mode=best&sort=cb` uses a confidence-aware CB score
2. focused backend tests prove the new ordering intent
3. snapshot materialization and landing warmers still function for `cb`
4. the propagation sequence is documented and reproducible
5. the updated docs match the shipped code and tests