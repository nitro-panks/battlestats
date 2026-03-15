# WoWS API Contract Strategy

Last verified: 2026-03-14

## Why This Matters

The repo depends on the World of Warships public API, but the upstream surface is only partially reliable and some documented endpoints behave inconsistently in production. Future work will go faster if we separate three concerns:

- narrative knowledge about what the upstream API actually does,
- machine-readable contracts for our own normalized data products,
- request/response documentation for HTTP interfaces.

This note evaluates whether the Open Data Contract Standard (ODCS) is a good fit for that job.

## Current Conclusion

- Yes, a contract layer would be useful.
- No, ODCS should not be the primary contract format for the raw WoWS upstream HTTP API.
- Yes, ODCS is a good fit for the normalized datasets this repo derives from WoWS API data.
- For raw HTTP endpoint behavior, repo-local YAML endpoint profiles are the chosen fit for this repo.

## Repo Reality Check

Current integration shape in this repo:

- Upstream calls are centralized through `warships.api.client.make_api_request()`.
- The upstream API is consumed as HTTP GET endpoints with query params and JSON responses.
- The repo already contains endpoint-specific behavioral knowledge, such as `account/statsbydate` returning `pvp: null` in live tests.
- Internal product logic does not consume the raw API directly; it normalizes data into `Player`, `Snapshot`, and derived JSON payloads used by the UI.

That means there are really two contracts to think about:

1. the unstable third-party producer contract from Wargaming,
2. the stable internal data contract we want the app to rely on.

## What ODCS Is Good At

ODCS is strongest when you want to describe a stable data product with:

- contract metadata and ownership,
- schema of objects and properties,
- quality expectations,
- SLA and support information,
- business meaning plus implementation details.

That maps well to things like:

- `player_daily_snapshots`
- `player_summary`
- `player_explorer_rows`
- `ranked_season_rows`

These are internal, curated, and should have explicit field meanings and freshness expectations.

## Where ODCS Is Weak For This Use Case

ODCS is not the best primary tool for describing a flaky third-party HTTP API because the main pain points here are:

- endpoint path and host differences by realm,
- query parameter behavior,
- undocumented null semantics,
- hidden-profile and missing-data behavior,
- retries, throttling, and operational quirks,
- divergence between docs and live responses.

ODCS can describe schema and operational expectations, but it is not naturally optimized for HTTP endpoint contracts in the same way OpenAPI is.

For the raw WG API, forcing everything into ODCS would likely create a contract that is technically valid but awkward to maintain and less useful during debugging.

## Recommended Layered Approach

### 1. Knowledge notes for upstream behavior

Keep narrative findings in `agents/knowledge/`.

Use this for:

- live endpoint investigations,
- doc/runtime mismatches,
- known broken endpoints,
- realm-specific caveats,
- practical reproduction commands.

Existing example:

- `agents/knowledge/wows-statsbydate-status.md`

### 2. Contract docs for raw upstream endpoints

Use a lightweight endpoint contract format for Wargaming itself.

Chosen format:

- repo-local YAML profiles that capture path, params, happy-path schema, and known deviations.

This is the best layer for answering questions like:

- what params do we send,
- what fields do we expect,
- what are known null / hidden / missing cases,
- which endpoints are considered trustworthy.

### 3. ODCS for internal normalized datasets

Use ODCS for the stable data products this repo exposes internally or conceptually depends on.

This is the best layer for answering questions like:

- what does `interval_battles` mean,
- what is the freshness expectation for `player_daily_snapshots`,
- which field is authoritative when upstream endpoints disagree,
- what quality checks should derived datasets satisfy.

## Recommendation For This Repo

Adopt both of these, not one alone:

- `agents/knowledge/` for narrative upstream knowledge,
- `agents/contracts/data-products/*.odcs.yaml` for normalized internal datasets.
- `agents/contracts/upstream/*.yaml` for raw WoWS endpoint profiles.

Do not try to model the entire WoWS upstream API as ODCS first.

For machine-readable coverage of WG endpoints, use the upstream YAML profile area instead of ODCS.

## Progress Since Initial Decision

The strategy is now partially implemented in repo artifacts instead of only being proposed.

- ODCS contracts now exist for `player_daily_snapshots`, `player_summary`, and `player_explorer_rows`.
- Upstream endpoint profiles now exist for `account/info`, `account/list`, `account/statsbydate`, and `clans/accountinfo`.
- Encyclopedia endpoint coverage now includes verified upstream profiles for `encyclopedia/info`, `encyclopedia/ships`, and `encyclopedia/modules`, plus a player-scoped ship-surface profile for `ships/badges` and narrative notes for the broader encyclopedia/ship namespace.
- The backend test suite includes contract-alignment checks for upstream endpoint field usage and serializer-backed payload behavior for player summary, explorer, clan membership, and ranked-history surfaces.
- Recent development expanded stable internal semantics around ranked-history retention and roster markers, which reinforces the need to keep derived contracts tied to current serializer/API output rather than upstream schemas alone.

The practical outcome is that the repo now has a real layered contract baseline: endpoint-focused YAML for unstable upstream behavior, ODCS for stable derived data products, and knowledge notes for investigative evidence.

## Good First Contracts

If we start small, the best first ODCS candidates are:

1. `player-daily-snapshots.odcs.yaml`
2. `player-summary.odcs.yaml`
3. `player-explorer-rows.odcs.yaml`

These are the places where the repo most needs consistent semantics across backend code, charts, and future UI/API work.

## Decision

- Use ODCS for internal derived datasets.
- Use knowledge notes plus upstream YAML profiles for raw WG API behavior.
- Treat the raw WoWS API as an unreliable source, not the contract boundary the rest of the app should bind to.

## Next Checks

- Keep the ODCS contracts in sync with serializer fields when player-summary or explorer payloads change.
- Add upstream YAML profiles for additional relied-on endpoints such as ranked and clan-battle endpoints as their product importance increases.
- Add upstream YAML profiles for `encyclopedia/ships` and `encyclopedia/modules` if ship-reference or build-comparison features start depending on them directly.
- Validate derived payloads in tests against contract expectations so contract drift fails in CI rather than in documentation review.
- Add knowledge notes when live endpoint behavior changes or when repo logic adopts a new upstream fallback strategy.
