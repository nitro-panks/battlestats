# Runbook: Player Combat Achievements Data Lane

_Last updated: 2026-03-16_

_Status: Active implementation runbook_

## Purpose

Implement the backend-only data lane for player combat achievements so Battlestats can fetch, store, normalize, and test meaningful combat awards without mixing in event, campaign, album, or PvE-only noise.

This runbook covers the next implementation tranche only. It does not cover player-detail UI or explorer UI.

## Source Of Truth

The planning source for this runbook is:

- [agents/work-items/archive/player-achievements-data-spec.md](agents/work-items/archive/player-achievements-data-spec.md)

The live upstream example used to ground this work is the NA `lil_boots` payload, which confirms that `account/achievements/` mixes combat codes such as `PCH016_FirstBlood` and `PCH023_Warrior` with campaign, album, and PvE codes that must be excluded from the curated lane.

## Outcome Of This Tranche

At the end of this runbook, the repo should have:

1. a documented upstream contract for `account/achievements/`,
2. raw achievement storage on `Player`,
3. a curated combat-achievement catalog in code,
4. a normalized `PlayerAchievementStat` model,
5. a refresh service that rebuilds curated rows from the raw payload,
6. automated tests for parsing, filtering, and idempotent persistence,
7. an optional backfill command for existing players.

## Agent Routing

- Engineer-Web-Dev owns implementation and test coverage.
- Architect reviews contract boundaries and storage design.
- Project Coordinator updates runbooks and contract docs.
- QA signs off against the checks in the final section.

## Task 1: Add The Upstream Contract Artifact

### Goal

Document the upstream `account/achievements/` endpoint in the same contract style already used for `account/info`, `account/list`, and `clans/accountinfo`.

### Files To Add Or Update

1. `agents/contracts/upstream/wows-account-achievements.yaml`
2. `agents/contracts/upstream/README.md`

### Required Content

The contract file should document:

1. endpoint path: `/wows/account/achievements/`
2. supported realms: EU, NA, ASIA
3. request parameters:
   - `application_id`
   - `account_id`
   - `fields`
   - `access_token`
   - `language`
4. response envelope:
   - `status`
   - `meta.count`
   - `meta.hidden`
   - `data[account_id]`
5. response payload shape:
   - `battle` associative array
   - `progress` associative array
6. observed behavior notes:
   - hidden profiles are omitted from `data`
   - raw keys are opaque WG codes
   - the endpoint is not user-ready without local catalog mapping

### Validation

1. Contract file is linked from `agents/contracts/upstream/README.md`.
2. Contract wording clearly distinguishes raw WG codes from Battlestats-friendly labels.
3. `server/warships/tests/test_upstream_contracts.py` is updated so code-used field paths for `account/achievements/` remain contract-backed.

## Task 2: Add Raw Storage To The Player Model

### Goal

Persist the raw per-player achievements payload for auditability and future reprocessing.

### Files To Add Or Update

1. `server/warships/models.py`
2. new Django migration under `server/warships/migrations/`
3. `server/warships/serializers.py` only if later required by internal read paths

### Model Changes

Add to `Player`:

1. `achievements_json = models.JSONField(null=True, blank=True)`
2. `achievements_updated_at = models.DateTimeField(null=True, blank=True)`

### Guardrails

1. Store only the per-account payload, not the entire outer response envelope.
2. Do not interpret missing upstream data as zero achievements.
3. Do not read this raw field directly from the frontend in this tranche.
4. Keep `progress` in the raw payload even though MVP normalization ignores it; it is preserved for auditability and future product work.

### Validation

1. Migration applies cleanly.
2. Existing player reads continue to work with nullable new fields.

## Task 3: Create The Achievement Catalog

### Goal

Define the local mapping from opaque WG achievement codes to the curated combat achievements Battlestats wants to expose.

### Files To Add Or Update

1. `server/warships/achievements_catalog.py`

### Required MVP Catalog Entries

Include at least these combat mappings:

1. `PCH001_DoubleKill`
2. `PCH003_MainCaliber`
3. `PCH004_Dreadnought`
4. `PCH005_Support`
5. `PCH006_Withering`
6. `PCH011_InstantKill`
7. `PCH012_Arsonist`
8. `PCH013_Liquidator`
9. `PCH014_Headbutt`
10. `PCH016_FirstBlood`
11. `PCH017_Fireproof`
12. `PCH018_Unsinkable`
13. `PCH019_Detonated`
14. `PCH020_ATBACaliber`
15. `PCH023_Warrior`

### Catalog Requirements

Each entry should define:

1. code
2. slug
3. label
4. category
5. kind
6. `enabled_for_player_surface`
7. notes when the WG code name and public-facing label differ

### Guardrails

