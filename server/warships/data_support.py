from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Optional

from django.utils import timezone as django_timezone


def clamp(value: float, lower: float, upper: float) -> float:
    """Clamp `value` into `[lower, upper]`. Canonical home for what was
    previously duplicated as `_clamp` in both `data.py` and `landing.py`."""
    return max(lower, min(upper, value))


def _coerce_dict_rows(rows: Any) -> list[dict]:
    if not isinstance(rows, list):
        return []

    return [row for row in rows if isinstance(row, dict)]


def _coerce_activity_rows(activity_rows: Any) -> list[dict]:
    rows = []
    for row in _coerce_dict_rows(activity_rows):
        rows.append({
            'date': row.get('date'),
            'battles': int(row.get('battles', 0) or 0),
            'wins': int(row.get('wins', 0) or 0),
        })

    return rows


def _coerce_ranked_rows(ranked_rows: Any) -> list[dict]:
    rows = _coerce_dict_rows(ranked_rows)
    return sorted(rows, key=lambda row: int(row.get('season_id', 0) or 0), reverse=True)


def _coerce_battle_rows(battles_rows: Any) -> list[dict]:
    return _coerce_dict_rows(battles_rows)


def _coerce_efficiency_rows(efficiency_rows: Any) -> list[dict]:
    return _coerce_dict_rows(efficiency_rows)


def _is_stale_timestamp(updated_at: Optional[datetime], stale_after: timedelta) -> bool:
    if updated_at is None:
        return True

    if django_timezone.is_aware(updated_at):
        current_time = datetime.now(timezone.utc)
        normalized_updated_at = updated_at
    else:
        current_time = datetime.now()
        normalized_updated_at = updated_at

    return current_time - normalized_updated_at >= stale_after


def _timestamped_payload_needs_refresh(
    payload: Any,
    updated_at: Optional[datetime],
    stale_after: timedelta,
) -> bool:
    if payload is None or updated_at is None:
        return True

    return _is_stale_timestamp(updated_at, stale_after)


def _normalize_timestamp_value(updated_at: Optional[datetime]) -> Optional[datetime]:
    if updated_at is None:
        return None

    if django_timezone.is_aware(updated_at):
        return updated_at.astimezone(timezone.utc)

    return updated_at.replace(tzinfo=timezone.utc)


def _has_newer_source_timestamp(
    derived_updated_at: Optional[datetime],
    *source_updated_ats: Optional[datetime],
) -> bool:
    normalized_derived = _normalize_timestamp_value(derived_updated_at)
    if normalized_derived is None:
        return True

    for source_updated_at in source_updated_ats:
        normalized_source = _normalize_timestamp_value(source_updated_at)
        if normalized_source is not None and normalized_source > normalized_derived:
            return True

    return False


def _queue_limited_player_hydration(
    players: Iterable[Any],
    should_refresh: Callable[[Any], bool],
    is_refresh_pending: Callable[[int], bool],
    enqueue_refresh: Callable[[int], dict[str, Any]],
    max_in_flight: int,
) -> dict[str, Any]:
    eligible_players = [player for player in players if should_refresh(player)]
    eligible_player_ids = {player.player_id for player in eligible_players}
    pending_player_ids: set[int] = set()
    queued_player_ids: set[int] = set()
    deferred_player_ids: set[int] = set()

    for player in eligible_players:
        if is_refresh_pending(player.player_id):
            pending_player_ids.add(player.player_id)

    available_slots = max(0, max_in_flight - len(pending_player_ids))

    for player in eligible_players:
        if player.player_id in pending_player_ids:
            continue

        if available_slots <= 0:
            deferred_player_ids.add(player.player_id)
            continue

        enqueue_result = enqueue_refresh(player.player_id)
        if enqueue_result.get('status') == 'queued':
            pending_player_ids.add(player.player_id)
            queued_player_ids.add(player.player_id)
            available_slots -= 1
            continue

        if enqueue_result.get('reason') == 'enqueue-failed':
            deferred_player_ids.update(
                queued_player.player_id
                for queued_player in eligible_players
                if queued_player.player_id not in pending_player_ids and queued_player.player_id != player.player_id
            )
            deferred_player_ids.add(player.player_id)
            break

    # Intentionally do NOT fold deferred_player_ids into pending_player_ids.
    # `pending` is the set of players with work actually in flight (max_in_flight slots).
    # Deferred players are eligible-but-waiting; they will be picked up on subsequent
    # polls as in-flight slots free up. Folding them in caused the clan-members
    # "Updating N members" banner to report the entire stale population on every
    # poll instead of the actual in-flight count, which combined with the frontend
    # poll cap produced a wedged banner. See
    # runbook-clan-members-hydration-wedge-2026-04-07.md.

    return {
        'pending_player_ids': pending_player_ids,
        'queued_player_ids': queued_player_ids,
        'deferred_player_ids': deferred_player_ids,
        'eligible_player_ids': eligible_player_ids,
        'max_in_flight': max_in_flight,
    }
