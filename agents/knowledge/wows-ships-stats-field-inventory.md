# WoWS `ships/stats/` Field Inventory

Last verified: 2026-04-30 (Phase 7 widening shipped in v1.11.0 — 14 fields moved from "Discarded" to "Currently Captured")

## Why This Matters

Every player refresh on the droplet (~3 h cadence per realm via `incremental_player_refresh_task`) calls Wargaming's `ships/stats/` endpoint and receives a per-ship `pvp` block. Today, `incremental_battles._coerce_ship_snapshot` extracts only **eight** fields from that block (`battles`, `wins`, `losses`, `frags`, `damage_dealt`, `xp`, `planes_killed`, `survived_battles`) and discards the rest.

The discarded fields cost zero additional WG API budget to capture — they are already in every response we pull. Knowing the full surface up-front avoids re-discovery the next time the team plans a coverage expansion.

## Current Conclusion

- `ships/stats/` `pvp` block exposes ~25–30 cumulative counters and ~6–8 per-record bests per ship.
- Today we capture 8 cumulative counters and **zero** bests.
- The cumulative counters we discard cover three large product surfaces we do not yet measure: **gunnery accuracy**, **torpedo accuracy**, and **vision/objective play** (spotting, caps).
- Per-record bests (`max_*`) are not deltable but are useful as "career best" badges with no derivation logic.

## Currently Captured (`incremental_battles.py:_coerce_ship_snapshot`)

### Original 8 (since rollout Phase 2)

| Field | Type | Why we keep it |
|---|---|---|
| `battles` | counter | Drives `BattleEvent.created` and the per-day rollup |
| `wins` | counter | Win-rate computation in `compute_battle_events` |
| `losses` | counter | Same |
| `frags` | counter | KDR; per-ship frag totals |
| `damage_dealt` | counter | Avg damage; lifetime damage |
| `xp` | counter | Per-period XP, used in efficiency rank surfaces |
| `planes_killed` | counter | Air-defense signal, CV-aware |
| `survived_battles` | counter | KDR denominator, survival-rate distribution |

### Phase 7 widening (since v1.11.0, 2026-04-30)

Captured per-event in `BattleEvent.<name>_delta` and aggregated daily in `PlayerDailyShipStats.<name>`. Migration `0056_battle_event_phase7_widening`. See `agents/runbooks/runbook-battle-history-phase7-data-widening-2026-04-29.md` for the rollout history.

| Family | Source field | Future surface motivation |
|---|---|---|
| Gunnery | `main_battery.shots` | Main-battery accuracy = hits/shots |
| Gunnery | `main_battery.hits` | Same |
| Gunnery | `main_battery.frags` | Frag-source breakdown |
| Gunnery | `second_battery.shots` | Secondary accuracy (brawl identity) |
| Gunnery | `second_battery.hits` | Same |
| Gunnery | `second_battery.frags` | Niche brawl frag-source |
| Torpedoes | `torpedoes.shots` | Torp accuracy = hits/shots; "top-quartile torp" identity icon |
| Torpedoes | `torpedoes.hits` | Same |
| Torpedoes | `torpedoes.frags` | Torp-frag share of total frags |
| Spotting | `damage_scouting` | Vision-game contribution; "scout" identity icon |
| Spotting | `ships_spotted` | Spotting volume |
| Caps | `capture_points` | Objective play; "cap player" identity icon |
| Caps | `dropped_capture_points` | Cap-defense signal |
| Caps | `team_capture_points` | Indirect team-play index |

## Still Discarded — Cumulative Counters (Deltable)

These are running totals just like `battles`. The diff machinery in `compute_battle_events` would handle them with no architectural change — just additional fields on `ShipSnapshot`, additional `*_delta` fields on the resulting `BattleEvent`, and additional columns on `PlayerDailyShipStats`. Phase 7 widening covered the high-value fields; what remains here is intentionally deferred.

### Frag-source breakdown (rounding out `frags`)

| Field | Definition |
|---|---|
| `ramming.frags` | Frags by ramming (cumulative; rare) |
| `aircraft.frags` | Frags by carrier-launched aircraft (CV identity) |

### Damage-taken / position-quality

