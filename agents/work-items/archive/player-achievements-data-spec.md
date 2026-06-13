# Player Combat Achievements — Data, Fetching, And Testing Specification

**Author:** Project Manager Agent  
**Date:** 2026-03-16  
**Status:** Draft for implementation planning  
**Scope:** Backend data acquisition, normalization, persistence, and testing for player combat achievements  
**Primary Surface:** Stored player achievement data for future player-detail and explorer use

---

## 1. Objective

**Core question this feature answers:** _"Which meaningful combat achievements has this player actually earned, and how often?"_

The World of Warships `account/achievements/` endpoint exposes a large mixed achievement inventory. For real player analysis, Battlestats should support the combat achievements users actually recognize, such as:

1. Kraken-like combat awards,
2. First Blood,
3. Main Caliber,
4. Dreadnought,
5. Arsonist,
6. Warrior,
7. Witherer,
8. Liquidator,
9. Fireproof,
10. Support.

This feature is explicitly **not** about:

1. event participation,
2. campaign completion,
3. loot box or album progress,
4. PvE-only achievement clutter,
5. rendering a UI yet.

This specification defines how Battlestats should:

1. fetch achievement data from upstream,
2. separate combat achievements from event/campaign noise,
3. persist both raw and curated data safely,
4. refresh the data over time,
5. test the pipeline so it stays correct.

---

## 2. Upstream Reality

### Endpoint

Recommended upstream source:

`GET /wows/account/achievements/`

Docs-confirmed behavior:

1. accepts up to 100 `account_id`s,
2. supports `fields`,
3. returns `battle` and `progress`,
4. excludes hidden profiles from `data` and reports them in `meta.hidden`.

### Actual payload shape

The endpoint does **not** return a typed array of friendly achievements.

It returns a per-account object like:

```json
{
  "status": "ok",
  "meta": {
    "count": 1,
    "hidden": null
  },
  "data": {
    "1031615890": {
      "battle": {
        "PCH003_MainCaliber": 109,
        "PCH016_FirstBlood": 359
      },
      "progress": {
        "PCH031_EarningMoney1": 0
      }
    }
  }
}
```

### Key constraint

Upstream achievement keys are opaque WG codes such as:

1. `PCH003_MainCaliber`
2. `PCH016_FirstBlood`
3. `PCH097_PVE_HON_WIN_ALL_DONE`
4. `PCH070_Campaign1Completed`
5. `PCH087_FillAlbum`

That means Battlestats must maintain its own curated combat-achievement catalog rather than trusting the raw upstream payload to be user-ready.

---

## 3. Live Example: `lil_boots`

Verified NA example account:

1. player name: `lil_boots`
2. account id: `1031615890`

The endpoint returned:

1. `149` keys in `battle`
2. `31` keys in `progress`

Representative combat-style rows for `lil_boots`:

1. `PCH001_DoubleKill: 16`
2. `PCH003_MainCaliber: 109`
3. `PCH004_Dreadnought: 133`
4. `PCH005_Support: 102`
5. `PCH006_Withering: 40`
6. `PCH011_InstantKill: 387`
7. `PCH012_Arsonist: 57`
8. `PCH013_Liquidator: 5`
9. `PCH014_Headbutt: 3`
10. `PCH016_FirstBlood: 359`
11. `PCH017_Fireproof: 17`
12. `PCH018_Unsinkable: 1`
13. `PCH019_Detonated: 48`
14. `PCH020_ATBACaliber: 52`
15. `PCH023_Warrior: 27`

The same payload also included rows Battlestats should **not** treat as player combat achievements for this feature, such as:

1. `PCH070_Campaign1Completed: 1`
2. `PCH087_FillAlbum: 1`
3. `PCH097_PVE_HON_WIN_ALL_DONE: 529`
4. `PCH150_Twitch_WG: 1`
5. `PCH230_FillAlbum_Azurlane: 1`

This confirms the need for a local allowlist.

---

## 4. Product Scope For The Data Layer

### In Scope

