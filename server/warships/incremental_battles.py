"""Incremental battle capture — pull-and-diff WG aggregate stats.

Companion runbooks:
* `agents/runbooks/runbook-incremental-battle-poc-2026-04-27.md` (PoC, 60 s poll)
* `agents/runbooks/runbook-battle-history-rollout-2026-04-28.md` (playerbase rollout)

The Wargaming public API exposes only running totals; per-battle deltas must be
computed by diffing successive snapshots.

The module exposes two entry points to the same diff machinery:

* `record_observation_from_payloads(player, player_data, ship_data)` — core
  orchestrator. Issues no WG calls. Takes the in-memory payloads the caller
  already fetched (or `None` for `player_data` to read aggregates straight
  off the `Player` row, which the rollout's piggyback hook does).
* `record_observation_and_diff(player_id, realm)` — thin wrapper. Resolves
  the `Player`, issues the WG calls, then defers to the core orchestrator.
  Used by the lil_boots PoC poll task and by tests that want to drive the
  end-to-end path with a stubbed WG client.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from django.db import connection, transaction
from django.db.models import F, Q


logger = logging.getLogger(__name__)

# Phase 0a instrumentation — aggregate wall-time of the per-mover battles_json
# rebuild (`apply_battles_json`), sampled by the bulk floor sweep so we can tell
# whether that rebuild is the floor's throughput throttle. Process-local: the
# bulk sweep resets it at entry and logs the total at exit. Safe because the
# `background` Celery pool is prefork (one process per slot) and a single
# `record_observations_bulk` call runs synchronously start-to-finish, so no other
# observation write interleaves in-process between reset and read.
_BATTLES_JSON_REBUILD_TIMING = {"count": 0, "total_ms": 0.0}


@dataclass(frozen=True)
class ShipSnapshot:
    ship_id: int
    battles: int
    wins: int
    losses: int
    frags: int
    damage_dealt: int
    xp: int
    planes_killed: int
    survived_battles: int
    # Phase 7 widening — gunnery / torpedoes / spotting / caps. All cumulative
    # counters from the same ships/stats/ pvp block, default 0 when WG omits
    # the nested object (e.g. ships with no torpedoes or no secondaries).
    main_shots: int = 0
    main_hits: int = 0
    main_frags: int = 0
    secondary_shots: int = 0
    secondary_hits: int = 0
    secondary_frags: int = 0
    torpedo_shots: int = 0
    torpedo_hits: int = 0
    torpedo_frags: int = 0
    damage_scouting: int = 0
    ships_spotted: int = 0
    capture_points: int = 0
    dropped_capture_points: int = 0
    team_capture_points: int = 0


@dataclass(frozen=True)
class PlayerSnapshot:
    pvp_battles: int
    pvp_wins: int
    pvp_losses: int
    pvp_frags: int
    pvp_survived_battles: int
    last_battle_time: Optional[datetime]
    ships: Dict[int, ShipSnapshot]


@dataclass(frozen=True)
class RankedShipSeasonSnapshot:
    """Per-ship per-season ranked snapshot from WG `seasons/shipstats/`.

    Phase 1 of the ranked battle-history rollout
    (runbook-ranked-battle-history-rollout-2026-05-02.md). Smaller field set
    than ShipSnapshot — the ranked endpoint doesn't carry the gunnery /
    torpedo / spotting / caps nested objects (Phase 7 widening only applies
    to randoms today). Diff key is the (ship_id, season_id) tuple so
    multiple active seasons coexist within a single observation.
    """
    ship_id: int
    season_id: int
    battles: int
    wins: int
    losses: int
    frags: int
    damage_dealt: int
    xp: int
    survived_battles: int


def _coerce_ship_snapshot(ship_dict: Dict[str, Any]) -> Optional[ShipSnapshot]:
    try:
        ship_id = int(ship_dict["ship_id"])
    except (KeyError, TypeError, ValueError):
        return None
    pvp = ship_dict.get("pvp") or {}
    main = pvp.get("main_battery") or {}
    secondary = pvp.get("second_battery") or {}
    torpedoes = pvp.get("torpedoes") or {}
    try:
        return ShipSnapshot(
            ship_id=ship_id,
            battles=int(pvp.get("battles", 0)),
            wins=int(pvp.get("wins", 0)),
            losses=int(pvp.get("losses", 0)),
            frags=int(pvp.get("frags", 0)),
            damage_dealt=int(pvp.get("damage_dealt", 0)),
            xp=int(pvp.get("xp", 0)),
            planes_killed=int(pvp.get("planes_killed", 0)),
            survived_battles=int(pvp.get("survived_battles", 0)),
            main_shots=int(main.get("shots", 0)),
            main_hits=int(main.get("hits", 0)),
            main_frags=int(main.get("frags", 0)),
            secondary_shots=int(secondary.get("shots", 0)),
            secondary_hits=int(secondary.get("hits", 0)),
            secondary_frags=int(secondary.get("frags", 0)),
            torpedo_shots=int(torpedoes.get("shots", 0)),
            torpedo_hits=int(torpedoes.get("hits", 0)),
            torpedo_frags=int(torpedoes.get("frags", 0)),
            damage_scouting=int(pvp.get("damage_scouting", 0)),
            ships_spotted=int(pvp.get("ships_spotted", 0)),
            capture_points=int(pvp.get("capture_points", 0)),
            dropped_capture_points=int(pvp.get("dropped_capture_points", 0)),
            team_capture_points=int(pvp.get("team_capture_points", 0)),
        )
    except (TypeError, ValueError):
        return None


def coerce_observation_payload(
    player_data: Dict[str, Any],
    ship_data: Iterable[Dict[str, Any]],
) -> Optional[PlayerSnapshot]:
    """Reduce raw WG payloads into a PlayerSnapshot. Returns None on hidden/empty."""
    if not player_data or player_data.get("hidden_profile"):
        return None
    statistics = player_data.get("statistics") or {}
    pvp = statistics.get("pvp") or {}
    last_battle_time_raw = player_data.get("last_battle_time")
    last_battle_time: Optional[datetime] = None
    if last_battle_time_raw:
        try:
            last_battle_time = datetime.fromtimestamp(
                int(last_battle_time_raw), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            last_battle_time = None

    ships = _ships_from_iterable(ship_data)

    try:
        return PlayerSnapshot(
            pvp_battles=int(pvp.get("battles", 0)),
            pvp_wins=int(pvp.get("wins", 0)),
            pvp_losses=int(pvp.get("losses", 0)),
            pvp_frags=int(pvp.get("frags", 0)),
            pvp_survived_battles=int(pvp.get("survived_battles", 0)),
            last_battle_time=last_battle_time,
            ships=ships,
        )
    except (TypeError, ValueError):
        return None


def _ships_from_iterable(ship_data: Iterable[Dict[str, Any]]) -> Dict[int, ShipSnapshot]:
    ships: Dict[int, ShipSnapshot] = {}
    for ship_dict in ship_data or []:
        ship_snapshot = _coerce_ship_snapshot(ship_dict)
        if ship_snapshot is None:
            continue
        ships[ship_snapshot.ship_id] = ship_snapshot
    return ships


# WG `seasons/shipstats/` payload shape (per ship):
#   {
#     "ship_id": <int>,
#     "seasons": {
#       "<season_id>": {
#         "<rank_tier>": {                    ← rank index, e.g. "0", "1"
#           "rank_solo": {battles, wins, ...} ← may be NULL
#           "rank_div2": {...} | NULL
#           "rank_div3": {...} | NULL
#         },
#         ...
#       }
#     }
#   }
#
# Per-ship per-season totals are aggregated by summing the leaf stat
# dicts across all rank tiers and all three div_mode buckets. The
# Phase-1 implementation read `seasons[id].get("battles")` directly,
# which always returned None — the diff lane silently produced zero
# events even when a player had real ranked play. Fixed 2026-05-02.
_RANKED_DIV_MODES = ("rank_solo", "rank_div2", "rank_div3")


def _coerce_ranked_season_stats(
    ship_id: int, season_id_raw: Any, stats: Dict[str, Any],
) -> Optional[RankedShipSeasonSnapshot]:
    if not isinstance(stats, dict):
        return None
    try:
        season_id = int(season_id_raw)
    except (TypeError, ValueError):
        return None

    totals = {
        "battles": 0, "wins": 0, "losses": 0, "frags": 0,
        "damage_dealt": 0, "xp": 0, "survived_battles": 0,
    }

    # Shape A (real WG payload): nested rank-tier × div_mode buckets.
    # Walk the rank tiers, sum the leaf stat dicts across all div modes.
    found_nested = False
    for rank_tier_value in stats.values():
        if not isinstance(rank_tier_value, dict):
            continue
        for div_key in _RANKED_DIV_MODES:
            leaf = rank_tier_value.get(div_key)
            if not isinstance(leaf, dict):
                continue
            found_nested = True
            for k in totals:
                v = leaf.get(k)
                if isinstance(v, (int, float)):
                    totals[k] += int(v)

    # Shape B (legacy / test fixtures): flat stat keys directly on the
    # season dict. Used by test fixtures and any future flattened source.
    # Only consult Shape B when Shape A produced nothing — never mix.
    if not found_nested:
        for k in totals:
            v = stats.get(k)
            if isinstance(v, (int, float)):
                totals[k] = int(v)

    return RankedShipSeasonSnapshot(
        ship_id=ship_id,
        season_id=season_id,
        battles=totals["battles"],
        wins=totals["wins"],
        losses=totals["losses"],
        frags=totals["frags"],
        damage_dealt=totals["damage_dealt"],
        xp=totals["xp"],
        survived_battles=totals["survived_battles"],
    )


def _ranked_ships_from_iterable(
    ranked_rows: Iterable[Dict[str, Any]],
) -> Dict[Tuple[int, int], RankedShipSeasonSnapshot]:
    """Coerce raw `seasons/shipstats/` rows into a (ship_id, season_id) map.

    Each row from WG looks like {ship_id: ..., seasons: {"22": {...},
    "21": {...}}}. We capture every (ship_id, season_id) the player has
    rows for so the diff covers ALL active seasons in one observation.
    Empty / malformed rows are silently dropped.
    """
    out: Dict[Tuple[int, int], RankedShipSeasonSnapshot] = {}
    for row in ranked_rows or []:
        if not isinstance(row, dict):
            continue
        try:
            ship_id = int(row["ship_id"])
        except (KeyError, TypeError, ValueError):
            continue
        seasons_payload = row.get("seasons")
        if not isinstance(seasons_payload, dict):
            continue
        for season_id_raw, season_stats in seasons_payload.items():
            snap = _coerce_ranked_season_stats(
                ship_id, season_id_raw, season_stats)
            if snap is None:
                continue
            out[(snap.ship_id, snap.season_id)] = snap
    return out


def compute_ranked_battle_events(
    previous_ranked: Dict[Tuple[int, int], RankedShipSeasonSnapshot],
    current_ranked: Dict[Tuple[int, int], RankedShipSeasonSnapshot],
) -> List[Dict[str, Any]]:
    """Return one delta row per (ship_id, season_id) that advanced.

    Mirrors compute_battle_events but keyed on (ship_id, season_id) instead
    of just ship_id. A new (ship_id, season_id) appearing in the current
    observation but absent from the previous one is treated as a baseline:
    the prior snapshot is implicitly zero, so the delta IS the current
    value. That correctly attributes all of the player's activity in that
    season-ship pair since the start of the season (we have no earlier
    observation to attribute against).
    """
    events: List[Dict[str, Any]] = []
    for key, current_ship in current_ranked.items():
        previous_ship = previous_ranked.get(key)
        prev_battles = previous_ship.battles if previous_ship else 0
        delta_battles = current_ship.battles - prev_battles
        if delta_battles <= 0:
            continue
        prev_survived = (
            previous_ship.survived_battles if previous_ship else 0)
        survived_delta = current_ship.survived_battles - prev_survived
        survived: Optional[bool] = None
        if delta_battles == 1:
            survived = survived_delta == 1
        events.append({
            "ship_id": current_ship.ship_id,
            "season_id": current_ship.season_id,
            "battles_delta": delta_battles,
            "wins_delta": current_ship.wins - (
                previous_ship.wins if previous_ship else 0),
            "losses_delta": current_ship.losses - (
                previous_ship.losses if previous_ship else 0),
            "frags_delta": current_ship.frags - (
                previous_ship.frags if previous_ship else 0),
            "damage_delta": current_ship.damage_dealt - (
                previous_ship.damage_dealt if previous_ship else 0),
            "xp_delta": current_ship.xp - (
                previous_ship.xp if previous_ship else 0),
            "survived_delta": survived_delta,
            "survived": survived,
        })
    return events


def _serialize_ranked_ships_payload(
    ranked_map: Dict[Tuple[int, int], RankedShipSeasonSnapshot],
) -> List[Dict[str, Any]]:
    """Encode a ranked snapshot map back into the WG row shape so it can be
    persisted in BattleObservation.ranked_ships_stats_json and re-hydrated
    by the next observation's diff lane."""
    by_ship: Dict[int, Dict[str, Any]] = {}
    for (ship_id, _season_id), snap in ranked_map.items():
        ship_entry = by_ship.setdefault(
            ship_id, {"ship_id": ship_id, "seasons": {}})
        ship_entry["seasons"][str(snap.season_id)] = {
            "battles": snap.battles,
            "wins": snap.wins,
            "losses": snap.losses,
            "frags": snap.frags,
            "damage_dealt": snap.damage_dealt,
            "xp": snap.xp,
            "survived_battles": snap.survived_battles,
        }
    return list(by_ship.values())


