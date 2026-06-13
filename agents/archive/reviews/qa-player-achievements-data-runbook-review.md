# QA Review: Player Achievements Data Lane Runbook

_Reviewed: 2026-03-16_

## Scope Reviewed

- [agents/runbooks/runbook-player-achievements-data-lane.md](agents/runbooks/runbook-player-achievements-data-lane.md)
- [agents/work-items/player-achievements-data-spec.md](agents/work-items/player-achievements-data-spec.md)

## QA Verdict

Approved as an implementation-planning artifact for the backend achievements data tranche.

The runbook is now complete enough to execute. It captures the actual next steps implied by the spec, makes the upstream contract and shared API client requirements explicit, chooses a hidden-profile rule, and includes the key tests needed to keep event and campaign noise out of the curated combat-achievement lane.

## What The Runbook Gets Right

1. It separates raw upstream storage from the curated combat-achievement dataset.
2. It treats `account/achievements/` as a raw code map, not a user-ready achievement list.
3. It keeps event, campaign, album, and PvE-only achievements out of the curated player surface.
4. It uses a local catalog so Battlestats owns the label mapping for codes like `PCH023_Warrior`.
5. It keeps request-time reads deferred until the data lane is stable.
6. It now names the shared WG API client pattern explicitly.
7. It now commits to a non-destructive hidden-profile rule for this tranche.

## QA Focus Areas

1. Correct combat-achievement filtering.
2. Non-destructive hidden-profile handling.
3. Idempotent refresh and backfill behavior.
4. Contract-backed upstream parsing.
5. Stable catalog labels and slugs.

## Required QA Checks

### Contract and parsing checks

- `agents/contracts/upstream/wows-account-achievements.yaml` exists and is linked from the upstream contracts README.
- `server/warships/tests/test_upstream_contracts.py` covers the field paths used by the achievements helper.
- the fetch helper safely handles:
  - normal success payloads
  - null account payloads
  - hidden-profile omission behavior
  - missing `battle` or `progress`

### Catalog checks

- `server/warships/tests/test_achievements_catalog.py` verifies the MVP combat code allowlist.
- `PCH016_FirstBlood` maps to `First Blood`.
- `PCH003_MainCaliber` maps to `Main Caliber`.
- `PCH023_Warrior` maps to the chosen Battlestats-facing `Kraken Unleashed` label.
- excluded event and campaign codes are not enabled for the player surface.

### Normalization checks

- the mixed `lil_boots` fixture yields curated combat rows for real combat achievements.
- campaign, album, and PvE-only rows from the same fixture are excluded.
- unknown codes remain raw-only and do not break normalization.
- repeated normalization against the same fixture yields the same curated slugs and labels.

### Persistence and refresh checks

- `Player.achievements_json` stores the per-account raw payload.
- `achievements_updated_at` updates on refresh.
- repeated refreshes do not duplicate `PlayerAchievementStat` rows.
- hidden-profile responses do not erase previously stored valid achievements.
- refresh logic does not crash when one of the upstream maps is absent.

### Backfill checks

- only-missing mode populates players with no achievements data.
- force mode refreshes already populated players cleanly.
- overlapping batches remain idempotent.

## Residual Risks

1. WG code names and public-facing achievement names are not guaranteed to align, so catalog mistakes would create misleading labels.
2. The raw `progress` map is intentionally stored but unused for MVP; future maintainers may try to surface it without a separate product review.
3. If achievements later enter player-detail or landing payloads, cache invalidation will need to be added at refresh time.

## QA Recommendations

1. Treat the mixed `lil_boots` fixture as a release gate for normalization correctness.
2. Keep the combat catalog intentionally small until more codes are verified.
3. Re-review the data contract before adding any public read endpoint or UI so the raw-vs-curated boundary does not blur.

## Exit Criteria

1. Upstream contract documentation is in place.
2. Raw storage and curated storage both exist.
3. The combat catalog is explicit and tested.
4. Event, campaign, album, and PvE-only rows stay excluded from curated player achievements.
5. Hidden-profile handling remains non-destructive.
6. Refresh and backfill behavior are idempotent.

## Final QA Position

Approved for the planned backend tranche.

The remaining risk is operational implementation detail, not planning completeness. The runbook now gives engineering and QA a sufficiently accurate sequence for building and validating the achievements data lane before any UI work begins.
