# QA Review — Player Profile Clan Battles

## Verdict

The spec is testable and implementation-ready. Ship only if the feature is verified as **player-specific**, renders in the left column under the clan list, and handles empty data without breaking player detail.

## QA Focus Areas

1. Correct data semantics: player seasons only, not clan roster aggregates.
2. Left-column layout stability under the existing clan plot and clan members stack.
3. Reliable loading, empty, and error states.
4. No regressions in player detail navigation, clan navigation, or ranked sections.

## Required QA Checks

1. Backend endpoint returns only player-scoped clan battle season rows.
2. Season ordering follows metadata dates rather than raw season ids.
3. A player with no clan battle history renders the empty state instead of a broken table.
4. A player with clan battle history renders summary values and season rows consistently.
5. The section appears below `ClanMembers` in the left column on player detail.
6. Hidden-profile behavior remains unchanged.
7. No TypeScript/editor errors in touched client files.
8. No Django serializer/view errors in touched backend files.

## Suggested Manual Fixtures

1. A player with a clan and known clan battle activity.
2. A player with a clan but no clan battle activity.
3. A player with no clan.
4. A hidden-profile player.

## Regression Risks

1. Left-column overflow or awkward vertical density.
2. Empty-state regressions where the section appears blank.
3. Mistaking WG per-player season rows for clan-level participation context.

## Review Outcome

Proceed with implementation. Validate with endpoint response inspection plus manual player-detail rendering checks.