1. Keep the MVP catalog explicit and small.
2. Do not include event, campaign, album, or PvE-only rows in the player-surface allowlist.
3. Treat squad combat achievements as optional follow-up work, not required for the initial tranche.

### Validation

1. The catalog exposes deterministic slugs and labels.
2. `PCH023_Warrior` is intentionally mapped to the Battlestats-facing `Kraken Unleashed` label.

## Task 4: Add The Upstream Fetch Helper

### Goal

Create a dedicated upstream helper that returns the per-account achievement payload in the same style as the repo's existing upstream wrappers.

### Files To Add Or Update

1. `server/warships/api/players.py`

### Recommended Function

`_fetch_player_achievements(account_id: int) -> dict | None`

### Required Client Pattern

Use the repo-standard shared WG client explicitly:

1. `from warships.api.client import make_api_request`
2. call `make_api_request('account/achievements/', params)`
3. return only the per-account payload from the returned `data` object

### Required Behavior

1. call `/wows/account/achievements/`
2. request `fields=battle,progress`
3. return the per-account payload only
4. tolerate `data[account_id] = null`
5. expose hidden-profile omission behavior clearly to the caller

### Guardrails

1. Reuse the shared WG request helper already used elsewhere in the repo.
2. Do not add browser-side fetches for this feature.
3. Do not collapse `battle` and `progress` into one map in the fetch layer.

### Validation

1. The helper returns a stable shape for success, null-account, and hidden-profile cases.

## Task 5: Add The Normalization Helper

### Goal

Convert raw upstream maps into a curated set of combat-achievement rows.

### Files To Add Or Update

1. `server/warships/data.py`
2. or a small dedicated module such as `server/warships/achievements.py`

### Recommended Function

`normalize_player_achievement_rows(raw_payload: dict) -> list[dict]`

### Required Rules

1. Read only the `battle` map for MVP curated rows.
2. Ignore the `progress` map for the curated player surface.
3. Emit rows only for allowlisted catalog entries.
4. Exclude event, campaign, album, and PvE-only codes.
5. Skip zero-count rows.
6. Preserve unknown codes in raw JSON only.
7. Log or otherwise surface unknown codes that appear combat-like so the catalog can expand intentionally later.

### Example Expectation From `lil_boots`

Curated rows should include:

1. `First Blood`
2. `Main Caliber`
3. `Kraken Unleashed`
4. other allowlisted combat rows that appear in the payload

Curated rows should exclude:

1. `PCH070_Campaign1Completed`
2. `PCH087_FillAlbum`
3. `PCH097_PVE_HON_WIN_ALL_DONE`

### Validation

1. The normalization helper produces deterministic rows.
2. Unknown codes do not break normalization.
3. The same `lil_boots` fixture always produces the same curated slug and label set.

## Task 6: Add The Curated Persistence Model

### Goal

Persist the allowlisted combat achievements as explicit rows rather than reparsing JSON on every future read.

### Files To Add Or Update

1. `server/warships/models.py`
2. new migration under `server/warships/migrations/`

### Recommended Model

`PlayerAchievementStat`

### Recommended Fields

1. `player`
2. `achievement_code`
3. `achievement_slug`
4. `achievement_label`
5. `category`
6. `count`
7. `source_kind`
8. `refreshed_at`

### Constraints

1. unique together on `(player, achievement_code, source_kind)`

### Guardrails

1. Only curated rows belong here.
2. Raw non-curated WG achievement keys stay in `Player.achievements_json` only.
3. Do not store empty or zero-count rows.

### Validation

1. Repeated refreshes do not create duplicate rows.
2. Curated rows can be rederived cleanly from raw JSON.

## Task 7: Add The Refresh Service

### Goal

Create the single backend service that fetches raw achievements and rebuilds curated rows.

### Files To Add Or Update

1. `server/warships/data.py`
2. optionally `server/warships/tasks.py`

### Recommended Function

`update_achievements_data(player_id: int, force_refresh: bool = False) -> None`

### Required Responsibilities

1. load the player
2. decide if the data is stale enough to refresh
3. fetch the raw upstream payload
4. store `achievements_json`
5. store `achievements_updated_at`
6. delete and rebuild curated `PlayerAchievementStat` rows in an idempotent way
7. leave a clear hook point for future player-detail or landing cache invalidation if achievements later enter read payloads

### Refresh Rules

Recommended MVP:

1. 24-hour freshness window is acceptable
2. `force_refresh=True` should bypass the staleness gate
3. request-time views should not call upstream directly in this tranche

### Hidden-Profile Rules

1. if upstream excludes the account because the profile is hidden, keep prior valid stored achievement data unchanged,
2. do not rebuild curated rows from an empty hidden response,
3. log the hidden-state outcome so the behavior is inspectable,
4. this tranche intentionally differs from the more destructive clearing behavior used by some other player JSON fields because achievements are being treated as slowly changing historical counts rather than volatile detailed views.