def _hydrate_previous_ranked_snapshot(
    previous,
    player=None,
) -> Dict[Tuple[int, int], RankedShipSeasonSnapshot]:
    """Rebuild the prior ranked map for the diff lane.

    Walk-back semantics: when the most recent observation's
    `ranked_ships_stats_json` is NULL (fetch failed for that tick), step
    back through earlier observations until we find one with a non-NULL
    payload. NULL means "ranked state unknown for that tick" — using it
    as the baseline would falsely attribute the player's entire ranked
    history to whatever activity happens after the failed tick.

    `[]` (empty list) is distinct from NULL: it means "fetched
    successfully, player had no ranked rows in any active season" and IS
    a legitimate zero-state baseline — treat as "no prior ranked play".

    `player` is optional; when omitted the walk-back is skipped (legacy
    callers that don't have the player handle keep the original behavior).
    """
    if previous is None:
        return {}
    if previous.ranked_ships_stats_json is not None:
        return _ranked_ships_from_iterable(previous.ranked_ships_stats_json)

    # Latest is NULL → walk back for the most recent non-NULL prior.
    # Bound the walk to observations at-or-before `previous.observed_at`
    # so we don't pick up the just-created current observation, which by
    # definition is chronologically newer than `previous` inside the
    # transaction.
    if player is None:
        return {}
    from warships.models import BattleObservation
    fallback = (
        BattleObservation.objects
        .filter(
            player=player,
            ranked_ships_stats_json__isnull=False,
            observed_at__lte=previous.observed_at,
        )
        .order_by("-observed_at")
        .first()
    )
    if fallback is None:
        return {}
    return _ranked_ships_from_iterable(fallback.ranked_ships_stats_json)


def _snapshot_from_player_row(player, ship_data: Iterable[Dict[str, Any]]) -> Optional[PlayerSnapshot]:
    """Build a PlayerSnapshot from a refreshed `Player` row + ship_data payload.

    Used by the rollout's piggyback hook in `update_battle_data`, which has
    already refreshed the Player aggregates via `update_player_data` and just
    finished the `ships/stats/` fetch.
    """
    if getattr(player, "is_hidden", False):
        return None
    return PlayerSnapshot(
        pvp_battles=int(player.pvp_battles or 0),
        pvp_wins=int(player.pvp_wins or 0),
        pvp_losses=int(player.pvp_losses or 0),
        pvp_frags=int(player.pvp_frags or 0),
        pvp_survived_battles=int(player.pvp_survived_battles or 0),
        last_battle_time=None,
        ships=_ships_from_iterable(ship_data),
    )


def compute_battle_events(
    previous: PlayerSnapshot,
    current: PlayerSnapshot,
) -> List[Dict[str, Any]]:
    """Return one delta row per ship whose pvp battle count advanced.

    Pure function: callable from tests without any DB. Returns a list of dicts
    with stable keys: ship_id, battles_delta, wins_delta, losses_delta,
    frags_delta, damage_delta, xp_delta, planes_killed_delta, survived_delta,
    survived. Empty list when nothing advanced.

    Note: deliberately does NOT short-circuit on `current.pvp_battles <=
    previous.pvp_battles`. WG's `account/info` (where pvp_battles comes
    from) and `ships/stats` (where per-ship battles come from) don't
    update in lockstep — ships can advance while account hasn't caught
    up, and vice versa. Diffing per-ship is the authoritative path; the
    per-ship loop below correctly returns [] when no ship advanced.
    """
    # Phase 7 widened delta vocabulary. Each entry: (output key, attr name).
    # All are simple `current.attr - previous.attr` cumulative diffs.
    PHASE7_DELTA_FIELDS = (
        ("main_shots_delta", "main_shots"),
        ("main_hits_delta", "main_hits"),
        ("main_frags_delta", "main_frags"),
        ("secondary_shots_delta", "secondary_shots"),
        ("secondary_hits_delta", "secondary_hits"),
        ("secondary_frags_delta", "secondary_frags"),
        ("torpedo_shots_delta", "torpedo_shots"),
        ("torpedo_hits_delta", "torpedo_hits"),
        ("torpedo_frags_delta", "torpedo_frags"),
        ("damage_scouting_delta", "damage_scouting"),
        ("ships_spotted_delta", "ships_spotted"),
        ("capture_points_delta", "capture_points"),
        ("dropped_capture_points_delta", "dropped_capture_points"),
        ("team_capture_points_delta", "team_capture_points"),
    )

    events: List[Dict[str, Any]] = []
    for ship_id, current_ship in current.ships.items():
        previous_ship = previous.ships.get(ship_id)
        prev_battles = previous_ship.battles if previous_ship else 0
        delta_battles = current_ship.battles - prev_battles
        if delta_battles <= 0:
            continue
        prev_wins = previous_ship.wins if previous_ship else 0
        prev_losses = previous_ship.losses if previous_ship else 0
        prev_frags = previous_ship.frags if previous_ship else 0
        prev_damage = previous_ship.damage_dealt if previous_ship else 0
        prev_xp = previous_ship.xp if previous_ship else 0
        prev_planes = previous_ship.planes_killed if previous_ship else 0
        prev_survived = previous_ship.survived_battles if previous_ship else 0
        survived_delta = current_ship.survived_battles - prev_survived
        # Only attribute survived/died for the single-match case; multi-match
        # gaps (>1 battles_delta) leave it ambiguous which match we survived.
        survived: Optional[bool] = None
        if delta_battles == 1:
            survived = survived_delta == 1
        event = {
            "ship_id": ship_id,
            "battles_delta": delta_battles,
            "wins_delta": current_ship.wins - prev_wins,
            "losses_delta": current_ship.losses - prev_losses,
            "frags_delta": current_ship.frags - prev_frags,
            "damage_delta": current_ship.damage_dealt - prev_damage,
            "xp_delta": current_ship.xp - prev_xp,
            "planes_killed_delta": current_ship.planes_killed - prev_planes,
            "survived_delta": survived_delta,
            "survived": survived,
        }
        for delta_key, attr in PHASE7_DELTA_FIELDS:
            prev_val = getattr(previous_ship, attr, 0) if previous_ship else 0
            event[delta_key] = getattr(current_ship, attr) - prev_val
        events.append(event)
    return events



def _serialize_ships_payload(snapshot: PlayerSnapshot) -> List[Dict[str, Any]]:
    return [
        {
            "ship_id": ship.ship_id,
            "battles": ship.battles,
            "wins": ship.wins,
            "losses": ship.losses,
            "frags": ship.frags,
            "damage_dealt": ship.damage_dealt,
            "xp": ship.xp,
            "planes_killed": ship.planes_killed,
            "survived_battles": ship.survived_battles,
            "main_shots": ship.main_shots,
            "main_hits": ship.main_hits,
            "main_frags": ship.main_frags,
            "secondary_shots": ship.secondary_shots,
            "secondary_hits": ship.secondary_hits,
            "secondary_frags": ship.secondary_frags,
            "torpedo_shots": ship.torpedo_shots,
            "torpedo_hits": ship.torpedo_hits,
            "torpedo_frags": ship.torpedo_frags,
            "damage_scouting": ship.damage_scouting,
            "ships_spotted": ship.ships_spotted,
            "capture_points": ship.capture_points,
            "dropped_capture_points": ship.dropped_capture_points,
            "team_capture_points": ship.team_capture_points,
        }
        for ship in snapshot.ships.values()
    ]


def _hydrate_previous_snapshot(previous) -> PlayerSnapshot:
    return PlayerSnapshot(
        pvp_battles=previous.pvp_battles,
        pvp_wins=previous.pvp_wins,
        pvp_losses=previous.pvp_losses,
        pvp_frags=previous.pvp_frags,
        pvp_survived_battles=previous.pvp_survived_battles,
        last_battle_time=previous.last_battle_time,
        ships={
            int(row["ship_id"]): ShipSnapshot(
                ship_id=int(row["ship_id"]),
                battles=int(row.get("battles", 0)),
                wins=int(row.get("wins", 0)),
                losses=int(row.get("losses", 0)),
                frags=int(row.get("frags", 0)),
                damage_dealt=int(row.get("damage_dealt", 0)),
                xp=int(row.get("xp", 0)),
                planes_killed=int(row.get("planes_killed", 0)),
                survived_battles=int(row.get("survived_battles", 0)),
                # Phase 7 widening — historical observations written before
                # the widening landed lack these keys, hence .get(..., 0).
                main_shots=int(row.get("main_shots", 0)),
                main_hits=int(row.get("main_hits", 0)),
                main_frags=int(row.get("main_frags", 0)),
                secondary_shots=int(row.get("secondary_shots", 0)),
                secondary_hits=int(row.get("secondary_hits", 0)),
                secondary_frags=int(row.get("secondary_frags", 0)),
                torpedo_shots=int(row.get("torpedo_shots", 0)),
                torpedo_hits=int(row.get("torpedo_hits", 0)),
                torpedo_frags=int(row.get("torpedo_frags", 0)),
                damage_scouting=int(row.get("damage_scouting", 0)),
                ships_spotted=int(row.get("ships_spotted", 0)),
                capture_points=int(row.get("capture_points", 0)),
                dropped_capture_points=int(row.get("dropped_capture_points", 0)),
                team_capture_points=int(row.get("team_capture_points", 0)),
            )
            for row in (previous.ships_stats_json or [])
            if row.get("ship_id") is not None
        },
    )


def _apply_event_to_daily_summary(event) -> None:
    """Update or create the PlayerDailyShipStats row covering `event`.

    Phase 3 of the battle-history rollout. Gated by
    BATTLE_HISTORY_ROLLUP_ENABLED — when off this function is a no-op.

    Called inside the same `transaction.atomic()` block as the BattleEvent
    insert so the rollup write cannot drift from the event row. The
    `(player, date, ship_id)` unique key is the dedup boundary; on conflict
    we F-add the deltas atomically so concurrent writers cannot race.
    """
    if os.getenv("BATTLE_HISTORY_ROLLUP_ENABLED", "0") != "1":
        return None

    from warships.models import BattleEvent, PlayerDailyShipStats

    # Phase 3 of the ranked rollout: PlayerDailyShipStats now has
    # mode + season_id columns with partial unique constraints per mode,
    # so ranked events roll up to their own (player, date, ship_id, mode,
    # season_id) rows without colliding with random rows. The partial
    # unique constraints in the model Meta enforce dedup correctly.
    event_mode = getattr(event, "mode", BattleEvent.MODE_RANDOM)
    event_season_id = getattr(event, "season_id", None)
    event_date = event.detected_at.date()
    survived_battles_increment = 1 if event.survived else 0

    # Phase 7 widening — fields that map straight from event delta column to
    # daily aggregate column. (event_attr, daily_attr) pairs.
    PHASE7_AGG_FIELDS = (
        ("main_shots_delta", "main_shots"),
        ("main_hits_delta", "main_hits"),
        ("main_frags_delta", "main_frags"),
        ("secondary_shots_delta", "secondary_shots"),
        ("secondary_hits_delta", "secondary_hits"),
        ("secondary_frags_delta", "secondary_frags"),
        ("torpedo_shots_delta", "torpedo_shots"),
        ("torpedo_hits_delta", "torpedo_hits"),
        ("torpedo_frags_delta", "torpedo_frags"),
        ("damage_scouting_delta", "damage_scouting"),
        ("ships_spotted_delta", "ships_spotted"),
        ("capture_points_delta", "capture_points"),
        ("dropped_capture_points_delta", "dropped_capture_points"),
        ("team_capture_points_delta", "team_capture_points"),
    )

    defaults = {
        "ship_name": event.ship_name,
        "battles": event.battles_delta,
        "wins": event.wins_delta,
        "losses": event.losses_delta,
        "frags": event.frags_delta,
        "damage": event.damage_delta or 0,
        "xp": event.xp_delta or 0,
        "planes_killed": event.planes_killed_delta or 0,
        "survived_battles": survived_battles_increment,
        "first_event_at": event.detected_at,
        "last_event_at": event.detected_at,
    }
    for event_attr, daily_attr in PHASE7_AGG_FIELDS:
        defaults[daily_attr] = getattr(event, event_attr, 0) or 0

    obj, created = PlayerDailyShipStats.objects.get_or_create(
        player_id=event.player_id,
        date=event_date,
        ship_id=event.ship_id,
        mode=event_mode,
        season_id=event_season_id,
        defaults=defaults,
    )
    if created:
        return None

    update_kwargs = {
        "battles": F("battles") + event.battles_delta,
        "wins": F("wins") + event.wins_delta,
        "losses": F("losses") + event.losses_delta,
        "frags": F("frags") + event.frags_delta,
        "damage": F("damage") + (event.damage_delta or 0),
        "xp": F("xp") + (event.xp_delta or 0),
        "planes_killed": F("planes_killed") + (event.planes_killed_delta or 0),
        "survived_battles": F("survived_battles") + survived_battles_increment,
        "last_event_at": event.detected_at,
        # Re-stamp ship_name in case it was empty when the row was created
        # earlier in the day (e.g. Ship row hadn't been resolved yet).
        "ship_name": event.ship_name or obj.ship_name,
    }
    for event_attr, daily_attr in PHASE7_AGG_FIELDS:
        update_kwargs[daily_attr] = F(daily_attr) + (getattr(event, event_attr, 0) or 0)

    PlayerDailyShipStats.objects.filter(pk=obj.pk).update(**update_kwargs)
    return None


