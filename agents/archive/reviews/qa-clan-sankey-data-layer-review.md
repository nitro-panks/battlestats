# QA Review — Clan Membership Sankey Data Layer

## Verdict

The specification is complete enough to proceed to implementation.

It is accurate about the current repo state:

1. current clan membership exists today,
2. transfer history does not,
3. `Player.clan` is destructive current state,
4. `clans/accountinfo/` is the strongest current-membership source,
5. a production Sankey needs append-only observations plus derived events.

The current version is implementation-ready provided the implementation keeps the spec's operational guardrails intact, especially around de-duplication, transaction boundaries, and reconciliation.

## QA Focus Areas

1. Historical truthfulness: no Sankey link may be derived from current `Player.clan` or `Snapshot` alone.
2. Dual-refresh safety: `update_clan_members()` and `update_player_data()` must not create duplicate observations for the same membership claim.
3. Reconciliation safety: roster disappearance must not become a transfer or leave event until player-level membership confirmation exists.
4. Timing semantics: `joined_at` must be treated as current-membership start for the destination clan only.
5. Confidence semantics: low-confidence diagnostics must not leak into the public Sankey payload.

## What QA Confirmed In The Spec

1. The spec distinguishes current-state APIs from historical data requirements.
2. The spec correctly identifies `GET /wows/clans/accountinfo/` as the main upstream membership truth source.
3. The spec correctly identifies `GET /wows/clans/info/` roster data as evidence for disappearance detection, not destination attribution.
4. The spec explicitly forbids building the Sankey from `Player.clan` or `Snapshot` alone.
5. The spec now defines a durable reconciliation state instead of relying on in-memory retries.
6. The spec defines null-response behavior so `confirmed no clan` and `lookup failed` are not conflated.
7. The spec includes timing precision and confidence fields in the event model.

## Required QA Checks During Implementation

1. Observation de-duplication test:
   - one `update_clan_members()` pass that also triggers `update_player_data()` must not emit two equivalent observations for the same player membership.
2. Role-only change test:
   - same clan, same `joined_at`, changed role should store a fresh observation and emit no Sankey event.
3. Leave reconciliation test:
   - player disappears from a clan roster, `clans/accountinfo/` confirms no clan after retries, and exactly one leave event is emitted.
4. Transfer reconciliation test:
   - player disappears from clan A and direct membership lookup confirms clan B, and exactly one transfer event is emitted.
5. Failed lookup test:
   - upstream membership request fails and no observation or event is written.
6. Confirmed no-clan test:
   - upstream membership request succeeds with no clan and the observation is stored with `current_clan=None` and direct evidence semantics.
7. Hidden-player test:
   - hidden profile state does not automatically create leave events, but successful clan membership lookups still count.
8. Transaction safety test:
   - observation write, event derivation, and `Player.clan` mutation stay consistent under rollback.
9. Aggregation integrity test:
   - Sankey read API aggregates only event rows at or above the configured confidence floor.
10. Performance check:

- run an `EXPLAIN` or equivalent timing check for the 30-day aggregation query and confirm the recommended indexes are being used.

## Residual Code Review Gates

These are not blockers for the spec, but they should be enforced during implementation review:

1. The de-duplication strategy must be explicit, not implied.
2. Batch identifiers must be propagated consistently through clan refresh and crawl paths.
3. Reconciliation records must expire or resolve cleanly; they cannot grow without bound.
4. Public payloads must expose coverage as `observed by Battlestats`, not universal lifetime clan history.

## Regression Risks

1. Duplicate observations from nested refresh paths.
2. False leave events caused by transient upstream gaps.
3. Overconfident timing labels on transfer edges.
4. Slow aggregation queries once observation and event volume grows.

## QA Outcome

Proceed with implementation.

The spec is accurate, honest about source limitations, and detailed enough for engineering to begin the backend data layer before any D3 Sankey UI work starts.
