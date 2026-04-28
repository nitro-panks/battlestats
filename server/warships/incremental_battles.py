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

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from django.db import transaction


logger = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class PlayerSnapshot:
    pvp_battles: int
    pvp_wins: int
    pvp_losses: int
    pvp_frags: int
    pvp_survived_battles: int
    last_battle_time: Optional[datetime]
    ships: Dict[int, ShipSnapshot]


def _coerce_ship_snapshot(ship_dict: Dict[str, Any]) -> Optional[ShipSnapshot]:
    try:
        ship_id = int(ship_dict["ship_id"])
    except (KeyError, TypeError, ValueError):
        return None
    pvp = ship_dict.get("pvp") or {}
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
    """
    if current.pvp_battles <= previous.pvp_battles:
        return []

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
        events.append({
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
        })
    return events


def fetch_player_observation_payload(player_id: int, realm: str) -> Optional[PlayerSnapshot]:
    """Single WG poll → PlayerSnapshot. None on flake (caller should retry next tick)."""
    from warships.api.players import _fetch_player_personal_data
    from warships.api.ships import _fetch_ship_stats_for_player

    try:
        player_data = _fetch_player_personal_data(player_id, realm=realm)
    except Exception:
        logger.exception("WG account/info fetch failed for player_id=%s", player_id)
        return None
    if not player_data:
        logger.info("WG account/info empty for player_id=%s", player_id)
        return None

    try:
        ship_data = _fetch_ship_stats_for_player(player_id, realm=realm)
    except Exception:
        logger.exception("WG ships/stats fetch failed for player_id=%s", player_id)
        return None
    if ship_data is None:
        return None
    if isinstance(ship_data, dict):
        ship_data = []

    return coerce_observation_payload(player_data, ship_data)


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
            )
            for row in (previous.ships_stats_json or [])
            if row.get("ship_id") is not None
        },
    )


def _apply_event_to_daily_summary(event) -> None:
    """Stub. Phase 3 of the rollout fills this in to update PlayerDailyShipStats.

    Called inside the same `transaction.atomic()` block as the BattleEvent
    insert so the rollup write cannot drift from the event row. No-op today;
    the BattleEvent row alone is the durable artifact until Phase 3.
    """
    return None


def record_observation_from_payloads(
    player,
    *,
    player_data: Optional[Dict[str, Any]] = None,
    ship_data: Iterable[Dict[str, Any]],
    source: str = None,
) -> Dict[str, Any]:
    """Persist a `BattleObservation` for `player` and emit `BattleEvent` rows.

    Issues no WG calls — caller supplies the payloads. The two valid input
    shapes are:

    * `player_data` is the raw `account/info/` dict, `ship_data` is the
      `ships/stats/` list. PoC poll path uses this.
    * `player_data` is `None` (or omitted), `ship_data` is the `ships/stats/`
      list, and `player.pvp_*` columns are already up to date. Rollout
      piggyback hook in `update_battle_data` uses this — `update_player_data`
      has just refreshed those columns.

    Returns a status dict matching `record_observation_and_diff`.
    """
    from warships.models import BattleEvent, BattleObservation, Ship

    if player_data is not None:
        snapshot = coerce_observation_payload(player_data, ship_data)
    else:
        snapshot = _snapshot_from_player_row(player, ship_data)

    if snapshot is None:
        return {"status": "skipped", "reason": "wg-fetch-failed-or-hidden"}

    ships_payload = _serialize_ships_payload(snapshot)
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
            source=source,
        )

        if previous is None:
            return {
                "status": "completed",
                "observation_id": observation.id,
                "events_created": 0,
                "reason": "baseline",
            }

        previous_snapshot = _hydrate_previous_snapshot(previous)
        events = compute_battle_events(previous_snapshot, snapshot)
        if not events:
            return {
                "status": "completed",
                "observation_id": observation.id,
                "events_created": 0,
            }

        ship_ids = [event["ship_id"] for event in events]
        ship_names = dict(
            Ship.objects.filter(ship_id__in=ship_ids)
            .values_list("ship_id", "name")
        )

        created = 0
        for event in events:
            event_row = BattleEvent.objects.create(
                player=player,
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
                from_observation=previous,
                to_observation=observation,
            )
            _apply_event_to_daily_summary(event_row)
            created += 1

        return {
            "status": "completed",
            "observation_id": observation.id,
            "events_created": created,
        }


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
    )