def record_observation_from_payloads(
    player,
    *,
    player_data: Optional[Dict[str, Any]] = None,
    ship_data: Iterable[Dict[str, Any]],
    ranked_ship_data: Optional[Iterable[Dict[str, Any]]] = None,
    source: str = None,
    refresh_battles_json: bool = False,
) -> Dict[str, Any]:
    """Persist a `BattleObservation` for `player` and emit `BattleEvent` rows.

    Issues no WG calls — caller supplies the payloads. The valid input
    shapes are:

    * `player_data` is the raw `account/info/` dict, `ship_data` is the
      `ships/stats/` list. PoC poll path uses this.
    * `player_data` is `None` (or omitted), `ship_data` is the `ships/stats/`
      list, and `player.pvp_*` columns are already up to date. Rollout
      piggyback hook in `update_battle_data` uses this — `update_player_data`
      has just refreshed those columns.

    `ranked_ship_data` (Phase 1 of the ranked rollout) is the optional
    `seasons/shipstats/` list. When provided AND the env flag
    `BATTLE_HISTORY_RANKED_CAPTURE_ENABLED=1` is on at the call site, the
    payload is persisted to `BattleObservation.ranked_ships_stats_json` and
    diffed against the prior observation's ranked map to emit
    `mode='ranked'` BattleEvent rows per (ship_id, season_id) that
    advanced. NULL ranked payload means "no ranked capture this tick" —
    matches the pre-Phase-1 behavior exactly.

    Returns a status dict matching `record_observation_and_diff`.
    """
    from warships.models import BattleEvent, BattleObservation, Player, Ship

    if player_data is not None:
        snapshot = coerce_observation_payload(player_data, ship_data)
    else:
        snapshot = _snapshot_from_player_row(player, ship_data)

    if snapshot is None:
        return {"status": "skipped", "reason": "wg-fetch-failed-or-hidden"}

    # Reuse this same `ships/stats` payload to refresh the player's displayed
    # per-ship stats (`battles_json` + `battles_updated_at`) — no second WG call.
    # Floor/poll callers opt in via `refresh_battles_json=True`; gated by a kill
    # switch and wrapped so it can NEVER break the observation write. Skipped on
    # empty `ship_data` so a transient empty fetch can't blank a player's stats.
    #
    # ORDERING CONTRACT: called only AFTER the observation/diff work below —
    # `apply_battles_json` bumps `battles_updated_at`, the anchor of
    # `X-Player-Refresh-Pending` (views._player_refresh_signals). Refreshing
    # first opens a gap where a watching page's poll sees "landed" before the
    # BattleEvents commit + battle-history cache invalidation, refetches once,
    # and caches the pre-session payload (2026-07-17 stale-rehydrate
    # investigation; same contract as the visit path in update_battle_data).
    def _refresh_displayed_stats() -> None:
        if not (refresh_battles_json
                and ship_data
                and os.getenv(
                    "FLOOR_REFRESH_BATTLES_JSON_ENABLED", "1") == "1"):
            return
        _rebuild_t0 = time.perf_counter()
        try:
            from warships.data import apply_battles_json
            apply_battles_json(player, list(ship_data), realm=player.realm)
        except Exception:
            logger.exception(
                "floor battles_json refresh failed for player_id=%s realm=%s",
                player.player_id, player.realm,
            )
        finally:
            # Phase 0a — accumulate into the process-local timer the bulk sweep
            # brackets; counts even when the rebuild raised (cost was still paid).
            _BATTLES_JSON_REBUILD_TIMING["count"] += 1
            _BATTLES_JSON_REBUILD_TIMING["total_ms"] += (
                time.perf_counter() - _rebuild_t0) * 1000.0

    ships_payload = _serialize_ships_payload(snapshot)
    ranked_map = (
        _ranked_ships_from_iterable(ranked_ship_data)
        if ranked_ship_data is not None else {}
    )
    ranked_payload = (
        _serialize_ranked_ships_payload(ranked_map)
        if ranked_ship_data is not None else None
    )
    if source is None:
        source = BattleObservation.SOURCE_POLL

    with transaction.atomic():
        previous = (
            BattleObservation.objects
            .filter(player=player)
            .order_by("-observed_at")
            .first()
        )

        observation = BattleObservation.objects.create(
            player=player,
            pvp_battles=snapshot.pvp_battles,
            pvp_wins=snapshot.pvp_wins,
            pvp_losses=snapshot.pvp_losses,
            pvp_frags=snapshot.pvp_frags,
            pvp_survived_battles=snapshot.pvp_survived_battles,
            last_battle_time=snapshot.last_battle_time,
            ships_stats_json=ships_payload,
            ranked_ships_stats_json=ranked_payload,
            source=source,
        )

        if previous is None:
            _refresh_displayed_stats()
            return {
                "status": "completed",
                "observation_id": observation.id,
                "events_created": 0,
                "random_events_created": 0,
                "ranked_events_created": 0,
                "reason": "baseline",
            }

        previous_snapshot = _hydrate_previous_snapshot(previous)
        # Random-side broken-prior guard: if the previous observation's
        # per-ship snapshot is empty BUT the previous account aggregate
        # (pvp_battles) shows the player had random history, the prior is
        # broken (e.g. ships_stats_json was [] from a flaked fetch). Emitting
        # diffs against a zero per-ship map would attribute the player's
        # entire random career to the current observation. Treat as baseline.
        random_prior_broken = (
            len(previous_snapshot.ships) == 0
            and (previous.pvp_battles or 0) > 0
        )
        if random_prior_broken:
            logger.warning(
                "random prior broken for player_id=%s — previous obs has "
                "pvp_battles=%s but empty ships_stats_json; treating current "
                "observation as random baseline (no events).",
                player.player_id, previous.pvp_battles,
            )
            events = []
        else:
            events = compute_battle_events(previous_snapshot, snapshot)

        # Fidelity instrument — compare the account-level pvp battle advance
        # (account/info, authoritative count) against the sum of per-ship
        # deltas the diff actually captured (ships/stats). They should track
        # closely for a densely-observed player; a large gap means ships/stats
        # lagged account/info at fetch time (the "not in lockstep" case noted
        # in compute_battle_events), so the battle-history timeline under-counts
        # real play. Common on sparsely-observed / returning players. Logged
        # here, at diff time, before the nightly prune compacts ships_stats_json
        # to NULL — so a live case can be diagnosed from the raw observations.
        if not random_prior_broken:
            account_delta = (snapshot.pvp_battles or 0) - (previous.pvp_battles or 0)
            ship_delta_sum = sum(e["battles_delta"] for e in events)
            if account_delta > 0 and abs(account_delta - ship_delta_sum) > max(5, account_delta // 4):
                gap_hours = (
                    observation.observed_at - previous.observed_at
                ).total_seconds() / 3600.0
                logger.warning(
                    "battle-event diff fidelity gap player_id=%s realm=%s "
                    "account_delta=%s ship_delta_sum=%s missed=%s gap_hours=%.1f "
                    "prev_obs=%s ship_events=%s",
                    player.player_id, player.realm, account_delta, ship_delta_sum,
                    account_delta - ship_delta_sum, gap_hours,
                    previous.observed_at.isoformat(), len(events),
                )

        previous_ranked = _hydrate_previous_ranked_snapshot(
            previous, player=player)
        # Ranked-side broken-prior guard: if walk-back found no non-NULL
        # ranked observation in the chain (previous_ranked is empty), AND
        # the current ranked map has data, treat as baseline. The next
        # observation will diff against the now-correct baseline.
        ranked_prior_broken = (
            ranked_ship_data is not None
            and len(previous_ranked) == 0
            and len(ranked_map) > 0
        )
        if ranked_prior_broken:
            logger.info(
                "ranked prior empty for player_id=%s — treating current "
                "observation as ranked baseline (no events).",
                player.player_id,
            )
            ranked_events = []
        else:
            ranked_events = (
                compute_ranked_battle_events(previous_ranked, ranked_map)
                if ranked_ship_data is not None else []
            )

        if not events and not ranked_events:
            _refresh_displayed_stats()
            return {
                "status": "completed",
                "observation_id": observation.id,
                "events_created": 0,
                "random_events_created": 0,
                "ranked_events_created": 0,
            }

        # Primary-source signal: the diff just proved the player played
        # between `previous.observed_at` and now. Bump the shared field that
        # powers "Last played N days ago" so the player-detail header and
        # clan-member rail stop lagging the battle-history chart.
        # See: agents/runbooks/runbook-last-battle-date-from-observation-2026-05-23.md
        today_utc = datetime.now(timezone.utc).date()
        Player.objects.filter(pk=player.pk).update(
            last_battle_date=today_utc,
            days_since_last_battle=0,
        )

        def _invalidate_caches():
            from warships.data import invalidate_player_detail_cache
            from warships.views import invalidate_battle_history_cache
            invalidate_player_detail_cache(
                player.player_id, realm=player.realm)
            # Drop any empty-window battle-history payload a page-load read
            # cached just before this capture committed, so the freshly
            # written events surface on the next fetch instead of after the
            # 5-min TTL.
            invalidate_battle_history_cache(player.realm, player.name)
        transaction.on_commit(_invalidate_caches)

        all_ship_ids = (
            [event["ship_id"] for event in events]
            + [event["ship_id"] for event in ranked_events]
        )
        ship_names = dict(
            Ship.objects.filter(ship_id__in=all_ship_ids)
            .values_list("ship_id", "name")
        )

        created = 0
        latest_detected_at = None
        for event in events:
            event_row = BattleEvent.objects.create(
                player=player,
                mode=BattleEvent.MODE_RANDOM,
                ship_id=event["ship_id"],
                ship_name=ship_names.get(event["ship_id"], ""),
                battles_delta=event["battles_delta"],
                wins_delta=event["wins_delta"],
                losses_delta=event["losses_delta"],
                frags_delta=event["frags_delta"],
                damage_delta=event["damage_delta"],
                xp_delta=event["xp_delta"],
                planes_killed_delta=event["planes_killed_delta"],
                survived=event["survived"],
                main_shots_delta=event["main_shots_delta"],
                main_hits_delta=event["main_hits_delta"],
                main_frags_delta=event["main_frags_delta"],
                secondary_shots_delta=event["secondary_shots_delta"],
                secondary_hits_delta=event["secondary_hits_delta"],
                secondary_frags_delta=event["secondary_frags_delta"],
                torpedo_shots_delta=event["torpedo_shots_delta"],
                torpedo_hits_delta=event["torpedo_hits_delta"],
                torpedo_frags_delta=event["torpedo_frags_delta"],
                damage_scouting_delta=event["damage_scouting_delta"],
                ships_spotted_delta=event["ships_spotted_delta"],
                capture_points_delta=event["capture_points_delta"],
                dropped_capture_points_delta=event["dropped_capture_points_delta"],
                team_capture_points_delta=event["team_capture_points_delta"],
                from_observation=previous,
                to_observation=observation,
            )
            _apply_event_to_daily_summary(event_row)
            if latest_detected_at is None or event_row.detected_at > latest_detected_at:
                latest_detected_at = event_row.detected_at
            created += 1

        # Ranked events. Phase-7-widening fields aren't carried by the WG
        # `seasons/shipstats/` endpoint so they default to 0 on the row;
        # `planes_killed_delta` is also defaulted because ranked rarely
        # involves carriers. The model's mode-aware partial unique
        # constraints keep these from colliding with random rows.
        ranked_created = 0
        for event in ranked_events:
            event_row = BattleEvent.objects.create(
                player=player,
                mode=BattleEvent.MODE_RANKED,
                season_id=event["season_id"],
                ship_id=event["ship_id"],
                ship_name=ship_names.get(event["ship_id"], ""),
                battles_delta=event["battles_delta"],
                wins_delta=event["wins_delta"],
                losses_delta=event["losses_delta"],
                frags_delta=event["frags_delta"],
                damage_delta=event["damage_delta"],
                xp_delta=event["xp_delta"],
                planes_killed_delta=None,
                survived=event["survived"],
                from_observation=previous,
                to_observation=observation,
            )
            _apply_event_to_daily_summary(event_row)
            ranked_created += 1

        if created > 0 and latest_detected_at is not None:
            # Drives the landing "Active" sub-sort. The conditional UPDATE
            # only advances the column forward — if a concurrent writer
            # already set a later value, the WHERE clause excludes us and
            # the UPDATE is a no-op. Single atomic statement, portable
            # across SQLite (tests) and Postgres (prod).
            #
            # Note: ranked events do NOT advance last_random_battle_at —
            # the column name is literal. The Active landing pill stays
            # randoms-only by design (per
            # runbook-ranked-battle-history-rollout-2026-05-02.md
            # Operational watchpoint #4).
            Player.objects.filter(pk=player.pk).filter(
                Q(last_random_battle_at__isnull=True)
                | Q(last_random_battle_at__lt=latest_detected_at)
            ).update(last_random_battle_at=latest_detected_at)

    total_created = created + ranked_created
    if total_created > 0:
        _invalidate_battle_history_cache(player)

    # After the event commit + cache invalidations, so the pending anchor
    # (battles_updated_at) can only advance once the fresh battle-history
    # payload is servable.
    _refresh_displayed_stats()

    return {
        "status": "completed",
        "observation_id": observation.id,
        "events_created": total_created,
        "random_events_created": created,
        "ranked_events_created": ranked_created,
    }


# Chunk size for the bulk WG fetch — WG supports up to 100 comma-separated
# account_ids per call; mirrors enrich_player_data.BULK_API_BATCH_SIZE.
_BULK_OBSERVATION_CHUNK = 100


def _fetch_with_407_retry(fetch_fn, label: str, realm: str):
    """Call ``fetch_fn() -> (data, err)``, retrying on 407 with backoff.

    The shared WG token-bucket already paces egress, so a `REQUEST_LIMIT_EXCEEDED`
    here is the *rare* transient case (bucket fail-open, or a WG-side burst). The
    legacy behaviour aborted the whole sweep on the first 407, throwing away the
    rest of the cycle — cheap when the floor drew ~2 of 9 req/s, but wasteful once
    we densify the per-mover fetch (`FETCH_CONCURRENCY > 1`). This retries up to
    ``BATTLE_OBSERVATION_FLOOR_407_RETRIES`` times with a capped exponential
    backoff, then returns the final ``(data, err)`` so the caller's abort path
    still triggers if the limit is genuinely sustained. Any non-407 result
    (success, INVALID_ACCOUNT_ID, transient) returns immediately, unchanged.
    """
    try:
        max_retries = max(
            0, int(os.getenv("BATTLE_OBSERVATION_FLOOR_407_RETRIES", "3") or 3))
    except ValueError:
        max_retries = 3
    try:
        base = float(
            os.getenv("BATTLE_OBSERVATION_FLOOR_407_BACKOFF_SECONDS", "2.0") or 2.0)
    except ValueError:
        base = 2.0
    try:
        cap = float(
            os.getenv("BATTLE_OBSERVATION_FLOOR_407_BACKOFF_MAX_SECONDS", "15.0") or 15.0)
    except ValueError:
        cap = 15.0

    attempt = 0
    while True:
        data, err = fetch_fn()
        if err != "REQUEST_LIMIT_EXCEEDED" or attempt >= max_retries:
            return data, err
        delay = min(cap, base * (2 ** attempt))
        logger.warning(
            "bulk observation floor 407 on %s [%s] — backoff %.1fs (retry %d/%d)",
            label, realm.upper(), delay, attempt + 1, max_retries)
        time.sleep(delay)
        attempt += 1


def _gate_needs_ships(acct: Optional[Dict[str, Any]],
                      prior_battles: Optional[int]):
    """Change-detector decision: does this player need a `ships/stats` fetch?

    The cheap bulk `account/info` slice (`acct`) carries the player's current
    random battle count. Compared to `prior_battles` (their last observation's
    `pvp_battles`), it tells us whether they played randoms since last capture —
    so we can skip the expensive per-player `ships/stats` for the ~half who
    didn't. Returns:

    * ``True``  — fetch ships (player played, OR has no prior so needs a baseline)
    * ``False`` — skip ships (battle count unchanged → nothing new to capture)
    * ``None``  — can't/needn't fetch (account absent, hidden, or no pvp stats)
    """
    if not acct or acct.get("hidden_profile"):
        return None
    cur = ((acct.get("statistics") or {}).get("pvp") or {}).get("battles")
    if cur is None:
        return None
    if prior_battles is None:
        return True  # no prior observation → must establish a baseline
    return cur > prior_battles


def record_observations_bulk(
    player_ids: Iterable[int],
    realm: str,
    *,
    chunk_delay: float = 0.0,
    source: Optional[str] = None,
    change_gate: bool = False,
    progress_callback=None,
) -> Dict[str, Any]:
    """Bulk-capture random battle observations for many players (R1).

    Random-only: feeds the bulk `account/info` + (per-player) `ships/stats`
    slices into the zero-WG persistence core `record_observation_from_payloads`,
    so the per-player persistence + diff is byte-identical to the legacy
    per-player path (`record_observation_and_diff`) — parity-by-construction.
    Spec: runbook-bulk-battle-observation-capture-2026-06-06.md (D2-D8).

    Per chunk of 100: bulk `account/info` (the only WG endpoint that truly
    bulks), the D5 error taxonomy, then per player a D4 slice →
    `record_observation_from_payloads`. NB `ships/stats` cannot bulk (WG rejects
    ≥2 ids), so it always falls back to per-player; that is the dominant cost.

    `change_gate=True` enables the change-detector: it issues the expensive
    per-player `ships/stats` ONLY for players whose `account/info` random battle
    count moved since their last observation (or who have no prior → baseline),
    skipping the ~half who didn't play. `chunk_delay` paces *per chunk* (NOT the
    legacy per-player `--delay`).

    Returns a tally dict:
    `{status, completed, baseline, events, wg_failed, not_found,
      skipped_missing, gated_skipped, other, aborted}`.
    """
    # Function-local imports: match this module's convention and keep the
    # api<-core boundary clean (the bulk fetchers live in the shared API
    # layer per D10). Resolving at call-time also lets tests patch the
    # fetchers at their source module.
    from warships.api.players import (
        _bulk_fetch_account_info,
        _per_player_account_fallback,
    )
    from warships.api.ships import (
        _bulk_fetch_ship_stats,
        _per_player_ship_fallback,
    )
    from warships.models import BattleObservation, Player

    if source is None:
        source = BattleObservation.SOURCE_BULK_FLOOR

    # Gate-skip cooldown (default-off). When > 0, stamp change-gated non-movers
    # so _candidates() suppresses them for the window — draining the candidate
    # pool to genuine work so the self-chain can terminate. See
    # Player.floor_gate_skipped_at + runbook-floor-throughput-tuning-2026-06-13.md.
    try:
        _gate_skip_cooldown_on = int(
            os.getenv("BATTLE_OBSERVATION_FLOOR_GATE_SKIP_COOLDOWN_HOURS", "0")
            or 0) > 0
    except ValueError:
        _gate_skip_cooldown_on = False

    # Concurrency for the per-player ships/stats fallback (the dominant floor
    # cost — serial WG latency, ~1.3s/mover on ASIA vs ~0.5s on EU). >1 overlaps
    # that latency via a bounded thread pool in _per_player_ship_fallback; the
    # shared blocking WG token-bucket limiter still caps the global budget, so
    # this only fills the floor's existing WG headroom (it pulls ~0.75 req/s of
    # the ~9 req/s bucket). Default 1 (serial) → no-op until the knob is set.
    try:
        _ship_fetch_concurrency = max(
            1, int(os.getenv("BATTLE_OBSERVATION_FLOOR_FETCH_CONCURRENCY", "1") or 1))
    except ValueError:
        _ship_fetch_concurrency = 1

    ids = [int(pid) for pid in player_ids]
    tally = {
        "status": "completed",
        "completed": 0,
        "baseline": 0,
        "events": 0,
        "wg_failed": 0,
        "not_found": 0,
        "skipped_missing": 0,
        "gated_skipped": 0,
        "other": 0,
        "aborted": False,
    }

    # Phase 0a — bracket the per-mover battles_json rebuild timer around this
    # sweep so we can attribute floor wall-time to the rebuild vs. the rest.
    _cycle_start = time.perf_counter()
    _BATTLES_JSON_REBUILD_TIMING["count"] = 0
    _BATTLES_JSON_REBUILD_TIMING["total_ms"] = 0.0

    for chunk_start in range(0, len(ids), _BULK_OBSERVATION_CHUNK):
        chunk_ids = ids[chunk_start:chunk_start + _BULK_OBSERVATION_CHUNK]

        # D7: resolve Player rows once per chunk, scoped to realm — player_id is
        # not globally unique, so the legacy single path uses get(.., realm=..).
        players_qs = Player.objects.filter(player_id__in=chunk_ids, realm=realm)
        if change_gate:
            # Annotate each player's latest observed pvp_battles so the gate can
            # decide who played randoms since their last capture, in one query.
            from django.db.models import OuterRef, Subquery

            from warships.models import BattleObservation as _BO
            _latest = (
                _BO.objects.filter(player=OuterRef("pk"))
                .order_by("-observed_at").values("pvp_battles")[:1]
            )
            players_qs = players_qs.annotate(_last_obs_battles=Subquery(_latest))
        players = {p.player_id: p for p in players_qs}

        # ── Bulk account/info FIRST — the only WG endpoint that truly bulks
        # (ships/stats can't). Its error taxonomy mirrors the ships taxonomy
        # below: 407 aborts the sweep (shared ~10 req/s budget; must not keep
        # hammering); INVALID_ACCOUNT_ID isolates the bad id via per-player
        # fallback; any other transient error skips the chunk.
        acct_data, acct_err = _fetch_with_407_retry(
            lambda: _bulk_fetch_account_info(chunk_ids, realm),
            "account/info", realm)
        if acct_err == "REQUEST_LIMIT_EXCEEDED":
            logger.warning(
                "bulk observation floor hit 407 on account/info [%s] after "
                "retries — aborting sweep, partial results persisted",
                realm.upper())
            tally["aborted"] = True
            tally["status"] = "aborted"
            break
        if acct_err == "INVALID_ACCOUNT_ID":
            acct_data = _per_player_account_fallback(chunk_ids, realm)
        elif acct_err:
            logger.warning(
                "bulk observation floor skipping chunk [%s] on transient "
                "account/info error (%s)", realm.upper(), acct_err)
            tally["wg_failed"] += len(chunk_ids)
            if chunk_delay:
                time.sleep(chunk_delay)
            continue

        # ── Change-detector gate (option B): fetch the expensive per-player
        # ships/stats ONLY for players whose random battle count moved since
        # their last observation (or who have no prior → baseline). The ~half
        # who didn't play are skipped here, never paying a ships call.
        if change_gate:
            ships_ids = []
            gated_skip_pids = []
            for pid in chunk_ids:
                player = players.get(pid)
                if player is None:
                    tally["not_found"] += 1
                    continue
                decision = _gate_needs_ships(
                    acct_data.get(str(pid)),
                    getattr(player, "_last_obs_battles", None),
                )
                if decision is True:
                    ships_ids.append(pid)
                elif decision is False:
                    tally["gated_skipped"] += 1
                    gated_skip_pids.append(pid)
                else:  # None — account absent / hidden / no pvp stats
                    tally["skipped_missing"] += 1
            # Cooldown stamp (default-off): these non-movers wrote no
            # observation, so they stay observation-stale and would re-fill
            # _candidates() every cycle (the non-mover wall that makes
            # self-chain spin). Stamping suppresses them for the cooldown
            # window. A captured mover is excluded by the staleness filter
            # regardless, so this never delays a player who actually played
            # beyond the window. One bulk UPDATE per chunk (≤100 rows).
            if gated_skip_pids and _gate_skip_cooldown_on:
                from django.utils import timezone as _dj_tz
                Player.objects.filter(
                    player_id__in=gated_skip_pids, realm=realm,
                ).update(floor_gate_skipped_at=_dj_tz.now())
        else:
            ships_ids = chunk_ids

        if not ships_ids:
            if progress_callback is not None:
                progress_callback(dict(tally))
            if chunk_delay:
                time.sleep(chunk_delay)
            continue

        # ── Bulk ships/stats for the (gated) subset. WG rejects ≥2 ids with
        # INVALID_ACCOUNT_ID, so this falls back to per-player; the gate keeps
        # that fallback small. 407 aborts; other transient skips the subset.
        ship_data, ship_err = _fetch_with_407_retry(
            lambda: _bulk_fetch_ship_stats(ships_ids, realm),
            "ships/stats", realm)
        if ship_err == "REQUEST_LIMIT_EXCEEDED":
            logger.warning(
                "bulk observation floor hit 407 on ships/stats [%s] after "
                "retries — aborting sweep, partial results persisted",
                realm.upper())
            tally["aborted"] = True
            tally["status"] = "aborted"
            break
        if ship_err == "INVALID_ACCOUNT_ID":
            ship_data = _per_player_ship_fallback(
                ships_ids, realm, max_workers=_ship_fetch_concurrency)
        elif ship_err:
            logger.warning(
                "bulk observation floor skipping ships for chunk [%s] on "
                "transient error (%s)", realm.upper(), ship_err)
            tally["wg_failed"] += len(ships_ids)
            if chunk_delay:
                time.sleep(chunk_delay)
            continue

        # ── Per-player slice + persist (D4) over the players we fetched ships for.
        for pid in ships_ids:
            player = players.get(pid)
            if player is None:
                tally["not_found"] += 1
                continue

            # D4 — ships slice handling. `None` (absent/null) or the "SKIP"
            # sentinel from _per_player_ship_fallback (transient per-player
            # failure) → SKIP this tick. Writing an empty-ships observation
            # would create a broken prior that trips the random_prior_broken
            # guard next tick and silently suppress a real diff. This
            # None/"SKIP" → skip is STRICTLY SAFER than the legacy
            # {}→[]→write; it is intentional, not a parity bug.
            ships = ship_data.get(str(pid))
            if ships is None or ships == "SKIP":
                tally["skipped_missing"] += 1
                continue
            if isinstance(ships, dict):
                # Legacy parity: record_observation_and_diff coerces a dict
                # ships payload (WG's empty/odd shape) to [] before persisting.
                ships = []

            # D3 — always pass the FRESH account/info slice (never the stale
            # player.pvp_* column path); the floor exists because those
            # columns lag. `None` (absent) → skip, same as legacy's `if not
            # player_data`. A hidden profile is a non-None dict here, but
            # coerce_observation_payload returns None for it, so
            # record_observation_from_payloads skips it for free.
            acct = acct_data.get(str(pid))
            if acct is None:
                tally["skipped_missing"] += 1
                continue

            try:
                # D7: record_observation_from_payloads keeps its own per-player
                # transaction.atomic — one bad player must NOT roll back the
                # chunk, so we do not wrap the loop in a transaction.
                result = record_observation_from_payloads(
                    player, player_data=acct, ship_data=ships, source=source,
                    refresh_battles_json=True,
                )
            except Exception:
                logger.exception(
                    "bulk observation floor: persist failed for player_id=%s "
                    "realm=%s", pid, realm,
                )
                tally["other"] += 1
                continue

            status = result.get("status")
            reason = result.get("reason")
            if status == "completed":
                tally["completed"] += 1
                if reason == "baseline":
                    tally["baseline"] += 1
                tally["events"] += (
                    int(result.get("random_events_created") or 0)
                    + int(result.get("ranked_events_created") or 0)
                )
            elif reason == "wg-fetch-failed-or-hidden":
                # Hidden profile / coerce returned None. Mirrors the legacy
                # command's bucketing of this reason.
                tally["wg_failed"] += 1
            else:
                tally["other"] += 1

        if progress_callback is not None:
            progress_callback(dict(tally))
        if chunk_delay:
            time.sleep(chunk_delay)

    # Phase 0a — single per-cycle line into journalctl (logger.info, not stdout,
    # which Celery only forwards unreliably at WARNING): how much of the sweep's
    # wall-time the per-mover battles_json rebuild consumed.
    logger.info(
        "bulk floor done realm=%s movers=%d battles_json_rebuilds=%d "
        "battles_json_total_ms=%.0f cycle_ms=%.0f aborted=%s",
        realm, tally["completed"], _BATTLES_JSON_REBUILD_TIMING["count"],
        _BATTLES_JSON_REBUILD_TIMING["total_ms"],
        (time.perf_counter() - _cycle_start) * 1000.0, tally["aborted"],
    )
    return tally


def _invalidate_battle_history_cache(player) -> None:
    """Drop all battle-history Redis cache entries for this player so the
    next API read returns the fresh rollup. Called when new events have
    just been written.

    Iterates the supported (period, windows) combinations — bounded and
    cheaper than a delete_pattern scan over Redis keyspace.
    """
    from django.core.cache import cache

    from warships.models import realm_cache_key

    name = (player.name or "").strip().lower()
    if not name:
        return
    realm = player.realm or "na"
    # Phase 6 cache keys: {realm}:battle-history:{name}:{period}:{windows}.
    period_caps = {"daily": 30, "weekly": 52, "monthly": 36, "yearly": 20}
    keys = []
    for period, cap in period_caps.items():
        for windows in range(1, cap + 1):
            keys.append(realm_cache_key(realm, f"battle-history:{name}:{period}:{windows}"))
    cache.delete_many(keys)


def _utc_day_bounds(target_date) -> Tuple[datetime, datetime]:
    """Half-open naive-UTC datetime bounds ``[day_start, next_day_start)``.

    The project runs ``USE_TZ=False`` / UTC, so ``BattleEvent.detected_at`` is
    a naive UTC datetime. Filtering with a half-open range predicate lets the
    BRIN index on ``detected_at`` range-prune; a ``detected_at__date=`` lookup
    wraps the column in a function and cannot use the index. See
    ``agents/runbooks/runbook-battle-history-rollup-durability-2026-06-06.md``.
    """
    day_start = datetime(target_date.year, target_date.month, target_date.day)
    return day_start, day_start + timedelta(days=1)


def rebuild_daily_ship_stats_for_date(target_date) -> Dict[str, Any]:
    """Rebuild `PlayerDailyShipStats` rows for `target_date` from BattleEvent.

    Idempotent: deletes rows for the date, then recomputes from scratch.
    Used by the nightly sweeper task and by the
    `rebuild_player_daily_ship_stats` management command.

    Always runs regardless of BATTLE_HISTORY_ROLLUP_ENABLED, since the caller
    has explicitly asked for a rebuild.
    """
    from warships.models import BattleEvent, PlayerDailyShipStats

    day_start, next_day_start = _utc_day_bounds(target_date)

    with transaction.atomic():
        deleted, _ = PlayerDailyShipStats.objects.filter(
            date=target_date,
        ).delete()

        # NOTE: this loads one calendar day of BattleEvent rows into Python
        # (~40K today — safe). A single day stays small, but a multi-day
        # backfill via rebuild_player_daily_ship_stats would load proportionally
        # more. TODO(2026-Q3): if BattleEvent grows past ~200K/day or backfills
        # span many days, rewrite this as a DB-side values().annotate() group-by
        # (Count(filter=survived) for survived, grouped by
        # player/ship/mode/season_id).
        events = BattleEvent.objects.filter(
            detected_at__gte=day_start,
            detected_at__lt=next_day_start,
        ).order_by("detected_at")

        rows: Dict[tuple, Dict[str, Any]] = {}
        for event in events:
            # Phase 3 ranked rollup: key by (player, ship, mode, season_id)
            # so a single (player, date, ship_id) can carry separate
            # rollup rows for random-mode AND ranked-mode in different
            # active seasons. season_id is NULL for randoms — Python's
            # tuple equality treats None as a distinct key correctly.
            event_mode = getattr(event, "mode", "random")
            event_season_id = getattr(event, "season_id", None)
            key = (event.player_id, event.ship_id, event_mode, event_season_id)
            row = rows.get(key)
            if row is None:
                row = {
                    "player_id": event.player_id,
                    "date": target_date,
                    "ship_id": event.ship_id,
                    "ship_name": event.ship_name or "",
                    "mode": event_mode,
                    "season_id": event_season_id,
                    "battles": 0, "wins": 0, "losses": 0, "frags": 0,
                    "damage": 0, "xp": 0, "planes_killed": 0,
                    "survived_battles": 0,
                    "first_event_at": event.detected_at,
                    "last_event_at": event.detected_at,
                }
                rows[key] = row
            row["battles"] += event.battles_delta or 0
            row["wins"] += event.wins_delta or 0
            row["losses"] += event.losses_delta or 0
            row["frags"] += event.frags_delta or 0
            row["damage"] += event.damage_delta or 0
            row["xp"] += event.xp_delta or 0
            row["planes_killed"] += event.planes_killed_delta or 0
            if event.survived:
                row["survived_battles"] += 1
            row["last_event_at"] = event.detected_at
            if event.ship_name and not row["ship_name"]:
                row["ship_name"] = event.ship_name

        if rows:
            PlayerDailyShipStats.objects.bulk_create([
                PlayerDailyShipStats(**row) for row in rows.values()
            ])

    return {
        "status": "completed",
        "date": str(target_date),
        "rows_deleted": deleted,
        "rows_written": len(rows),
        "events_seen": events.count() if rows else BattleEvent.objects.filter(
            detected_at__gte=day_start,
            detected_at__lt=next_day_start,
        ).count(),
    }


# ---------------------------------------------------------------------------
# BattleObservation payload compaction (disk retention)
# ---------------------------------------------------------------------------

COMPACT_KEEP_PER_PLAYER_DEFAULT = 3
COMPACT_BATCH_SIZE_DEFAULT = 2000
COMPACT_STATEMENT_TIMEOUT_DEFAULT = 180


def _compact_candidate_sql() -> str:
    """SQL selecting observations whose JSON payloads are safe to clear.

    A single table scan with two window functions — deliberately NOT the
    ``id NOT IN (SELECT … window …)`` anti-join shape (that degenerated into
    a multi-scan query that ran 40+ min on the production table on
    2026-05-24). One ``ROW_NUMBER()`` ranks each player's observations newest
    first (the ``rn > keep`` rows fall outside the random diff baseline); a
    second, partitioned by ``(player_id, ranked-payload-present)``, finds the
    latest non-NULL-ranked observation (``rrn = 1`` within the has-ranked
    group) so the ranked walk-back baseline is preserved.

    Critically this touches **only heap columns**: ``IS NOT NULL`` reads the
    tuple null-bitmap, never the value, so the 21 GB of TOASTed JSON is never
    read. (An earlier version computed ``pg_column_size(ships_stats_json)``
    inline for a reclaim estimate — that detoasted every row and blew the
    statement timeout. Reclaim is now estimated from catalog stats instead.)
    Window functions work on both Postgres and the sqlite used in tests.
    """
    return """
        SELECT id, player_id
        FROM (
            SELECT
                id,
                player_id,
                (ships_stats_json IS NOT NULL) AS has_ships,
                (ranked_ships_stats_json IS NOT NULL) AS has_ranked,
                observed_at,
                ROW_NUMBER() OVER (
                    PARTITION BY player_id ORDER BY observed_at DESC, id DESC
                ) AS rn,
                ROW_NUMBER() OVER (
                    PARTITION BY player_id, (ranked_ships_stats_json IS NOT NULL)
                    ORDER BY observed_at DESC, id DESC
                ) AS rrn
            FROM warships_battleobservation
        ) w
        WHERE (w.has_ships OR w.has_ranked)
          AND w.observed_at < %(cutoff)s
          AND w.rn > %(keep)s
          AND NOT (w.has_ranked AND w.rrn = 1)
    """


def _estimate_avg_observation_payload_bytes() -> Optional[float]:
    """Mean TOASTed-JSON bytes per observation, from catalog stats (instant).

    Used to estimate compaction reclaim without reading the 21 GB of TOAST.
    Postgres-only; returns None elsewhere or when stats are unavailable.
    """
    if connection.vendor != "postgresql":
        return None
    with connection.cursor() as cur:
        cur.execute(
            "SELECT pg_total_relation_size(reltoastrelid), reltuples "
            "FROM pg_class WHERE relname = 'warships_battleobservation' "
            "AND reltoastrelid <> 0"
        )
        row = cur.fetchone()
    if not row or not row[1] or row[1] <= 0:
        return None
    return float(row[0]) / float(row[1])


def _apply_statement_timeout(cur, seconds, is_pg) -> None:
    """Bound a query so it can never run unbounded on prod again (PG only).

    Must be called inside a transaction (SET LOCAL scopes to the txn).
    """
    if is_pg and seconds and seconds > 0:
        cur.execute("SET LOCAL statement_timeout = %s", [int(seconds * 1000)])


def compact_battle_observation_payloads(
    *,
    keep_per_player: int = COMPACT_KEEP_PER_PLAYER_DEFAULT,
    min_age_hours: int = 0,
    batch_size: int = COMPACT_BATCH_SIZE_DEFAULT,
    max_rows: int = 0,
    dry_run: bool = False,
    sleep_between_batches: float = 0.0,
    statement_timeout_s: int = COMPACT_STATEMENT_TIMEOUT_DEFAULT,
) -> Dict[str, Any]:
    """Reclaim disk by NULLing stale BattleObservation JSON payloads.

    The battle-history rollout (2026-05-01/02) made observation capture
    append-only: every visit / crawl / floor refresh writes a row carrying a
    full per-ship ``ships_stats_json`` (and, with ranked capture on, a
    ``ranked_ships_stats_json``) blob. With no retention this table is the
    primary disk consumer behind the cluster's read-only / disk alerts — see
    ``agents/runbooks/runbook-db-cpu-saturation-2026-05-24.md``.

    We deliberately do **not** delete observation rows:
    ``BattleEvent.from_observation`` and ``to_observation`` are CASCADE FKs,
    so deleting a row would destroy the durable per-battle event record that
    powers the charts. Instead we NULL the heavy JSON columns on observations
    no longer needed as a diff baseline. Per player we keep full JSON on:

      * the latest ``keep_per_player`` observations — the random diff baseline
        (``record_observation_from_payloads`` diffs the next capture against
        ``previous.ships_stats_json``), and
      * the latest observation with a non-NULL ``ranked_ships_stats_json`` —
        the ranked walk-back baseline (``_hydrate_previous_ranked_snapshot``).

    Everything older has BOTH JSON columns set to NULL. Rows survive, so the
    BattleEvent FKs stay intact and the rollup (which reads BattleEvent, not
    observations) is unaffected.

    ``dry_run=True`` reports the candidate count + affected players in a
    single heap-only scan (reclaim is estimated from catalog stats, not by
    reading the JSON) — run it first. Live runs collect candidate ids in one
    scan (capped by ``max_rows``), then clear by primary key in ``batch_size``
    chunks — so the table is scanned once, not once per batch.
    ``statement_timeout_s`` bounds every query (Postgres) so a pathological
    plan fails fast instead of hanging.
    """
    keep = max(1, int(keep_per_player))
    cutoff = datetime.now(timezone.utc) - timedelta(
        hours=max(0, int(min_age_hours)))
    params = {"cutoff": cutoff, "keep": keep}
    is_pg = connection.vendor == "postgresql"

    if dry_run:
        candidate_sql = _compact_candidate_sql()
        with transaction.atomic():
            with connection.cursor() as cur:
                _apply_statement_timeout(cur, statement_timeout_s, is_pg)
                cur.execute(
                    f"SELECT COUNT(*), COUNT(DISTINCT player_id) "
                    f"FROM ({candidate_sql}) c",
                    params,
                )
                row = cur.fetchone()
        count, players = int(row[0] or 0), int(row[1] or 0)
        avg_bytes = _estimate_avg_observation_payload_bytes()
        reclaimable_bytes = (
            int(avg_bytes * count) if avg_bytes is not None else None)
        return {
            "status": "completed",
            "dry_run": True,
            "keep_per_player": keep,
            "min_age_hours": int(min_age_hours),
            "candidates": count,
            "players_affected": players,
            "reclaimable_bytes": reclaimable_bytes,
            "cleared": 0,
            "batches": 0,
        }

    # One scan to collect candidate ids (capped by max_rows), then clear by
    # primary key in chunks. Avoids re-scanning the whole table per batch.
    candidate_sql = _compact_candidate_sql()
    if max_rows and max_rows > 0:
        candidate_sql += " LIMIT %(maxrows)s"
        params["maxrows"] = int(max_rows)
    with transaction.atomic():
        with connection.cursor() as cur:
            _apply_statement_timeout(cur, statement_timeout_s, is_pg)
            cur.execute(f"SELECT id FROM ({candidate_sql}) c", params)
            ids = [r[0] for r in cur.fetchall()]

    cleared = 0
    batches = 0
    for start in range(0, len(ids), batch_size):
        chunk = ids[start:start + batch_size]
        placeholders = ",".join(["%s"] * len(chunk))
        with transaction.atomic():
            with connection.cursor() as cur:
                _apply_statement_timeout(cur, statement_timeout_s, is_pg)
                cur.execute(
                    f"UPDATE warships_battleobservation "
                    f"SET ships_stats_json = NULL, "
                    f"ranked_ships_stats_json = NULL "
                    f"WHERE id IN ({placeholders})",
                    chunk,
                )
                affected = cur.rowcount
        cleared += max(affected, 0)
        batches += 1
        if sleep_between_batches:
            time.sleep(sleep_between_batches)

    return {
        "status": "completed",
        "dry_run": False,
        "keep_per_player": keep,
        "min_age_hours": int(min_age_hours),
        "cleared": cleared,
        "batches": batches,
    }


# ---------------------------------------------------------------------------
# Inactive-player battles_json prune (DB-growth Tier-1)
#
# The battle-history pipeline (2026-05) repopulates Player.battles_json on every
# visit / floor refresh, so the displayed Random-Battles blob erodes back onto
# long-inactive players who get a one-off page view. For a >cutoff-day-inactive
# account that blob is dead weight: battle history is empty anyway, the wire
# serializer already omits the field (PlayerSerializer.Meta.exclude), and the
# /randoms endpoint falls back to randoms_json when battles_json is NULL. This
# prune NULLs *only* battles_json on those rows, reclaiming TOAST while leaving
# the derived chart columns (tiers/type/randoms/activity_json) intact.
#
# Disjoint from the floor by construction: FLOOR_REFRESH_BATTLES_JSON_ENABLED
# only repopulates the active-7d set, and the prune cutoff is far older
# (default 180d), so the two sets never overlap — the prune does not fight the
# floor. Pruned rows refetch battles_json on the player's next profile view.
#
# Enrichment safety: battles_json IS NULL is one of the enrichment candidate
# match conditions (enrich_player_data._candidates), so NULLing it on a row the
# enrichment pool would otherwise pick up could feed the private-at-fetch spin
# loop. Two belt-and-suspenders guards make that impossible:
#   1. PENDING rows are excluded from the prune predicate outright.
#   2. The core refuses to run unless inactive_days > ENRICH_MAX_INACTIVE_DAYS
#      (the enrichment activity ceiling), so a pruned row can never satisfy the
#      candidate query's days_since_last_battle <= max_inactive_days condition.
# ---------------------------------------------------------------------------

PRUNE_BATTLES_JSON_BATCH_SIZE_DEFAULT = 5000
PRUNE_BATTLES_JSON_INACTIVE_DAYS_DEFAULT = 180
PRUNE_BATTLES_JSON_STATEMENT_TIMEOUT_DEFAULT = 180


def _prune_battles_json_candidate_sql() -> str:
    """Rows whose battles_json is safe to NULL — a single plain WHERE filter.

    Touches only heap columns: ``battles_json IS NOT NULL`` reads the tuple
    null-bitmap, never the TOASTed value, so the blob is never detoasted (the
    timeout trap the observation-compaction docstring warns about). No window
    functions are needed — this is a flat per-row predicate, unlike the
    observation keep-set. Excludes hidden accounts, PENDING enrichment rows
    (guard 1), and NULL ``last_battle_date`` (``NULL < cutoff`` is unknown →
    excluded, the safe default). The ``< %(cutoff)s`` is strict, so a row whose
    ``last_battle_date`` equals the cutoff date is kept.
    """
    return """
        SELECT id
        FROM warships_player
        WHERE is_hidden = FALSE
          AND battles_json IS NOT NULL
          AND last_battle_date IS NOT NULL
          AND last_battle_date < %(cutoff)s
          AND enrichment_status <> %(pending)s
    """


def _prune_battles_json_pending_intersection_sql() -> str:
    """The same inactive/visible band BUT including PENDING rows, filtered to
    PENDING — the gating dry-run count. Expect 0 after guard 1; a non-zero
    pre-guard value proves the enrichment-overlap risk was real for this band.
    """
    return """
        SELECT COUNT(*)
        FROM warships_player
        WHERE is_hidden = FALSE
          AND battles_json IS NOT NULL
          AND last_battle_date IS NOT NULL
          AND last_battle_date < %(cutoff)s
          AND enrichment_status = %(pending)s
    """


def _estimate_avg_player_battles_json_bytes() -> Optional[float]:
    """Mean TOASTed-JSON bytes per player row, from catalog stats (instant).

    Approximate: ``warships_player`` TOASTs five JSON columns
    (tiers/type/randoms/activity/achievements_json) besides ``battles_json``,
    so total-TOAST / row-count is an upper-bound proxy for per-row
    ``battles_json`` size, not an exact measure. We deliberately do NOT use
    ``pg_column_size(battles_json)`` for precision — that detoasts every row and
    is exactly the 40-min timeout pathology from 2026-05-24. Postgres-only.
    """
    if connection.vendor != "postgresql":
        return None
    with connection.cursor() as cur:
        cur.execute(
            "SELECT pg_total_relation_size(reltoastrelid), reltuples "
            "FROM pg_class WHERE relname = 'warships_player' "
            "AND reltoastrelid <> 0"
        )
        row = cur.fetchone()
    if not row or not row[1] or row[1] <= 0:
        return None
    return float(row[0]) / float(row[1])


def prune_inactive_player_battles_json(
    *,
    inactive_days: int = PRUNE_BATTLES_JSON_INACTIVE_DAYS_DEFAULT,
    max_inactive_days: int,
    batch_size: int = PRUNE_BATTLES_JSON_BATCH_SIZE_DEFAULT,
    max_rows: int = 0,
    dry_run: bool = False,
    sleep_between_batches: float = 0.0,
    statement_timeout_s: int = PRUNE_BATTLES_JSON_STATEMENT_TIMEOUT_DEFAULT,
) -> Dict[str, Any]:
    """Reclaim disk by NULLing ``battles_json`` on long-inactive players.

    NULLs **only** ``battles_json`` (keeps tiers/type/randoms/activity_json)
    where ``is_hidden = false AND battles_json IS NOT NULL AND
    last_battle_date < today - inactive_days AND enrichment_status <> PENDING``.

    ``max_inactive_days`` is the enrichment activity ceiling
    (``ENRICH_MAX_INACTIVE_DAYS``, read by the caller, not at import time so the
    guard stays testable). This function **refuses to run** (raises
    ``ValueError``) unless ``inactive_days > max_inactive_days`` — that enforced
    precondition guarantees a pruned row can never satisfy the enrichment
    candidate query's ``days_since_last_battle <= max_inactive_days``, so the
    prune cannot create a fresh enrichment candidate regardless of env config.

    ``dry_run=True`` reports the candidate count, the PENDING-intersection count
    (over the predicate *without* the PENDING exclusion — expect 0), and an
    approximate reclaimable-bytes estimate from catalog stats; it writes
    nothing. Live runs collect candidate ids in one scan (capped by
    ``max_rows``), then NULL by primary key in ``batch_size`` chunks — the table
    is scanned once, not once per batch. ``statement_timeout_s`` bounds every
    query (Postgres) so a pathological plan fails fast.
    """
    from warships.models import Player

    inactive_days = int(inactive_days)
    max_inactive_days = int(max_inactive_days)
    if inactive_days <= max_inactive_days:
        raise ValueError(
            f"--inactive-days ({inactive_days}) must be strictly greater than "
            f"ENRICH_MAX_INACTIVE_DAYS ({max_inactive_days}); refusing to run "
            "so the prune can never create a fresh enrichment candidate."
        )

    cutoff = date.today() - timedelta(days=inactive_days)
    params = {
        "cutoff": cutoff,
        "pending": Player.ENRICHMENT_PENDING,
    }
    is_pg = connection.vendor == "postgresql"

    if dry_run:
        candidate_sql = _prune_battles_json_candidate_sql()
        with transaction.atomic():
            with connection.cursor() as cur:
                _apply_statement_timeout(cur, statement_timeout_s, is_pg)
                cur.execute(
                    f"SELECT COUNT(*) FROM ({candidate_sql}) c", params)
                count = int(cur.fetchone()[0] or 0)
                cur.execute(
                    _prune_battles_json_pending_intersection_sql(), params)
                pending_intersection = int(cur.fetchone()[0] or 0)
        avg_bytes = _estimate_avg_player_battles_json_bytes()
        reclaimable_bytes = (
            int(avg_bytes * count) if avg_bytes is not None else None)
        return {
            "status": "completed",
            "dry_run": True,
            "inactive_days": inactive_days,
            "max_inactive_days": max_inactive_days,
            "cutoff": cutoff.isoformat(),
            "candidates": count,
            "pending_intersection": pending_intersection,
            "reclaimable_bytes": reclaimable_bytes,
            "cleared": 0,
            "batches": 0,
        }

    # One scan to collect candidate ids (capped by max_rows), then NULL by
    # primary key in chunks. Avoids re-scanning the whole table per batch.
    candidate_sql = _prune_battles_json_candidate_sql()
    if max_rows and max_rows > 0:
        candidate_sql += " LIMIT %(maxrows)s"
        params["maxrows"] = int(max_rows)
    with transaction.atomic():
        with connection.cursor() as cur:
            _apply_statement_timeout(cur, statement_timeout_s, is_pg)
            cur.execute(f"SELECT id FROM ({candidate_sql}) c", params)
            ids = [r[0] for r in cur.fetchall()]

    cleared = 0
    batches = 0
    for start in range(0, len(ids), batch_size):
        chunk = ids[start:start + batch_size]
        placeholders = ",".join(["%s"] * len(chunk))
        with transaction.atomic():
            with connection.cursor() as cur:
                _apply_statement_timeout(cur, statement_timeout_s, is_pg)
                cur.execute(
                    f"UPDATE warships_player SET battles_json = NULL "
                    f"WHERE id IN ({placeholders})",
                    chunk,
                )
                affected = cur.rowcount
        cleared += max(affected, 0)
        batches += 1
        if sleep_between_batches:
            time.sleep(sleep_between_batches)

    return {
        "status": "completed",
        "dry_run": False,
        "inactive_days": inactive_days,
        "max_inactive_days": max_inactive_days,
        "cutoff": cutoff.isoformat(),
        "cleared": cleared,
        "batches": batches,
    }


def reconcile_daily_rollup_coverage(audit_days: int = 30) -> Dict[str, Any]:
    """Alert-only audit: compare BattleEvent vs PlayerDailyShipStats coverage.

    Per `(date, mode)` over the trailing `audit_days` window (excluding today,
    which is still mid-capture), compares `SUM(BattleEvent.battles_delta)`
    against `SUM(PlayerDailyShipStats.battles)`. Both sides are DB-side
    aggregates — no Python row load. Flags any date where BattleEvent has
    battles the daily layer is missing or under-counts; legitimately-zero days
    are ignored. Compares per mode because the daily layer carries random +
    ranked while the period tiers are randoms-only — so we reconcile the daily
    layer, never the period tiers.

    Returns the discrepancy list; writes nothing. Repair beyond the self-heal
    window is the human-run `rebuild_player_daily_ship_stats` command. See
    `agents/runbooks/runbook-battle-history-rollup-durability-2026-06-06.md`.
    """
    from django.db.models import Sum
    from django.db.models.functions import TruncDate

    from warships.models import BattleEvent, PlayerDailyShipStats

    today = datetime.now(timezone.utc).date()
    window_start = today - timedelta(days=audit_days)
    start_dt = datetime(window_start.year, window_start.month, window_start.day)
    end_dt = datetime(today.year, today.month, today.day)

    be_rows = (
        BattleEvent.objects
        .filter(detected_at__gte=start_dt, detected_at__lt=end_dt)
        .annotate(d=TruncDate("detected_at"))
        .values("d", "mode")
        .annotate(be_battles=Sum("battles_delta"))
        .order_by()
    )
    be_map = {(r["d"], r["mode"]): (r["be_battles"] or 0) for r in be_rows}

    pds_rows = (
        PlayerDailyShipStats.objects
        .filter(date__gte=window_start, date__lt=today)
        .values("date", "mode")
        .annotate(pds_battles=Sum("battles"))
        .order_by()
    )
    pds_map = {(r["date"], r["mode"]): (r["pds_battles"] or 0) for r in pds_rows}

    discrepancies: List[Dict[str, Any]] = []
    for (d, mode), be_battles in sorted(be_map.items()):
        if be_battles <= 0:
            continue  # ignore legitimately-zero days
        pds_battles = pds_map.get((d, mode), 0)
        if pds_battles < be_battles:
            discrepancies.append({
                "date": str(d),
                "mode": mode,
                "be_battles": be_battles,
                "pds_battles": pds_battles,
                "delta": be_battles - pds_battles,
            })

    return {"discrepancies": discrepancies, "audit_days": audit_days}


def record_observation_and_diff(player_id: int, realm: str) -> Dict[str, Any]:
    """Fetch WG, then run the orchestrator. PoC poll path.

    Thin wrapper around `record_observation_from_payloads`. Issues two WG
    calls (`account/info/` + `ships/stats/`). Used by
    `poll_tracked_player_battles_task` for `lil_boots` and by tests that
    want to drive the full pipeline with a stubbed WG client.
    """
    from warships.api.players import _fetch_player_personal_data
    from warships.api.ships import _fetch_ship_stats_for_player
    from warships.models import Player

    try:
        player = Player.objects.get(player_id=player_id, realm=realm)
    except Player.DoesNotExist:
        logger.warning("Tracked player not found locally: player_id=%s realm=%s",
                       player_id, realm)
        return {"status": "skipped", "reason": "player-not-found"}

    try:
        player_data = _fetch_player_personal_data(player_id, realm=realm)
    except Exception:
        logger.exception("WG account/info fetch failed for player_id=%s", player_id)
        return {"status": "skipped", "reason": "wg-fetch-failed-or-hidden"}
    if not player_data:
        return {"status": "skipped", "reason": "wg-fetch-failed-or-hidden"}

    try:
        ship_data = _fetch_ship_stats_for_player(player_id, realm=realm)
    except Exception:
        logger.exception("WG ships/stats fetch failed for player_id=%s", player_id)
        return {"status": "skipped", "reason": "wg-fetch-failed-or-hidden"}
    if ship_data is None:
        return {"status": "skipped", "reason": "wg-fetch-failed-or-hidden"}
    if isinstance(ship_data, dict):
        ship_data = []

    return record_observation_from_payloads(
        player,
        player_data=player_data,
        ship_data=ship_data,
        refresh_battles_json=True,
    )


def record_ranked_observation_and_diff(player_id: int, realm: str) -> Dict[str, Any]:
    """Like `record_observation_and_diff` but also fetches `seasons/shipstats/`.

    Used by the `establish_ranked_baseline` management command and any
    future ranked-PoC dispatchers. Issues three WG calls
    (`account/info/` + `ships/stats/` + `seasons/shipstats/`) and routes
    all three payloads into `record_observation_from_payloads`.

    The ranked payload may legitimately come back empty (off-season,
    player hasn't played ranked this season). In that case we still
    write a `BattleObservation` with `ranked_ships_stats_json=[]` so the
    next observation can diff against a known-empty prior — without
    that, the diff lane treats "missing" and "empty" identically and
    wouldn't emit an event when the player first plays ranked.
    """
    from warships.api.players import _fetch_player_personal_data
    from warships.api.ships import (
        _fetch_ranked_ship_stats_for_player,
        _fetch_ship_stats_for_player,
    )
    from warships.models import Player

    try:
        player = Player.objects.get(player_id=player_id, realm=realm)
    except Player.DoesNotExist:
        logger.warning("Tracked player not found locally: player_id=%s realm=%s",
                       player_id, realm)
        return {"status": "skipped", "reason": "player-not-found"}

    try:
        player_data = _fetch_player_personal_data(player_id, realm=realm)
    except Exception:
        logger.exception("WG account/info fetch failed for player_id=%s", player_id)
        return {"status": "skipped", "reason": "wg-fetch-failed-or-hidden"}
    if not player_data:
        return {"status": "skipped", "reason": "wg-fetch-failed-or-hidden"}

    try:
        ship_data = _fetch_ship_stats_for_player(player_id, realm=realm)
    except Exception:
        logger.exception("WG ships/stats fetch failed for player_id=%s", player_id)
        return {"status": "skipped", "reason": "wg-fetch-failed-or-hidden"}
    if ship_data is None:
        return {"status": "skipped", "reason": "wg-fetch-failed-or-hidden"}
    if isinstance(ship_data, dict):
        ship_data = []

    try:
        ranked_ship_data = _fetch_ranked_ship_stats_for_player(
            player_id, realm=realm,
        )
    except Exception:
        logger.exception(
            "WG seasons/shipstats fetch failed for player_id=%s — "
            "writing observation with NULL ranked payload so the diff "
            "lane falls back to the most recent successful prior",
            player_id,
        )
        # Critical: do NOT write `[]` on fetch failure. `[]` is "fetched
        # successfully, player has no ranked play" and is a legitimate
        # zero-state baseline. NULL means "fetch failed, ranked state
        # unknown for this tick" and the diff lane walks back to the
        # most recent observation with non-NULL ranked data.
        ranked_ship_data = None

    return record_observation_from_payloads(
        player,
        player_data=player_data,
        ship_data=ship_data,
        ranked_ship_data=ranked_ship_data,
        refresh_battles_json=True,
    )


# ---------------------------------------------------------------------------
# Battle-history cold-archive + prune (monthly retention)
#
# Runbook: agents/runbooks/runbook-battle-history-archive-prune-2026-06-17.md
#
# Enforces a rolling retention window on the two append-only, no-retention
# battle-history tables (BattleEvent, PlayerDailyShipStats) by exporting rows
# older than the cutoff to a compressed CSV + manifest on local disk, verifying
# the archive, then deleting ONLY the rows that physically landed in the
# verified archive. BattleObservation is intentionally OUT of scope (its heavy
# JSON is handled by compact_battle_observation_payloads, and its CASCADE FKs
# from BattleEvent make row deletion unsafe).
#
# Safety spine:
#   * Both tables are FK leaves -> deletes cascade nothing.
#   * The delete set is read back out of the archived CSV (column 0 == id), so
#     we can only ever delete rows that were successfully written AND re-read.
#     A truncated / disk-full archive fails to fully decompress -> we abort and
#     delete nothing. The full decompress IS the completeness check.
#   * The archive + manifest (count + sha256) are written BEFORE any delete.
#   * Per-table independent: a failure on one table never deletes another's.
#   * COPY is one long streaming op (NOT bounded by statement_timeout); only the
#     bounded count query and the per-batch deletes carry SET LOCAL timeouts.
# ---------------------------------------------------------------------------

# Must stay comfortably above SHIP_LEADERBOARD_WINDOW_DAYS (30): the nightly
# ship-standings snapshot aggregates BattleEvent over that trailing window.
ARCHIVE_RETENTION_DAYS_DEFAULT = 92
ARCHIVE_BATCH_SIZE_DEFAULT = 2000
ARCHIVE_STATEMENT_TIMEOUT_DEFAULT = 180

# table-key -> (real table, age column). Hardcoded allowlist: these strings are
# interpolated into SQL (COPY / VACUUM / DELETE cannot bind identifiers), so
# they must never originate from caller input. `date` columns compare against a
# date cutoff; datetime columns against a naive-UTC datetime (USE_TZ=False, so
# the columns are `timestamp without time zone` / `date`).
ARCHIVE_TABLES: Dict[str, Dict[str, str]] = {
    "battleevent": {"table": "warships_battleevent", "date_col": "detected_at"},
    "playerdailyshipstats": {
        "table": "warships_playerdailyshipstats", "date_col": "date"},
}


def _archive_cutoff(retention_days: int, *, as_date: bool):
    """midnight-UTC(now) - retention_days, naive (USE_TZ=False columns)."""
    now = datetime.utcnow()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff = midnight - timedelta(days=max(0, int(retention_days)))
    return cutoff.date() if as_date else cutoff


def _read_app_version() -> str:
    try:
        from django.conf import settings
        path = os.path.join(str(settings.BASE_DIR), "..", "VERSION")
        with open(path) as fh:
            return fh.read().strip()
    except Exception:
        return os.getenv("NEXT_PUBLIC_APP_VERSION", "unknown")


class _ArchiveLock:
    """Non-blocking flock so an overrunning prior run can't overlap the next.

    File-based (not the Django cache) so it works in a standalone management
    command / systemd invocation regardless of the cache backend.
    """

    def __init__(self, path: str):
        self.path = path
        self._fd = None

    def acquire(self) -> bool:
        import fcntl
        self._fd = open(self.path, "w")
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self._fd.close()
            self._fd = None
            return False

    def release(self) -> None:
        if self._fd is not None:
            import fcntl
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                self._fd.close()
                self._fd = None


def _export_table_csv_gz(table, date_col, cutoff, max_rows, archive_path,
                         is_pg) -> None:
    """Stream rows older than cutoff to a gzip CSV (header + data).

    Postgres: server-side COPY straight into the gzip stream (never
    materialised in memory). sqlite (tests): a plain csv.writer over the
    same query, so the count/verify/delete logic is exercised on either
    backend.
    """
    limit_clause = ""
    params: List[Any] = [cutoff]
    if max_rows and max_rows > 0:
        limit_clause = " LIMIT %s"
        params.append(int(max_rows))
    select_sql = (
        f"SELECT * FROM {table} WHERE {date_col} < %s ORDER BY id{limit_clause}")
    with open(archive_path, "wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb") as gz:
            if is_pg:
                with connection.cursor() as cur:
                    if hasattr(cur, "copy_expert"):
                        # psycopg2: mogrify (bytes on older builds, str on
                        # newer) + copy_expert streams to the file object.
                        mog = cur.mogrify(select_sql, params)
                        if isinstance(mog, (bytes, bytearray)):
                            mog = mog.decode()
                        cur.copy_expert(
                            "COPY (" + mog + ") TO STDOUT WITH CSV HEADER", gz)
                    else:
                        # psycopg3 (prod): no copy_expert. cursor.copy() binds
                        # params itself and yields bytes chunks to stream out.
                        copy_sql = ("COPY (" + select_sql
                                    + ") TO STDOUT (FORMAT csv, HEADER)")
                        with cur.copy(copy_sql, params) as copy:
                            for chunk in copy:
                                gz.write(chunk)
            else:
                text = io.TextIOWrapper(gz, encoding="utf-8", newline="")
                writer = csv.writer(text)
                with connection.cursor() as cur:
                    cur.execute(select_sql, params)
                    writer.writerow([c[0] for c in cur.description])
                    for row in cur.fetchall():
                        writer.writerow(
                            ["" if v is None else v for v in row])
                text.flush()
                text.detach()  # leave the gzip stream open for its context


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_ids_and_count(archive_path: str):
    """Fully decompress the archive, spill column-0 (id) to a temp file.

    Returns (ids_path, data_row_count, columns). Fully reading the gzip is the
    completeness check — a truncated/disk-full file raises here, before any
    delete. ids spill to disk (not memory): steady-state runs are millions of
    rows on an 8 GB box.
    """
    fd, ids_path = tempfile.mkstemp(prefix="bh_archive_ids_", suffix=".txt")
    count = 0
    columns: List[str] = []
    with os.fdopen(fd, "w") as out:
        with gzip.open(archive_path, "rt", encoding="utf-8", newline="") as gz:
            reader = csv.reader(gz)
            header = next(reader, None)
            if header is None:
                return ids_path, 0, []
            columns = list(header)
            for row in reader:
                if not row:
                    continue
                out.write(row[0])
                out.write("\n")
                count += 1
    return ids_path, count, columns


def _delete_id_chunk(table, ids, statement_timeout_s, is_pg) -> int:
    placeholders = ",".join(["%s"] * len(ids))
    with transaction.atomic():
        with connection.cursor() as cur:
            _apply_statement_timeout(cur, statement_timeout_s, is_pg)
            cur.execute(
                f"DELETE FROM {table} WHERE id IN ({placeholders})", ids)
            return max(cur.rowcount, 0)


def _delete_ids_from_file(table, ids_path, batch_size, sleep_s,
                          statement_timeout_s, is_pg) -> int:
    deleted = 0
    chunk: List[int] = []
    with open(ids_path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            chunk.append(int(line))
            if len(chunk) >= batch_size:
                deleted += _delete_id_chunk(
                    table, chunk, statement_timeout_s, is_pg)
                chunk = []
                if sleep_s:
                    time.sleep(sleep_s)
        if chunk:
            deleted += _delete_id_chunk(
                table, chunk, statement_timeout_s, is_pg)
    return deleted


def _vacuum_analyze(table: str) -> bool:
    """VACUUM (ANALYZE) — must run in autocommit (cannot be inside a txn).

    Skipped when a transaction is open (e.g. under TestCase's per-test atomic
    wrapper); the management command runs it in Django's default autocommit.
    Best effort: the deletes already committed, so a vacuum failure is logged,
    not fatal."""
    if connection.in_atomic_block:
        logger.info(
            "archive_battle_history: skipping VACUUM (ANALYZE) %s "
            "(inside a transaction block)", table)
        return False
    try:
        with connection.cursor() as cur:
            cur.execute(f"VACUUM (ANALYZE) {table}")
        return True
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "archive_battle_history: VACUUM (ANALYZE) %s failed: %s",
            table, exc)
        return False


def _archive_one_table(*, key, spec, retention_days, run_dir, is_pg,
                       batch_size, max_rows, dry_run, sleep_between_batches,
                       statement_timeout_s, skip_vacuum, app_version):
    table = spec["table"]
    date_col = spec["date_col"]
    cutoff = _archive_cutoff(retention_days, as_date=(date_col == "date"))

    with transaction.atomic():
        with connection.cursor() as cur:
            _apply_statement_timeout(cur, statement_timeout_s, is_pg)
            cur.execute(
                f"SELECT COUNT(*), MIN({date_col}), MAX({date_col}) "
                f"FROM {table} WHERE {date_col} < %s", [cutoff])
            row = cur.fetchone()
    total_count = int(row[0] or 0)
    min_d, max_d = row[1], row[2]

    def _iso(v):
        # Postgres returns date/datetime objects; sqlite returns ISO strings.
        if v is None:
            return None
        return v.isoformat() if hasattr(v, "isoformat") else str(v)

    base: Dict[str, Any] = {
        "key": key, "table": table, "date_col": date_col,
        "cutoff": cutoff.isoformat(), "retention_days": int(retention_days),
        "candidates": total_count,
        "min_date": _iso(min_d), "max_date": _iso(max_d),
    }

    if dry_run:
        base.update({
            "status": "dry_run", "exported": 0, "deleted": 0,
            "archive_file": os.path.join(run_dir, f"{table}.csv.gz")})
        return base

    if total_count == 0:
        base.update({"status": "skipped", "reason": "no rows older than cutoff",
                     "exported": 0, "deleted": 0})
        return base

    os.makedirs(run_dir, exist_ok=True)
    archive_path = os.path.join(run_dir, f"{table}.csv.gz")
    manifest_path = os.path.join(run_dir, f"{table}.manifest.json")

    _export_table_csv_gz(
        table, date_col, cutoff, max_rows, archive_path, is_pg)

    sha = _sha256_file(archive_path)
    ids_path, exported, columns = _read_ids_and_count(archive_path)
    try:
        if exported == 0:
            base.update({
                "status": "failed",
                "reason": "archive contained no data rows after export",
                "archive_file": archive_path, "exported": 0, "deleted": 0})
            return base

        if max_rows == 0 and exported != total_count:
            # Benign (e.g. a concurrent rollup delete between count + export):
            # we delete only what we archived, so this is a warning, not an
            # abort. A truncated archive would have raised in _read_ids_*.
            logger.warning(
                "archive_battle_history: %s exported %d != candidate count %d "
                "— deleting only the %d archived+verified rows",
                table, exported, total_count, exported)

        manifest = {
            **base, "exported": exported, "sha256": sha, "columns": columns,
            "archive_file": os.path.basename(archive_path),
            "app_version": app_version, "max_rows": int(max_rows)}
        with open(manifest_path, "w") as fh:
            json.dump(manifest, fh, indent=2, sort_keys=True)

        deleted = _delete_ids_from_file(
            table, ids_path, batch_size, sleep_between_batches,
            statement_timeout_s, is_pg)
    finally:
        try:
            os.unlink(ids_path)
        except OSError:
            pass

    vacuumed = False
    if deleted and not skip_vacuum and is_pg:
        vacuumed = _vacuum_analyze(table)

    base.update({
        "status": "completed", "exported": exported, "deleted": deleted,
        "sha256": sha, "archive_file": archive_path,
        "manifest_file": manifest_path, "vacuumed": vacuumed})
    return base


def archive_and_prune_battle_history(
    *,
    retention_days: int = ARCHIVE_RETENTION_DAYS_DEFAULT,
    tables: Optional[Iterable[str]] = None,
    archive_dir: str,
    batch_size: int = ARCHIVE_BATCH_SIZE_DEFAULT,
    max_rows: int = 0,
    dry_run: bool = False,
    sleep_between_batches: float = 0.0,
    statement_timeout_s: int = ARCHIVE_STATEMENT_TIMEOUT_DEFAULT,
    skip_vacuum: bool = False,
) -> Dict[str, Any]:
    """Archive (gzip CSV + manifest) then prune battle-history rows older than
    ``retention_days``. See the module header for the safety spine. Returns a
    summary dict; per-table failures are isolated (status == 'failed') and do
    not delete from other tables."""
    keys = list(tables) if tables else list(ARCHIVE_TABLES.keys())
    for k in keys:
        if k not in ARCHIVE_TABLES:
            raise ValueError(
                f"unknown table key {k!r}; valid: {sorted(ARCHIVE_TABLES)}")
    is_pg = connection.vendor == "postgresql"
    run_date = datetime.utcnow().strftime("%Y-%m-%d")
    run_dir = os.path.join(archive_dir, run_date)
    app_version = _read_app_version()

    os.makedirs(archive_dir, exist_ok=True)
    lock = _ArchiveLock(os.path.join(archive_dir, ".archive_battle_history.lock"))
    if not lock.acquire():
        return {"status": "skipped", "reason": "already-running",
                "dry_run": dry_run}
    try:
        results = []
        overall = "completed"
        for key in keys:
            try:
                res = _archive_one_table(
                    key=key, spec=ARCHIVE_TABLES[key],
                    retention_days=retention_days, run_dir=run_dir, is_pg=is_pg,
                    batch_size=batch_size, max_rows=max_rows, dry_run=dry_run,
                    sleep_between_batches=sleep_between_batches,
                    statement_timeout_s=statement_timeout_s,
                    skip_vacuum=skip_vacuum, app_version=app_version)
            except Exception as exc:
                logger.exception(
                    "archive_battle_history: table %s failed", key)
                res = {"key": key, "table": ARCHIVE_TABLES[key]["table"],
                       "status": "failed", "reason": str(exc),
                       "exported": 0, "deleted": 0}
            results.append(res)
            if res.get("status") == "failed":
                overall = "failed"
        return {"status": overall, "dry_run": dry_run, "run_dir": run_dir,
                "run_date": run_date, "app_version": app_version,
                "tables": results}
    finally:
        lock.release()


# ── BattleObservation row retention (DB audit F5) ─────────────────────────
#
# The observation table never deleted rows: ~89% are JSON-stripped skeletons
# (per-row provenance no reader consumes at any age) and ~19% are fully-empty
# polls. This tier deletes, in one batched pass:
#   * stripped skeletons (both JSON columns NULL) older than
#     ``retention_days`` (default 32 — the operational poll-trail lookback;
#     deliberately NOT tied to the 92d BattleEvent archive retention, since
#     events are archived to CSV while skeletons are archived nowhere), and
#   * fully-empty polls (no last_battle_time, no JSON) older than
#     ``empty_retention_days`` (default 7).
# Two invariants, enforced in the candidate SQL itself:
#   * a row carrying JSON (randoms or ranked) is NEVER deleted — the
#     keep-latest-3 compaction owns JSON lifecycle; and
#   * each player's latest observation is NEVER deleted (the floor's
#     change-gate freshness anchor), via an EXISTS-newer guard.
# Delete-only (no CSV export): the safety spine is the guarded candidate
# query + chunked deletes + VACUUM, mirroring the archive path's batching.

OBSERVATION_ROW_RETENTION_DAYS_DEFAULT = 32
OBSERVATION_EMPTY_RETENTION_DAYS_DEFAULT = 7
OBSERVATION_RETENTION_STATEMENT_TIMEOUT_DEFAULT = 600

_OBSERVATION_TABLE = "warships_battleobservation"

_OBSERVATION_CANDIDATE_WHERE = (
    "o.ships_stats_json IS NULL AND o.ranked_ships_stats_json IS NULL "
    "AND (o.observed_at < %s "
    "     OR (o.last_battle_time IS NULL AND o.observed_at < %s)) "
    "AND EXISTS (SELECT 1 FROM warships_battleobservation n "
    "            WHERE n.player_id = o.player_id "
    "            AND n.observed_at > o.observed_at)"
)


def prune_battle_observation_rows(
    *,
    retention_days: int = OBSERVATION_ROW_RETENTION_DAYS_DEFAULT,
    empty_retention_days: int = OBSERVATION_EMPTY_RETENTION_DAYS_DEFAULT,
    batch_size: int = ARCHIVE_BATCH_SIZE_DEFAULT,
    max_rows: int = 0,
    dry_run: bool = False,
    sleep_between_batches: float = 0.0,
    statement_timeout_s: int = OBSERVATION_RETENTION_STATEMENT_TIMEOUT_DEFAULT,
    skip_vacuum: bool = False,
) -> Dict[str, Any]:
    is_pg = connection.vendor == "postgresql"
    cutoff = _archive_cutoff(retention_days, as_date=False)
    empty_cutoff = _archive_cutoff(empty_retention_days, as_date=False)
    params: List[Any] = [cutoff, empty_cutoff]

    base: Dict[str, Any] = {
        "table": _OBSERVATION_TABLE,
        "retention_days": int(retention_days),
        "empty_retention_days": int(empty_retention_days),
        "cutoff": cutoff.isoformat(),
        "empty_cutoff": empty_cutoff.isoformat(),
    }

    if dry_run:
        with transaction.atomic():
            with connection.cursor() as cur:
                _apply_statement_timeout(cur, statement_timeout_s, is_pg)
                cur.execute(
                    f"SELECT COUNT(*) FROM {_OBSERVATION_TABLE} o "
                    f"WHERE {_OBSERVATION_CANDIDATE_WHERE}", params)
                candidates = int(cur.fetchone()[0] or 0)
        base.update({"status": "dry_run", "candidates": candidates,
                     "deleted": 0})
        return base

    limit_clause = ""
    if max_rows and max_rows > 0:
        limit_clause = " LIMIT %s"
        params = params + [int(max_rows)]

    # Single collection pass spilled to disk (millions of ids on the first
    # run), then chunked deletes — the same shape as the archive delete path.
    fd, ids_path = tempfile.mkstemp(prefix="obs_prune_ids_", suffix=".txt")
    candidates = 0
    try:
        with os.fdopen(fd, "w") as out:
            with transaction.atomic():
                with connection.cursor() as cur:
                    _apply_statement_timeout(cur, statement_timeout_s, is_pg)
                    cur.execute(
                        f"SELECT o.id FROM {_OBSERVATION_TABLE} o "
                        f"WHERE {_OBSERVATION_CANDIDATE_WHERE}{limit_clause}",
                        params)
                    while True:
                        rows = cur.fetchmany(10000)
                        if not rows:
                            break
                        for (row_id,) in rows:
                            out.write(f"{row_id}\n")
                            candidates += 1

        deleted = _delete_ids_from_file(
            _OBSERVATION_TABLE, ids_path, batch_size, sleep_between_batches,
            statement_timeout_s, is_pg)
    finally:
        try:
            os.unlink(ids_path)
        except OSError:
            pass

    vacuumed = False
    if deleted and not skip_vacuum and is_pg:
        vacuumed = _vacuum_analyze(_OBSERVATION_TABLE)

    base.update({"status": "completed", "candidates": candidates,
                 "deleted": deleted, "vacuumed": vacuumed})
    return base