### Cache Rule

1. this tranche adds no public read endpoint, so no new cache invalidation is required yet,
2. if achievements later enter player detail or landing payloads, call the appropriate player-facing cache invalidation path at refresh time rather than letting stale achievement summaries linger.

### Clan Crawl Rule

1. do not wire achievements refresh into `warships.clan_crawl.save_player()` in this tranche,
2. keep achievements refresh on opportunistic player refresh and explicit maintenance paths first,
3. revisit crawl integration only after upstream-load characteristics are understood.

### Validation

1. Service is idempotent.
2. Hidden-profile responses are non-destructive.
3. Staleness gating behaves predictably.

## Task 8: Add A Backfill Command Or Task

### Goal

Provide a maintenance path to populate achievements for already known players without waiting for opportunistic refreshes.

### Files To Add Or Update

1. new management command under `server/warships/management/commands/`
2. optionally `server/warships/tasks.py`

### Recommended Command

`python manage.py backfill_achievements_data`

### Recommended Scope Controls

1. `--player-id`
2. `--limit`
3. `--batch-size`
4. `--force`
5. `--only-missing`
6. `--older-than-hours`

### Command Pattern

Follow the repo's existing backfill style:

1. structured progress logging,
2. batched iteration,
3. `--force` support,
4. room for resumable checkpoints later if dataset size justifies it.

### Guardrails

1. Default to visible players first unless product decides otherwise.
2. Avoid uncontrolled upstream load.
3. Keep the command resumable later if the dataset size makes that necessary.

### Validation

1. The command can populate missing achievement rows for a known player.
2. Force mode refreshes an already populated player.

## Task 9: Add Tests

### Goal

Lock down the behavior so the curated combat lane does not regress into raw-noise ingestion.

### Files To Add Or Update

1. `server/warships/tests/test_data.py`
2. `server/warships/tests/test_upstream_contracts.py`
3. `server/warships/tests/test_achievements_catalog.py`
4. any new command-specific test module if needed

### Required Test Buckets

#### Upstream helper tests

1. successful per-account payload
2. null account payload
3. hidden-profile omission behavior
4. missing `battle` or `progress`

#### Catalog tests

1. required combat codes are present
2. excluded event and campaign codes are not player-enabled
3. labels stay stable for `First Blood`, `Main Caliber`, and `Kraken Unleashed`
4. repeated normalization against the same fixture yields the same curated slugs and labels

#### Normalization tests

1. `PCH016_FirstBlood` becomes a curated row
2. `PCH023_Warrior` becomes `Kraken Unleashed`
3. `PCH070_Campaign1Completed` is excluded
4. `PCH087_FillAlbum` is excluded
5. `PCH097_PVE_HON_WIN_ALL_DONE` is excluded
6. unknown codes stay raw-only

#### Refresh service tests

1. raw JSON is stored on the player
2. timestamp is updated
3. curated rows are rebuilt idempotently
4. repeated refresh does not duplicate rows
5. hidden response does not wipe prior valid data
6. missing `battle` or `progress` maps do not crash refresh

#### Backfill command tests

1. only-missing path populates a player without achievements
2. force path refreshes an already populated player
3. overlapping batches remain idempotent

#### Contract enforcement tests

1. `test_upstream_contracts.py` covers the field paths relied on by the achievements fetch helper

### Suggested Realistic Fixture

Build one fixture from the verified mixed `lil_boots` payload containing both:

1. real combat rows
2. real campaign/event/PvE noise rows

This one fixture should prove the filter behavior end-to-end.

## Task 10: Keep Read Paths Deferred

### Goal

Avoid over-scoping this tranche into endpoint or UI work before the data lane is reliable.

### Rules

1. Do not add frontend code in this runbook.
2. Do not add a public read endpoint unless the data lane is already validated.
3. If a read endpoint is added early for debugging, keep it internal or clearly temporary.

## Execution Evidence To Capture

When implementation is complete, the PR or follow-up review should include:

1. migration names for the new model and player fields
2. the new upstream contract file path
3. the exact test commands run
4. one real mixed-payload normalization example proving excluded rows stay excluded
5. the chosen hidden-profile behavior and evidence that it remained non-destructive

## Suggested Validation Commands

### Focused test targets

```bash
cd server && python manage.py test warships.tests.test_data warships.tests.test_upstream_contracts --keepdb
```

If command coverage lands in a separate module, add it explicitly.

### Optional manual shell check

```bash
cd server && python manage.py shell
```

Inside the shell, verify:

1. `Player.achievements_json` stores the raw per-account payload
2. curated `PlayerAchievementStat` rows exist only for allowlisted combat achievements

## QA Sign-Off Template

- Scope reviewed:
- Automated tests run:
- Manual checks run:
- Pass/fail result:
- Open risks or waivers:
