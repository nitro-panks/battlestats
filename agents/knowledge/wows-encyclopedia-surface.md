# WoWS Encyclopedia API Surface

Last verified: 2026-03-15

## Why This Matters

The repo already depends on WoWS encyclopedia data for ship metadata and ship catalog sync, but the broader encyclopedia namespace exposes more than just per-ship name/tier/type rows. This note captures the verified live surface so future feature work can build on the namespace deliberately instead of treating it as an opaque ship lookup.

## Current Conclusion

- `encyclopedia/info` is a lightweight, stable metadata dictionary endpoint for global game-reference data.
- `encyclopedia/ships` is the main structured catalog endpoint for ship-level metadata and profiles.
- `encyclopedia/modules` is available and returns typed module metadata plus module-specific profile fragments.
- `encyclopedia/consumables` is broader than its name suggests; live results can include cosmetic or skin-like items, not only battle consumables.
- `ships/badges` is not an encyclopedia endpoint; it is a player-scoped ship-statistics surface that returns mastery-style badge classes by ship.
- The encyclopedia namespace looks useful for richer ship-reference features, but repo contracts should stay limited to verified endpoints rather than guessed namespace coverage.

## Verified Endpoints

### 1. `encyclopedia/info`

Verified live URL:

`https://api.worldofwarships.com/wows/encyclopedia/info/?application_id=APP_ID`

Observed response shape:

- top-level envelope: `status`, `meta`, `data`
- `meta.count = 8` in the current response
- `data` currently includes:
  - `ships_updated_at`
  - `ship_types`
  - `languages`
  - `ship_modifications`
  - `ship_modules`
  - `ship_type_images`
  - `ship_nations`
  - `game_version`

Practical capabilities:

- resolve localization dictionaries for ship types and nations,
- discover currently supported API languages,
- map modification and module type IDs to human-readable labels,
- obtain ship-type icon sets for standard, premium, and elite variants,
- detect a coarse encyclopedia refresh timestamp via `ships_updated_at`,
- expose the current upstream game version without calling a heavier catalog endpoint.

### 2. `encyclopedia/ships`

Verified live URL:

`https://api.worldofwarships.com/wows/encyclopedia/ships/?application_id=APP_ID&limit=1`

Observed response shape:

- paginated envelope with `meta.count`, `meta.page_total`, `meta.total`, `meta.limit`, `meta.page`
- `data` keyed by `ship_id`
- per-ship payload currently includes fields like:
  - `ship_id`, `ship_id_str`, `name`, `description`
  - `nation`, `tier`, `type`
  - `is_premium`, `is_special`
  - `images`
  - `modules`
  - `modules_tree`
  - `default_profile`
  - `upgrades`
  - `mod_slots`
  - `next_ships`

Repo relevance:

- this is the current authoritative encyclopedia endpoint already used in repo code for ship metadata hydration and ship catalog sync,
- it is rich enough to support ship-reference pages, module previews, and capability cards without scraping other sources.

### 3. `encyclopedia/modules`

Verified live URL:

`https://api.worldofwarships.com/wows/encyclopedia/modules/?application_id=APP_ID&limit=1`

Observed response shape:

- paginated envelope similar to `encyclopedia/ships`
- `data` keyed by `module_id`
- module rows currently include fields like:
  - `module_id`, `module_id_str`
  - `name`
  - `type`
  - `tag`
  - `image`
  - `price_credit`
  - `profile`

Practical capabilities:

- resolve individual module labels and art,
- inspect module stat fragments such as torpedo speed, damage, or range,
- enrich ship module trees from `encyclopedia/ships` with typed module detail.

### 4. `encyclopedia/consumables`

Verified live URL:

`https://api.worldofwarships.com/wows/encyclopedia/consumables/?application_id=APP_ID&limit=1`

Observed response shape:

- paginated envelope similar to the other encyclopedia endpoints
- live sample row included:
  - `consumable_id`
  - `name`
  - `type = Skin`
  - `image`
  - `price_gold`
  - `price_credit`
  - `profile = {}`

Important caveat:

- do not assume this endpoint is battle-consumables-only. The verified sample behaved more like a cosmetic or skin catalog entry than a consumable gameplay object.

## Related Non-Encyclopedia Surface

### 5. `ships/badges`

Verified live URL:

`https://api.worldofwarships.com/wows/ships/badges/?application_id=APP_ID&account_id=1001162884`

Observed behavior:

- calling the endpoint without `account_id` returns:
  - `status = error`
  - `code = 402`
  - `message = ACCOUNT_ID_NOT_SPECIFIED`
- with a real account ID, the envelope returns:
  - `status`
  - `meta.count`
  - `meta.hidden`
  - `data[account_id] = [...]`
- each item in the returned array currently includes:
  - `ship_id`
  - `top_grade_class`

Practical meaning:

- this endpoint appears to expose each player's highest ship mastery badge class by ship,
- it is ship-performance metadata tied to a player account, not a static encyclopedia dictionary,
- it pairs naturally with local ship metadata from `encyclopedia/ships` if the site ever wants to show mastery badges on player ship rows.

Official badge meaning:

- this is the World of Warships Efficiency Badges feature,
- `top_grade_class = 1` maps to `Expert`,
- `top_grade_class = 2` maps to `Grade I`,
- `top_grade_class = 3` maps to `Grade II`,
- `top_grade_class = 4` maps to `Grade III`.