1. Fetch and store raw achievement payloads.
2. Build a curated combat-achievement catalog.
3. Normalize only the combat achievements Battlestats wants to expose.
4. Exclude event, campaign, album, and PvE-only achievements from the curated dataset.
5. Support future player-detail or explorer reads without refetching upstream on every page load.

### Out Of Scope

1. UI rendering.
2. Charts, badges, or cards.
3. Achievement chronology over time.
4. Event/campaign achievement browsing.
5. Inferring gameplay quality from non-combat collectibles.

---

## 5. Data Model Recommendation

Battlestats should keep both a raw lane and a curated lane.

### 5.1 Raw Payload Lane

Recommended additions on `Player`:

1. `achievements_json` JSONField nullable
2. `achievements_updated_at` DateTimeField nullable

Purpose:

1. preserve the upstream response shape for auditing and reprocessing,
2. let the curated catalog evolve without losing the original raw data,
3. avoid repeated upstream fetches during development of future derived views.

Recommended stored raw shape:

```json
{
  "battle": {
    "PCH003_MainCaliber": 109,
    "PCH016_FirstBlood": 359
  },
  "progress": {
    "PCH031_EarningMoney1": 0
  }
}
```

Do not store the full outer envelope on the player row if it adds no value. The per-account payload is enough.

### 5.2 Curated Catalog Lane

Add a small static catalog in code, for example:

`server/warships/achievements_catalog.py`

Each entry should define:

1. `code`
2. `slug`
3. `label`
4. `category`
5. `kind`
   - `combat`
   - `combat_squad`
   - `pve`
   - `event`
   - `campaign`
   - `album`
   - `other`
6. `enabled_for_player_surface`
7. `notes`

### 5.3 Curated Player Achievement Lane

Recommended normalized model:

`PlayerAchievementStat`

Recommended fields:

1. `player` FK
2. `achievement_code` string
3. `achievement_slug` string
4. `achievement_label` string
5. `category` string
6. `count` integer
7. `source_kind` string
   - `battle`
   - `progress`
8. `refreshed_at` datetime

Recommended constraints:

1. unique together on `(player, achievement_code, source_kind)`

Purpose:

1. make future reads simple and explicit,
2. avoid repeatedly parsing arbitrary JSON maps at request time,
3. support easy filtering to only combat achievements.

### 5.4 MVP Simplification Rule

For MVP, curated rows should only be written for:

1. `kind = combat`
2. optionally `kind = combat_squad` if Battlestats decides squad achievements belong in the same feature later.

Do **not** create curated rows for:

1. `event`
2. `campaign`
3. `album`
4. `pve`
5. unrelated collectible or progression codes.

---

## 6. Initial Combat Achievement Catalog

Recommended MVP allowlist:

1. `PCH001_DoubleKill` -> `Double Strike`
2. `PCH003_MainCaliber` -> `Main Caliber`
3. `PCH004_Dreadnought` -> `Dreadnought`
4. `PCH005_Support` -> `Confederate/Support` using the official Battlestats-facing label chosen by product
5. `PCH006_Withering` -> `Witherer`
6. `PCH011_InstantKill` -> `Devastating Strike`
7. `PCH012_Arsonist` -> `Arsonist`
8. `PCH013_Liquidator` -> `Liquidator`
9. `PCH014_Headbutt` -> `Die-Hard / Headbutt`
10. `PCH016_FirstBlood` -> `First Blood`
11. `PCH017_Fireproof` -> `Fireproof`
12. `PCH018_Unsinkable` -> `Unsinkable`
13. `PCH019_Detonated` -> `Detonation`
14. `PCH020_ATBACaliber` -> `Close Quarters Expert`
15. `PCH023_Warrior` -> `Kraken Unleashed`

Optional later additions:

1. `PCH364_MainCaliber_Squad`
2. `PCH366_Warrior_Squad`
3. `PCH367_Support_Squad`

Important note:

1. WG internal codes do not always match the user-facing achievement names,
2. Battlestats must own the label mapping explicitly.

