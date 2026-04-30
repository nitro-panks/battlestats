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
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from django.db import transaction
from django.db.models import F, Q


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

    from warships.models import PlayerDailyShipStats

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
    from warships.models import BattleEvent, BattleObservation, Player, Ship

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
        latest_detected_at = None
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

        if created > 0 and latest_detected_at is not None:
            # Drives the landing "Active" sub-sort. The conditional UPDATE
            # only advances the column forward — if a concurrent writer
            # already set a later value, the WHERE clause excludes us and
            # the UPDATE is a no-op. Single atomic statement, portable
            # across SQLite (tests) and Postgres (prod).
            Player.objects.filter(pk=player.pk).filter(
                Q(last_random_battle_at__isnull=True)
                | Q(last_random_battle_at__lt=latest_detected_at)
            ).update(last_random_battle_at=latest_detected_at)

    if created > 0:
        _invalidate_battle_history_cache(player)
        _invalidate_landing_recent_players_cache(player)

    return {
        "status": "completed",
        "observation_id": observation.id,
        "events_created": created,
    }


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


def _invalidate_landing_recent_players_cache(player) -> None:
    """Mark the landing Recent sub-sort cache dirty for this player's
    realm so the next read rebuilds with the fresh ordering. Coalesced
    via a 30-second cooldown inside the invalidator.

    Recent now means recently-battled (driven by Player.last_random_battle_at),
    so the BattleEvent capture path is the authoritative invalidation point.
    """
    from warships.landing import invalidate_landing_recent_player_cache

    realm = player.realm or "na"
    invalidate_landing_recent_player_cache(realm=realm)


def rebuild_daily_ship_stats_for_date(target_date) -> Dict[str, Any]:
    """Rebuild `PlayerDailyShipStats` rows for `target_date` from BattleEvent.

    Idempotent: deletes rows for the date, then recomputes from scratch.
    Used by the nightly sweeper task and by the
    `rebuild_player_daily_ship_stats` management command.

    Always runs regardless of BATTLE_HISTORY_ROLLUP_ENABLED, since the caller
    has explicitly asked for a rebuild.
    """
    from warships.models import BattleEvent, PlayerDailyShipStats

    with transaction.atomic():
        deleted, _ = PlayerDailyShipStats.objects.filter(
            date=target_date,
        ).delete()

        events = BattleEvent.objects.filter(
            detected_at__date=target_date,
        ).order_by("detected_at")

        rows: Dict[tuple, Dict[str, Any]] = {}
        for event in events:
            key = (event.player_id, event.ship_id)
            row = rows.get(key)
            if row is None:
                row = {
                    "player_id": event.player_id,
                    "date": target_date,
                    "ship_id": event.ship_id,
                    "ship_name": event.ship_name or "",
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
            detected_at__date=target_date,
        ).count(),
    }


# ---------------------------------------------------------------------------
# Period rollups (Phase 6) — weekly / monthly / yearly aggregates of the
# daily layer. Materialized; daily layer remains the source of truth.
# ---------------------------------------------------------------------------

def _week_start(d) -> "date":
    """ISO week Monday for the date `d`."""
    from datetime import timedelta as _td
    return d - _td(days=d.weekday())


def _month_start(d) -> "date":
    return d.replace(day=1)


def _year_start(d) -> "date":
    return d.replace(month=1, day=1)


def _aggregate_into_period_table(
    target_period_start,
    period_end_inclusive,
    period_table,
):
    """Rebuild `period_table` rows for the `(target_period_start,
    period_end_inclusive)` window from `PlayerDailyShipStats`. Idempotent.

    `period_table` is one of `PlayerWeeklyShipStats`,
    `PlayerMonthlyShipStats`, `PlayerYearlyShipStats` — they share the
    same column shape via the abstract base.
    """
    from warships.models import PlayerDailyShipStats

    with transaction.atomic():
        period_table.objects.filter(period_start=target_period_start).delete()

        daily_qs = PlayerDailyShipStats.objects.filter(
            date__gte=target_period_start, date__lte=period_end_inclusive,
        ).order_by("date")

        rows: Dict[tuple, Dict[str, Any]] = {}
        for d in daily_qs:
            key = (d.player_id, d.ship_id)
            row = rows.get(key)
            if row is None:
                row = {
                    "player_id": d.player_id,
                    "period_start": target_period_start,
                    "ship_id": d.ship_id,
                    "ship_name": d.ship_name or "",
                    "battles": 0, "wins": 0, "losses": 0, "frags": 0,
                    "damage": 0, "xp": 0, "planes_killed": 0,
                    "survived_battles": 0,
                    "first_event_at": d.first_event_at,
                    "last_event_at": d.last_event_at,
                }
                rows[key] = row
            row["battles"] += d.battles
            row["wins"] += d.wins
            row["losses"] += d.losses
            row["frags"] += d.frags
            row["damage"] += d.damage
            row["xp"] += d.xp
            row["planes_killed"] += d.planes_killed
            row["survived_battles"] += d.survived_battles
            if d.first_event_at and (row["first_event_at"] is None
                                     or d.first_event_at < row["first_event_at"]):
                row["first_event_at"] = d.first_event_at
            if d.last_event_at and (row["last_event_at"] is None
                                    or d.last_event_at > row["last_event_at"]):
                row["last_event_at"] = d.last_event_at
            if d.ship_name and not row["ship_name"]:
                row["ship_name"] = d.ship_name

        if rows:
            period_table.objects.bulk_create([
                period_table(**row) for row in rows.values()
            ])

    return {
        "rows_written": len(rows),
        "period_start": str(target_period_start),
    }


def rebuild_period_rollups_for_date(target_date) -> Dict[str, Any]:
    """Rebuild weekly + monthly + yearly rollup rows that cover `target_date`.

    Called by the nightly sweeper after `rebuild_daily_ship_stats_for_date`,
    so the period rollups always reflect the latest daily layer. Idempotent.
    """
    from datetime import timedelta as _td

    from warships.models import (
        PlayerMonthlyShipStats,
        PlayerWeeklyShipStats,
        PlayerYearlyShipStats,
    )

    week_start = _week_start(target_date)
    week_end = week_start + _td(days=6)

    month_start = _month_start(target_date)
    if month_start.month == 12:
        next_month = month_start.replace(year=month_start.year + 1, month=1)
    else:
        next_month = month_start.replace(month=month_start.month + 1)
    month_end = next_month - _td(days=1)

    year_start = _year_start(target_date)
    year_end = year_start.replace(month=12, day=31)

    weekly = _aggregate_into_period_table(
        week_start, week_end, PlayerWeeklyShipStats)
    monthly = _aggregate_into_period_table(
        month_start, month_end, PlayerMonthlyShipStats)
    yearly = _aggregate_into_period_table(
        year_start, year_end, PlayerYearlyShipStats)

    return {
        "status": "completed",
        "target_date": str(target_date),
        "weekly": weekly,
        "monthly": monthly,
        "yearly": yearly,
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
