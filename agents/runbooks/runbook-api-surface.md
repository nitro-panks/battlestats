# Runbook: API Surface Coverage

_Last updated: 2026-03-17_

_Status: Active operational reference_

Tracks every public API endpoint, its smoke test case name, and coverage status.
Update this file when endpoints are added, removed, or renamed.

This runbook also records response-shape and operational context for stable endpoints when recent implementation work changes how QA should evaluate them.

## Smoke Test

```bash
docker compose exec -T server python scripts/smoke_test_site_endpoints.py
```

## Endpoint Registry

### Landing / Discovery

| Endpoint                                     | Method | Smoke Case           | Covered |
| -------------------------------------------- | ------ | -------------------- | ------- |
| `/api/landing/clans/`                        | GET    | `landing_clans`      | Yes     |
| `/api/landing/players/`                      | GET    | `landing_players`    | Yes     |
| `/api/landing/recent/`                       | GET    | `landing_recent`     | Yes     |
| `/api/landing/player-suggestions/?q=<query>` | GET    | `player_suggestions` | Yes     |

### Player (Router — PlayerViewSet)

| Endpoint              | Method       | Smoke Case               | Covered                                   |
| --------------------- | ------------ | ------------------------ | ----------------------------------------- |
| `/api/player/<name>/` | GET (detail) | `player_detail_shinn000` | Yes                                       |
| `/api/player/<name>/` | GET (404)    | `player_missing_404`     | Yes                                       |
| `/api/player/`        | GET (list)   | —                        | Skipped (unpaginated, too slow for smoke) |

**Key fields validated**: `name`, `pvp_ratio`, `pvp_survival_rate`, `verdict`, `kill_ratio`, `actual_kdr`, `player_score`, `is_clan_leader`, `highest_ranked_league`

**Behavior notes**:

- Detail reads should return `200` for existing players even when background task enqueue fails because the broker is unavailable.
- Background refresh is best-effort only; request-time payload delivery takes priority over async enqueue success.
- Player detail now exposes best historical ranked league semantics instead of relying only on the latest ranked season.
- Player detail now exposes `actual_kdr` as the literal kills-over-deaths metric while `kill_ratio` remains the weighted explorer/scoring signal.

### Player Fetch Endpoints

| Endpoint                                 | Method          | Smoke Case                | Covered |
| ---------------------------------------- | --------------- | ------------------------- | ------- |
| `/api/fetch/player_summary/<player_id>/` | GET             | `player_summary_shinn000` | Yes     |
| `/api/fetch/randoms_data/<player_id>/`   | GET             | `randoms_maraxus1`        | Yes     |
| `/api/fetch/tier_data/<player_id>/`      | GET             | `tier_secap`              | Yes     |
| `/api/fetch/type_data/<player_id>/`      | GET             | `type_fourgate`           | Yes     |
| `/api/fetch/activity_data/<player_id>/`  | GET             | `activity_fourgate`       | Yes     |
| `/api/fetch/ranked_data/<player_id>/`    | GET (populated) | `ranked_punkhunter25`     | Yes     |
| `/api/fetch/ranked_data/<player_id>/`    | GET (empty)     | `ranked_empty_kevik70`    | Yes     |

**Behavior notes**:

- `/api/fetch/player_summary/<player_id>/` is contract-backed by the derived ODCS player-summary artifact and should stay aligned with serializer fields.
- `/api/fetch/ranked_data/<player_id>/` now serves the full non-empty ranked history persisted on the player, not only the last 10 seasons.
- Fresh ranked cache rows should be served even when `top_ship_name` enrichment is missing; enrichment repair belongs to maintenance commands, not read paths.

### Clan (Router — ClanViewSet)

| Endpoint               | Method       | Smoke Case              | Covered                                   |
| ---------------------- | ------------ | ----------------------- | ----------------------------------------- |
| `/api/clan/<clan_id>/` | GET (detail) | `clan_detail_naumachia` | Yes                                       |
| `/api/clan/`           | GET (list)   | —                       | Skipped (unpaginated, too slow for smoke) |

### Clan Fetch Endpoints

| Endpoint                                    | Method    | Smoke Case                | Covered |
| ------------------------------------------- | --------- | ------------------------- | ------- |
| `/api/fetch/clan_data/<clan_id>:<filter>`   | GET       | `clan_data_naumachia`     | Yes     |
| `/api/fetch/clan_data/<clan_id>:<filter>`   | GET (400) | `clan_filter_invalid_400` | Yes     |
| `/api/fetch/clan_members/<clan_id>/`        | GET       | `clan_members_naumachia`  | Yes     |
| `/api/fetch/clan_battle_seasons/<clan_id>/` | GET       | `clan_battles_naumachia`  | Yes     |