---

## 7. Fetching Strategy

### 7.1 Recommended Upstream Call

Use:

`GET /wows/account/achievements/`

Recommended request parameters:

1. `application_id`
2. `account_id`
3. `fields=battle,progress`

Why this matters:

1. it keeps the payload narrower,
2. it documents that only these two maps are expected,
3. it aligns with the curated/raw split.

### 7.2 Fetch Helper

Add a dedicated upstream helper in:

`server/warships/api/players.py`

Recommended function:

`_fetch_player_achievements(account_id: int) -> dict`

Behavior requirements:

1. return the per-account payload only,
2. tolerate `data[account_id] = null`,
3. surface hidden-profile behavior clearly,
4. preserve `battle` and `progress` as maps.

### 7.3 Refresh Lane

Recommended initial backend service:

`update_achievements_data(player_id)`

Responsibilities:

1. fetch the raw payload,
2. persist `achievements_json`,
3. stamp `achievements_updated_at`,
4. rebuild curated `PlayerAchievementStat` rows from the allowlist.

### 7.4 When To Refresh

Recommended MVP behavior:

1. do **not** fetch achievements on every player-detail read,
2. allow player refresh workflows to update achievements opportunistically,
3. allow a dedicated async task or management command to backfill achievements for known players.

Recommended freshness rule:

1. treat achievements as slowly changing,
2. a refresh interval like 24 hours is acceptable for MVP,
3. manual or forced refresh should still be possible.

### 7.5 Hidden Profiles

Upstream docs state hidden profiles are excluded from response and listed in `meta.hidden`.

Rules:

1. if the account is hidden upstream, do not overwrite prior curated data with fake zeroes,
2. either keep the previous stored data and mark it stale, or clear with an explicit hidden-state rule,
3. do not infer that missing response means no achievements.

Recommended MVP rule:

1. if upstream marks the account hidden, keep existing stored achievements unchanged and record the refresh attempt timestamp separately in logs or task state.

---

## 8. Normalization Rules

### 8.1 Raw Ingest

Store both maps if present:

1. `battle`
2. `progress`

### 8.2 Curated Player Rows

For MVP:

1. read only the `battle` map,
2. ignore `progress` for player combat achievements,
3. emit curated rows only for allowlisted `combat` achievements,
4. ignore unknown codes unless they are later added to the catalog.

### 8.3 Unknown Codes

Rules:

1. unknown raw codes stay in `achievements_json`,
2. unknown codes do not become curated rows,
3. log or report unknown codes if they look like candidate combat achievements for future catalog expansion.

### 8.4 Zero Counts

Rules:

1. `battle` entries are effectively earned counts and can be stored as positive integers,
2. skip curated rows with zero counts,
3. if an allowlisted achievement is missing from the raw `battle` map, treat it as absent rather than storing a zero row.

### 8.5 Progress Map

MVP rule:

1. persist `progress` in raw JSON,
2. do not normalize it into curated rows,
3. do not use it for combat-achievement UX until there is a separate product need.

---

## 9. Read Contract Recommendation

Even without UI work now, the data plan should target a stable future read contract.

Recommended endpoint later:

`GET /api/fetch/player_achievements/<player_id>/`

Recommended response shape:

```json
{
  "player_id": 1031615890,
  "name": "lil_boots",
  "updated_at": "2026-03-16T15:00:00Z",
  "achievements": [
    {
      "code": "PCH023_Warrior",
      "slug": "kraken-unleashed",
      "label": "Kraken Unleashed",
      "category": "combat",
      "count": 27
    },
    {
      "code": "PCH016_FirstBlood",
      "slug": "first-blood",
      "label": "First Blood",
      "category": "combat",
      "count": 359
    }
  ]
}
```

This read contract is not required immediately, but the stored data should make it trivial.

---

## 10. Testing Strategy

Testing needs to cover three layers:

1. upstream parsing,
2. normalization/filtering,
3. persistence and refresh behavior.

### 10.1 Upstream Parsing Tests