Official calculation notes:

- badges are awarded per ship, based on a player's best qualifying result for that ship,
- the official article says badges are determined by Base XP earned in a single Random Battle,
- comparisons are ship-specific and condition-specific: the player is measured against other players who recently played the same ship under the same conditions,
- only Tier V or higher ships in Random Battles are eligible,
- if a player later earns a higher badge on the same ship, it replaces the displayed lower badge,
- badges are not lost once earned.

Official percentile thresholds from the April 2025 feature announcement:

- `Expert`: top 1% of Base XP earned,
- `Grade I`: top 5% of Base XP earned,
- `Grade II`: top 20% of Base XP earned,
- `Grade III`: top 50% of Base XP earned,
- performances outside the top 50% do not receive a badge.

Important interpretation note:

- the API returns only the numeric `top_grade_class`, so the class-to-label mapping above must be maintained in repo knowledge or product code if the site wants to render human-readable badge names.

## Repo Usage Today

Current verified repo usage:

- `server/warships/api/ships.py`
  - `_fetch_ship_info()` uses `encyclopedia/ships/` for per-ship metadata.
  - `sync_ship_catalog()` paginates through `encyclopedia/ships/` for bulk ship sync.
- `server/scripts/smoke_test_wg_api.py`
  - uses `encyclopedia/info/` as the lightweight reachability smoke test for WG API availability.

The repo does not currently appear to consume `encyclopedia/modules` or `encyclopedia/consumables` in product code.

The repo now consumes `ships/badges` in backend hydration code for player fetches and clan crawl saves, persisting badge rows onto `Player.efficiency_json`.

Stored badge rows currently enrich the raw `top_grade_class` with local ship metadata and human-readable labels, including `top_grade_label` and the older `badge_label` alias.

The player detail page now renders these stored rows in an `Efficiency Badges` section that summarizes strongest class and tier patterns alongside a raw ship table.

## Surface Map For Product Planning

### Good fit right now

- ship type and nation labels,
- ship-type iconography,
- current game-version display,
- ship metadata enrichment,
- ship module-tree and module-name enrichment.

### Promising but needs careful scoping

- consumable or cosmetic catalogs,
- ship build / upgrade recommendation surfaces,
- ship reference browsing outside the player-centric flows.
- player-specific ship mastery badges layered onto top-ship or ship-detail views.

### Not yet verified here

- other encyclopedia namespace endpoints should not be treated as product-ready until they are live-checked and documented in the same way.

## Product Ideas

1. Ship reference drawer on player detail
   - Use `encyclopedia/ships` plus cached ship IDs from battle rows to show ship art, description, type, tier, and module-slot counts when a user clicks a top ship.
2. Ship build explainer
   - Use `encyclopedia/ships` plus `encyclopedia/modules` to show a compact module tree and explain what the stock/default setup looks like for frequently played ships.
3. Ranked top-ship enrichment card
   - When a ranked season shows `top_ship_name`, add a hover or side card with the ship image, class icon, nation, and short encyclopedia description.
4. Homepage patch-awareness banner
   - Use `encyclopedia/info.game_version` and `ships_updated_at` to show when battlestats metadata may lag a new patch or when a ship-catalog refresh should run.
5. Ship-class visual consistency upgrade
   - Use `ship_type_images` from `encyclopedia/info` so ship-type badges on the site match WG class iconography more closely.
6. Nation and class explorer facets
   - Use `ship_nations` and `ship_types` dictionaries to build cleaner filtering and labels for any future ship or top-ship explorer UI.
7. Module-aware ship comparison tool
   - Use `encyclopedia/modules` profiles to explain differences in torpedo, artillery, or engine modules for ships users compare often.
8. Ship mastery badge strip on player ship lists
   - Use `ships/badges` plus local ship metadata to show mastery classes beside top ships, randoms ship rows, or future ship explorer entries.

## Reproduction

```bash
APP_ID=1a167f0d986f26f3fa7f792857b40151

curl -s "https://api.worldofwarships.com/wows/encyclopedia/info/?application_id=$APP_ID"
curl -s "https://api.worldofwarships.com/wows/encyclopedia/ships/?application_id=$APP_ID&limit=1"
curl -s "https://api.worldofwarships.com/wows/encyclopedia/modules/?application_id=$APP_ID&limit=1"
curl -s "https://api.worldofwarships.com/wows/encyclopedia/consumables/?application_id=$APP_ID&limit=1"
curl -s "https://api.worldofwarships.com/wows/ships/badges/?application_id=$APP_ID&account_id=1001162884"
```

Reference:

- Official article: `https://worldofwarships.eu/en/news/general-news/efficiency-badges-put-your-skill-on-display/`

## Next Checks

- add upstream YAML profiles for `encyclopedia/ships` and `encyclopedia/modules` if product work begins to depend on them directly,
- verify whether the docs and live behavior for `encyclopedia/consumables` consistently include cosmetic items,
- decide whether `ships/badges` should be documented in a broader ship-statistics knowledge note as additional non-encyclopedia ship surface, or kept here as a related companion endpoint,
- evaluate whether `ships_updated_at` is suitable as a cheap trigger for ship-catalog refresh decisions.