**Key fields validated for clan members**: `name`, `is_hidden`, `pvp_ratio`, `days_since_last_battle`, `activity_bucket`, `is_leader`, `is_pve_player`, `is_ranked_player`, `highest_ranked_league`

**Behavior notes**:

- Clan member payloads now expose derived roster markers for leader, PvE-heavy players, ranked-heavy players, and highest historical ranked league.
- Leader detection may come from either `leader_id` or a fallback `leader_name` match when upstream clan data is incomplete.

### Ship (Router — ShipViewSet)

| Endpoint          | Method       | Smoke Case    | Covered               |
| ----------------- | ------------ | ------------- | --------------------- |
| `/api/ship/<id>/` | GET (detail) | `ship_detail` | Yes                   |
| `/api/ship/`      | GET (list)   | —             | Skipped (unpaginated) |

### Player Explorer

| Endpoint                             | Method | Smoke Case         | Covered |
| ------------------------------------ | ------ | ------------------ | ------- |
| `/api/players/explorer/?page_size=5` | GET    | `players_explorer` | Yes     |

**Behavior notes**:

- Explorer rows are contract-backed by the derived ODCS player-explorer artifact.
- Explorer remains a reduced subset of full player-summary detail rather than a second independently-defined player model.

### Population Distributions

| Endpoint                                           | Method | Smoke Case                             | Covered |
| -------------------------------------------------- | ------ | -------------------------------------- | ------- |
| `/api/fetch/wr_distribution/`                      | GET    | `wr_distribution`                      | Yes     |
| `/api/fetch/player_distribution/win_rate/`         | GET    | `player_distribution_win_rate`         | Yes     |
| `/api/fetch/player_distribution/survival_rate/`    | GET    | `player_distribution_survival_rate`    | Yes     |
| `/api/fetch/player_distribution/battles_played/`   | GET    | `player_distribution_battles_played`   | Yes     |
| `/api/fetch/player_correlation/win_rate_survival/` | GET    | `player_correlation_win_rate_survival` | Yes     |

**Additional covered-but-not-smoked detail**:

- `/api/fetch/player_correlation/ranked_wr_battles/<player_id>/` is validated by backend tests rather than by the current smoke script.
- Ranked correlation tracked population should include only visible players meeting the configured ranked-battle threshold.

### Stats

| Endpoint      | Method | Smoke Case | Covered |
| ------------- | ------ | ---------- | ------- |
| `/api/stats/` | GET    | `stats`    | Yes     |

## Coverage Summary

- **Total endpoints**: 27 (counting distinct path+status combinations)
- **Smoke-tested**: 24
- **Skipped (perf)**: 3 (unpaginated list endpoints: player, clan, ship)
- **Not covered**: 0

## Notes

- `clan_battles_naumachia` depends on an async Celery worker to populate data; the test retries up to 6 times (5s apart) and passes with a warning if the worker isn't running.
- List endpoints for `/api/player/`, `/api/clan/`, `/api/ship/` are standard DRF router defaults. They are unpaginated and return all records, making them too slow for smoke testing against a production-sized dataset. They are not used by the client.
- Ranked maintenance is now split intentionally:
  - `backfill_ranked_data` repairs historic coverage and missing enrichment.
  - `incremental_ranked_data` keeps known-ranked rows fresh and samples discovery candidates.
- Efficiency badge maintenance now also has a dedicated sweep path:
  - `backfill_player_efficiency_badges` durably fills missing or unstamped `efficiency_json` rows for players in scope.
- Player detail now publishes and renders a Battlestats efficiency-rank header marker from the player payload fields `efficiency_rank_tier`, `efficiency_rank_percentile`, `efficiency_rank_population_size`, and `efficiency_rank_updated_at` when the published snapshot is fresh.
- API read paths should not be used as a ranked-data repair mechanism.
- Contract-backed surfaces for this API area currently include `player_summary` and `player_explorer_rows`; see the contracts runbook and ODCS artifacts when fields change.
- For deeper behavior and QA acceptance criteria around player-detail reads, ranked league semantics, ranked correlation, and ranked maintenance reconciliation, see [agents/runbooks/archive/runbook-player-detail-ranked-hardening.md](/home/august/code/archive/battlestats/agents/runbooks/archive/runbook-player-detail-ranked-hardening.md).

## Changelog

| Date       | Change                                                                                                                                                                                                    |
| ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 2026-03-10 | Added: player_suggestions, player_list, player_detail (with verdict), clan_list, clan_detail, ship_list, players_explorer, player_distribution_survival_rate. Reorganized test cases by category.         |
| 2026-03-14 | Added behavior context for player-detail broker-failure tolerance, clan-member roster markers, full ranked-history reads, ranked maintenance split, and contract-backed player summary/explorer surfaces. |
| 2026-03-17 | Updated player-detail contract notes to include `actual_kdr` and clarified that `kill_ratio` remains a weighted metric rather than literal K/D.                                                           |