Add tests for `_fetch_player_achievements()`:

1. success response with both `battle` and `progress`,
2. `data[account_id] = null`,
3. hidden profile case surfaced through `meta.hidden`,
4. missing `battle` or missing `progress` maps handled safely,
5. batched IDs later if batching is added.

### 10.2 Catalog Tests

Add tests for the catalog itself:

1. allowlisted combat codes map to stable slugs and labels,
2. event/campaign codes are not marked `enabled_for_player_surface`,
3. labels for known achievements like `First Blood` and `Kraken Unleashed` stay correct.

### 10.3 Normalization Tests

Add tests for the normalization helper:

1. `PCH016_FirstBlood` becomes a curated combat row,
2. `PCH023_Warrior` becomes `Kraken Unleashed`,
3. `PCH070_Campaign1Completed` is excluded,
4. `PCH087_FillAlbum` is excluded,
5. `PCH097_PVE_HON_WIN_ALL_DONE` is excluded,
6. missing allowlisted codes produce no zero rows,
7. unknown codes stay in raw JSON only.

### 10.4 Persistence Tests

Add tests for `update_achievements_data(player_id)`:

1. stores the raw JSON payload on the player,
2. updates the timestamp,
3. clears and rebuilds curated rows idempotently,
4. does not create duplicate curated rows on repeated refresh,
5. does not erase valid prior data when hidden-profile response excludes the account.

### 10.5 Contract Tests

When the read endpoint exists, add API tests for:

1. only curated combat achievements are returned,
2. rows are sorted deterministically by count descending, then label,
3. hidden players return correct empty/stale behavior according to chosen product rule.

### 10.6 Fixture Example

A useful fixture can be based on the verified `lil_boots` raw payload and should include at least:

1. `PCH016_FirstBlood: 359`
2. `PCH003_MainCaliber: 109`
3. `PCH023_Warrior: 27`
4. `PCH070_Campaign1Completed: 1`
5. `PCH087_FillAlbum: 1`
6. `PCH097_PVE_HON_WIN_ALL_DONE: 529`

This gives one realistic mixed payload that proves the filter logic.

---

## 11. Operational Recommendations

1. Add an upstream contract file for `/wows/account/achievements/` under `agents/contracts/upstream/`.
2. Add a management command for backfilling achievements on known players.
3. Prefer async refresh tasks over request-time live pulls.
4. Keep the combat catalog small and explicit at first.
5. Expand only when new codes are understood and intentionally mapped.

---

## 12. Risks And Guardrails

| Risk                                                     | Severity | Mitigation                                             |
| -------------------------------------------------------- | -------- | ------------------------------------------------------ |
| Event and campaign noise overwhelms the useful data      | High     | Use a strict allowlist for curated rows                |
| WG code names do not match user-facing labels            | High     | Own a local catalog with explicit label mapping        |
| Hidden profiles appear as empty achievement sets         | High     | Treat hidden-response absence as unavailable, not zero |
| Raw JSON becomes the permanent read path                 | Medium   | Add normalized curated rows early                      |
| Future unknown combat codes are silently ignored forever | Medium   | Log unknown candidate codes for review                 |

---

## 13. Acceptance Criteria

1. Battlestats can fetch and store raw achievement payloads from `account/achievements/`.
2. A curated catalog exists for the MVP combat achievements.
3. Event, campaign, album, and PvE-only achievements are excluded from curated player rows.
4. Repeated refreshes are idempotent.
5. Hidden-profile behavior is non-destructive and non-misleading.
6. The `lil_boots` example payload would yield curated rows for `First Blood`, `Main Caliber`, and `Kraken Unleashed`, while excluding campaign and album entries.

---

## 14. Recommended Implementation Order

1. Add the upstream contract doc for `account/achievements/`.
2. Add raw storage fields on `Player`.
3. Add the achievement catalog.
4. Add the normalization helper.
5. Add the refresh service and tests.
6. Add an optional management command/task for backfill.
7. Only then add any read endpoint or UI.
