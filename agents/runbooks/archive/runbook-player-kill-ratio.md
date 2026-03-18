# Runbook: Player Weighted KDR And Score

## Historical Status

This archived runbook is still accurate about the meaning of weighted `kill_ratio` and the construction of `player_score`, but it is no longer accurate about player-detail presentation.

Current product reality:

1. player detail now shows literal `actual_kdr`, not weighted `kill_ratio`,
2. weighted `kill_ratio` still exists for explorer sorting and `player_score`,
3. the follow-up runbook is [agents/runbooks/runbook-player-kdr-alignment.md](../runbook-player-kdr-alignment.md).

## Goal

Keep the weighted kill metric in place and add a new synthetic `player_score` that blends long-term skill and recency into a single detail-view score.

The score uses:

- PvP win rate
- weighted KDR
- survival rate
- total battles
- recent activity

The score is meant to read as a current-strength indicator, not a pure skill rating.

## Weighted KDR Shape

- Keep the existing `kill_ratio` field name in storage and API responses so explorer sorting and downstream consumers stay stable.
- Treat the repo's ship-row `kdr` as the input signal. In the current cached battle rows it is kills per battle, not literal kills divided by deaths.
- Compute a smoothed per-ship kill rate first, then combine ships with tier-group weights.

Formula:

1. Ship smoothing:
   - `smoothed_ship_kill_rate = ((ship_kdr * battles) + (0.7 * 12)) / (battles + 12)`
2. Tier-group weights:
   - tiers `1-4` -> `0.15`
   - tiers `5-7` -> `0.65`
   - tiers `8-11` -> `1.0`
3. Ship contribution weight:
   - `sqrt(battles) * tier_weight`
4. Final player metric:
   - weighted average of the smoothed ship kill rates across all played ships

This combination keeps tiny ship samples from spiking the metric, suppresses low-tier seal-clubbing, and still rewards broad high-tier performance.

## Player Score Shape

`player_score` is stored on `PlayerExplorerSummary` and exposed on player detail.

### Inputs

- WR contribution: normalized from PvP win rate
- weighted KDR contribution: normalized from the cached weighted KDR value
- survival contribution: normalized from PvP survival percent
- battle-volume contribution: normalized from total battles with logarithmic saturation
- recent-activity contribution: normalized from daily activity rows using Fibonacci-style recency windows

### Normalization

1. WR:

- `clamp((wr - 45) / 20, 0, 1)`

2. weighted KDR:

- `clamp((wkdr - 0.4) / 1.6, 0, 1)`

3. survival:

- `clamp((survival - 25) / 25, 0, 1)`

4. total battles:

- `clamp(log10(total_battles + 1) / 4, 0, 1)`

5. recent activity:

- each daily row is weighted by a Fibonacci-style cadence:
  - days `0-1` -> `34`
  - days `2-3` -> `21`
  - days `4-7` -> `13`
  - days `8-13` -> `8`
  - days `14-21` -> `5`
  - days `22-34` -> `3`
  - days `35-55` -> `2`
  - days `56-89` -> `1`
  - days `90-144` -> `0.55`
  - days `145-233` -> `0.34`
  - days `234-365` -> `0.21`
  - older than `365` -> `0.08`
- daily battle intensity is capped with `log1p(battles) / log1p(8)` so one extreme session does not dominate the score.

### Aggregate Formula

Use the weighted average of the available normalized inputs:

- WR: `0.36`
- weighted KDR: `0.24`
- survival: `0.14`
- total battles: `0.10`
- recent activity: `0.16`

Final score:

- `player_score = weighted_average(inputs) * 10`

### Dormant Account Rule

- If `days_since_last_battle > 365`, force the final score into `(0, 1)`.
- Implementation:
  - `clamp(base_score * 0.08, 0.05, 0.95)`
- This avoids showing `0`, which reads like a missing calculation, while still preserving a weak residual signal from the account's historical quality.

## Product Placement

- Player detail:
  - historical plan: keep weighted KDR in the four summary cards
  - current product: the four-card detail strip now shows `actual_kdr` instead
  - render `Player Score` right-aligned on the player-name row as a compact summary badge
- Player explorer:
  - keep the sortable field key as `kill_ratio`, but update copy so users understand it is tier-weighted rather than a plain average
  - add `player_score` as a visible explorer column and a supported sort option

## Data Flow

1. Compute the metric centrally in `warships.data._calculate_player_kill_ratio()`.
2. Compute `player_score` centrally in `warships.data._calculate_player_score()`.
3. Persist both values through `build_player_summary()` and `refresh_player_explorer_summary()`.
4. Expose them on player detail through `PlayerSerializer`.
5. Keep explorer and summary endpoints on the same stored values.
6. Allow explorer sorting by `player_score` in the API and render it in the client table.