| Field | Definition | Useful for | Why deferred |
|---|---|---|---|
| `art_agro` | Potential damage taken from enemy main batteries | "Tank rating" — how much fire you draw | Field-name uncertainty (`art_agro` vs alternatives in some live samples). Capture once a live sample confirms the name in production. |
| `torpedo_agro` (sometimes `torp_agro`) | Potential damage taken from enemy torpedoes | Map-positioning quality | Same — name uncertainty. |
| `damage_to_buildings` | Damage to forts/installations | Mostly Operations mode; low product value for randoms | Low ROI for randoms surface. |

### Outcome enrichment

| Field | Definition | Useful for |
|---|---|---|
| `draws` | Drawn battles | Closes the W/L/D triangle (today draws are bucketed nowhere) |
| `survived_wins` | Survived **and** won | Splits "carry survival" from "lost-but-lived" |

### Misc cumulative

| Field | Definition | Useful for |
|---|---|---|
| `suppressions_count` | Times you suppressed enemy secondary guns | Niche brawl metric |
| `distance` (where present) | Total km sailed | Aggressiveness proxy; very rough |
| `battles_since_*` | Internal rotation counters | Skip — not useful for product |

## Discarded — Per-Record Bests (Not Deltable)

These are running maxes WG never decrements. They are not amenable to the diff-and-aggregate pipeline, but the *latest value at observation time* is useful for "best ever" badges on the per-ship row of the BattleHistoryCard.

| Field | Definition |
|---|---|
| `max_damage_dealt` | Best damage in one battle (career) |
| `max_xp` | Best XP in one battle |
| `max_frags_battle` | Most frags in one battle |
| `max_planes_killed_battle` | Best CV-AA / fighter showing |
| `max_ships_spotted` | Best spotting performance |
| `max_damage_scouting` | Best spotting damage |
| `max_total_agro` | Most fire drawn in one battle |
| `max_frags_ship_id` | Ship the player killed `max_frags_battle` enemies on (paired) |

## Adjacent Endpoints

These are **not** part of `ships/stats/` but are closely related and worth recording in the same place so future planners see the full WG surface.

- `seasons/shipstats/` — per-ranked-season per-ship totals. Wrapped in this repo as `_fetch_ranked_ship_stats_for_player` (`server/warships/api/ships.py`). Same field shape as `pvp` block; gives ranked coverage when paired with a parallel `ranked_ships_stats_json` field on `BattleObservation`. Phase 7 of the rollout runbook.
- `clanbattles/shipstats/` — per-CB-season per-ship totals. Already used for the per-player CB summary, not currently diffed for per-event coverage.
- `account/info` — `oper` (Operations / PvE) block exposes the same field vocabulary for co-op play. Cheap to add for PvE-Enjoyer coverage.
- `account/achievements/` — per-player achievement totals. Diffing gives per-event achievement deltas (Solo Warrior, Confederate, Kraken Unleashed, etc.). Costs one extra WG call per refresh — opt-in for tracked players, not the full population.

## Reproduction

```bash
# Fetch a single player's ships/stats payload directly (replace account_id):
curl -s "https://api.worldofwarships.com/wows/ships/stats/?application_id=<APP_ID>&account_id=<ID>" \
  | jq '.data."<ID>"[0] | keys, .pvp | keys'
```

The first `keys` returns the per-ship envelope (`account_id`, `ship_id`, `pvp`, `pve`, `pvp_solo`, `pvp_div2`, `pvp_div3`, etc.). The second returns the field inventory enumerated above for the `pvp` block.

## Implications for this Repo

- Any future expansion of `BattleHistoryCard` per-period metrics is bounded by what we capture in `BattleObservation.ships_stats_json`. Widening that capture is a one-PR change with zero new WG API calls.
- Per-record `max_*` values are an underused opportunity — capturing them lets us decorate the card with "career best" badges that need no derivation logic and no historical depth.
- `damage_scouting`, `capture_points`, `dropped_capture_points`, and the gunnery/torpedo accuracy fields together span a class of "play-style" metrics this repo does not currently surface anywhere. They are the natural fuel for future percentile-based identity icons (top-quartile torp accuracy, frequent cap-defender, vision-DD, etc.).

## Open Questions / Next Checks

- Confirm `art_agro` and `torpedo_agro` field names — older WG docs use `art_agro` / `torp_agro`, newer responses sometimes show `art_agro` and `torp_agro` interchangeably. A live sample in the repo would settle it.
- Confirm `distance` is populated for randoms (it is documented but inconsistent in some live responses).
- Verify `dropped_capture_points` semantics — WG docs imply "capture points removed from being capped" (defensive), but a live sample with a reset action would confirm.