Schema change:

- add `player_score` to `PlayerExplorerSummary`
- migration: `warships.0023_playerexplorersummary_player_score`

## Backfill And Ongoing Refresh

- Existing players:
  - run `python manage.py migrate`
  - run `python manage.py backfill_player_explorer_summaries --batch-size 2000`
- Newly created or refreshed players:
  - no extra path is needed because `update_player_data()`, `update_activity_data()`, `update_battle_data()`, and the player-detail refresh path already refresh the explorer summary row.

## Ship Metadata Fetch Hardening

WG ship encyclopedia lookups occasionally return null payloads for ship IDs that still appear in player ship stats.

Without hardening, those rows get dropped from `battles_json`, which can suppress:

- weighted KDR
- player score
- ship-count metrics
- downstream charts

Current hardening behavior:

- if `_fetch_ship_info()` cannot resolve metadata for a ship row, `update_battle_data()` now keeps the row using fallback metadata
- fallback values:
  - `ship_name`: `Unknown Ship <ship_id>`
  - `ship_chart_name`: derived from the fallback name
  - `ship_type`: `Unknown`
  - `ship_tier`: `0`

Effect:

- battle volume and win/loss contribution are preserved
- explorer summary metrics keep the row instead of silently losing it
- tier-aware weighting falls back to the repo's mid-tier default when tier is unknown

## Build, Deploy, Test Strategy

### Build

- backend:
  - `docker compose exec -T server python manage.py migrate`
- frontend:
  - `cd client && npm run build`

### Deploy

1. ship the backend code and migration first
2. apply migrations
3. backfill explorer summaries so the stored `player_score` exists on historical rows
4. ship the frontend bundle that reads and renders `player_score`
5. restart the stack or redeploy containers

### Test

Backend:

- `docker compose exec -T server python manage.py test warships.tests.test_data.PlayerExplorerSummaryTests warships.tests.test_views.PlayerViewSetTests warships.tests.test_views.ApiContractTests.test_player_detail_includes_kill_ratio warships.tests.test_views.ApiContractTests.test_player_detail_backfills_missing_kill_ratio_from_stale_summary`
- `docker compose exec -T server python manage.py test warships.tests.test_data.RandomsDataRefreshTests warships.tests.test_views.ApiContractTests.test_players_explorer_sorts_by_player_score_desc`

Frontend:

- `cd client && npm run build`

Manual/API checks:

- confirm active strong accounts land well above average
- confirm dormant strong accounts land below `1` but above `0`
- confirm the detail header renders the score badge without shifting the player name layout

## Validation

- Automated:
  - targeted backend score and player-detail tests passed
  - client build passed
  - explorer sort test for `player_score` passed
  - ship metadata fallback test passed
- Real-player API validation:
  - `VL6NJH_E` -> score `8.31`, WR `91.57`, weighted KDR `5.47`, survival `90.23`, battles `4527`, days inactive `0`
  - `Slowhand57` -> score `8.4`, WR `86.79`, weighted KDR `2.38`, survival `78.33`, battles `34161`, days inactive `0`
  - `VL6NJH_` -> score `8.4`, WR `81.21`, weighted KDR `5.02`, survival `81.62`, battles `15375`, days inactive `0`
  - `Geargiong` -> score `6.8`, WR `80.66`, weighted KDR `0.96`, survival `54.13`, battles `7029`, days inactive `41`
  - `KamiSamurai` -> score `7.41`, WR `79.3`, weighted KDR `1.34`, survival `71.22`, battles `18208`, days inactive `0`
  - dormant check: `kaisei2020` -> score `0.61`, WR `92.56`, weighted KDR `1.55`, survival `86.41`, battles `5253`, days inactive `779`

Interpretation:

- the active elite accounts cluster high, between roughly `6.8` and `8.4`
- the less active but still strong account (`Geargiong`) lands meaningfully below the always-active elite accounts
- the dormant account rule behaved correctly: the account kept a small non-zero signal and stayed below `1`

## Performance Notes

- The computation is still `O(number_of_ship_rows)` and runs against already cached `battles_json`.
- Activity scoring is `O(number_of_activity_rows)` and runs against already cached `activity_json`.
- One schema migration is required for `player_score`.
- No extra network call is required for player detail because the serializer can read the cached explorer summary.
- When ship encyclopedia metadata is missing, fallback rows avoid extra retries in the detail path and preserve summary integrity with minimal extra work.

## Rollback

1. Revert the code change.
2. Optionally roll back migration `0023` if removing the field entirely.
3. Re-run `python manage.py backfill_player_explorer_summaries --batch-size 2000` to repopulate stored values with the previous formula.
