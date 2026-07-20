from warships.tasks import update_activity_data_task, update_battle_data_task, update_clan_data_task, update_clan_members_task, update_randoms_data_task, update_snapshot_data_task, update_tiers_data_task, update_type_data_task
from warships.api.clans import _fetch_clan_data, _fetch_clan_member_ids, _fetch_clan_membership_for_player, \
    _fetch_clan_battle_seasons_info, _fetch_clan_battle_season_stats
from warships.api.players import _fetch_player_personal_data, _fetch_ranked_account_info, _fetch_player_achievements
from warships.api.ships import _fetch_ship_stats_for_player, _fetch_ship_stats_for_player_with_hidden, _fetch_ship_info, _fetch_ranked_ship_stats_for_player, _fetch_efficiency_badges_for_player, build_ship_chart_name
from warships.achievements_catalog import get_achievement_catalog_entry
from warships.player_analytics import compute_player_verdict
from warships.data_support import _coerce_activity_rows, _coerce_battle_rows, _coerce_efficiency_rows, _coerce_ranked_rows, _has_newer_source_timestamp, _is_stale_timestamp, _queue_limited_player_hydration, _timestamped_payload_needs_refresh, clamp
from warships.player_records import BlockedAccountError, get_or_create_canonical_player
from warships.models import PlayerAchievementStat, MvPlayerDistributionStats
from warships.models import DEFAULT_REALM, realm_cache_key, Player, Snapshot, Clan, PlayerExplorerSummary, Ship
from django.utils import timezone as django_timezone
from django.db.models.functions import Cast, Lower, TruncMonth
from django.db.models import Avg, Case, Count, F, FloatField, IntegerField, Max, Min, Q, Sum, Value, When
from django.db import connection, transaction
from django.core.cache import cache
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from typing import Any, Optional, Iterable
from datetime import datetime, timezone, timedelta, date
import logging
import math
import os
import time

logger = logging.getLogger(__name__)

logging.basicConfig(level=logging.INFO)

ANALYTICAL_WORK_MEM = os.getenv('ANALYTICAL_WORK_MEM', '8MB')


@contextmanager
def _elevated_work_mem():
    """Temporarily raise work_mem for heavy analytical queries (distribution, correlation)."""
    if connection.vendor != 'postgresql':
        yield
        return

    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL work_mem = %s", [ANALYTICAL_WORK_MEM])
    try:
        yield
    finally:
        pass  # SET LOCAL resets at transaction end


KILL_RATIO_LOW_TIER_WEIGHT = 0.15
KILL_RATIO_MID_TIER_WEIGHT = 0.65
KILL_RATIO_HIGH_TIER_WEIGHT = 1.0
KILL_RATIO_SMOOTHING_BATTLES = 12.0
KILL_RATIO_PRIOR = 0.7
PLAYER_SCORE_WR_WEIGHT = 0.40
PLAYER_SCORE_KDR_WEIGHT = 0.28
PLAYER_SCORE_SURVIVAL_WEIGHT = 0.18
PLAYER_SCORE_BATTLES_WEIGHT = 0.14
PLAYER_SCORE_MAX = 10.0
PLAYER_SCORE_INACTIVITY_GRACE_DAYS = 7
PLAYER_SCORE_180_DAY_CAP = 2.0
PLAYER_SCORE_365_DAY_CAP = 1.0
PLAYER_SCORE_DORMANT_MIN = 0.05
PLAYER_SCORE_POST_YEAR_DECAY_DAYS = 180.0
PLAYER_SCORE_ACTIVITY_SATURATION_BATTLES = 8.0
PLAYER_SCORE_LOW_TIER_WEIGHT = 0.02
PLAYER_SCORE_MID_TIER_WEIGHT = 0.60
PLAYER_SCORE_HIGH_TIER_WEIGHT = 1.0
PLAYER_SCORE_LOW_TIER_FLOOR = 0.25
CLAN_BATTLE_ENJOYER_MIN_BATTLES = 40
CLAN_BATTLE_ENJOYER_MIN_SEASONS = 2
SLEEPY_PLAYER_DAYS_THRESHOLD = 365
CLAN_RANKED_HYDRATION_STALE_AFTER = timedelta(hours=24)
CLAN_BATTLE_PLAYER_HYDRATION_MAX_IN_FLIGHT = max(
    1, int(os.getenv('CLAN_BATTLE_PLAYER_HYDRATION_MAX_IN_FLIGHT', '8')))
CLAN_BATTLE_SUMMARY_STALE_DAYS = max(
    1, int(os.getenv('CLAN_BATTLE_SUMMARY_STALE_DAYS', '7')))
CLAN_BATTLE_BADGE_REFRESH_DAYS = CLAN_BATTLE_SUMMARY_STALE_DAYS
CLAN_EFFICIENCY_HYDRATION_MAX_IN_FLIGHT = max(
    1, int(os.getenv('CLAN_EFFICIENCY_HYDRATION_MAX_IN_FLIGHT', '8')))
PLAYER_EFFICIENCY_STALE_AFTER = timedelta(hours=24)
PLAYER_ACHIEVEMENTS_STALE_AFTER = timedelta(hours=24)
EFFICIENCY_BADGE_CLASS_LABELS = {
    1: 'Expert',
    2: 'Grade I',
    3: 'Grade II',
    4: 'Grade III',
}
EFFICIENCY_RANK_MIN_PVP_BATTLES = 200
EFFICIENCY_RANK_MIN_SHIP_BATTLES = 5
EFFICIENCY_RANK_MIN_ELIGIBLE_SHIPS = 5
EFFICIENCY_RANK_MAX_BADGE_POINTS_PER_SHIP = 8
EFFICIENCY_RANK_SHRINKAGE_K = 12.0
EFFICIENCY_RANK_UNMAPPED_SHARE_LIMIT = 0.10
EFFICIENCY_RANK_SNAPSHOT_STALE_AFTER = timedelta(hours=48)
EFFICIENCY_RANK_MIN_VISIBLE_PERCENTILE = 0.50
EFFICIENCY_RANK_GRADE_II_PERCENTILE = 0.75
EFFICIENCY_RANK_GRADE_I_PERCENTILE = 0.90
EFFICIENCY_RANK_EXPERT_PERCENTILE = 0.97
EFFICIENCY_BADGE_CLASS_POINTS = {
    1: 8,
    2: 4,
    3: 2,
    4: 1,
}
EFFICIENCY_RANK_TIER_LABELS = {
    'E': 'Expert',
    'I': 'Grade I',
    'II': 'Grade II',
    'III': 'Grade III',
}
CLAN_RANKED_HYDRATION_MAX_IN_FLIGHT = max(
    1, int(os.getenv('CLAN_RANKED_HYDRATION_MAX_IN_FLIGHT', '8')))
PLAYER_DETAIL_STALE_AFTER = timedelta(minutes=15)
PLAYER_BATTLE_DATA_STALE_AFTER = timedelta(minutes=15)
PLAYER_ACTIVITY_DATA_STALE_AFTER = timedelta(minutes=15)
PLAYER_DERIVED_DATA_STALE_AFTER = timedelta(days=1)
PLAYER_RANKED_DATA_STALE_AFTER = timedelta(hours=1)
CLAN_DETAIL_STALE_AFTER = timedelta(hours=12)
HOT_ENTITY_PLAYER_LIMIT = max(
    1, int(os.getenv('HOT_ENTITY_PLAYER_LIMIT', '20')))
HOT_ENTITY_CLAN_LIMIT = max(
    1, int(os.getenv('HOT_ENTITY_CLAN_LIMIT', '10')))
# Empty by default: pinning is opt-in via the HOT_ENTITY_PINNED_PLAYER_NAMES env
# var. (Was historically defaulted to a single personal account, 'lil_boots';
# removed 2026-05-28 at the owner's request so no specific record is perpetually
# warmed unless explicitly configured.)
HOT_ENTITY_PINNED_PLAYER_NAMES = [
    n.strip() for n in os.getenv('HOT_ENTITY_PINNED_PLAYER_NAMES', '').split(',') if n.strip()
]
CLAN_PLOT_DATA_CACHE_TTL = 15 * 60


def _dispatch_async_refresh(task, *args, **kwargs) -> None:
    try:
        task.delay(*args, **kwargs)
    except Exception as error:
        logging.warning(
            'Skipping async refresh for %s because broker dispatch failed: %s',
            getattr(task, 'name', repr(task)),
            error,
        )


def _dispatch_async_correlation_warm(realm: str = DEFAULT_REALM) -> None:
    # Routes through the lock-aware gate so cold-cache page-load bursts
    # don't fan out one warm task per request. See
    # agents/runbooks/runbook-post-rollout-followups-2026-05-01.md Phase 1.
    from warships.tasks import queue_warm_player_correlations
    queue_warm_player_correlations(realm=realm)


def player_detail_needs_refresh(
    player: Player,
    stale_after: timedelta = PLAYER_DETAIL_STALE_AFTER,
) -> bool:
    return _is_stale_timestamp(player.last_fetch, stale_after)


def player_battle_data_needs_refresh(
    player: Player,
    stale_after: timedelta = PLAYER_BATTLE_DATA_STALE_AFTER,
) -> bool:
    return _timestamped_payload_needs_refresh(
        player.battles_json,
        player.battles_updated_at,
        stale_after,
    )


def player_activity_data_needs_refresh(
    player: Player,
    stale_after: timedelta = PLAYER_ACTIVITY_DATA_STALE_AFTER,
) -> bool:
    activity_rows = player.activity_json
    if activity_rows is None:
        return True

    updated_at = player.activity_updated_at
    if updated_at is None:
        return True

    def _is_empty_activity(rows: Any) -> bool:
        return not isinstance(rows, list) or not rows

    def _looks_like_cumulative_spike(rows: Any) -> bool:
        if not isinstance(rows, list) or not rows:
            return False
        non_zero_days = [row for row in rows if (
            row.get('battles', 0) or 0) > 0]
        total_battles = sum((row.get('battles', 0) or 0) for row in rows)
        return len(non_zero_days) == 1 and total_battles > 1000

    if _is_empty_activity(activity_rows) or _looks_like_cumulative_spike(activity_rows):
        return True

    return _is_stale_timestamp(updated_at, stale_after)


def refresh_player_detail_payloads(
    player: Player,
    *,
    force_refresh: bool = False,
    refresh_core: bool = True,
) -> None:
    realm = player.realm or DEFAULT_REALM

    if refresh_core and (force_refresh or player_detail_needs_refresh(player)):
        update_player_data(player, force_refresh=force_refresh)
        player.refresh_from_db()

    if player.is_hidden:
        return

    if force_refresh or player_battle_data_needs_refresh(player):
        update_battle_data(player.player_id, realm=realm)
        player.refresh_from_db()

    if force_refresh or player.activity_json is None or player_activity_data_needs_refresh(player):
        update_snapshot_data(player.player_id, realm=realm,
                             refresh_player=False)
        player.refresh_from_db()

    if force_refresh or player.tiers_json is None or _has_newer_source_timestamp(player.tiers_updated_at, player.battles_updated_at):
        update_tiers_data(player.player_id, realm=realm)
    if force_refresh or player.type_json is None or _has_newer_source_timestamp(player.type_updated_at, player.battles_updated_at):
        update_type_data(player.player_id, realm=realm)
    if force_refresh or player.randoms_json is None or _has_newer_source_timestamp(player.randoms_updated_at, player.battles_updated_at):
        update_randoms_data(player.player_id, realm=realm)
    if force_refresh or player_ranked_data_needs_refresh(player):
        update_ranked_data(player.player_id, realm=realm)

    player.refresh_from_db()
    if force_refresh or _player_explorer_summary_source_changed(player):
        refresh_player_explorer_summary(player)
        player.refresh_from_db()


def _player_explorer_summary_source_changed(player: Player) -> bool:
    explorer_summary = getattr(player, 'explorer_summary', None)
    if explorer_summary is None:
        return True

    return _has_newer_source_timestamp(
        explorer_summary.refreshed_at,
        player.last_fetch,
        player.battles_updated_at,
        player.activity_updated_at,
        player.ranked_updated_at,
        player.efficiency_updated_at,
        player.achievements_updated_at,
    )


def player_ranked_data_needs_refresh(
    player: Player,
    stale_after: timedelta = PLAYER_RANKED_DATA_STALE_AFTER,
) -> bool:
    return _timestamped_payload_needs_refresh(
        player.ranked_json,
        player.ranked_updated_at,
        stale_after,
    )


# Profile-render trigger threshold for the per-render ranked-observation
# refresh. Matches PLAYER_BATTLE_DATA_STALE_AFTER (random side) so both
# random and ranked refresh on the same 15-min cooldown when a user
# visits a profile — uniform "freshen on visit, but cap to one fetch
# per quarter-hour per player" behavior.
RANKED_OBSERVATION_RENDER_STALE_AFTER = timedelta(minutes=15)


def _ranked_observation_is_stale(
    player: Player,
    stale_after: timedelta = RANKED_OBSERVATION_RENDER_STALE_AFTER,
) -> bool:
    """Return True if the player's most recent BattleObservation is missing
    a ranked payload OR is older than `stale_after`.

    Drives the on-render dispatch: when this returns True, we enqueue a
    fresh 3-WG-call observation so the BattleHistoryCard's Ranked / All
    views reflect the player's latest state. Hidden players are excluded
    upstream by the surrounding `if not player.is_hidden:` guard.
    """
    from warships.models import BattleObservation

    latest = (
        BattleObservation.objects.filter(player=player)
        .order_by("-observed_at")
        .first()
    )
    if latest is None:
        return True
    if not latest.ranked_ships_stats_json:
        return True
    return (django_timezone.now() - latest.observed_at) >= stale_after


def clan_detail_needs_refresh(
    clan: Clan,
    stale_after: timedelta = CLAN_DETAIL_STALE_AFTER,
) -> bool:
    return _is_stale_timestamp(clan.last_fetch, stale_after)


def clan_members_missing_or_incomplete(clan: Clan, member_count: Optional[int] = None) -> bool:
    if not clan.members_count:
        return True
    if member_count is None:
        member_count = clan.player_set.exclude(name='').count()
    return member_count < clan.members_count


def clan_ranked_hydration_needs_refresh(
    player: Player,
    stale_after: timedelta = CLAN_RANKED_HYDRATION_STALE_AFTER,
) -> bool:
    ranked_updated_at = player.ranked_updated_at
    if ranked_updated_at is None:
        return True

    return django_timezone.now() - ranked_updated_at >= stale_after


def player_efficiency_needs_refresh(
    player: Player,
    stale_after: timedelta = PLAYER_EFFICIENCY_STALE_AFTER,
) -> bool:
    if player.is_hidden:
        return False

    return _timestamped_payload_needs_refresh(
        player.efficiency_json,
        player.efficiency_updated_at,
        stale_after,
    )


def _build_efficiency_badge_rows(raw_rows: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_rows, list):
        return []

    rows: list[dict[str, Any]] = []
    ship_ids: list[int] = []

    for row in raw_rows:
        if not isinstance(row, dict):
            continue

        try:
            ship_id = int(row.get('ship_id') or 0)
            badge_class = int(row.get('top_grade_class') or 0)
        except (TypeError, ValueError):
            continue

        if ship_id <= 0 or badge_class <= 0:
            continue

        ship_ids.append(ship_id)
        label = EFFICIENCY_BADGE_CLASS_LABELS.get(badge_class)
        rows.append({
            'ship_id': ship_id,
            'top_grade_class': badge_class,
            'top_grade_label': label,
            'badge_label': label,
        })

    ships_by_id = Ship.objects.in_bulk(
        ship_ids, field_name='ship_id') if ship_ids else {}
    for row in rows:
        ship = ships_by_id.get(row['ship_id'])
        if ship is None:
            continue

        row['ship_name'] = ship.name
        row['ship_chart_name'] = ship.chart_name
        row['ship_type'] = ship.ship_type
        row['ship_tier'] = ship.tier
        row['nation'] = ship.nation

    rows.sort(
        key=lambda row: (
            int(row.get('top_grade_class') or 99),
            int(row.get('ship_tier') or 99),
            str(row.get('ship_name') or ''),
            int(row.get('ship_id') or 0),
        )
    )
    return rows


def update_player_efficiency_data(player: Player, force_refresh: bool = False, realm: str = DEFAULT_REALM) -> list[dict[str, Any]]:
    if player.is_hidden:
        if player.efficiency_json is not None or player.efficiency_updated_at is not None:
            player.efficiency_json = None
            player.efficiency_updated_at = None
            player.save(update_fields=[
                        'efficiency_json', 'efficiency_updated_at'])
        return []

    if not force_refresh and not player_efficiency_needs_refresh(player):
        return player.efficiency_json or []

    if (player.pvp_battles or 0) <= 0:
        player.efficiency_json = []
        player.efficiency_updated_at = django_timezone.now()
        player.save(update_fields=['efficiency_json', 'efficiency_updated_at'])
        return []

    rows = _build_efficiency_badge_rows(
        _fetch_efficiency_badges_for_player(player.player_id, realm=realm)
    )
    player.efficiency_json = rows
    player.efficiency_updated_at = django_timezone.now()
    player.save(update_fields=['efficiency_json', 'efficiency_updated_at'])
    return rows


def player_achievements_need_refresh(
    player: Player,
    stale_after: timedelta = PLAYER_ACHIEVEMENTS_STALE_AFTER,
) -> bool:
    return _timestamped_payload_needs_refresh(
        player.achievements_json,
        player.achievements_updated_at,
        stale_after,
    )


def _stored_player_achievement_rows(player: Player) -> list[dict[str, Any]]:
    return list(player.achievement_stats.order_by('achievement_slug').values(
        'achievement_code',
        'achievement_slug',
        'achievement_label',
        'category',
        'count',
        'source_kind',
    ))


def _coerce_achievement_count(value: Any) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        return 0
    return count if count > 0 else 0


def _looks_like_combat_achievement_code(code: str) -> bool:
    lowered = str(code or '').casefold()
    if not lowered.startswith('pch'):
        return False

    excluded_markers = ('campaign', 'album', 'pve',
                        'twitch', 'dockyard', 'collection')
    return not any(marker in lowered for marker in excluded_markers)


def normalize_player_achievement_rows(raw_payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(raw_payload, dict):
        return []

    battle_map = raw_payload.get('battle')
    if not isinstance(battle_map, dict):
        return []

    rows: list[dict[str, Any]] = []
    unknown_combat_like_codes: list[str] = []

    for code, raw_count in battle_map.items():
        count = _coerce_achievement_count(raw_count)
        if count <= 0:
            continue

        entry = get_achievement_catalog_entry(code)
        if entry is None:
            if _looks_like_combat_achievement_code(code):
                unknown_combat_like_codes.append(code)
            continue

        if not entry.get('enabled_for_player_surface'):
            continue

        if entry.get('kind') != 'combat':
            continue

        rows.append({
            'achievement_code': str(entry['code']),
            'achievement_slug': str(entry['slug']),
            'achievement_label': str(entry['label']),
            'category': str(entry['category']),
            'count': count,
            'source_kind': 'battle',
        })

    if unknown_combat_like_codes:
        logging.info(
            'Ignoring unknown combat-like achievement codes for curated lane: %s',
            ', '.join(sorted(set(unknown_combat_like_codes))),
        )

    rows.sort(key=lambda row: (
        row['achievement_label'], row['achievement_code']))
    return rows


def update_achievements_data(player_id: int, force_refresh: bool = False, realm: str = DEFAULT_REALM) -> list[dict[str, Any]]:
    player = Player.objects.get(player_id=player_id, realm=realm)

    if player.is_hidden:
        logging.info(
            'Skipping achievements refresh for hidden player_id=%s; retaining stored data.',
            player.player_id,
        )
        return _stored_player_achievement_rows(player)

    if not force_refresh and not player_achievements_need_refresh(player):
        return _stored_player_achievement_rows(player)

    raw_payload = _fetch_player_achievements(player.player_id, realm=realm)
    if raw_payload is None:
        logging.info(
            'Skipping achievements refresh because upstream returned no payload for player_id=%s',
            player.player_id,
        )
        return _stored_player_achievement_rows(player)

    normalized_rows = normalize_player_achievement_rows(raw_payload)
    refreshed_at = django_timezone.now()

    with transaction.atomic():
        player.achievements_json = raw_payload
        player.achievements_updated_at = refreshed_at
        player.save(update_fields=[
                    'achievements_json', 'achievements_updated_at'])

        PlayerAchievementStat.objects.filter(player=player).delete()
        PlayerAchievementStat.objects.bulk_create([
            PlayerAchievementStat(
                player=player,
                achievement_code=row['achievement_code'],
                achievement_slug=row['achievement_slug'],
                achievement_label=row['achievement_label'],
                category=row['category'],
                count=row['count'],
                source_kind=row['source_kind'],
                refreshed_at=refreshed_at,
            )
            for row in normalized_rows
        ])

    return normalized_rows


def queue_clan_ranked_hydration(players: Iterable[Player], realm: str = DEFAULT_REALM) -> dict[str, Any]:
    from warships.tasks import is_ranked_data_refresh_pending, queue_ranked_data_refresh

    return _queue_limited_player_hydration(
        players,
        should_refresh=clan_ranked_hydration_needs_refresh,
        is_refresh_pending=is_ranked_data_refresh_pending,
        enqueue_refresh=lambda player_id: queue_ranked_data_refresh(
            player_id, realm=realm),
        max_in_flight=CLAN_RANKED_HYDRATION_MAX_IN_FLIGHT,
    )


def clan_battle_summary_is_stale(player: Player) -> bool:
    """Return True if the player's clan battle summary needs a refresh."""
    summary = getattr(player, 'explorer_summary', None)
    if summary is None:
        return True
    updated_at = summary.clan_battle_summary_updated_at
    if updated_at is None:
        return True
    if summary.clan_battle_current_season_id is None:
        # Pre-0081 rows (or a persist that ran while the current season was
        # unresolvable): the current-season fields behind the CB shield were
        # never computed. Treating this as stale lets the existing clan-view
        # hydration machinery backfill organically — bounded to
        # CLAN_BATTLE_PLAYER_HYDRATION_MAX_IN_FLIGHT per view.
        return True
    return (django_timezone.now() - updated_at).days >= CLAN_BATTLE_SUMMARY_STALE_DAYS


def maybe_refresh_clan_battle_data(player: Player, realm: str = DEFAULT_REALM) -> None:
    """Enqueue a background CB refresh if the player's summary is stale."""
    from warships.tasks import queue_clan_battle_data_refresh
    if player.is_hidden:
        return
    if not clan_battle_summary_is_stale(player):
        return
    queue_clan_battle_data_refresh(player.player_id, realm=realm)


def queue_clan_efficiency_hydration(players: Iterable[Player], realm: str = DEFAULT_REALM) -> dict[str, Any]:
    from warships.tasks import is_efficiency_data_refresh_pending, is_efficiency_rank_snapshot_refresh_pending, queue_efficiency_data_refresh, queue_efficiency_rank_snapshot_refresh

    players = list(players)
    eligible_player_ids = {
        player.player_id for player in players if player_efficiency_needs_refresh(player)
    }
    publication_stale_players = [
        player for player in players
        if player.player_id not in eligible_player_ids
        and efficiency_rank_publication_needs_refresh(player)
    ]
    hydration_state = _queue_limited_player_hydration(
        players,
        should_refresh=player_efficiency_needs_refresh,
        is_refresh_pending=is_efficiency_data_refresh_pending,
        enqueue_refresh=lambda player_id: queue_efficiency_data_refresh(
            player_id, realm=realm),
        max_in_flight=CLAN_EFFICIENCY_HYDRATION_MAX_IN_FLIGHT,
    )

    # Publication-stale players have fresh efficiency *data* but a stale rank
    # *snapshot*.  The snapshot is a single global background task that can sit
    # behind a long queue.  Enqueue it, but do NOT add these players to
    # pending_player_ids — they should not block the client poll loop or show
    # as "pending" in the UI.
    if publication_stale_players and not is_efficiency_rank_snapshot_refresh_pending():
        queue_efficiency_rank_snapshot_refresh(realm=realm)

    return hydration_state


def _ranked_rows_have_top_ship(rows: Any) -> bool:
    normalized_rows = _coerce_ranked_rows(rows)
    return all('top_ship_name' in row for row in normalized_rows)


def _extract_ranked_ship_battles(season_stats: Any) -> int:
    if not isinstance(season_stats, dict):
        return 0

    if 'battles' in season_stats:
        return int(season_stats.get('battles', 0) or 0)

    total_battles = 0
    for mode_key in ('rank_solo', 'rank_div2', 'rank_div3'):
        mode_stats = season_stats.get(mode_key)
        if not isinstance(mode_stats, dict):
            continue
        total_battles += int(mode_stats.get('battles', 0) or 0)

    if total_battles > 0:
        return total_battles

    # WG API nests stats under sprint keys ("0", "1", …); sum across sprints.
    for key, sprint_stats in season_stats.items():
        if not isinstance(sprint_stats, dict):
            continue
        for mode_key in ('rank_solo', 'rank_div2', 'rank_div3'):
            mode_stats = sprint_stats.get(mode_key)
            if not isinstance(mode_stats, dict):
                continue
            total_battles += int(mode_stats.get('battles', 0) or 0)

    return total_battles


def _build_top_ranked_ship_names_by_season(
    ranked_ship_stats_rows: Any,
    requested_season_ids: list[int],
) -> dict[int, Optional[str]]:
    if not isinstance(ranked_ship_stats_rows, list):
        return {}

    top_ship_ids_by_season: dict[int, int] = {}
    top_ship_battles_by_season: dict[int, int] = {}

    for row in ranked_ship_stats_rows:
        if not isinstance(row, dict):
            continue

        ship_id = row.get('ship_id')
        try:
            ship_id_int = int(ship_id)
        except (TypeError, ValueError):
            continue

        seasons_payload = row.get('seasons')
        if isinstance(seasons_payload, dict):
            season_items = seasons_payload.items()
        elif len(requested_season_ids) == 1:
            season_items = [(requested_season_ids[0], row)]
        else:
            continue

        for season_id_raw, season_stats in season_items:
            try:
                season_id = int(season_id_raw)
            except (TypeError, ValueError):
                continue

            battles = _extract_ranked_ship_battles(season_stats)
            if battles <= 0:
                continue

            current_best_battles = top_ship_battles_by_season.get(
                season_id, -1)
            current_best_ship_id = top_ship_ids_by_season.get(season_id)
            if battles > current_best_battles or (battles == current_best_battles and (current_best_ship_id is None or ship_id_int < current_best_ship_id)):
                top_ship_battles_by_season[season_id] = battles
                top_ship_ids_by_season[season_id] = ship_id_int

    ship_names_by_id: dict[int, Optional[str]] = {}
    for ship_id in set(top_ship_ids_by_season.values()):
        ship = _fetch_ship_info(str(ship_id))
        ship_names_by_id[ship_id] = ship.name if ship else None

    return {
        season_id: ship_names_by_id.get(ship_id)
        for season_id, ship_id in top_ship_ids_by_season.items()
    }


def _calculate_activity_trend_direction(activity_rows: list[dict]) -> str:
    if not activity_rows:
        return 'flat'

    midpoint = len(activity_rows) // 2
    earlier_rows = activity_rows[:midpoint]
    later_rows = activity_rows[midpoint:]

    earlier_total = sum(int(row.get('battles', 0) or 0)
                        for row in earlier_rows)
    later_total = sum(int(row.get('battles', 0) or 0) for row in later_rows)

    if earlier_total == 0 and later_total == 0:
        return 'flat'

    threshold = max(3, int(max(earlier_total, later_total) * 0.15))
    delta = later_total - earlier_total
    if delta > threshold:
        return 'up'
    if delta < -threshold:
        return 'down'
    return 'flat'


def _build_efficiency_rank_inputs(
    player: Player,
    battle_rows: Any = None,
    efficiency_rows: Any = None,
) -> dict[str, Any]:
    if player.is_hidden:
        return {
            'eligible_ship_count': None,
            'efficiency_badge_rows_total': None,
            'badge_rows_unmapped': None,
            'expert_count': None,
            'grade_i_count': None,
            'grade_ii_count': None,
            'grade_iii_count': None,
            'raw_badge_points': None,
            'normalized_badge_strength': None,
        }

    normalized_efficiency_rows = _coerce_efficiency_rows(
        player.efficiency_json if efficiency_rows is None else efficiency_rows)
    badge_counts: Counter[int] = Counter()
    badge_rows_total = 0
    badge_rows_unmapped = 0

    for row in normalized_efficiency_rows:
        try:
            badge_class = int(row.get('top_grade_class') or 0)
        except (TypeError, ValueError):
            continue

        if badge_class not in EFFICIENCY_BADGE_CLASS_POINTS:
            continue

        badge_rows_total += 1

        try:
            ship_tier = int(row.get('ship_tier'))
        except (TypeError, ValueError):
            badge_rows_unmapped += 1
            continue

        if ship_tier < 5:
            continue

        badge_counts[badge_class] += 1

    eligible_ship_count = sum(badge_counts.values())

    raw_badge_points = sum(
        EFFICIENCY_BADGE_CLASS_POINTS[badge_class] * count
        for badge_class, count in badge_counts.items()
    )

    normalized_badge_strength = None
    if eligible_ship_count > 0:
        normalized_badge_strength = round(
            raw_badge_points / (eligible_ship_count *
                                EFFICIENCY_RANK_MAX_BADGE_POINTS_PER_SHIP),
            6,
        )

    return {
        'eligible_ship_count': eligible_ship_count,
        'efficiency_badge_rows_total': badge_rows_total,
        'badge_rows_unmapped': badge_rows_unmapped,
        'expert_count': badge_counts.get(1, 0),
        'grade_i_count': badge_counts.get(2, 0),
        'grade_ii_count': badge_counts.get(3, 0),
        'grade_iii_count': badge_counts.get(4, 0),
        'raw_badge_points': raw_badge_points,
        'normalized_badge_strength': normalized_badge_strength,
    }


def _efficiency_rank_inputs_match_summary(player: Player, explorer_summary: PlayerExplorerSummary) -> bool:
    expected_inputs = _build_efficiency_rank_inputs(player)

    return (
        explorer_summary.eligible_ship_count == expected_inputs['eligible_ship_count'] and
        explorer_summary.efficiency_badge_rows_total == expected_inputs['efficiency_badge_rows_total'] and
        explorer_summary.badge_rows_unmapped == expected_inputs['badge_rows_unmapped'] and
        explorer_summary.expert_count == expected_inputs['expert_count'] and
        explorer_summary.grade_i_count == expected_inputs['grade_i_count'] and
        explorer_summary.grade_ii_count == expected_inputs['grade_ii_count'] and
        explorer_summary.grade_iii_count == expected_inputs['grade_iii_count'] and
        explorer_summary.raw_badge_points == expected_inputs['raw_badge_points'] and
        explorer_summary.normalized_badge_strength == expected_inputs['normalized_badge_strength']
    )


def _efficiency_rank_snapshot_is_fresh(
    player: Player,
    explorer_summary: Optional[PlayerExplorerSummary],
    stale_after: timedelta = EFFICIENCY_RANK_SNAPSHOT_STALE_AFTER,
) -> bool:
    if player.is_hidden or explorer_summary is None:
        return False

    updated_at = explorer_summary.efficiency_rank_updated_at
    if updated_at is None:
        return False

    if django_timezone.now() - updated_at >= stale_after:
        return False

    latest_input_updated_at = max(
        (timestamp for timestamp in (player.efficiency_updated_at,
         player.battles_updated_at) if timestamp is not None),
        default=None,
    )
    if latest_input_updated_at is not None and latest_input_updated_at > updated_at:
        return False

    return True


def get_published_efficiency_rank_payload(player: Player) -> dict[str, Any]:
    # Serve the last-known snapshot whenever one exists. We intentionally do
    # NOT call _efficiency_rank_snapshot_is_fresh() here because that helper
    # also returns False when the player's efficiency_updated_at or
    # battles_updated_at advance past efficiency_rank_updated_at — which is the
    # right signal for "schedule a re-rank" (efficiency_rank_publication_needs_refresh)
    # but the wrong signal for "blank the icon in the UI". Doing so causes
    # sigma/efficiency-rank icons to flicker off across a clan members list
    # whenever background hydration bumps any input timestamp, until the next
    # refresh_efficiency_rank_snapshot pass restores them.
    explorer_summary = getattr(player, 'explorer_summary', None)
    if (
        player.is_hidden
        or explorer_summary is None
        or explorer_summary.efficiency_rank_updated_at is None
    ):
        return {
            'efficiency_rank_percentile': None,
            'efficiency_rank_tier': None,
            'has_efficiency_rank_icon': False,
            'efficiency_rank_population_size': None,
            'efficiency_rank_updated_at': None,
        }

    return {
        'efficiency_rank_percentile': explorer_summary.efficiency_rank_percentile,
        'efficiency_rank_tier': explorer_summary.efficiency_rank_tier,
        'has_efficiency_rank_icon': bool(explorer_summary.has_efficiency_rank_icon),
        'efficiency_rank_population_size': explorer_summary.efficiency_rank_population_size,
        'efficiency_rank_updated_at': explorer_summary.efficiency_rank_updated_at,
    }


def efficiency_rank_publication_needs_refresh(player: Player) -> bool:
    if player.is_hidden:
        return False

    explorer_summary = getattr(player, 'explorer_summary', None)
    if explorer_summary is None:
        return False

    if _efficiency_rank_eligibility_reason(player, explorer_summary) is not None:
        return False

    return not _efficiency_rank_snapshot_is_fresh(player, explorer_summary)


def _efficiency_rank_eligibility_reason(player: Player, explorer_summary: PlayerExplorerSummary) -> Optional[str]:
    total_badge_rows = int(explorer_summary.efficiency_badge_rows_total or 0)
    badge_rows_unmapped = int(explorer_summary.badge_rows_unmapped or 0)

    if player.is_hidden:
        return 'hidden'
    if (player.pvp_battles or 0) < EFFICIENCY_RANK_MIN_PVP_BATTLES:
        return 'low_pvp_battles'
    if explorer_summary.eligible_ship_count is None:
        return 'missing_denominator'
    if (explorer_summary.eligible_ship_count or 0) < EFFICIENCY_RANK_MIN_ELIGIBLE_SHIPS:
        return 'too_few_eligible_ships'
    if total_badge_rows <= 0:
        return 'no_badge_rows'
    if explorer_summary.normalized_badge_strength is None:
        return 'missing_strength'
    if total_badge_rows > 0 and (badge_rows_unmapped / total_badge_rows) > EFFICIENCY_RANK_UNMAPPED_SHARE_LIMIT:
        return 'unmapped_badge_gate'

    return None


def _interpolate_quantile(sorted_values: list[float], percentile: float) -> Optional[float]:
    if not sorted_values:
        return None

    if len(sorted_values) == 1:
        return round(sorted_values[0], 6)

    position = min(max(percentile, 0.0), 1.0) * (len(sorted_values) - 1)
    lower_index = int(math.floor(position))
    upper_index = int(math.ceil(position))
    lower_value = sorted_values[lower_index]
    upper_value = sorted_values[upper_index]
    if lower_index == upper_index:
        return round(lower_value, 6)

    fraction = position - lower_index
    return round(lower_value + ((upper_value - lower_value) * fraction), 6)


def recompute_efficiency_rank_snapshot(
    *,
    player_limit: int = 0,
    skip_refresh: bool = False,
    publish_partial: bool = False,
    realm: str = DEFAULT_REALM,
) -> dict[str, Any]:
    refresh_queryset = Player.objects.filter(realm=realm).order_by('id')
    if player_limit > 0:
        refresh_queryset = refresh_queryset[:player_limit]

    if not skip_refresh:
        for player in refresh_queryset.iterator(chunk_size=100):
            refresh_player_explorer_summary(player)

    return _recompute_efficiency_rank_snapshot_sql(
        player_limit=player_limit,
        publish_partial=publish_partial,
    )


def _recompute_efficiency_rank_snapshot_sql(
    *,
    player_limit: int = 0,
    publish_partial: bool = False,
) -> dict[str, Any]:
    """SQL-accelerated efficiency rank computation.

    Pushes the ranking computation (shrinkage estimator, percentile,
    tier assignment) into PostgreSQL using window functions instead of
    iterating 275K+ rows in Python.

    Optimized to use a single UPDATE with LEFT JOIN that sets eligible
    rows to computed values and non-eligible rows to NULL in one pass,
    avoiding the expensive NOT IN subquery pattern.
    """
    shrinkage_k = EFFICIENCY_RANK_SHRINKAGE_K
    min_pvp = EFFICIENCY_RANK_MIN_PVP_BATTLES
    min_ships = EFFICIENCY_RANK_MIN_ELIGIBLE_SHIPS
    unmapped_limit = EFFICIENCY_RANK_UNMAPPED_SHARE_LIMIT
    expert_pct = EFFICIENCY_RANK_EXPERT_PERCENTILE
    grade_i_pct = EFFICIENCY_RANK_GRADE_I_PERCENTILE
    grade_ii_pct = EFFICIENCY_RANK_GRADE_II_PERCENTILE
    grade_iii_pct = EFFICIENCY_RANK_MIN_VISIBLE_PERCENTILE

    limit_clause = f'LIMIT {int(player_limit)}' if player_limit > 0 else ''
    player_scope_cte = f"""
        player_scope AS (
            SELECT id FROM warships_player ORDER BY id {limit_clause}
        ),
    """ if player_limit > 0 else ''
    player_scope_join = (
        'JOIN player_scope ps ON ps.id = p.id'
        if player_limit > 0 else ''
    )

    # Step 1: Compute field_mean_strength, population_size, and suppressed
    # counts in a single table scan.
    stats_sql = """
        SELECT
            COUNT(*) FILTER (WHERE eligible) AS pop,
            COALESCE(AVG(nbs) FILTER (WHERE eligible), 0) AS mean,
            COUNT(*) FILTER (WHERE has_summary AND NOT eligible
                AND COALESCE(pvp_battles, 0) < %s) AS low_battles,
            COUNT(*) FILTER (WHERE has_summary AND NOT eligible
                AND COALESCE(pvp_battles, 0) >= %s
                AND COALESCE(esc, 0) < %s) AS low_ships,
            COUNT(*) FILTER (WHERE has_summary AND NOT eligible
                AND COALESCE(pvp_battles, 0) >= %s
                AND COALESCE(esc, 0) >= %s
                AND COALESCE(ebrt, 0) > 0
                AND nbs IS NOT NULL
                AND (COALESCE(bru, 0)::float / ebrt) > %s) AS unmapped_badge_gate
        FROM (
            SELECT
                es.id IS NOT NULL AS has_summary,
                es.normalized_badge_strength AS nbs,
                es.eligible_ship_count AS esc,
                es.efficiency_badge_rows_total AS ebrt,
                es.badge_rows_unmapped AS bru,
                p.pvp_battles,
                (p.is_hidden = false
                 AND COALESCE(p.pvp_battles, 0) >= %s
                 AND es.eligible_ship_count IS NOT NULL
                 AND COALESCE(es.eligible_ship_count, 0) >= %s
                 AND COALESCE(es.efficiency_badge_rows_total, 0) > 0
                 AND es.normalized_badge_strength IS NOT NULL
                 AND (COALESCE(es.badge_rows_unmapped, 0)::float
                      / es.efficiency_badge_rows_total) <= %s
                ) AS eligible
            FROM warships_player p
            LEFT JOIN warships_playerexplorersummary es ON es.player_id = p.id
            WHERE p.is_hidden = false
        ) sub
    """

    with connection.cursor() as cursor:
        cursor.execute(stats_sql, [
            min_pvp, min_pvp, min_ships,
            min_pvp, min_ships, unmapped_limit,
            min_pvp, min_ships, unmapped_limit,
        ])
        row = cursor.fetchone()
        population_size = row[0]
        field_mean_strength = round(
            float(row[1]), 6) if population_size else 0.0
        suppressed_counts = {}
        if row[2]:
            suppressed_counts['low_battles'] = row[2]
        if row[3]:
            suppressed_counts['low_ships'] = row[3]
        if row[4]:
            suppressed_counts['unmapped_badge_gate'] = row[4]

    if population_size == 0:
        return {
            'publish_applied': False,
            'partial_population': player_limit > 0,
            'population_size': 0,
            'qualifying_count': 0,
            'qualifying_share': 0.0,
            'eligibility_basis': {
                'denominator_source': 'mapped_tier_v_plus_efficiency_badge_rows',
                'min_pvp_battles': min_pvp,
                'min_mapped_badge_rows': min_ships,
                'unmapped_share_limit': unmapped_limit,
            },
            'field_mean_strength': 0.0,
            'tier_thresholds': {
                'III': grade_iii_pct, 'II': grade_ii_pct,
                'I': grade_i_pct, 'E': expert_pct,
            },
            'tier_counts': {},
            'suppressed_counts': suppressed_counts,
            'distribution': {'p50': None, 'p67': None, 'p75': None, 'p90': None},
        }

    snapshot_updated_at = django_timezone.now()
    publish_applied = player_limit <= 0 or publish_partial

    if publish_applied:
        # Two-step approach:
        # Step 2a: UPDATE eligible rows with computed rank values (~55K rows)
        # Step 2b: Clear stale ranks — only rows that previously had a rank
        #          but weren't touched in Step 2a (uses timestamp, not NOT IN)
        #
        # PERCENT_RANK() gives (rank-1)/(count-1) with 0 at top,
        # but our percentile is (pop-rank)/(pop-1) with 1 at top.
        # So: our_percentile = 1.0 - PERCENT_RANK().
        update_sql = f"""
            WITH {player_scope_cte if player_scope_cte else ''}
            eligible AS (
                SELECT
                    es.id AS summary_id,
                    es.eligible_ship_count,
                    es.normalized_badge_strength,
                    ROUND((
                        (es.eligible_ship_count::float / (es.eligible_ship_count + %s))
                        * es.normalized_badge_strength
                        + (1.0 - es.eligible_ship_count::float / (es.eligible_ship_count + %s))
                        * %s
                    )::numeric, 6) AS shrunken_strength
                FROM warships_playerexplorersummary es
                JOIN warships_player p ON p.id = es.player_id
                {player_scope_join}
                WHERE p.is_hidden = false
                  AND COALESCE(p.pvp_battles, 0) >= %s
                  AND es.eligible_ship_count IS NOT NULL
                  AND COALESCE(es.eligible_ship_count, 0) >= %s
                  AND COALESCE(es.efficiency_badge_rows_total, 0) > 0
                  AND es.normalized_badge_strength IS NOT NULL
                  AND (COALESCE(es.badge_rows_unmapped, 0)::float
                       / es.efficiency_badge_rows_total) <= %s
            ),
            ranked AS (
                SELECT
                    summary_id,
                    shrunken_strength,
                    ROUND((1.0 - PERCENT_RANK() OVER (
                        ORDER BY shrunken_strength DESC
                    ))::numeric, 6) AS percentile
                FROM eligible
            )
            UPDATE warships_playerexplorersummary SET
                shrunken_efficiency_strength = ranked.shrunken_strength,
                efficiency_rank_percentile = ranked.percentile,
                efficiency_rank_tier = CASE
                    WHEN ranked.percentile >= %s THEN 'E'
                    WHEN ranked.percentile >= %s THEN 'I'
                    WHEN ranked.percentile >= %s THEN 'II'
                    WHEN ranked.percentile >= %s THEN 'III'
                    ELSE NULL
                END,
                has_efficiency_rank_icon = (ranked.percentile >= %s),
                efficiency_rank_population_size = %s,
                efficiency_rank_updated_at = %s
            FROM ranked
            WHERE warships_playerexplorersummary.id = ranked.summary_id
        """

        # Clear stale ranks: rows that previously had a rank but were not
        # updated in the step above. Uses the timestamp to identify stale
        # rows — much faster than NOT IN over 275K rows.
        scope_where = (
            'AND player_id IN (SELECT id FROM player_scope)'
            if player_limit > 0 else ''
        )
        clear_sql = f"""
            UPDATE warships_playerexplorersummary SET
                shrunken_efficiency_strength = NULL,
                efficiency_rank_percentile = NULL,
                efficiency_rank_tier = NULL,
                has_efficiency_rank_icon = false,
                efficiency_rank_population_size = NULL,
                efficiency_rank_updated_at = %s
            WHERE efficiency_rank_tier IS NOT NULL
              AND efficiency_rank_updated_at != %s
            {scope_where}
        """

        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(update_sql, [
                    shrinkage_k, shrinkage_k, field_mean_strength,
                    min_pvp, min_ships, unmapped_limit,
                    expert_pct, grade_i_pct, grade_ii_pct, grade_iii_pct,
                    grade_iii_pct,
                    population_size, snapshot_updated_at,
                ])

                cursor.execute(clear_sql, [
                    snapshot_updated_at, snapshot_updated_at,
                ])

    # Gather tier counts and distribution stats for the return value.
    tier_sql = """
        SELECT efficiency_rank_tier, COUNT(*)
        FROM warships_playerexplorersummary
        WHERE efficiency_rank_tier IS NOT NULL
          AND efficiency_rank_updated_at = %s
        GROUP BY efficiency_rank_tier
    """
    dist_sql = """
        SELECT shrunken_efficiency_strength
        FROM warships_playerexplorersummary
        WHERE shrunken_efficiency_strength IS NOT NULL
          AND efficiency_rank_updated_at = %s
        ORDER BY shrunken_efficiency_strength
    """
    with connection.cursor() as cursor:
        cursor.execute(tier_sql, [snapshot_updated_at])
        tier_counts = {row[0]: row[1] for row in cursor.fetchall()}
        qualifying_count = sum(tier_counts.values())

        cursor.execute(dist_sql, [snapshot_updated_at])
        sorted_strengths = [float(row[0]) for row in cursor.fetchall()]

    return {
        'status': 'completed',
        'snapshot_updated_at': snapshot_updated_at,
        'publish_applied': publish_applied,
        'partial_population': player_limit > 0,
        'population_size': population_size,
        'qualifying_count': qualifying_count,
        'qualifying_share': round(qualifying_count / population_size, 6) if population_size else 0.0,
        'eligibility_basis': {
            'denominator_source': 'mapped_tier_v_plus_efficiency_badge_rows',
            'min_pvp_battles': min_pvp,
            'min_mapped_badge_rows': min_ships,
            'unmapped_share_limit': unmapped_limit,
        },
        'field_mean_strength': field_mean_strength,
        'tier_thresholds': {
            'III': grade_iii_pct,
            'II': grade_ii_pct,
            'I': grade_i_pct,
            'E': expert_pct,
        },
        'tier_counts': dict(sorted(tier_counts.items())),
        'suppressed_counts': suppressed_counts,
        'distribution': {
            'p50': _interpolate_quantile(sorted_strengths, 0.50),
            'p67': _interpolate_quantile(sorted_strengths, 0.67),
            'p75': _interpolate_quantile(sorted_strengths, 0.75),
            'p90': _interpolate_quantile(sorted_strengths, 0.90),
        },
    }


def _kill_ratio_tier_weight(ship_tier: Any) -> float:
    try:
        tier = int(ship_tier)
    except (TypeError, ValueError):
        return KILL_RATIO_MID_TIER_WEIGHT

    if 1 <= tier <= 4:
        return KILL_RATIO_LOW_TIER_WEIGHT
    if 5 <= tier <= 7:
        return KILL_RATIO_MID_TIER_WEIGHT
    return KILL_RATIO_HIGH_TIER_WEIGHT


def _player_score_tier_weight(ship_tier: Any) -> float:
    try:
        tier = int(ship_tier)
    except (TypeError, ValueError):
        return PLAYER_SCORE_MID_TIER_WEIGHT

    if 1 <= tier <= 4:
        return PLAYER_SCORE_LOW_TIER_WEIGHT
    if 5 <= tier <= 7:
        return PLAYER_SCORE_MID_TIER_WEIGHT
    return PLAYER_SCORE_HIGH_TIER_WEIGHT


def _extract_row_kill_rate(row: dict, battles: int) -> Optional[float]:
    if battles <= 0:
        return None

    frags = row.get('frags')
    if frags is not None:
        return float(frags or 0) / battles

    if row.get('kdr') is None:
        return None

    return float(row.get('kdr') or 0)


def _calculate_actual_kdr(
    pvp_battles: int | None,
    pvp_frags: int | None,
    pvp_survived_battles: int | None,
) -> tuple[int, Optional[float]]:
    battles = max(int(pvp_battles or 0), 0)
    frags = max(int(pvp_frags or 0), 0)
    survived_battles = max(int(pvp_survived_battles or 0), 0)

    if battles <= 0:
        return 0, None

    deaths = max(battles - min(survived_battles, battles), 0)
    if deaths <= 0:
        return deaths, None

    return deaths, round(frags / deaths, 2)


def _calculate_player_kill_ratio(battle_rows: list[dict]) -> Optional[float]:
    weighted_sum = 0.0
    total_weight = 0.0
    has_battle_volume = False

    for row in battle_rows:
        battles = int(row.get('pvp_battles', 0) or 0)
        if battles <= 0:
            continue

        has_battle_volume = True
        observed_kill_rate = _extract_row_kill_rate(row, battles)
        if observed_kill_rate is None:
            continue

        smoothed_kill_rate = (
            (observed_kill_rate * battles) +
            (KILL_RATIO_PRIOR * KILL_RATIO_SMOOTHING_BATTLES)
        ) / (battles + KILL_RATIO_SMOOTHING_BATTLES)
        ship_weight = math.sqrt(battles) * \
            _kill_ratio_tier_weight(row.get('ship_tier'))
        weighted_sum += smoothed_kill_rate * ship_weight
        total_weight += ship_weight

    if total_weight <= 0:
        if has_battle_volume:
            return 0.0
        return None

    return round(weighted_sum / total_weight, 2)


def calculate_tier_filtered_pvp_record(
    battles_rows: Any,
    minimum_tier: int = 5,
) -> tuple[int, Optional[float]]:
    total_battles = 0
    total_wins = 0.0

    for row in _coerce_battle_rows(battles_rows):
        try:
            ship_tier = int(row.get('ship_tier') or 0)
        except (TypeError, ValueError):
            continue

        if ship_tier < minimum_tier:
            continue

        battles = int(row.get('pvp_battles', 0) or 0)
        if battles <= 0:
            continue

        wins = row.get('wins')
        if wins is None and row.get('win_ratio') is not None:
            wins = float(row.get('win_ratio') or 0.0) * battles

        total_battles += battles
        total_wins += float(wins or 0.0)

    if total_battles <= 0:
        return 0, None

    return total_battles, round((total_wins / total_battles) * 100, 2)


def calculate_pve_battle_count(total_battles: Optional[int], pvp_battles: Optional[int]) -> int:
    total = max(int(total_battles or 0), 0)
    pvp = max(int(pvp_battles or 0), 0)
    return max(total - pvp, 0)


def calculate_pve_share_total(total_battles: Optional[int], pvp_battles: Optional[int]) -> float:
    total = max(int(total_battles or 0), 0)
    if total <= 0:
        return 0.0

    return calculate_pve_battle_count(total, pvp_battles) / total


def is_pve_player(total_battles: Optional[int], pvp_battles: Optional[int]) -> bool:
    total = max(int(total_battles or 0), 0)
    pve = calculate_pve_battle_count(total, pvp_battles)
    pve_share_total = calculate_pve_share_total(total, pvp_battles)
    return total > 500 and pve >= 1500 and pve_share_total >= 0.30


def is_sleepy_player(days_since_last_battle: Optional[int], minimum_days: int = SLEEPY_PLAYER_DAYS_THRESHOLD) -> bool:
    if days_since_last_battle is None:
        return False

    return int(days_since_last_battle) > minimum_days


def is_ranked_player(ranked_rows: Any, minimum_ranked_battles: int = 100) -> bool:
    total_battles, _win_rate = _calculate_ranked_record(ranked_rows)
    return total_battles > minimum_ranked_battles


def summarize_clan_battle_seasons(season_rows: Any) -> dict[str, Any]:
    total_battles = 0
    total_wins = 0
    seasons_participated = 0

    for row in season_rows or []:
        battles = int(row.get('battles', 0) or 0)
        if battles <= 0:
            continue

        total_battles += battles
        total_wins += int(row.get('wins', 0) or 0)
        seasons_participated += 1

    return {
        'seasons_participated': seasons_participated,
        'total_battles': total_battles,
        'win_rate': round((total_wins / total_battles) * 100, 1) if total_battles > 0 else None,
    }


def summarize_current_clan_battle_season(
    season_rows: Any,
    current_season_id: Optional[int],
) -> dict[str, Any]:
    """Extract the player's current-season CB row for persistence.

    Returns all-None when the current season is unresolvable (empty
    ClanBattleSeason reference) — the stale check treats that row as
    never-computed so it retries once the reference is seeded. A resolvable
    season the player sat out persists battles=0 / win_rate=None, which is a
    real (non-stale) answer.
    """
    if current_season_id is None:
        return {'season_id': None, 'battles': None, 'win_rate': None}

    battles = 0
    wins = 0
    for row in season_rows or []:
        try:
            sid = int(row.get('season_id', 0) or 0)
        except (TypeError, ValueError):
            continue
        if sid != int(current_season_id):
            continue
        battles = int(row.get('battles', 0) or 0)
        wins = int(row.get('wins', 0) or 0)
        break

    return {
        'season_id': int(current_season_id),
        'battles': battles,
        'win_rate': round((wins / battles) * 100, 1) if battles > 0 else None,
    }


def get_published_clan_battle_summary_payload(
    player: Optional[Player],
) -> dict[str, Any]:
    payload = {
        'seasons_participated': 0,
        'total_battles': 0,
        'win_rate': None,
        'updated_at': None,
    }
    if player is None or player.is_hidden:
        return payload

    explorer_summary = getattr(player, 'explorer_summary', None)
    if explorer_summary is None:
        return payload

    has_durable_summary = (
        explorer_summary.clan_battle_summary_updated_at is not None
        or explorer_summary.clan_battle_total_battles is not None
        or explorer_summary.clan_battle_seasons_participated is not None
        or explorer_summary.clan_battle_overall_win_rate is not None
    )
    if not has_durable_summary:
        return payload

    return {
        'seasons_participated': int(explorer_summary.clan_battle_seasons_participated or 0),
        'total_battles': int(explorer_summary.clan_battle_total_battles or 0),
        'win_rate': explorer_summary.clan_battle_overall_win_rate,
        'updated_at': explorer_summary.clan_battle_summary_updated_at,
    }


def is_clan_battle_enjoyer(
    total_battles: Optional[int],
    seasons_participated: Optional[int],
    minimum_battles: int = CLAN_BATTLE_ENJOYER_MIN_BATTLES,
    minimum_seasons: int = CLAN_BATTLE_ENJOYER_MIN_SEASONS,
) -> bool:
    return int(total_battles or 0) >= minimum_battles and int(seasons_participated or 0) >= minimum_seasons


def is_current_season_clan_battle_player(
    explorer_summary: Any,
    current_season_id: Optional[int],
) -> bool:
    """ClanBattleShieldIcon gate: any battles recorded in the current CB season.

    Replaces the career `is_clan_battle_enjoyer` (40 battles / 2 seasons) in
    the icon path — the shield now marks active participation in the current
    clan-battle season. The stored season id is double-checked against the
    live current season, so a row persisted during a finished season stops
    qualifying at rollover without a write. The Clan Battles tab
    (`clan_battle_header_eligible`) opens on career 40/2 OR this criteria,
    so shield wearers always get the tab. Runbook:
    `agents/runbooks/runbook-cb-icon-current-season-2026-07-15.md`.
    """
    if explorer_summary is None or current_season_id is None:
        return False

    stored_season_id = explorer_summary.clan_battle_current_season_id
    if stored_season_id is None or int(stored_season_id) != int(current_season_id):
        return False

    return int(explorer_summary.clan_battle_current_season_battles or 0) > 0


def get_current_season_clan_battle_win_rate(
    explorer_summary: Any,
    current_season_id: Optional[int],
) -> Optional[float]:
    """Current-season CB win rate for the shield's tint, or None."""
    if not is_current_season_clan_battle_player(explorer_summary, current_season_id):
        return None

    return explorer_summary.clan_battle_current_season_win_rate


def _ranked_row_league(row: dict) -> Optional[int]:
    """Normalize a ranked_json row's league to 1..3 (1=Gold), or None."""
    league_value = row.get('highest_league')
    try:
        league = int(league_value)
    except (TypeError, ValueError):
        league_name = str(row.get('highest_league_name')
                          or '').strip().lower()
        league = {
            'gold': 1,
            'silver': 2,
            'bronze': 3,
        }.get(league_name)

    if league is None or league < 1 or league > 3:
        return None

    return league


def get_highest_ranked_league_name(ranked_rows: Any) -> Optional[str]:
    normalized_rows = _coerce_ranked_rows(ranked_rows)
    best_league: Optional[int] = None

    for row in normalized_rows:
        if int(row.get('total_battles', 0) or 0) <= 0:
            continue

        league = _ranked_row_league(row)
        if league is None:
            continue

        if best_league is None or league < best_league:
            best_league = league

    if best_league is None:
        return None

    return LEAGUE_NAMES.get(best_league)


def _current_season_ranked_row(ranked_rows: Any, current_season_id: Optional[int]) -> Optional[dict]:
    """The player's ranked_json row for the current season with battles, or None."""
    if current_season_id is None:
        return None

    for row in _coerce_ranked_rows(ranked_rows):
        try:
            season_id = int(row.get('season_id'))
        except (TypeError, ValueError):
            continue

        if season_id == int(current_season_id) and int(row.get('total_battles', 0) or 0) > 0:
            return row

    return None


def is_current_season_ranked_player(ranked_rows: Any, current_season_id: Optional[int]) -> bool:
    """Ranked Enjoyer icon gate: any battles recorded in the current season.

    Replaces the career `is_ranked_player` (>100 lifetime battles) in the icon
    path — the star now marks active participation in the current ranked
    window, colored by `get_current_season_ranked_league`. Spec:
    `agents/work-items/ranked-enjoyer-current-season-spec.md`.
    """
    return _current_season_ranked_row(ranked_rows, current_season_id) is not None


def get_current_season_ranked_league(ranked_rows: Any, current_season_id: Optional[int]) -> Optional[str]:
    """League name ('Gold'/'Silver'/'Bronze') reached in the current season, or None."""
    row = _current_season_ranked_row(ranked_rows, current_season_id)
    if row is None:
        return None

    league = _ranked_row_league(row)
    if league is None:
        return None

    return LEAGUE_NAMES.get(league)


def _summary_has_battle_data(player: Player, battle_rows: list[dict]) -> bool:
    if battle_rows:
        return True

    return (player.pvp_battles or 0) <= 0


def _build_ship_row_metadata(ship_id: Any, ship_model: Optional[Ship]) -> dict[str, Any]:
    try:
        normalized_ship_id = int(ship_id)
    except (TypeError, ValueError):
        normalized_ship_id = 0

    if ship_model is None:
        fallback_name = f"Unknown Ship {normalized_ship_id}" if normalized_ship_id else "Unknown Ship"
        return {
            'ship_id': normalized_ship_id,
            'ship_name': fallback_name,
            'ship_chart_name': build_ship_chart_name(fallback_name),
            'ship_tier': 0,
            'ship_type': 'Unknown',
        }

    ship_name = ship_model.name or (
        f"Unknown Ship {ship_model.ship_id}" if ship_model.ship_id else "Unknown Ship"
    )
    return {
        'ship_id': ship_model.ship_id,
        'ship_name': ship_name,
        'ship_chart_name': ship_model.chart_name or build_ship_chart_name(ship_name),
        'ship_tier': ship_model.tier if ship_model.tier is not None else 0,
        'ship_type': ship_model.ship_type or 'Unknown',
    }


def _normalize_wr_score(pvp_ratio: Optional[float]) -> Optional[float]:
    if pvp_ratio is None:
        return None

    return clamp((float(pvp_ratio) - 45.0) / 20.0, 0.0, 1.0)


def _normalize_kdr_score(kill_ratio: Optional[float]) -> Optional[float]:
    if kill_ratio is None:
        return None

    return clamp((float(kill_ratio) - 0.4) / 1.6, 0.0, 1.0)


def _normalize_survival_score(pvp_survival_rate: Optional[float]) -> Optional[float]:
    if pvp_survival_rate is None:
        return None

    return clamp((float(pvp_survival_rate) - 25.0) / 25.0, 0.0, 1.0)


def _calculate_effective_battle_volume(battle_rows: list[dict], fallback_battles: Optional[int]) -> Optional[float]:
    total_battles = 0
    weighted_battles = 0.0

    for row in battle_rows:
        battles = max(int(row.get('pvp_battles', 0) or 0), 0)
        if battles <= 0:
            continue

        total_battles += battles
        weighted_battles += battles * \
            _player_score_tier_weight(row.get('ship_tier'))

    if total_battles > 0:
        competitive_share = clamp(weighted_battles / total_battles, 0.0, 1.0)
        baseline_battles = max(int(fallback_battles or 0), total_battles)
        return float(baseline_battles) * competitive_share

    if fallback_battles is None:
        return None

    return float(max(int(fallback_battles or 0), 0))


def _normalize_battle_volume_score(total_battles: Optional[int], battle_rows: list[dict]) -> Optional[float]:
    effective_battles = _calculate_effective_battle_volume(
        battle_rows, total_battles)
    if effective_battles is None:
        return None

    battles = max(float(effective_battles), 0.0)
    if battles <= 0:
        return 0.0

    return clamp(math.log10(battles + 1) / 4.0, 0.0, 1.0)


def _calculate_player_score(
    *,
    pvp_ratio: Optional[float],
    kill_ratio: Optional[float],
    pvp_survival_rate: Optional[float],
    total_battles: Optional[int],
    battle_rows: list[dict],
) -> Optional[float]:
    component_values = [
        (PLAYER_SCORE_WR_WEIGHT, _normalize_wr_score(pvp_ratio)),
        (PLAYER_SCORE_KDR_WEIGHT, _normalize_kdr_score(kill_ratio)),
        (PLAYER_SCORE_SURVIVAL_WEIGHT, _normalize_survival_score(pvp_survival_rate)),
        (PLAYER_SCORE_BATTLES_WEIGHT, _normalize_battle_volume_score(
            total_battles, battle_rows)),
    ]

    weighted_sum = 0.0
    total_weight = 0.0
    for weight, value in component_values:
        if value is None:
            continue
        weighted_sum += weight * value
        total_weight += weight

    if total_weight <= 0:
        return None

    score = round((weighted_sum / total_weight) * PLAYER_SCORE_MAX, 2)
    return score


def explorer_summary_needs_refresh(player: Player) -> bool:
    explorer_summary = getattr(player, 'explorer_summary', None)
    if explorer_summary is None:
        return True

    battle_rows = _coerce_battle_rows(player.battles_json)
    has_battle_data = _summary_has_battle_data(player, battle_rows)
    played_rows = [
        row for row in battle_rows
        if int(row.get('pvp_battles', 0) or 0) > 0
    ]

    expected_ships_played_total = len(played_rows) if has_battle_data else None
    expected_kill_ratio = _calculate_player_kill_ratio(
        battle_rows) if has_battle_data else None
    expected_player_score = _calculate_player_score(
        pvp_ratio=player.pvp_ratio,
        kill_ratio=expected_kill_ratio,
        pvp_survival_rate=player.pvp_survival_rate,
        total_battles=player.total_battles or player.pvp_battles,
        battle_rows=battle_rows,
    )

    if explorer_summary.ships_played_total != expected_ships_played_total:
        return True
    if explorer_summary.kill_ratio != expected_kill_ratio:
        return True
    if explorer_summary.player_score != expected_player_score:
        return True
    if not _efficiency_rank_inputs_match_summary(player, explorer_summary):
        return True
    if explorer_summary.ranked_seasons_participated is None and isinstance(player.ranked_json, list):
        return True
    if explorer_summary.battles_last_29_days is None and isinstance(player.activity_json, list):
        return True

    return False


def derive_days_since_last_battle(last_battle_date) -> Optional[int]:
    """Return days from `last_battle_date` to today (UTC). None if missing.

    The single source of truth for "X days ago" displays. Computed at
    READ time so the value is always current — the stored
    `Player.days_since_last_battle` column drifts +1/day between
    refreshes and should not be surfaced directly to users. The clan
    member list (`views.py:_days_since_last_battle`) uses the same
    derivation so player-detail and clan-member surfaces agree.
    """
    if last_battle_date is None:
        return None
    return max(0, (datetime.now(timezone.utc).date() - last_battle_date).days)


def build_player_summary(
    player: Player,
    activity_rows: Any = None,
    ranked_rows: Any = None,
    battles_rows: Any = None,
    use_cached_summary: bool = True,
) -> dict:
    account_age_days = None
    if player.creation_date:
        account_age_days = (datetime.now(
            timezone.utc).date() - player.creation_date.date()).days

    summary = {
        'player_id': player.player_id,
        'name': player.name,
        'is_hidden': player.is_hidden,
        # Derive at read time so the displayed "X days ago" is always
        # current — the stored column is stale by 1/day until the next
        # refresh of this player.
        'days_since_last_battle': derive_days_since_last_battle(player.last_battle_date),
        'last_battle_date': player.last_battle_date.isoformat() if player.last_battle_date else None,
        'account_age_days': account_age_days,
        'pvp_ratio': player.pvp_ratio,
        'pvp_battles': player.pvp_battles,
        'pvp_survival_rate': player.pvp_survival_rate,
        'kill_ratio': None,
        'player_score': None,
        'battles_last_29_days': None,
        'wins_last_29_days': None,
        'active_days_last_29_days': None,
        'recent_win_rate': None,
        'activity_trend_direction': None,
        'ships_played_total': None,
        'ship_type_spread': None,
        'tier_spread': None,
        'eligible_ship_count': None,
        'efficiency_badge_rows_total': None,
        'badge_rows_unmapped': None,
        'expert_count': None,
        'grade_i_count': None,
        'grade_ii_count': None,
        'grade_iii_count': None,
        'raw_badge_points': None,
        'normalized_badge_strength': None,
        'ranked_seasons_participated': None,
        'latest_ranked_battles': None,
        'highest_ranked_league_recent': None,
    }
    if player.is_hidden:
        return summary

    explorer_summary = getattr(player, 'explorer_summary', None)
    if use_cached_summary and activity_rows is None and ranked_rows is None and battles_rows is None and explorer_summary is not None:
        summary.update({
            'battles_last_29_days': explorer_summary.battles_last_29_days,
            'wins_last_29_days': explorer_summary.wins_last_29_days,
            'active_days_last_29_days': explorer_summary.active_days_last_29_days,
            'recent_win_rate': explorer_summary.recent_win_rate,
            'activity_trend_direction': explorer_summary.activity_trend_direction,
            'kill_ratio': explorer_summary.kill_ratio,
            'player_score': explorer_summary.player_score,
            'ships_played_total': explorer_summary.ships_played_total,
            'ship_type_spread': explorer_summary.ship_type_spread,
            'tier_spread': explorer_summary.tier_spread,
            'eligible_ship_count': explorer_summary.eligible_ship_count,
            'efficiency_badge_rows_total': explorer_summary.efficiency_badge_rows_total,
            'badge_rows_unmapped': explorer_summary.badge_rows_unmapped,
            'expert_count': explorer_summary.expert_count,
            'grade_i_count': explorer_summary.grade_i_count,
            'grade_ii_count': explorer_summary.grade_ii_count,
            'grade_iii_count': explorer_summary.grade_iii_count,
            'raw_badge_points': explorer_summary.raw_badge_points,
            'normalized_badge_strength': explorer_summary.normalized_badge_strength,
            'ranked_seasons_participated': explorer_summary.ranked_seasons_participated,
            'latest_ranked_battles': explorer_summary.latest_ranked_battles,
            'highest_ranked_league_recent': explorer_summary.highest_ranked_league_recent,
        })
        return summary

    normalized_battles_rows = _coerce_battle_rows(
        battles_rows if battles_rows is not None else player.battles_json
    )
    has_battle_data = _summary_has_battle_data(player, normalized_battles_rows)
    summary['kill_ratio'] = _calculate_player_kill_ratio(
        normalized_battles_rows) if has_battle_data else None
    summary.update(_build_efficiency_rank_inputs(
        player,
        battle_rows=normalized_battles_rows,
    ))

    normalized_activity_rows = _coerce_activity_rows(
        player.activity_json if activity_rows is None else activity_rows)
    battles_last_29_days = sum(row['battles']
                               for row in normalized_activity_rows)
    wins_last_29_days = sum(row['wins'] for row in normalized_activity_rows)
    active_days_last_29_days = sum(
        1 for row in normalized_activity_rows if row['battles'] > 0)

    played_rows = [
        row for row in normalized_battles_rows
        if isinstance(row, dict) and int(row.get('pvp_battles', 0) or 0) > 0
    ]

    ship_type_spread = len({
        row.get('ship_type') for row in played_rows if row.get('ship_type') is not None
    })
    tier_spread = len({
        row.get('ship_tier') for row in played_rows if row.get('ship_tier') is not None
    })

    normalized_ranked_rows = _coerce_ranked_rows(
        player.ranked_json if ranked_rows is None else ranked_rows)
    latest_ranked_row = normalized_ranked_rows[0] if normalized_ranked_rows else None

    summary.update({
        'battles_last_29_days': battles_last_29_days,
        'wins_last_29_days': wins_last_29_days,
        'active_days_last_29_days': active_days_last_29_days,
        'recent_win_rate': round(wins_last_29_days / battles_last_29_days, 3) if battles_last_29_days > 0 else None,
        'activity_trend_direction': _calculate_activity_trend_direction(normalized_activity_rows),
        'player_score': _calculate_player_score(
            pvp_ratio=player.pvp_ratio,
            kill_ratio=summary['kill_ratio'],
            pvp_survival_rate=player.pvp_survival_rate,
            total_battles=player.total_battles or player.pvp_battles,
            battle_rows=normalized_battles_rows,
        ),
        'ships_played_total': len(played_rows) if has_battle_data else None,
        'ship_type_spread': ship_type_spread if has_battle_data else None,
        'tier_spread': tier_spread if has_battle_data else None,
        'eligible_ship_count': summary['eligible_ship_count'],
        'efficiency_badge_rows_total': summary['efficiency_badge_rows_total'],
        'badge_rows_unmapped': summary['badge_rows_unmapped'],
        'expert_count': summary['expert_count'],
        'grade_i_count': summary['grade_i_count'],
        'grade_ii_count': summary['grade_ii_count'],
        'grade_iii_count': summary['grade_iii_count'],
        'raw_badge_points': summary['raw_badge_points'],
        'normalized_badge_strength': summary['normalized_badge_strength'],
        'ranked_seasons_participated': len(normalized_ranked_rows),
        'latest_ranked_battles': int(latest_ranked_row.get('total_battles', 0) or 0) if latest_ranked_row else None,
        'highest_ranked_league_recent': latest_ranked_row.get('highest_league_name') if latest_ranked_row else None,
    })
    return summary


def refresh_player_explorer_summary(
    player: Player,
    activity_rows: Any = None,
    ranked_rows: Any = None,
    battles_rows: Any = None,
) -> PlayerExplorerSummary:
    summary = build_player_summary(
        player,
        activity_rows=activity_rows,
        ranked_rows=ranked_rows,
        battles_rows=battles_rows,
        use_cached_summary=False,
    )

    explorer_summary, _ = PlayerExplorerSummary.objects.update_or_create(
        player=player,
        defaults={
            'realm': player.realm,
            'battles_last_29_days': summary['battles_last_29_days'],
            'wins_last_29_days': summary['wins_last_29_days'],
            'active_days_last_29_days': summary['active_days_last_29_days'],
            'recent_win_rate': summary['recent_win_rate'],
            'activity_trend_direction': summary['activity_trend_direction'],
            'player_score': summary['player_score'],
            'ships_played_total': summary['ships_played_total'],
            'ship_type_spread': summary['ship_type_spread'],
            'tier_spread': summary['tier_spread'],
            'eligible_ship_count': summary['eligible_ship_count'],
            'efficiency_badge_rows_total': summary['efficiency_badge_rows_total'],
            'badge_rows_unmapped': summary['badge_rows_unmapped'],
            'expert_count': summary['expert_count'],
            'grade_i_count': summary['grade_i_count'],
            'grade_ii_count': summary['grade_ii_count'],
            'grade_iii_count': summary['grade_iii_count'],
            'raw_badge_points': summary['raw_badge_points'],
            'normalized_badge_strength': summary['normalized_badge_strength'],
            'ranked_seasons_participated': summary['ranked_seasons_participated'],
            'latest_ranked_battles': summary['latest_ranked_battles'],
            'highest_ranked_league_recent': summary['highest_ranked_league_recent'],
            'kill_ratio': summary['kill_ratio'],
        },
    )

    if player.is_hidden:
        explorer_summary.shrunken_efficiency_strength = None
        explorer_summary.efficiency_rank_percentile = None
        explorer_summary.efficiency_rank_tier = None
        explorer_summary.has_efficiency_rank_icon = False
        explorer_summary.efficiency_rank_population_size = None
        explorer_summary.efficiency_rank_updated_at = None
        explorer_summary.save(update_fields=[
            'shrunken_efficiency_strength',
            'efficiency_rank_percentile',
            'efficiency_rank_tier',
            'has_efficiency_rank_icon',
            'efficiency_rank_population_size',
            'efficiency_rank_updated_at',
        ])

    player.explorer_summary = explorer_summary
    return explorer_summary


def fetch_player_summary(player_id: str, realm: str = DEFAULT_REALM) -> dict:
    player = Player.objects.get(player_id=player_id, realm=realm)

    if not player.is_hidden:
        # Dedup: skip dispatch if we already queued refreshes for this player recently
        dedup_key = f'player:refresh_dispatched:{player_id}'
        if cache.add(dedup_key, 1, timeout=60):
            # Per-field lazy hydration: dispatch refresh for any missing or stale
            # JSON field independently, so partial data (e.g. ranked_json set but
            # battles_json missing) no longer blocks hydration.
            if player.battles_json is None:
                _dispatch_async_refresh(
                    update_battle_data_task, player_id=player_id, realm=realm)
            elif player_battle_data_needs_refresh(player):
                _dispatch_async_refresh(
                    update_battle_data_task, player_id=player_id, realm=realm)

            if player.activity_json is None:
                _dispatch_async_refresh(
                    update_snapshot_data_task, player_id, realm=realm)
                _dispatch_async_refresh(
                    update_activity_data_task, player_id, realm=realm)
            elif player_activity_data_needs_refresh(player):
                _dispatch_async_refresh(
                    update_snapshot_data_task, player_id, realm=realm)
                _dispatch_async_refresh(
                    update_activity_data_task, player_id, realm=realm)

            if player.ranked_json is None:
                from warships.tasks import queue_ranked_data_refresh
                queue_ranked_data_refresh(player_id, realm=realm)
            elif player_ranked_data_needs_refresh(player):
                from warships.tasks import queue_ranked_data_refresh
                queue_ranked_data_refresh(player_id, realm=realm)

            # On-render ranked-observation refresh: dispatch a fresh
            # BattleObservation + ranked capture so the BattleHistoryCard's
            # Ranked / All views always reflect the latest state without
            # waiting for the next regular crawl tick. Gated on the same
            # env flags as the capture seam in update_battle_data so we
            # don't spend the third WG call on realms where ranked
            # capture is disabled.
            ranked_capture_on = (
                os.getenv("BATTLE_HISTORY_RANKED_CAPTURE_ENABLED", "0") == "1"
            )
            ranked_realms = {
                r.strip() for r in os.getenv(
                    "BATTLE_HISTORY_RANKED_CAPTURE_REALMS", ""
                ).split(",") if r.strip()
            }
            if ranked_capture_on and realm in ranked_realms:
                if _ranked_observation_is_stale(player):
                    from warships.tasks import (
                        queue_ranked_observation_refresh,
                    )
                    queue_ranked_observation_refresh(
                        player_id, realm=realm)

    if getattr(player, 'explorer_summary', None) is None and (
        player.battles_json is not None or player.activity_json is not None or player.ranked_json is not None
    ):
        refresh_player_explorer_summary(player)

    return build_player_summary(player)


def extract_randoms_rows(battles_json: Any, limit: Optional[int] = 20) -> list[dict]:
    if not isinstance(battles_json, list):
        return []

    rows = []
    for row in battles_json:
        if not isinstance(row, dict):
            continue

        ship_name = row.get('ship_name')
        ship_type = row.get('ship_type')
        ship_tier = row.get('ship_tier')
        if ship_name is None or ship_type is None or ship_tier is None:
            continue

        rows.append({
            'pvp_battles': int(row.get('pvp_battles', 0) or 0),
            'ship_name': ship_name,
            'ship_chart_name': row.get('ship_chart_name') or build_ship_chart_name(str(ship_name)),
            'ship_type': ship_type,
            'ship_tier': ship_tier,
            'win_ratio': float(row.get('win_ratio', 0) or 0),
            'wins': int(row.get('wins', 0) or 0),
        })

    rows.sort(key=lambda row: row['pvp_battles'], reverse=True)
    return rows if limit is None else rows[:limit]


def _aggregate_battles_by_key(battles_json: Any, group_key: str) -> list[dict]:
    if not isinstance(battles_json, list):
        return []

    aggregates: dict[Any, dict[str, int]] = {}
    for row in battles_json:
        if not isinstance(row, dict):
            continue

        key = row.get(group_key)
        if key is None:
            continue

        aggregate = aggregates.setdefault(key, {'pvp_battles': 0, 'wins': 0})
        aggregate['pvp_battles'] += int(row.get('pvp_battles', 0) or 0)
        aggregate['wins'] += int(row.get('wins', 0) or 0)

    result = []
    for key, aggregate in aggregates.items():
        battles = aggregate['pvp_battles']
        wins = aggregate['wins']
        result.append({
            group_key: key,
            'pvp_battles': battles,
            'wins': wins,
            'win_ratio': round(wins / battles, 2) if battles > 0 else 0,
        })

    result.sort(key=lambda row: row['pvp_battles'], reverse=True)
    return result


def apply_battles_json(player, ship_data, realm: str = DEFAULT_REALM) -> list:
    """Build + persist `battles_json` from an already-fetched `ships/stats` list.

    Advances `battles_updated_at`, refreshes the derived per-tier / per-type /
    randoms tables + explorer summary, and busts the player-detail cache —
    exactly what `update_battle_data` did inline. Factored out so the
    battle-observation floor can refresh a player's displayed per-ship stats from
    the SAME `ships/stats` response it already fetched for the observation (no
    second WG call). Callers must pass a NON-empty `ship_data`; the empty/hidden
    case is the caller's responsibility (the floor skips it to avoid blanking a
    transiently-empty fetch; `update_battle_data` records [] deliberately).
    """
    prepared_data = []
    for ship in ship_data:
        ship_model = _fetch_ship_info(ship['ship_id'])
        ship_metadata = _build_ship_row_metadata(ship.get('ship_id'), ship_model)
        if ship_model is None:
            logging.warning(
                'Falling back to placeholder ship metadata for ship_id=%s while updating player_id=%s',
                ship.get('ship_id'), player.player_id,
            )
        pvp_battles = ship['pvp']['battles']
        wins = ship['pvp']['wins']
        losses = ship['pvp']['losses']
        frags = ship['pvp']['frags']
        battles = ship['battles']
        distance = ship['distance']
        ship_info = {
            'ship_id': ship_metadata['ship_id'],
            'ship_name': ship_metadata['ship_name'],
            'ship_chart_name': ship_metadata['ship_chart_name'],
            'ship_tier': ship_metadata['ship_tier'],
            'all_battles': battles,
            'distance': distance,
            'wins': wins,
            'losses': losses,
            'ship_type': ship_metadata['ship_type'],
            'pve_battles': battles - (wins + losses),
            'pvp_battles': pvp_battles,
            'win_ratio': round(wins / pvp_battles, 2) if pvp_battles > 0 else 0,
            'kdr': round(frags / pvp_battles, 2) if pvp_battles > 0 else 0,
        }
        prepared_data.append(ship_info)

    sorted_data = sorted(prepared_data, key=lambda x: x.get('pvp_battles', 0), reverse=True)
    player.battles_updated_at = datetime.now()
    player.battles_json = sorted_data
    player.save(update_fields=['battles_json', 'battles_updated_at'])
    update_tiers_data(player.player_id, realm=realm)
    update_type_data(player.player_id, realm=realm)
    update_randoms_data(player.player_id, realm=realm)
    refresh_player_explorer_summary(player, battles_rows=sorted_data)
    # Bust the player-detail bulk cache so the next visit / live-update poll
    # serves the freshly-refreshed stats. Cheap cache.delete; no-op for the
    # ~uncached majority.
    invalidate_player_detail_cache(player.player_id, realm=realm)
    return sorted_data


def update_battle_data(player_id: str, realm: str = DEFAULT_REALM,
                       force_refresh: bool = False) -> None:
    """
    Updates the battle data for a given player.

    This function fetches the latest battle data for a player from an external API if the cached data is older than 15 minutes.
    The fetched data is then processed and saved back to the player's record in the database.

    Args:
        player_id (str): The ID of the player whose battle data needs to be updated.
        realm (str): The realm to scope the query to.
        force_refresh (bool): Bypass the 15-minute cache guard and always refetch.
            The hot-players freshness sweep needs this: it re-refreshes hot players
            at a sub-15-min cadence to keep ``battles_updated_at`` inside the
            15-min visit-freshness window, but the default guard below (15 min)
            would early-return without advancing the timestamp for exactly the
            [cadence, 15min) band it targets. Default False keeps every existing
            caller (incl. ``update_battle_data_task``) unchanged.

    Returns:
        None
    """
    player = Player.objects.get(player_id=player_id, realm=realm)

    # Check if the cached data is less than 15 minutes old
    if not force_refresh and player.battles_json and player.battles_updated_at and datetime.now() - player.battles_updated_at < timedelta(minutes=15):
        logging.debug(
            f'Cache exists and is fresh: returning cached data')
        return player.battles_json

    logging.info(
        f'Battles data empty or outdated: fetching new data for {player.name}')

    # Fetch ship stats for the player (with WG's hidden-profile flag).
    ship_data, profile_hidden = _fetch_ship_stats_for_player_with_hidden(
        player_id, realm=realm)
    if ship_data is None:
        # Transient/transport failure — the fetch did not complete. Do NOT
        # clobber the stored battles_json to [] (that would drop the chart until
        # some other trigger repopulates) and do NOT flip is_hidden. Leave the
        # row untouched so the floor / next view retries it.
        logging.warning(
            f'Transient ship-stats fetch failure for player_id={player_id}; '
            'leaving battles_json unchanged for retry.'
        )
        return
    if not ship_data:
        logging.warning(
            f'No ship stats returned for player_id={player_id}; recording empty battles_json to avoid re-selection.'
        )
        update_fields = ['battles_json', 'battles_updated_at']
        # WG's meta.hidden is a reliable hidden signal, distinct from a
        # transient empty/error response (profile_hidden=False). Flipping
        # is_hidden here makes the WHOLE profile reflect the hide, instead of
        # leaving one chart (the tier-type correlation) to "warm" forever on a
        # battles_json that can never repopulate while the account is hidden.
        if profile_hidden and not player.is_hidden:
            player.is_hidden = True
            update_fields.append('is_hidden')
            logging.info(
                f'WG now hides {player.name} (player_id={player_id}); '
                'flipping is_hidden=True.')
        player.battles_json = []
        player.battles_updated_at = datetime.now()
        player.save(update_fields=update_fields)
        return

    # Battle-history capture hook (Phase 2 of the playerbase rollout).
    # Runbook: agents/runbooks/runbook-battle-history-rollout-2026-04-28.md
    # Off in production until BATTLE_HISTORY_CAPTURE_ENABLED=1 is set.
    #
    # ORDERING CONTRACT: the capture must run BEFORE apply_battles_json.
    # `X-Player-Refresh-Pending` is anchored on `battles_updated_at`
    # (views._player_refresh_signals), which apply_battles_json bumps — so the
    # bump must land only after the capture has committed its BattleEvents and
    # invalidated the battle-history cache. Bumping first opens a gap where
    # the client's 2s poll sees "landed", fires its single nonce-bumped
    # battle-history refetch, and caches the pre-session payload until a
    # manual reload (2026-07-17 stale-rehydrate investigation).
    #
    # Ranked extension (Phase 1, runbook-ranked-battle-history-rollout-2026-05-02.md):
    # When BATTLE_HISTORY_RANKED_CAPTURE_ENABLED=1 AND this realm is in the
    # comma-separated BATTLE_HISTORY_RANKED_CAPTURE_REALMS list (default
    # empty = no ranked capture anywhere), one extra `seasons/shipstats/`
    # WG call is made per refresh and the result is passed through to the
    # capture orchestrator so the diff lane can emit ranked BattleEvents.
    # The ranked fetch failing (or returning [] for ranked-inactive
    # players) is benign — the orchestrator just records ranked_payload
    # as [] and emits no ranked events.
    if os.getenv("BATTLE_HISTORY_CAPTURE_ENABLED", "0") == "1":
        try:
            from warships.incremental_battles import record_observation_from_payloads
            from warships.models import BattleObservation

            ranked_ship_data = None
            ranked_enabled = os.getenv(
                "BATTLE_HISTORY_RANKED_CAPTURE_ENABLED", "0") == "1"
            ranked_realms = {
                r.strip() for r in os.getenv(
                    "BATTLE_HISTORY_RANKED_CAPTURE_REALMS", ""
                ).split(",") if r.strip()
            }
            if ranked_enabled and realm in ranked_realms:
                try:
                    ranked_ship_data = _fetch_ranked_ship_stats_for_player(
                        int(player.player_id), realm=realm)
                except Exception:
                    logging.exception(
                        "ranked seasons/shipstats fetch failed for "
                        "player_id=%s realm=%s — falling back to randoms-only",
                        player.player_id, realm,
                    )
                    ranked_ship_data = None

            record_observation_from_payloads(
                player,
                player_data=None,
                ship_data=ship_data,
                ranked_ship_data=ranked_ship_data,
                source=BattleObservation.SOURCE_POLL,
            )
        except Exception:
            logging.exception(
                "battle-history capture failed for player_id=%s realm=%s",
                player.player_id, realm,
            )

    # Bump battles_updated_at (the pending-header anchor) LAST — see the
    # ordering contract above. A capture failure is swallowed above, so the
    # refresh path stays whole either way.
    apply_battles_json(player, ship_data, realm=realm)
    logging.info(f"Updated battles_json data: {player.name}")


def fetch_tier_data(player_id: str, realm: str = DEFAULT_REALM) -> list:
    """
    Fetches and processes tier data for a given player. Tier data is a subset of battle data.

    This function updates the battle data for a player and then processes it to calculate the number of battles,
    wins, and win ratio for each ship tier. The processed data is saved back to the player's record in the database.

    Args:
        player_id (str): The ID of the player whose tier data needs to be fetched.
        realm (str): The realm to scope the query to.

    Returns:
        str: A JSON response containing the processed tier data.
    """
    try:
        player = Player.objects.get(player_id=player_id, realm=realm)
        if not player.battles_json:
            _dispatch_async_refresh(
                update_battle_data_task, player_id=player_id, realm=realm)
            return player.tiers_json or []
    except Player.DoesNotExist:
        return []

    if player.tiers_json is not None:
        if player_battle_data_needs_refresh(player):
            _dispatch_async_refresh(
                update_battle_data_task, player_id=player_id, realm=realm)
        return player.tiers_json

    _dispatch_async_refresh(update_tiers_data_task, player_id, realm=realm)
    return []


def update_tiers_data(player_id: str, realm: str = DEFAULT_REALM) -> list:
    player = Player.objects.get(player_id=player_id, realm=realm)
    tier_aggregates = {tier: {'pvp_battles': 0, 'wins': 0}
                       for tier in range(1, 12)}
    for row in player.battles_json or []:
        if not isinstance(row, dict):
            continue

        tier = row.get('ship_tier')
        if not isinstance(tier, int) or tier not in tier_aggregates:
            continue

        tier_aggregates[tier]['pvp_battles'] += int(
            row.get('pvp_battles', 0) or 0)
        tier_aggregates[tier]['wins'] += int(row.get('wins', 0) or 0)

    data = []
    for tier in range(11, 0, -1):
        battles = tier_aggregates[tier]['pvp_battles']
        wins = tier_aggregates[tier]['wins']
        data.append({
            'ship_tier': tier,
            'pvp_battles': battles,
            'wins': wins,
            'win_ratio': round(wins / battles, 2) if battles > 0 else 0,
        })

    player.tiers_json = data
    player.tiers_updated_at = datetime.now()
    # Scoped: owns only tiers_*. A bare save() writes back this regenerator's stale
    # snapshot of battles_json / ranked_json / etc., reverting concurrent scoped
    # writes (runbook-player-refresh-pill-clobber-2026-06-21 #2 — bare-save audit).
    player.save(update_fields=['tiers_json', 'tiers_updated_at'])


def update_snapshot_data(player_id: int, realm: str = DEFAULT_REALM, refresh_player: bool = True) -> str:
    """
    Records today's cumulative PvP stats as a Snapshot and computes
    daily interval_battles / interval_wins from successive snapshots.

    The WoWS account/statsbydate endpoint no longer returns pvp data,
    so we use the Player model's pvp_battles / pvp_wins (kept current
    by update_player_data via account/info) as today's cumulative values.

    Delta gate (``SNAPSHOT_DELTA_GATE_ENABLED``, default on): when the
    cumulative stats haven't moved since the player's latest stored row,
    the whole write path is skipped — no today-row, no purge, no interval
    churn. Readers synthesize zero-battle days for missing dates, so the
    zero rows carry no information. Activity still refreshes on both paths
    (the 29-day window slides daily). Returns ``'written'`` or
    ``'skipped-unchanged'``. Spec:
    ``agents/work-items/snapshot-delta-gated-writes-spec.md``.
    """
    player = Player.objects.get(player_id=player_id, realm=realm)

    # Ensure the player model has fresh stats
    if refresh_player:
        from warships.data import update_player_data
        update_player_data(player, force_refresh=True)
        player.refresh_from_db()

    today = datetime.now().date()
    start_date = today - timedelta(days=28)

    current_battles = int(player.pvp_battles or 0)
    current_wins = int(player.pvp_wins or 0)

    # A today-row means the write path already ran today — keep maintaining
    # it (the per-view path may upsert several times a day as stats move).
    has_today = Snapshot.objects.filter(player=player, date=today).exists()
    prior = (Snapshot.objects.filter(player=player, date__lt=today)
             .order_by('-date').first())

    if (os.getenv('SNAPSHOT_DELTA_GATE_ENABLED', '1') == '1'
            and not has_today and prior is not None
            and int(prior.battles or 0) == current_battles
            and int(prior.wins or 0) == current_wins):
        # First unchanged pass of the day still rebuilds activity (the 29-day
        # window slides); repeats produce the identical payload, so throttle
        # them to spare the Player/PES churn the gate exists to remove.
        if (player.activity_json is None
                or player.activity_updated_at is None
                or player.activity_updated_at.date() < today):
            update_activity_data(player_id, realm=realm)
        logging.info(
            f'Snapshot unchanged for player {player.name} — skipped write')
        return 'skipped-unchanged'

    # Purge stale zero-value snapshots left by the broken statsbydate API
    Snapshot.objects.filter(
        player=player, battles=0, wins=0
    ).exclude(date=today).delete()

    # Upsert today's snapshot with current cumulative totals
    snapshot, _ = Snapshot.objects.get_or_create(player=player, date=today)
    snapshot.battles = current_battles
    snapshot.wins = current_wins
    snapshot.save()

    # Recompute intervals for the whole 28-day window, seeded from the
    # latest pre-window row: under sparse (delta-gated) storage the previous
    # row can predate the window, and without the seed a returning mover's
    # real delta would be zeroed at the window edge.
    snapshots = list(Snapshot.objects.filter(
        player=player, date__gte=start_date, date__lte=today).order_by('date'))

    seed = (Snapshot.objects.filter(player=player, date__lt=start_date)
            .order_by('-date').first())
    previous_battles = seed.battles if seed else None
    previous_wins = seed.wins if seed else None
    changed = []
    for snap in snapshots:
        before = (snap.interval_battles, snap.interval_wins)
        if previous_battles is None or previous_wins is None:
            snap.interval_battles = 0
            snap.interval_wins = 0
        else:
            snap.interval_battles = max(
                0, int(snap.battles or 0) - int(previous_battles or 0))
            snap.interval_wins = max(
                0, int(snap.wins or 0) - int(previous_wins or 0))

        if (snap.interval_battles, snap.interval_wins) != before:
            changed.append(snap)
        previous_battles = snap.battles
        previous_wins = snap.wins

    if changed:
        Snapshot.objects.bulk_update(
            changed, ['interval_battles', 'interval_wins'])

    update_activity_data(player_id, realm=realm)
    logging.info(f'Updated snapshot data for player {player.name}')
    return 'written'


def fetch_activity_data(player_id: str, realm: str = DEFAULT_REALM) -> list:
    try:
        player = Player.objects.get(player_id=player_id, realm=realm)
    except Player.DoesNotExist:
        return []

    if player.activity_json is not None:
        logging.info(f'Activity data exists for player {player.name}')
        if player_activity_data_needs_refresh(player):
            logging.info(
                'Scheduling async activity refresh for %s : %s',
                player.name,
                player.player_id,
            )
            _dispatch_async_refresh(
                update_snapshot_data_task, player.player_id, realm=realm)
        else:
            logging.info(
                f'Activity fetch datetime is fresh: returning cached data for player {player.name}')
        return player.activity_json

    _dispatch_async_refresh(update_snapshot_data_task,
                            player.player_id, realm=realm)
    return []


def update_activity_data(player_id: int, realm: str = DEFAULT_REALM) -> None:
    player = Player.objects.get(player_id=player_id, realm=realm)
    month = []
    # Only the trailing 29-day window is rendered; don't load the whole
    # snapshot history (unbounded growth — DB audit F3).
    window_start = (datetime.now() - timedelta(28)).date()
    snapshots = list(Snapshot.objects.filter(
        player=player, date__gte=window_start).order_by('date'))

    latest_snapshot_by_date = {snap.date: snap for snap in snapshots}

    for i in range(29):
        date = (datetime.now() - timedelta(28) + timedelta(days=i)).date()

        snap = latest_snapshot_by_date.get(date)

        month.append({
            "date": date.strftime("%Y-%m-%d"),
            "battles": (snap.interval_battles if snap else 0) or 0,
            "wins": (snap.interval_wins if snap else 0) or 0
        })

    player.activity_json = month
    player.activity_updated_at = datetime.now()
    # Scoped: owns only activity_* (bare-save audit, see update_tiers_data).
    player.save(update_fields=['activity_json', 'activity_updated_at'])
    refresh_player_explorer_summary(player, activity_rows=month)

    logging.info(f'Updated activity data for player {player.name}')


# ──────────────────────────────────────────────────────────
#  Ranked Battles data
# ──────────────────────────────────────────────────────────

LEAGUE_NAMES = {1: 'Gold', 2: 'Silver', 3: 'Bronze'}

PLAYER_DISTRIBUTION_CACHE_TTL = 43200  # 12 hours
PLAYER_CORRELATION_CACHE_TTL = 43200  # 12 hours
PLAYER_DISTRIBUTION_CONFIGS = {
    'win_rate': {
        'label': 'Win Rate',
        'x_label': 'Rate',
        'scale': 'linear',
        'value_format': 'percent',
        'field_name': 'pvp_ratio',
        'min_population_battles': 100,
        'range_min': 35.0,
        'range_max': 75.0,
        'bin_width': 1.0,
    },
    'survival_rate': {
        'label': 'Survival Rate',
        'x_label': 'Rate',
        'scale': 'linear',
        'value_format': 'percent',
        'field_name': 'pvp_survival_rate',
        'min_population_battles': 100,
        'range_min': 15.0,
        'range_max': 75.0,
        'bin_width': 1.0,
    },
    'battles_played': {
        'label': 'PvP Battles',
        'x_label': 'PvP Battles',
        'scale': 'log',
        'value_format': 'integer',
        'field_name': 'pvp_battles',
        'min_population_battles': 100,
        'bin_edges': [100, 200, 400, 800, 1600, 3200, 6400, 12800, 25600, 51200, 102400],
    },
    'player_score': {
        'label': 'Player Score',
        'x_label': 'Score',
        'scale': 'linear',
        'value_format': 'decimal',
        'field_name': 'player_score',
        'min_population_battles': 100,
        'range_min': 2.0,
        'range_max': 10.0,
        'bin_width': 0.5,
        'source_model': 'explorer_summary',
    },
}

PLAYER_WR_SURVIVAL_CORRELATION_CONFIG = {
    'label': 'Win Rate vs Survival',
    'x_label': 'Survival Rate',
    'y_label': 'Win Rate',
    'min_population_battles': 100,
    # exclude noisy sub-15% population (new/bot/trash accounts)
    'min_survival_rate': 15.0,
    # x is now survival rate, y is now win rate (axes flipped 2026-04-07)
    'x_min': 15.0,
    'x_max': 75.0,
    'x_bin_width': 1.5,
    'y_min': 35.0,
    'y_max': 75.0,
    'y_bin_width': 1.0,
}

PLAYER_RANKED_WR_BATTLES_CORRELATION_CONFIG = {
    'label': 'Ranked Games vs Win Rate',
    'x_label': 'Total Ranked Games',
    'y_label': 'Ranked Win Rate',
    'x_scale': 'log',
    'y_scale': 'linear',
    'min_battles': 50,
    'base_x_edges': [50],
    'x_bin_growth_factor': math.sqrt(math.sqrt(2)),
    'y_min': 35.0,
    'y_max': 75.0,
    'y_bin_width': 0.75,
}
PLAYER_RANKED_WR_BATTLES_CORRELATION_CACHE_VERSION = 'ranked_wr_battles:v6'

PLAYER_TIER_TYPE_CORRELATION_CONFIG = {
    'label': 'Tier vs Ship Type',
    'x_label': 'Ship Type',
    'y_label': 'Tier',
    'min_population_battles': 100,
}

PLAYER_TIER_TYPE_ORDER = {
    'Destroyer': 0,
    'Cruiser': 1,
    'Battleship': 2,
    'Aircraft Carrier': 3,
    'Submarine': 4,
}
_SHIP_TYPE_ALIASES: dict[str, str] = {
    'AirCarrier': 'Aircraft Carrier',
}
_SHIP_TYPE_EXCLUDED_FROM_HEATMAP: set[str] = {'Unknown'}
PLAYER_TIER_TYPE_CACHE_VERSION = 'tier_type_population:v3'


def _player_distribution_cache_key(metric: str, realm: str = DEFAULT_REALM) -> str:
    return realm_cache_key(realm, f'players:distribution:v2:{metric}')


def _player_distribution_published_cache_key(metric: str, realm: str = DEFAULT_REALM) -> str:
    return f'{_player_distribution_cache_key(metric, realm=realm)}:published'


def _player_correlation_cache_key(metric: str, realm: str = DEFAULT_REALM) -> str:
    return realm_cache_key(realm, f'players:correlation:v2:{metric}')


def _player_correlation_published_cache_key(metric: str, realm: str = DEFAULT_REALM) -> str:
    return f'{_player_correlation_cache_key(metric, realm=realm)}:published'


def _build_doubling_bin_edges(max_value: int, seed_edges: list[int]) -> list[int]:
    if not seed_edges:
        return [1, max(2, max_value)]

    edges = sorted(set(max(1, int(edge)) for edge in seed_edges))
    while edges[-1] < max_value:
        edges.append(edges[-1] * 2)

    return edges


def _build_geometric_bin_edges(max_value: int, seed_edges: list[int], growth_factor: float) -> list[int]:
    if not seed_edges:
        return [1, max(2, max_value)]

    edges = sorted(set(max(1, int(edge)) for edge in seed_edges))
    if growth_factor <= 1.0:
        return edges

    base_edge = edges[0]
    power = 0
    while edges[-1] < max_value:
        power += 1
        next_edge = int(round(base_edge * (growth_factor ** power)))
        if next_edge <= edges[-1]:
            next_edge = edges[-1] + 1
        edges.append(next_edge)

    return edges


def _build_linear_distribution_bins(qs, field_name: str, value_min: float, value_max: float, bin_width: float) -> list[dict]:
    edges: list[float] = []
    current = value_min
    while current < value_max:
        edges.append(round(current, 6))
        current = round(current + bin_width, 6)
    edges.append(round(value_max, 6))

    last = len(edges) - 2
    whens = []
    for index, lower in enumerate(edges[:-1]):
        upper = edges[index + 1]
        if index == last:
            whens.append(When(**{f'{field_name}__gte': lower,
                         f'{field_name}__lte': upper}, then=Value(index)))
        else:
            whens.append(When(**{f'{field_name}__gte': lower,
                         f'{field_name}__lt': upper}, then=Value(index)))

    counts_by_index = {
        row['bin_index']: row['count']
        for row in (
            qs.annotate(bin_index=Case(
                *whens, output_field=IntegerField(), default=Value(-1)))
            .filter(bin_index__gte=0)
            .values('bin_index')
            .annotate(count=Count('id'))
            .order_by()
        )
    }

    return [
        {'bin_min': round(edges[i], 4), 'bin_max': round(
            edges[i + 1], 4), 'count': counts_by_index.get(i, 0)}
        for i in range(len(edges) - 1)
    ]


def _build_explicit_distribution_bins(qs, field_name: str, bin_edges: list[int]) -> list[dict]:
    last = len(bin_edges) - 2
    whens = []
    for index, lower in enumerate(bin_edges[:-1]):
        upper = bin_edges[index + 1]
        if index == last:
            whens.append(When(**{f'{field_name}__gte': lower,
                         f'{field_name}__lte': upper}, then=Value(index)))
        else:
            whens.append(When(**{f'{field_name}__gte': lower,
                         f'{field_name}__lt': upper}, then=Value(index)))

    counts_by_index = {
        row['bin_index']: row['count']
        for row in (
            qs.annotate(bin_index=Case(
                *whens, output_field=IntegerField(), default=Value(-1)))
            .filter(bin_index__gte=0)
            .values('bin_index')
            .annotate(count=Count('id'))
            .order_by()
        )
    }

    return [
        {'bin_min': bin_edges[i], 'bin_max': bin_edges[i + 1],
            'count': counts_by_index.get(i, 0)}
        for i in range(len(bin_edges) - 1)
    ]


def fetch_player_population_distribution(metric: str, realm: str = DEFAULT_REALM) -> dict:
    config = PLAYER_DISTRIBUTION_CONFIGS.get(metric)
    if config is None:
        raise ValueError(f'Unsupported player distribution metric: {metric}')

    cache_key = _player_distribution_cache_key(metric, realm=realm)
    published_cache_key = _player_distribution_published_cache_key(
        metric, realm=realm)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    # Fall back to durable published copy to avoid expensive table scans
    # in gunicorn workers when the primary cache expires between warmer runs.
    published = cache.get(published_cache_key)
    if published is not None:
        return published

    field_name = config['field_name']
    source_model = config.get('source_model')

    if source_model == 'explorer_summary':
        qs = PlayerExplorerSummary.objects.filter(
            realm=realm,
            **{f'{field_name}__isnull': False},
        )
    else:
        # MvPlayerDistributionStats is an unmanaged materialized view; where it
        # doesn't exist (test DB, fresh env) the probe raises a DB error. Wrap
        # it in its own savepoint so that error rolls back cleanly instead of
        # poisoning the surrounding transaction — psycopg3 (prod's driver) does
        # not tolerate continuing on an aborted transaction the way psycopg2 did.
        try:
            with transaction.atomic():
                qs = MvPlayerDistributionStats.objects.filter(
                    realm=realm,
                    pvp_battles__gte=config['min_population_battles'],
                    **{f'{field_name}__isnull': False},
                )
                if not qs.exists():
                    raise MvPlayerDistributionStats.DoesNotExist
        except Exception:
            qs = Player.objects.filter(
                realm=realm,
                is_hidden=False,
                pvp_battles__gte=config['min_population_battles'],
                **{f'{field_name}__isnull': False},
            )

    with transaction.atomic(), _elevated_work_mem():
        if config['scale'] == 'log':
            bins = _build_explicit_distribution_bins(
                qs, field_name, config['bin_edges'])
        else:
            bins = _build_linear_distribution_bins(
                qs,
                field_name,
                config['range_min'],
                config['range_max'],
                config['bin_width'],
            )

    payload = {
        'metric': metric,
        'label': config['label'],
        'x_label': config['x_label'],
        'scale': config['scale'],
        'value_format': config['value_format'],
        'tracked_population': sum(b['count'] for b in bins),
        'bins': bins,
    }

    cache.set(cache_key, payload, PLAYER_DISTRIBUTION_CACHE_TTL)
    cache.set(published_cache_key, payload, timeout=None)
    return payload


def warm_player_distributions(realm: str = DEFAULT_REALM) -> dict:
    """Pre-warm all player distribution caches so users never hit cold queries."""
    from django.db import connection as db_connection
    try:
        with db_connection.cursor() as cursor:
            cursor.execute(
                'REFRESH MATERIALIZED VIEW CONCURRENTLY mv_player_distribution_stats'
            )
    except Exception:
        logger.warning(
            'Could not refresh mv_player_distribution_stats — view may not exist yet')
    results = {}
    for metric in PLAYER_DISTRIBUTION_CONFIGS:
        cache_key = _player_distribution_cache_key(metric, realm=realm)
        cache.delete(cache_key)
        payload = fetch_player_population_distribution(metric, realm=realm)
        results[metric] = {
            'tracked_population': payload['tracked_population'],
            'bins': len(payload['bins']),
        }
    return results


def _clamp_to_open_upper_bound(value: float, value_min: float, value_max: float) -> float:
    epsilon = 1e-6
    return min(max(value, value_min), value_max - epsilon)


def _pearson_correlation(count: int, sum_x: float, sum_y: float, sum_xy: float, sum_x2: float, sum_y2: float) -> Optional[float]:
    if count <= 1:
        return None

    numerator = (count * sum_xy) - (sum_x * sum_y)
    denominator_left = (count * sum_x2) - (sum_x * sum_x)
    denominator_right = (count * sum_y2) - (sum_y * sum_y)
    denominator = math.sqrt(max(denominator_left, 0.0)
                            * max(denominator_right, 0.0))
    if denominator == 0:
        return None

    return numerator / denominator


def _calculate_ranked_record(ranked_rows: Any) -> tuple[int, Optional[float]]:
    total_battles = 0
    total_wins = 0.0

    for row in _coerce_ranked_rows(ranked_rows):
        battles = int(row.get('total_battles', 0) or 0)
        if battles <= 0:
            continue

        wins = row.get('total_wins')
        if wins is None and row.get('win_rate') is not None:
            win_rate = float(row.get('win_rate') or 0.0)
            if win_rate > 1.0:
                win_rate /= 100.0
            wins = win_rate * battles

        total_battles += battles
        total_wins += float(wins or 0.0)

    if total_battles <= 0:
        return 0, None

    return total_battles, round((total_wins / total_battles) * 100, 2)


def _find_explicit_bin_index(value: float, edges: list[int]) -> Optional[int]:
    if len(edges) < 2:
        return None

    for index, lower in enumerate(edges[:-1]):
        upper = edges[index + 1]
        if index == len(edges) - 2:
            if value >= lower:
                return index
        elif lower <= value < upper:
            return index

    return None


def _tier_type_sort_key(ship_type: str, ship_tier: Optional[int] = None) -> tuple[int, str, int]:
    tier_component = -(ship_tier or 0)
    return (PLAYER_TIER_TYPE_ORDER.get(ship_type, len(PLAYER_TIER_TYPE_ORDER)), ship_type, tier_component)


def _build_tier_type_x_labels(observed_ship_types: set[str]) -> list[str]:
    canonical_labels = [
        ship_type
        for ship_type, _ in sorted(
            PLAYER_TIER_TYPE_ORDER.items(),
            key=lambda item: item[1],
        )
    ]
    extra_labels = sorted(
        [
            ship_type
            for ship_type in observed_ship_types
            if ship_type not in PLAYER_TIER_TYPE_ORDER
        ],
        key=lambda ship_type: _tier_type_sort_key(ship_type),
    )
    return canonical_labels + extra_labels


def _build_tier_type_y_values() -> list[int]:
    return list(range(11, 0, -1))


def _extend_tier_type_x_labels(x_labels: list[str], player_cells: list[dict]) -> list[str]:
    labels = list(x_labels)
    seen = set(labels)
    extra_labels = sorted(
        {
            str(cell['ship_type'])
            for cell in player_cells
            if str(cell['ship_type']) not in seen
        },
        key=lambda ship_type: _tier_type_sort_key(ship_type),
    )
    labels.extend(extra_labels)
    return labels


def _extract_tier_type_battle_rows(battles_json: Any) -> list[dict[str, int | float | str]]:
    if not isinstance(battles_json, list):
        return []

    normalized_rows: list[dict[str, int | float | str]] = []
    for row in battles_json:
        if not isinstance(row, dict):
            continue

        ship_type = row.get('ship_type')
        if not isinstance(ship_type, str) or not ship_type.strip():
            continue

        try:
            ship_tier = int(row.get('ship_tier'))
            pvp_battles = int(row.get('pvp_battles', 0) or 0)
            wins = int(row.get('wins', 0) or 0)
        except (TypeError, ValueError):
            continue

        if ship_tier <= 0 or pvp_battles <= 0:
            continue

        resolved_type = _SHIP_TYPE_ALIASES.get(
            ship_type.strip(), ship_type.strip())
        if resolved_type in _SHIP_TYPE_EXCLUDED_FROM_HEATMAP:
            continue

        normalized_rows.append({
            'ship_type': resolved_type,
            'ship_tier': ship_tier,
            'pvp_battles': pvp_battles,
            'wins': max(wins, 0),
        })

    return normalized_rows


def _build_tier_type_player_cells(battles_json: Any) -> list[dict]:
    aggregates: dict[tuple[str, int], dict[str, int]] = {}
    for row in _extract_tier_type_battle_rows(battles_json):
        ship_type = str(row['ship_type'])
        ship_tier = int(row['ship_tier'])
        aggregate = aggregates.setdefault((ship_type, ship_tier), {
            'pvp_battles': 0,
            'wins': 0,
        })
        aggregate['pvp_battles'] += int(row['pvp_battles'])
        aggregate['wins'] += int(row['wins'])

    player_cells = []
    for (ship_type, ship_tier), aggregate in aggregates.items():
        battles = aggregate['pvp_battles']
        wins = aggregate['wins']
        player_cells.append({
            'ship_type': ship_type,
            'ship_tier': ship_tier,
            'pvp_battles': battles,
            'wins': wins,
            'win_ratio': round(wins / battles, 4) if battles > 0 else 0.0,
        })

    player_cells.sort(
        key=lambda row: (-row['pvp_battles'], _tier_type_sort_key(row['ship_type'], row['ship_tier'])))
    return player_cells


# Aggregates the tier-type population entirely inside Postgres (jsonb), so we
# never stream every qualifying player's battles_json into Python — that
# per-element parse + transfer was a ~8 min full scan per realm. The `()`
# grouping-set row carries tracked_population (distinct players among non-Unknown
# qualifying rows); the other rows are per-(ship_type, ship_tier) battle sums.
# The WHERE clause mirrors `_extract_tier_type_battle_rows` exactly: object
# elements, non-empty string ship_type, JSON-number ship_tier/pvp_battles both
# truncated-to-int and > 0, the AirCarrier alias, and the Unknown exclusion.
_TIER_TYPE_POPULATION_SQL = """
WITH qualifying AS (
    SELECT
        p.player_id,
        CASE WHEN btrim(elem->>'ship_type') = 'AirCarrier'
             THEN 'Aircraft Carrier'
             ELSE btrim(elem->>'ship_type') END AS ship_type,
        trunc((elem->>'ship_tier')::numeric)::int   AS ship_tier,
        trunc((elem->>'pvp_battles')::numeric)::int AS pvp_battles
    FROM warships_player p
    CROSS JOIN LATERAL jsonb_array_elements(p.battles_json) AS elem
    WHERE p.realm = %s
      AND p.is_hidden = false
      AND p.pvp_battles >= %s
      AND p.battles_json IS NOT NULL
      AND jsonb_typeof(p.battles_json) = 'array'
      AND jsonb_typeof(elem) = 'object'
      AND jsonb_typeof(elem->'ship_type') = 'string'
      AND btrim(elem->>'ship_type') <> ''
      AND jsonb_typeof(elem->'ship_tier') = 'number'
      AND jsonb_typeof(elem->'pvp_battles') = 'number'
      AND trunc((elem->>'ship_tier')::numeric)::int > 0
      AND trunc((elem->>'pvp_battles')::numeric)::int > 0
)
SELECT ship_type, ship_tier,
       SUM(pvp_battles)::bigint AS battles,
       COUNT(DISTINCT player_id) AS players
FROM qualifying
WHERE ship_type <> 'Unknown'
GROUP BY GROUPING SETS ((ship_type, ship_tier), ())
"""


def _aggregate_tier_type_population_sql(realm: str, min_population_battles: int) -> tuple[dict[tuple[str, int], int], int]:
    """Return (tile_counts, tracked_population) via a single in-Postgres jsonb
    aggregation. See `_TIER_TYPE_POPULATION_SQL`."""
    tile_counts: dict[tuple[str, int], int] = {}
    tracked_population = 0
    with connection.cursor() as cursor:
        cursor.execute(_TIER_TYPE_POPULATION_SQL,
                       [realm, min_population_battles])
        for ship_type, ship_tier, battles, players in cursor.fetchall():
            if ship_type is None and ship_tier is None:
                tracked_population = int(players or 0)
            else:
                tile_counts[(ship_type, int(ship_tier))] = int(battles or 0)
    return tile_counts, tracked_population


def _aggregate_tier_type_population_python(realm: str, min_population_battles: int) -> tuple[dict[tuple[str, int], int], int]:
    """Proven-correct fallback: stream battles_json and aggregate in Python
    (the original ~8 min/realm path). Used only if the SQL aggregation raises."""
    tile_counts: dict[tuple[str, int], int] = {}
    tracked_population = 0
    rows = Player.objects.filter(
        realm=realm,
        is_hidden=False,
        pvp_battles__gte=min_population_battles,
        battles_json__isnull=False,
    ).values_list('battles_json', flat=True)
    for battles_json in rows.iterator(chunk_size=1000):
        normalized_rows = _extract_tier_type_battle_rows(battles_json)
        if not normalized_rows:
            continue
        tracked_population += 1
        for row in normalized_rows:
            key = (str(row['ship_type']), int(row['ship_tier']))
            tile_counts[key] = tile_counts.get(
                key, 0) + int(row['pvp_battles'])
    return tile_counts, tracked_population


def _fetch_player_tier_type_population_correlation(realm: str = DEFAULT_REALM, *, allow_rebuild: bool = True, force_rebuild: bool = False) -> dict:
    cache_key = _player_correlation_cache_key(
        PLAYER_TIER_TYPE_CACHE_VERSION, realm=realm)
    published_cache_key = _player_correlation_published_cache_key(
        PLAYER_TIER_TYPE_CACHE_VERSION, realm=realm)
    # force_rebuild (used by the periodic warmer) must skip the read
    # short-circuit below — otherwise the durable `published` fallback is
    # returned before the rebuild, so a once-published empty/stale payload can
    # never be replaced. The rebuild overwrites both keys at the end, so the
    # old published value keeps serving until the fresh one lands (no gap).
    if not force_rebuild:
        cached = cache.get(cache_key)
        if cached is not None:
            cache.set(published_cache_key, cached, timeout=None)
            return cached

        published = cache.get(published_cache_key)
        if published is not None:
            return published

        if not allow_rebuild:
            return None

    config = PLAYER_TIER_TYPE_CORRELATION_CONFIG
    min_population_battles = config['min_population_battles']

    try:
        with transaction.atomic(), _elevated_work_mem():
            tile_counts, tracked_population = _aggregate_tier_type_population_sql(
                realm, min_population_battles)
    except Exception:
        logging.exception(
            "tier_type SQL aggregation failed for realm=%s; "
            "falling back to the Python scan", realm)
        with transaction.atomic(), _elevated_work_mem():
            tile_counts, tracked_population = _aggregate_tier_type_population_python(
                realm, min_population_battles)

    # trend and the observed ship-type set are fully derivable from the raw
    # per-(type, tier) battle sums (avg_tier = Σ tier·battles / Σ battles).
    # Derive from the unfiltered tile_counts so any tier outside the 1–11 board
    # still contributes to the trend exactly as the row-by-row scan did.
    observed_ship_types: set[str] = {ship_type for (ship_type, _tier) in tile_counts}
    trend_tier_weighted_sum: dict[str, float] = {}
    trend_battles: dict[str, int] = {}
    for (ship_type, ship_tier), count in tile_counts.items():
        trend_tier_weighted_sum[ship_type] = trend_tier_weighted_sum.get(
            ship_type, 0.0) + (ship_tier * count)
        trend_battles[ship_type] = trend_battles.get(ship_type, 0) + count

    x_labels = _build_tier_type_x_labels(observed_ship_types)
    y_values = _build_tier_type_y_values()
    x_index_by_label = {label: index for index, label in enumerate(x_labels)}
    y_index_by_value = {value: index for index, value in enumerate(y_values)}

    tiles = [
        {
            'x_index': x_index_by_label[ship_type],
            'y_index': y_index_by_value[ship_tier],
            'count': count,
        }
        for (ship_type, ship_tier), count in sorted(
            tile_counts.items(),
            key=lambda item: _tier_type_sort_key(item[0][0], item[0][1]),
        )
        if ship_type in x_index_by_label and ship_tier in y_index_by_value
    ]

    trend = [
        {
            'x_index': x_index_by_label[ship_type],
            'avg_tier': round(trend_tier_weighted_sum[ship_type] / total_battles, 4),
            'count': total_battles,
        }
        for ship_type, total_battles in sorted(
            trend_battles.items(),
            key=lambda item: _tier_type_sort_key(item[0]),
        )
        if total_battles > 0 and ship_type in x_index_by_label
    ]

    payload = {
        'metric': 'tier_type',
        'label': config['label'],
        'x_label': config['x_label'],
        'y_label': config['y_label'],
        'tracked_population': tracked_population,
        'x_labels': x_labels,
        'y_values': y_values,
        'tiles': tiles,
        'trend': trend,
    }
    cache.set(cache_key, payload, PLAYER_CORRELATION_CACHE_TTL)
    cache.set(published_cache_key, payload, timeout=None)
    return payload


def warm_player_tier_type_population_correlation(realm: str = DEFAULT_REALM) -> dict:
    """Refresh the tier-type population correlation cache.

    The rebuild is a full scan over every qualifying player's ``battles_json``
    (~8 min/realm on prod), so we only force it when the TTL'd cache is stale
    or empty — including a realm frozen at ``tracked_population=0`` by a warm
    that ran before its population was enriched (the original asia bug). A
    fresh, non-empty cache short-circuits to a cheap no-op, so the periodic
    (≤55 min) warmer runs the heavy scan at most once per
    ``PLAYER_CORRELATION_CACHE_TTL`` (12 h) per realm instead of every cycle.
    """
    cache_key = _player_correlation_cache_key(
        PLAYER_TIER_TYPE_CACHE_VERSION, realm=realm)
    fresh = cache.get(cache_key)
    if isinstance(fresh, dict) and fresh.get('tracked_population', 0) > 0:
        return fresh
    return _fetch_player_tier_type_population_correlation(realm=realm, force_rebuild=True)


def warm_player_wr_survival_correlation(realm: str = DEFAULT_REALM) -> dict:
    """Force-rebuild the win-rate vs survival correlation cache."""
    return fetch_player_wr_survival_correlation(realm=realm, force_rebuild=True)


def warm_player_correlations(realm: str = DEFAULT_REALM) -> dict:
    """Pre-warm all population correlation caches."""
    results = {}

    tier_type = warm_player_tier_type_population_correlation(realm=realm)
    results['tier_type'] = {
        'tracked_population': tier_type.get('tracked_population', 0)}

    win_rate_survival = warm_player_wr_survival_correlation(realm=realm)
    results['win_rate_survival'] = {
        'tracked_population': win_rate_survival.get('tracked_population', 0)}

    ranked = warm_player_ranked_wr_battles_population_correlation(realm=realm)
    results['ranked_wr_battles'] = {
        'tracked_population': ranked.get('tracked_population', 0)}

    return results


def fetch_player_tier_type_correlation(player_id: str, player: Player | None = None, realm: str = DEFAULT_REALM) -> dict:
    player = player or Player.objects.get(player_id=player_id, realm=realm)
    population_payload = _fetch_player_tier_type_population_correlation(
        realm=realm, allow_rebuild=False)

    if population_payload is None:
        _dispatch_async_correlation_warm(realm=realm)
        config = PLAYER_TIER_TYPE_CORRELATION_CONFIG
        return {
            'metric': 'tier_type',
            'label': config['label'],
            'x_label': config['x_label'],
            'y_label': config['y_label'],
            'tracked_population': 0,
            'x_labels': [],
            'y_values': _build_tier_type_y_values(),
            'tiles': [],
            'trend': [],
            'player_cells': [],
            '_population_pending': True,
        }

    if player.battles_json is None:
        # Never fetched — legitimately warming. Kick a refresh; the view signals
        # X-Tier-Type-Pending and the client polls until it lands. An empty list
        # (battles_json == []) is NOT this case: it means the fetch ran and came
        # back empty (hidden profile / no ships) and will never repopulate, so we
        # fall through to a terminal empty player_cells instead of re-dispatching
        # a refresh loop that spins the chart forever.
        _dispatch_async_refresh(update_battle_data_task,
                                player_id=player_id, realm=realm)
        return {
            **population_payload,
            'player_cells': [],
        }

    player_cells = _build_tier_type_player_cells(player.battles_json)

    return {
        **population_payload,
        'x_labels': _extend_tier_type_x_labels(population_payload['x_labels'], player_cells),
        'player_cells': player_cells,
    }


def fetch_player_wr_survival_correlation(realm: str = DEFAULT_REALM, *, force_rebuild: bool = False) -> dict:
    cache_key = _player_correlation_cache_key('win_rate_survival', realm=realm)
    published_cache_key = _player_correlation_published_cache_key(
        'win_rate_survival', realm=realm)
    # force_rebuild (periodic warmer) skips the read short-circuit so a stale
    # `published` payload can be replaced; see the tier-type builder for the
    # full rationale. Both keys are overwritten at the end.
    if not force_rebuild:
        cached = cache.get(cache_key)
        if cached is not None:
            cache.set(published_cache_key, cached, timeout=None)
            return cached

        published = cache.get(published_cache_key)
        if published is not None:
            return published

    config = PLAYER_WR_SURVIVAL_CORRELATION_CONFIG
    x_min = config['x_min']
    x_max = config['x_max']
    x_bin_width = config['x_bin_width']
    y_min = config['y_min']
    y_max = config['y_max']
    y_bin_width = config['y_bin_width']
    x_bin_count = int((x_max - x_min) / x_bin_width)
    y_bin_count = int((y_max - y_min) / y_bin_width)

    tile_counts: dict[tuple[int, int], int] = {}
    trend_sum_y = [0.0 for _ in range(x_bin_count)]
    trend_counts = [0 for _ in range(x_bin_count)]

    tracked_population = 0
    sum_x = 0.0
    sum_y = 0.0
    sum_xy = 0.0
    sum_x2 = 0.0
    sum_y2 = 0.0

    with transaction.atomic(), _elevated_work_mem():
        # Savepoint-isolate the unmanaged-Mv probe (see fetch_player_population_
        # distribution): a missing-relation error must roll back to the
        # savepoint, not abort this outer atomic — psycopg3 won't run the
        # fallback iterator on an aborted transaction.
        try:
            with transaction.atomic():
                mv_qs = MvPlayerDistributionStats.objects.filter(
                    realm=realm,
                    pvp_battles__gte=config['min_population_battles'],
                    pvp_ratio__isnull=False,
                    pvp_survival_rate__isnull=False,
                    pvp_survival_rate__gte=config['min_survival_rate'],
                )
                if not mv_qs.exists():
                    raise MvPlayerDistributionStats.DoesNotExist
                rows = mv_qs.values_list('pvp_ratio', 'pvp_survival_rate')
        except Exception:
            rows = Player.objects.filter(
                realm=realm,
                is_hidden=False,
                pvp_battles__gte=config['min_population_battles'],
                pvp_ratio__isnull=False,
                pvp_survival_rate__isnull=False,
                pvp_survival_rate__gte=config['min_survival_rate'],
            ).values_list('pvp_ratio', 'pvp_survival_rate')

        for win_rate, survival_rate in rows.iterator(chunk_size=5000):
            if win_rate is None or survival_rate is None:
                continue

            # Axes flipped: x = survival rate, y = win rate
            x_value = float(survival_rate)
            y_value = float(win_rate)

            tracked_population += 1
            sum_x += x_value
            sum_y += y_value
            sum_xy += x_value * y_value
            sum_x2 += x_value * x_value
            sum_y2 += y_value * y_value

            x_clamped = _clamp_to_open_upper_bound(x_value, x_min, x_max)
            y_clamped = _clamp_to_open_upper_bound(y_value, y_min, y_max)

            x_index = min(int((x_clamped - x_min) / x_bin_width),
                          x_bin_count - 1)
            y_index = min(int((y_clamped - y_min) / y_bin_width),
                          y_bin_count - 1)

            tile_counts[(x_index, y_index)] = tile_counts.get(
                (x_index, y_index), 0) + 1
            trend_sum_y[x_index] += y_value
            trend_counts[x_index] += 1

    tiles = []
    for (x_index, y_index), count in sorted(tile_counts.items()):
        tiles.append({
            'x_index': x_index,
            'y_index': y_index,
            'count': count,
        })

    trend = []
    for index, count in enumerate(trend_counts):
        if count == 0:
            continue

        trend.append({
            'x_index': index,
            'y': round(trend_sum_y[index] / count, 4),
            'count': count,
        })

    payload = {
        'metric': 'win_rate_survival',
        'label': config['label'],
        'x_label': config['x_label'],
        'y_label': config['y_label'],
        'tracked_population': tracked_population,
        'correlation': round(_pearson_correlation(tracked_population, sum_x, sum_y, sum_xy, sum_x2, sum_y2), 4) if tracked_population > 1 else None,
        'x_domain': {
            'min': x_min,
            'max': x_max,
            'bin_width': x_bin_width,
        },
        'y_domain': {
            'min': y_min,
            'max': y_max,
            'bin_width': y_bin_width,
        },
        'tiles': tiles,
        'trend': trend,
    }

    cache.set(cache_key, payload, PLAYER_CORRELATION_CACHE_TTL)
    cache.set(published_cache_key, payload, timeout=None)
    return payload


def _build_empty_player_ranked_wr_battles_population_correlation_payload() -> dict:
    config = PLAYER_RANKED_WR_BATTLES_CORRELATION_CONFIG
    seed_edges = config['base_x_edges']
    base_edge = float(seed_edges[0]) if seed_edges else 1.0
    x_max = float(max(base_edge * 2, base_edge + 1))

    return {
        'metric': 'ranked_wr_battles',
        'label': config['label'],
        'x_label': config['x_label'],
        'y_label': config['y_label'],
        'x_scale': config['x_scale'],
        'y_scale': config['y_scale'],
        'x_ticks': [base_edge, x_max],
        'x_edges': [base_edge, x_max],
        'tracked_population': 0,
        'correlation': None,
        'y_domain': {
            'min': config['y_min'],
            'max': config['y_max'],
            'bin_width': config['y_bin_width'],
        },
        'tiles': [],
        'trend': [],
    }


def _build_player_ranked_wr_battles_population_correlation_payload(realm: str = DEFAULT_REALM) -> dict:
    config = PLAYER_RANKED_WR_BATTLES_CORRELATION_CONFIG
    y_min = config['y_min']
    y_max = config['y_max']
    y_bin_width = config['y_bin_width']
    y_bin_count = int((y_max - y_min) / y_bin_width)

    records: list[tuple[int, float]] = []
    max_battles = config['min_battles']

    with transaction.atomic(), _elevated_work_mem():
        rows = Player.objects.filter(
            realm=realm,
            is_hidden=False,
            ranked_json__isnull=False,
        ).values_list('ranked_json', flat=True)

        for ranked_rows in rows.iterator(chunk_size=2000):
            total_battles, win_rate = _calculate_ranked_record(ranked_rows)
            if total_battles < config['min_battles'] or win_rate is None:
                continue

            records.append((total_battles, win_rate))
            max_battles = max(max_battles, total_battles)

    x_edges = _build_geometric_bin_edges(
        max_battles,
        config['base_x_edges'],
        config['x_bin_growth_factor'],
    )
    major_x_ticks = _build_doubling_bin_edges(
        max_battles, config['base_x_edges'])
    tile_counts: dict[tuple[int, int], int] = {}
    trend_sum_y = [0.0 for _ in range(len(x_edges) - 1)]
    trend_counts = [0 for _ in range(len(x_edges) - 1)]
    tracked_population = 0
    sum_x = 0.0
    sum_y = 0.0
    sum_xy = 0.0
    sum_x2 = 0.0
    sum_y2 = 0.0

    for total_battles, win_rate in records:
        x_index = _find_explicit_bin_index(total_battles, x_edges)
        if x_index is None:
            continue

        y_clamped = _clamp_to_open_upper_bound(win_rate, y_min, y_max)
        y_index = min(int((y_clamped - y_min) / y_bin_width), y_bin_count - 1)

        tracked_population += 1
        sum_x += float(total_battles)
        sum_y += win_rate
        sum_xy += float(total_battles) * win_rate
        sum_x2 += float(total_battles) * float(total_battles)
        sum_y2 += win_rate * win_rate

        tile_counts[(x_index, y_index)] = tile_counts.get(
            (x_index, y_index), 0) + 1
        trend_sum_y[x_index] += win_rate
        trend_counts[x_index] += 1

    tiles = []
    for (x_index, y_index), count in sorted(tile_counts.items()):
        tiles.append({
            'x_index': x_index,
            'y_index': y_index,
            'count': count,
        })

    trend = []
    for index, count in enumerate(trend_counts):
        if count == 0:
            continue

        trend.append({
            'x_index': index,
            'y': round(trend_sum_y[index] / count, 4),
            'count': count,
        })

    return {
        'metric': 'ranked_wr_battles',
        'label': config['label'],
        'x_label': config['x_label'],
        'y_label': config['y_label'],
        'x_scale': config['x_scale'],
        'y_scale': config['y_scale'],
        'x_ticks': [float(tick) for tick in major_x_ticks],
        'x_edges': [float(edge) for edge in x_edges],
        'tracked_population': tracked_population,
        'correlation': round(_pearson_correlation(tracked_population, sum_x, sum_y, sum_xy, sum_x2, sum_y2), 4) if tracked_population > 1 else None,
        'y_domain': {
            'min': y_min,
            'max': y_max,
            'bin_width': y_bin_width,
        },
        'tiles': tiles,
        'trend': trend,
    }


def warm_player_ranked_wr_battles_population_correlation(realm: str = DEFAULT_REALM) -> dict:
    payload = _build_player_ranked_wr_battles_population_correlation_payload(
        realm=realm)
    cache_key = _player_correlation_cache_key(
        PLAYER_RANKED_WR_BATTLES_CORRELATION_CACHE_VERSION, realm=realm)
    published_cache_key = _player_correlation_published_cache_key(
        PLAYER_RANKED_WR_BATTLES_CORRELATION_CACHE_VERSION, realm=realm)
    cache.set(cache_key, payload, PLAYER_CORRELATION_CACHE_TTL)
    cache.set(published_cache_key, payload, timeout=None)
    return payload


def fetch_player_ranked_wr_battles_correlation(player_id: str, realm: str = DEFAULT_REALM) -> dict:
    from warships.tasks import queue_player_ranked_wr_battles_correlation_refresh

    player = Player.objects.get(player_id=player_id, realm=realm)
    ranked_rows = player.ranked_json if player.ranked_json is not None else []
    total_battles, win_rate = _calculate_ranked_record(ranked_rows)
    cache_key = _player_correlation_cache_key(
        PLAYER_RANKED_WR_BATTLES_CORRELATION_CACHE_VERSION, realm=realm)
    published_cache_key = _player_correlation_published_cache_key(
        PLAYER_RANKED_WR_BATTLES_CORRELATION_CACHE_VERSION, realm=realm)
    population_payload = cache.get(cache_key)
    pending = False
    if population_payload is not None:
        cache.set(published_cache_key, population_payload, timeout=None)
    else:
        published_payload = cache.get(published_cache_key)
        if published_payload is not None:
            population_payload = published_payload
            queue_player_ranked_wr_battles_correlation_refresh(realm=realm)
        else:
            queue_player_ranked_wr_battles_correlation_refresh(realm=realm)
            population_payload = _build_empty_player_ranked_wr_battles_population_correlation_payload()
            pending = True

    if population_payload is None:
        queue_player_ranked_wr_battles_correlation_refresh(realm=realm)
        population_payload = _build_empty_player_ranked_wr_battles_population_correlation_payload()
        pending = True

    result = {
        **population_payload,
        'player_point': {
            'x': float(total_battles),
            'y': win_rate,
            'label': player.name,
        } if total_battles > 0 and win_rate is not None else None,
    }
    result['_pending'] = pending
    return result


def fetch_wr_distribution(realm: str = DEFAULT_REALM) -> list[dict]:
    """Return a histogram of player WR distribution, cached for 1 hour."""
    payload = fetch_player_population_distribution('win_rate', realm=realm)
    return [
        {
            'wr_min': row['bin_min'],
            'wr_max': row['bin_max'],
            'count': row['count'],
        }
        for row in payload['bins']
    ]


RANKED_SEASONS_CACHE_KEY = 'ranked:seasons:metadata'
RANKED_SEASONS_CACHE_TTL = 86400  # 24 hours in seconds
CLAN_BATTLE_SEASONS_CACHE_KEY = 'clan_battles:seasons:metadata'
CLAN_BATTLE_SEASONS_CACHE_TTL = 86400
# WG's `clans/season/` id space: regular ladder seasons are 1..~34; ids 100+
# are historical brawl/special events (101+/201+/301+, all 2018-2021 dates).
# Current-season resolution and the rollover self-heal only consider regular
# ids (verified against the live payload 2026-07-15; revisit if regular
# seasons ever approach 100 — ~16 years away at 3/yr).
CLAN_BATTLE_REGULAR_SEASON_MAX_ID = 99
CLAN_BATTLE_PLAYER_STATS_CACHE_TTL = 21600
CLAN_BATTLE_SUMMARY_CACHE_TTL = 3600
# Short negative-cache TTL for a player's CB season stats when the upstream
# fetch fails (commonly REQUEST_LIMIT_EXCEEDED during warm bursts). Keeps us
# from poisoning the 6h cache — and the aggregated clan summary — with a wrong
# "0 clan-battle battles" until the next retry.
CLAN_BATTLE_PLAYER_STATS_ERROR_TTL = max(
    1, int(os.getenv('CLAN_BATTLE_PLAYER_STATS_ERROR_TTL', '300')))
# Per-task thread fan-out for the per-member clans/seasonstats/ fetch in
# refresh_clan_battle_seasons_cache. WG rejects batched account_id on this
# endpoint, so each member is a separate call; capped low (× the hydration
# worker concurrency) to stay under WG's ~10 req/s per-app-id ceiling,
# especially while the clan crawl is also hitting the API.
CLAN_BATTLE_SUMMARY_FETCH_CONCURRENCY = max(
    1, int(os.getenv('CLAN_BATTLE_SUMMARY_FETCH_CONCURRENCY', '3')))


def _get_clan_battle_summary_cache_key(clan_id: str, realm: str = DEFAULT_REALM) -> str:
    return realm_cache_key(realm, f'clan_battles:summary:v2:{clan_id}')


def has_clan_battle_summary_cache(clan_id: str, realm: str = DEFAULT_REALM) -> bool:
    return cache.get(_get_clan_battle_summary_cache_key(clan_id, realm=realm)) is not None


def _invalidate_clan_battle_summary_cache(clan_id: str, realm: str = DEFAULT_REALM) -> None:
    cache.delete(_get_clan_battle_summary_cache_key(clan_id, realm=realm))


def _clan_battle_season_sort_key(summary: dict) -> tuple:
    start_date = summary.get('start_date')
    end_date = summary.get('end_date')

    parsed_start = datetime.min
    parsed_end = datetime.min

    if start_date:
        try:
            parsed_start = datetime.strptime(start_date, '%Y-%m-%d')
        except ValueError:
            pass

    if end_date:
        try:
            parsed_end = datetime.strptime(end_date, '%Y-%m-%d')
        except ValueError:
            pass

    return parsed_start, parsed_end, summary.get('season_id', 0)


def _upsert_ranked_seasons_reference(metadata: dict) -> None:
    """Persist fetched season metadata into the durable RankedSeason table.

    Prod Redis is `allkeys-lru` (even no-TTL keys can evict), so the season
    dates behind the Ranked Enjoyer icon's current-season resolution need a DB
    home that survives eviction and WG outages.
    """
    from warships.models import RankedSeason

    for season_id, info in metadata.items():
        try:
            RankedSeason.objects.update_or_create(
                season_id=int(season_id),
                defaults={
                    'name': info.get('name') or '',
                    'label': info.get('label') or '',
                    'start_date': info.get('start_date'),
                    'end_date': info.get('end_date'),
                },
            )
        except Exception:
            logging.exception(
                'Failed to upsert RankedSeason %s', season_id)


def _ranked_seasons_metadata_from_db() -> dict:
    """Rebuild the metadata dict from the durable RankedSeason reference."""
    from warships.models import RankedSeason

    return {
        row.season_id: {
            'name': row.name,
            'label': row.label,
            'start_date': row.start_date.strftime('%Y-%m-%d') if row.start_date else None,
            'end_date': row.end_date.strftime('%Y-%m-%d') if row.end_date else None,
        }
        for row in RankedSeason.objects.all()
    }


def _get_ranked_seasons_metadata(force_refresh: bool = False) -> dict:
    """Return season_id → {name, label, start_date, end_date}.

    Resolution order: Redis fresh key (24h) → WG `seasons/info/` fetch (which
    also upserts the durable RankedSeason table) → RankedSeason DB read when
    the WG fetch fails (not re-cached, so the next call retries WG).
    `force_refresh` skips the Redis read — the self-healing rollover in
    `update_ranked_data` uses it when a player's rank_info names a season newer
    than anything on record.
    """
    from warships.api.players import _fetch_ranked_seasons_info

    if not force_refresh:
        cached = cache.get(RANKED_SEASONS_CACHE_KEY)
        if cached is not None:
            return cached

    raw = _fetch_ranked_seasons_info()
    if not raw:
        return _ranked_seasons_metadata_from_db()

    result = {}
    for sid, info in raw.items():
        sid_int = int(sid)
        season_name = info.get('season_name', f'Season {sid_int - 1000}')
        label = f'S{sid_int - 1000}'
        start_ts = info.get('start_at')
        close_ts = info.get('close_at')
        result[sid_int] = {
            'name': season_name,
            'label': label,
            'start_date': datetime.fromtimestamp(start_ts).strftime('%Y-%m-%d') if start_ts else None,
            'end_date': datetime.fromtimestamp(close_ts).strftime('%Y-%m-%d') if close_ts else None,
        }

    _upsert_ranked_seasons_reference(result)
    cache.set(RANKED_SEASONS_CACHE_KEY, result, RANKED_SEASONS_CACHE_TTL)
    return result


def get_current_ranked_season_id() -> Optional[int]:
    """Newest ranked season that has started, or None when nothing is on record.

    "Latest season persists" heuristic: the current season stays current
    through the off-season gap until the next one starts; a season listed with
    a future start_date is not yet current. Reads only the durable RankedSeason
    table — never the WG API — so it is safe on the request thread
    (clan_members, player serializer) and survives Redis eviction.
    """
    from warships.models import RankedSeason

    today = date.today()
    candidates = [
        row.season_id
        for row in RankedSeason.objects.only('season_id', 'start_date')
        if row.start_date is None or row.start_date <= today
    ]
    return max(candidates) if candidates else None


def _upsert_clan_battle_seasons_reference(metadata: dict) -> None:
    """Persist fetched CB season metadata into the durable ClanBattleSeason table.

    Same durability rationale as `_upsert_ranked_seasons_reference`: prod Redis
    is `allkeys-lru`, and the season dates behind the ClanBattleShieldIcon's
    current-season resolution must survive eviction and WG outages.
    """
    from warships.models import ClanBattleSeason

    for season_id, info in metadata.items():
        try:
            ClanBattleSeason.objects.update_or_create(
                season_id=int(season_id),
                defaults={
                    'name': info.get('name') or '',
                    'label': info.get('label') or '',
                    'start_date': info.get('start_date'),
                    'end_date': info.get('end_date'),
                    'ship_tier_min': info.get('ship_tier_min'),
                    'ship_tier_max': info.get('ship_tier_max'),
                },
            )
        except Exception:
            logging.exception(
                'Failed to upsert ClanBattleSeason %s', season_id)


def _clan_battle_seasons_metadata_from_db() -> dict:
    """Rebuild the metadata dict from the durable ClanBattleSeason reference."""
    from warships.models import ClanBattleSeason

    return {
        row.season_id: {
            'name': row.name,
            'label': row.label,
            'start_date': row.start_date.strftime('%Y-%m-%d') if row.start_date else None,
            'end_date': row.end_date.strftime('%Y-%m-%d') if row.end_date else None,
            'ship_tier_min': row.ship_tier_min,
            'ship_tier_max': row.ship_tier_max,
        }
        for row in ClanBattleSeason.objects.all()
    }


def _get_clan_battle_seasons_metadata(force_refresh: bool = False) -> dict:
    """Return season_id -> clan battle season metadata. Cached for 24h.

    Resolution order: Redis fresh key (24h) → WG `clans/season/` fetch (which
    also upserts the durable ClanBattleSeason table) → ClanBattleSeason DB
    read when the WG fetch fails (not re-cached, so the next call retries WG).
    `force_refresh` skips the Redis read — the self-healing rollover in
    `fetch_player_clan_battle_seasons` uses it when a player's season stats
    name a regular season newer than anything on record.
    """
    if not force_refresh:
        cached = cache.get(CLAN_BATTLE_SEASONS_CACHE_KEY)
        if cached is not None:
            return cached

    raw = _fetch_clan_battle_seasons_info()
    if not raw:
        return _clan_battle_seasons_metadata_from_db()

    result = {}
    for sid, info in raw.items():
        sid_int = int(sid)
        start_ts = info.get('start_time')
        finish_ts = info.get('finish_time')
        result[sid_int] = {
            'name': info.get('name', f'Season {sid_int}'),
            'label': f'S{sid_int}',
            'start_date': datetime.fromtimestamp(start_ts).strftime('%Y-%m-%d') if start_ts else None,
            'end_date': datetime.fromtimestamp(finish_ts).strftime('%Y-%m-%d') if finish_ts else None,
            'ship_tier_min': info.get('ship_tier_min'),
            'ship_tier_max': info.get('ship_tier_max'),
        }

    _upsert_clan_battle_seasons_reference(result)
    cache.set(CLAN_BATTLE_SEASONS_CACHE_KEY,
              result, CLAN_BATTLE_SEASONS_CACHE_TTL)
    return result


def get_current_clan_battle_season_id() -> Optional[int]:
    """Newest started regular CB season, or None when nothing is on record.

    Same "latest season persists" heuristic as `get_current_ranked_season_id`
    — the current season stays current through the off-season gap until the
    next one starts; a future-dated season is not yet current — but resolved
    by max (start_date, season_id) instead of max id, and filtered to regular
    ladder ids (< CLAN_BATTLE_REGULAR_SEASON_MAX_ID): WG's `clans/season/` id
    space mixes regular seasons (1..~34) with 2018-2021 brawl/special events
    at ids 101+/201+/301+, which max(season_id) would wrongly pick (verified
    against the live payload 2026-07-15). Reads only the durable table —
    never the WG API — so it is safe on the request thread and survives Redis
    eviction. Runbook:
    `agents/runbooks/runbook-cb-icon-current-season-2026-07-15.md`.
    """
    from warships.models import ClanBattleSeason

    today = date.today()
    candidates = [
        (row.start_date or date.min, row.season_id)
        for row in ClanBattleSeason.objects.only('season_id', 'start_date')
        if row.season_id <= CLAN_BATTLE_REGULAR_SEASON_MAX_ID
        and (row.start_date is None or row.start_date <= today)
    ]
    if not candidates:
        return None

    return max(candidates)[1]


def _player_clan_battle_season_cache_key(account_id: int, realm: str = DEFAULT_REALM) -> str:
    return realm_cache_key(realm, f'clan_battles:player:{account_id}')


def has_player_clan_battle_season_cache(account_id: int, realm: str = DEFAULT_REALM) -> bool:
    """True when a per-player CB-seasons cache entry exists (warm, incl. empty).

    Lets the view distinguish a genuinely-empty (already-fetched) player from
    a cold-miss player, so it only signals pending / re-queues for the latter.
    """
    return cache.get(_player_clan_battle_season_cache_key(int(account_id), realm=realm)) is not None


def _get_player_clan_battle_season_stats(
    account_id: int,
    realm: str = DEFAULT_REALM,
    allow_remote_fetch: bool = True,
) -> list | None:
    """Return cached clan battle season stats for a player.

    Returns None (not []) when the cache is cold and `allow_remote_fetch` is
    False — the signal that no WG fetch was performed, so callers must skip
    persistence (which would clobber the stored summary with zeros and fire a
    landing-cache invalidation). The request path passes False (cache-or-empty
    + queue async); the background task passes True (does the WG fetch).
    """
    cache_key = _player_clan_battle_season_cache_key(int(account_id), realm=realm)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    if not allow_remote_fetch:
        return None

    raw = _fetch_clan_battle_season_stats(account_id, realm=realm)
    if raw is None:
        # Upstream fetch failed (often REQUEST_LIMIT_EXCEEDED during warm
        # bursts). Cache empty only briefly so we retry soon instead of
        # persisting a wrong "no seasons" for the full TTL.
        cache.set(cache_key, [], CLAN_BATTLE_PLAYER_STATS_ERROR_TTL)
        return []

    seasons = raw.get('seasons', []) or []
    cache.set(cache_key, seasons, CLAN_BATTLE_PLAYER_STATS_CACHE_TTL)
    return seasons


def _persist_player_clan_battle_summary(
    account_id: int,
    summary: dict[str, Any],
    realm: str = DEFAULT_REALM,
    current_season: Optional[dict[str, Any]] = None,
) -> None:
    player = Player.objects.filter(player_id=account_id, realm=realm).first()
    if player is None:
        return

    explorer_summary, created = PlayerExplorerSummary.objects.get_or_create(
        player=player, defaults={'realm': player.realm})
    if not created and not explorer_summary.realm:
        explorer_summary.realm = player.realm
    total_battles = int(summary.get('total_battles') or 0)
    seasons_participated = int(summary.get('seasons_participated') or 0)
    win_rate = summary.get('win_rate')
    payload_changed = any([
        explorer_summary.clan_battle_total_battles != total_battles,
        explorer_summary.clan_battle_seasons_participated != seasons_participated,
        explorer_summary.clan_battle_overall_win_rate != win_rate,
    ])

    explorer_summary.clan_battle_total_battles = total_battles
    explorer_summary.clan_battle_seasons_participated = seasons_participated
    explorer_summary.clan_battle_overall_win_rate = win_rate
    # Current-season participation behind the CB shield (all-None when the
    # season was unresolvable — the stale check retries those). Resolution
    # must stay DB-only here: persist also runs on the request thread when
    # the player's Redis season cache is warm.
    current_season = current_season or {
        'season_id': None, 'battles': None, 'win_rate': None}
    explorer_summary.clan_battle_current_season_id = current_season.get(
        'season_id')
    explorer_summary.clan_battle_current_season_battles = current_season.get(
        'battles')
    explorer_summary.clan_battle_current_season_win_rate = current_season.get(
        'win_rate')
    explorer_summary.clan_battle_summary_updated_at = django_timezone.now()
    explorer_summary.save(update_fields=[
        'realm',
        'clan_battle_total_battles',
        'clan_battle_seasons_participated',
        'clan_battle_overall_win_rate',
        'clan_battle_current_season_id',
        'clan_battle_current_season_battles',
        'clan_battle_current_season_win_rate',
        'clan_battle_summary_updated_at',
    ])


def fetch_player_clan_battle_seasons(
    account_id: int,
    realm: str = DEFAULT_REALM,
    allow_remote_fetch: bool = True,
) -> list:
    """Return a single player's clan battle seasons enriched with season metadata.

    `allow_remote_fetch=False` (request path) serves cache-or-empty without a
    synchronous WG call: on a cold cache it returns [] and skips persistence,
    leaving the caller to queue an async refresh + set the pending header.
    """
    if not account_id:
        return []

    seasons = _get_player_clan_battle_season_stats(
        int(account_id), realm=realm, allow_remote_fetch=allow_remote_fetch)
    if seasons is None:
        # Cold cache on the request path: no WG fetch performed. Return empty
        # without persisting (zero-clobber + landing-cache invalidation storm)
        # and without fetching season metadata (another cold WG call).
        return []

    # Metadata before persist, so a fresh WG metadata fetch has upserted the
    # durable ClanBattleSeason table that current-season resolution reads.
    season_meta = _get_clan_battle_seasons_metadata()

    # Self-healing rollover: the metadata key is 24h-cached, and WG lists new
    # seasons on its own schedule. A regular-season row newer than anything on
    # record means the reference is stale — refetch once before resolving.
    known_regular_ids = [
        sid for sid in season_meta
        if int(sid) <= CLAN_BATTLE_REGULAR_SEASON_MAX_ID
    ]
    max_known_regular = max(known_regular_ids, default=0)
    has_unknown_regular_season = any(
        0 < int(season.get('season_id', 0) or 0) <= CLAN_BATTLE_REGULAR_SEASON_MAX_ID
        and int(season.get('season_id', 0) or 0) > max_known_regular
        for season in seasons
    )
    if has_unknown_regular_season:
        season_meta = _get_clan_battle_seasons_metadata(force_refresh=True)

    current_season_id = get_current_clan_battle_season_id()
    _persist_player_clan_battle_summary(
        int(account_id),
        summarize_clan_battle_seasons(seasons),
        realm=realm,
        current_season=summarize_current_clan_battle_season(
            seasons, current_season_id),
    )
    result = []

    for season in seasons:
        battles = int(season.get('battles', 0) or 0)
        if battles <= 0:
            continue

        sid = int(season.get('season_id', 0) or 0)
        if sid <= 0:
            continue

        wins = int(season.get('wins', 0) or 0)
        losses = int(season.get('losses', 0) or 0)
        meta = season_meta.get(sid, {})
        result.append({
            'season_id': sid,
            'season_name': meta.get('name', f'Season {sid}'),
            'season_label': meta.get('label', f'S{sid}'),
            'start_date': meta.get('start_date'),
            'end_date': meta.get('end_date'),
            'ship_tier_min': meta.get('ship_tier_min'),
            'ship_tier_max': meta.get('ship_tier_max'),
            'battles': battles,
            'wins': wins,
            'losses': losses,
            'win_rate': round((wins / battles) * 100, 1) if battles > 0 else 0.0,
            # Server-computed currency marker: lets the live frontend fetch
            # update the CB shield without re-deriving season semantics.
            'is_current': current_season_id is not None and sid == current_season_id,
        })

    return sorted(result, key=_clan_battle_season_sort_key, reverse=True)


def fetch_clan_battle_seasons(clan_id: str, realm: str = DEFAULT_REALM) -> list:
    """Return cached clan battle summary, enqueueing background refresh on misses."""
    if not clan_id:
        return []

    cache_key = _get_clan_battle_summary_cache_key(clan_id, realm=realm)
    cached = cache.get(cache_key)
    if cached is not None:
        if cached:
            return cached

        try:
            clan = Clan.objects.get(clan_id=clan_id, realm=realm)
        except Clan.DoesNotExist:
            return []

        has_populated_roster = clan.members_count > 0 and clan.player_set.exclude(
            name='').exclude(player_id__isnull=True).exists()
        if has_populated_roster:
            from warships.tasks import queue_clan_battle_summary_refresh

            queue_clan_battle_summary_refresh(clan_id, realm=realm)

        return cached

    from warships.tasks import queue_clan_battle_summary_refresh

    queue_clan_battle_summary_refresh(clan_id, realm=realm)
    return []


def refresh_clan_battle_seasons_cache(clan_id: str, realm: str = DEFAULT_REALM) -> list:
    """Aggregate clan battle season stats across the clan's current roster and cache them."""
    if not clan_id:
        return []

    cache_key = _get_clan_battle_summary_cache_key(clan_id, realm=realm)

    try:
        clan = Clan.objects.get(clan_id=clan_id, realm=realm)
    except Clan.DoesNotExist:
        return []

    members = list(
        clan.player_set.exclude(name='').exclude(
            player_id__isnull=True).values('player_id', 'name')
    )

    if not members and clan.members_count:
        update_clan_members(clan_id=clan_id, realm=realm)
        members = list(
            clan.player_set.exclude(name='').exclude(
                player_id__isnull=True).values('player_id', 'name')
        )

    if not members:
        cache.set(cache_key, [], CLAN_BATTLE_SUMMARY_CACHE_TTL)
        return []

    season_meta = _get_clan_battle_seasons_metadata()
    season_summaries = {}

    with ThreadPoolExecutor(max_workers=CLAN_BATTLE_SUMMARY_FETCH_CONCURRENCY) as executor:
        futures = {
            executor.submit(_get_player_clan_battle_season_stats, member['player_id'], realm=realm): member
            for member in members
        }

        for future in as_completed(futures):
            member = futures[future]
            try:
                seasons = future.result()
            except Exception as error:
                logging.error(
                    'Failed clan battle stats fetch for %s (%s): %s',
                    member['name'],
                    member['player_id'],
                    error,
                )
                continue

            for season in seasons:
                battles = int(season.get('battles', 0) or 0)
                if battles <= 0:
                    continue

                sid = int(season.get('season_id', 0) or 0)
                if sid <= 0:
                    continue

                wins = int(season.get('wins', 0) or 0)
                losses = int(season.get('losses', 0) or 0)

                summary = season_summaries.setdefault(sid, {
                    'season_id': sid,
                    'season_name': season_meta.get(sid, {}).get('name', f'Season {sid}'),
                    'season_label': season_meta.get(sid, {}).get('label', f'S{sid}'),
                    'start_date': season_meta.get(sid, {}).get('start_date'),
                    'end_date': season_meta.get(sid, {}).get('end_date'),
                    'ship_tier_min': season_meta.get(sid, {}).get('ship_tier_min'),
                    'ship_tier_max': season_meta.get(sid, {}).get('ship_tier_max'),
                    'participants': 0,
                    'roster_battles': 0,
                    'roster_wins': 0,
                    'roster_losses': 0,
                    'clan_battles': 0,
                    'clan_wins': 0,
                })

                summary['participants'] += 1
                summary['roster_battles'] += battles
                summary['roster_wins'] += wins
                summary['roster_losses'] += losses
                if battles > summary['clan_battles']:
                    summary['clan_battles'] = battles
                    summary['clan_wins'] = wins

    result = []
    for summary in sorted(season_summaries.values(), key=_clan_battle_season_sort_key, reverse=True):
        battles = summary['roster_battles']
        summary['roster_win_rate'] = round(
            summary['roster_wins'] / battles * 100, 1) if battles > 0 else 0.0
        result.append(summary)

    cache.set(cache_key, result, CLAN_BATTLE_SUMMARY_CACHE_TTL)
    return result


def _aggregate_ranked_seasons(rank_info: dict, season_meta: dict, top_ship_names_by_season: Optional[dict[int, Optional[str]]] = None) -> list:
    """
    Transform the WG API rank_info structure into a flat list of per-season summaries.

    rank_info structure: {season_id: {sprint_key: {league_key: {battles, victories, rank, ...}}}}
    """
    seasons = []

    for sid_str, sprints in rank_info.items():
        if sprints is None:
            continue
        sid = int(sid_str)
        meta = season_meta.get(sid, {})

        total_battles = 0
        total_wins = 0
        highest_league = 99  # lower = better (1=Gold)
        best_sprint = None
        sprint_details = []

        for sprint_key, leagues in sprints.items():
            if leagues is None:
                continue
            sprint_battles = 0
            sprint_wins = 0
            sprint_best_league = 99
            sprint_best_rank = 99

            for league_key, sprint_data in leagues.items():
                if sprint_data is None or not isinstance(sprint_data, dict):
                    continue

                # Skip mode-style keys (rank_solo, rank_div2, etc.) — use only numeric league keys
                try:
                    lk = int(league_key)
                except (ValueError, TypeError):
                    continue

                b = int(sprint_data.get('battles', 0) or 0)
                w = int(sprint_data.get('victories', 0) or 0)
                rank = sprint_data.get('rank', 99)
                best_rank_in_sprint = sprint_data.get(
                    'best_rank_in_sprint', sprint_data.get('rank_best', 99))

                # Older WG ranked seasons can report archived sprint victories while zeroing
                # the corresponding battle counts. Ignore those impossible totals in aggregates.
                if b <= 0 and w > 0:
                    w = 0

                sprint_battles += b
                sprint_wins += w

                if lk < sprint_best_league or (lk == sprint_best_league and best_rank_in_sprint < sprint_best_rank):
                    sprint_best_league = lk
                    sprint_best_rank = best_rank_in_sprint

                if lk < highest_league:
                    highest_league = lk

            total_battles += sprint_battles
            total_wins += sprint_wins

            sprint_detail = {
                'sprint_number': int(sprint_key) if sprint_key.isdigit() else 0,
                'league': sprint_best_league if sprint_best_league < 99 else 3,
                'league_name': LEAGUE_NAMES.get(sprint_best_league, 'Bronze'),
                'rank': sprint_best_rank if sprint_best_rank < 99 else 10,
                'best_rank': sprint_best_rank if sprint_best_rank < 99 else 10,
                'battles': sprint_battles,
                'wins': sprint_wins,
            }
            sprint_details.append(sprint_detail)

            # Determine best sprint (highest league, then lowest rank, then most wins)
            if best_sprint is None or \
                    sprint_best_league < best_sprint['league'] or \
                    (sprint_best_league == best_sprint['league'] and sprint_best_rank < best_sprint['best_rank']) or \
                    (sprint_best_league == best_sprint['league'] and sprint_best_rank == best_sprint['best_rank'] and sprint_wins > best_sprint['wins']):
                best_sprint = sprint_detail

        if total_battles == 0:
            continue

        if highest_league > 3:
            highest_league = 3

        win_rate = round(total_wins / total_battles,
                         4) if total_battles > 0 else 0.0

        seasons.append({
            'season_id': sid,
            'season_name': meta.get('name', f'Season {sid - 1000}'),
            'season_label': meta.get('label', f'S{sid - 1000}'),
            'start_date': meta.get('start_date'),
            'end_date': meta.get('end_date'),
            'highest_league': highest_league,
            'highest_league_name': LEAGUE_NAMES.get(highest_league, 'Bronze'),
            'total_battles': total_battles,
            'total_wins': total_wins,
            'win_rate': win_rate,
            'top_ship_name': (top_ship_names_by_season or {}).get(sid),
            'best_sprint': best_sprint,
            'sprints': sorted(sprint_details, key=lambda x: x['sprint_number']),
        })

    # Persist the full non-empty ranked history so downstream views can render all seasons.
    seasons.sort(key=lambda x: x['season_id'])
    return seasons


def fetch_ranked_data(player_id: str, realm: str = DEFAULT_REALM) -> list:
    """Fetch ranked battles data for a player. Caches as ranked_json."""
    try:
        player = Player.objects.get(player_id=player_id, realm=realm)
    except Player.DoesNotExist:
        return []

    # Return cached if fresh
    if player.ranked_json is not None:
        if player_ranked_data_needs_refresh(player):
            from warships.tasks import queue_ranked_data_refresh

            queue_ranked_data_refresh(player_id, realm=realm)
        else:
            logging.info(f'Ranked data cache fresh for {player.name}')
        return player.ranked_json

    # Cold cache: never block the request thread on the WG API. Queue an
    # async refresh (dedup-guarded) and return empty now; the view sets
    # X-Ranked-Pending and RankedSeasons.tsx polls until the fetch lands.
    from warships.tasks import queue_ranked_data_refresh

    logging.info(f'Queueing cold ranked data refresh for {player.name}')
    queue_ranked_data_refresh(player_id, realm=realm)
    return []


def ranked_last_season_from_json(ranked_json) -> Optional[int]:
    """Highest ranked season_id with battles in `ranked_json`, or None.

    Drives the observation floor's random-first routing (heavy ranked sweep only
    for current-season players). Shared by `update_ranked_data` and the
    `backfill_ranked_last_season` command so both compute it identically.
    """
    if not ranked_json:
        return None
    seasons = [
        row["season_id"] for row in ranked_json
        if isinstance(row, dict) and int(row.get("total_battles") or 0) > 0
    ]
    return max(seasons) if seasons else None


def update_ranked_data(player_id, realm: str = DEFAULT_REALM) -> None:
    """Fetch ranked data from WG API, aggregate, and cache on Player model."""
    player = Player.objects.get(player_id=player_id, realm=realm)

    # Get season metadata (cached globally)
    season_meta = _get_ranked_seasons_metadata()

    # Get player's rank_info
    account_data = _fetch_ranked_account_info(int(player_id), realm=realm)
    rank_info = account_data.get('rank_info') if account_data else None

    if not rank_info:
        logging.info(f'No ranked data for {player.name}')
        player.ranked_json = []
        player.ranked_updated_at = datetime.now()
        player.ranked_last_season_id = None
        # Scoped save: this task only owns the ranked_* columns. A bare save() would
        # write back EVERY field on the snapshot loaded at the top — including a now-stale
        # battles_updated_at — clobbering a concurrent update_battle_data now()-write and
        # re-arming the "Updating…" pill (runbook-player-refresh-pill-clobber-2026-06-21).
        player.save(update_fields=[
            'ranked_json', 'ranked_updated_at', 'ranked_last_season_id'])
        return

    requested_season_ids = sorted(
        [int(season_id)
         for season_id in rank_info.keys() if str(season_id).isdigit()]
    )
    # Self-healing rollover: a season id newer than anything in the (24h-cached)
    # metadata means WG opened a new season since the last seasons/info fetch.
    # Refetch once so the new season lands in the durable RankedSeason reference
    # and the Ranked Enjoyer icon's current-season resolution rolls over without
    # waiting out the cache TTL.
    if requested_season_ids and (
            not season_meta or max(requested_season_ids) > max(season_meta)):
        season_meta = _get_ranked_seasons_metadata(force_refresh=True)
    ranked_ship_stats_rows = _fetch_ranked_ship_stats_for_player(
        int(player_id), season_ids=requested_season_ids, realm=realm)
    top_ship_names_by_season = _build_top_ranked_ship_names_by_season(
        ranked_ship_stats_rows, requested_season_ids)

    # Aggregate into per-season summaries
    result = _aggregate_ranked_seasons(
        rank_info, season_meta, top_ship_names_by_season=top_ship_names_by_season)

    if len(result) > 50:
        logging.warning(
            'Unusually large ranked history for %s (%s): %s seasons',
            player.name,
            player.player_id,
            len(result),
        )

    player.ranked_json = result
    player.ranked_updated_at = datetime.now()
    # Highest season_id the player actually has ranked battles in — drives the
    # observation floor's random-first routing (heavy ranked sweep only for
    # current-season players). NULL when they have no ranked battles.
    player.ranked_last_season_id = ranked_last_season_from_json(result)
    # Scoped save — see the no-rank_info branch above: a bare save() races a concurrent
    # update_battle_data now()-write on battles_updated_at and re-arms the live-refresh
    # pill (runbook-player-refresh-pill-clobber-2026-06-21).
    player.save(update_fields=[
        'ranked_json', 'ranked_updated_at', 'ranked_last_season_id'])
    refresh_player_explorer_summary(player, ranked_rows=result)
    logging.info(
        f'Updated ranked data for {player.name}: {len(result)} seasons')


def fetch_type_data(player_id: str, realm: str = DEFAULT_REALM) -> list:
    try:
        player = Player.objects.get(player_id=player_id, realm=realm)
        if not player.battles_json:
            _dispatch_async_refresh(
                update_battle_data_task, player_id=player_id, realm=realm)
            return player.type_json or []
    except Player.DoesNotExist:
        return []

    if player.type_json is not None:
        if player_battle_data_needs_refresh(player):
            _dispatch_async_refresh(
                update_battle_data_task, player_id=player_id, realm=realm)
        return player.type_json

    _dispatch_async_refresh(update_type_data_task, player_id, realm=realm)
    return []


def update_type_data(player_id: str, realm: str = DEFAULT_REALM) -> list:
    player = Player.objects.get(player_id=player_id, realm=realm)
    player.type_json = _aggregate_battles_by_key(
        player.battles_json, 'ship_type')
    player.type_updated_at = datetime.now()
    # Scoped: owns only type_* (bare-save audit, see update_tiers_data).
    player.save(update_fields=['type_json', 'type_updated_at'])

    logging.info(f'Updated type data for player {player.name}')


def fetch_randoms_data(player_id: str, realm: str = DEFAULT_REALM) -> list:
    try:
        player = Player.objects.get(player_id=player_id, realm=realm)
        if not player.battles_json:
            _dispatch_async_refresh(
                update_battle_data_task, player_id=player_id, realm=realm)
            return extract_randoms_rows(player.randoms_json, limit=20)
    except Player.DoesNotExist:
        return []

    if player.randoms_json is not None:
        has_required_fields = isinstance(player.randoms_json, list) and all(
            isinstance(row, dict) and 'ship_type' in row and 'ship_tier' in row
            for row in player.randoms_json
        )

        if not has_required_fields:
            _dispatch_async_refresh(
                update_randoms_data_task, player_id, realm=realm)
            return []

        if player_battle_data_needs_refresh(player):
            _dispatch_async_refresh(
                update_battle_data_task, player_id=player_id, realm=realm)
        return extract_randoms_rows(player.randoms_json, limit=20)

    extracted_battle_rows = extract_randoms_rows(
        player.battles_json, limit=20)
    if extracted_battle_rows:
        _dispatch_async_refresh(
            update_randoms_data_task, player_id, realm=realm)
        return extracted_battle_rows

    _dispatch_async_refresh(update_randoms_data_task, player_id, realm=realm)
    return []


def fetch_clan_plot_data(clan_id: str, filter_type: str = 'active', realm: str = DEFAULT_REALM) -> list:
    def build_plot_payload(members_queryset) -> list:
        data = []
        for member in members_queryset:
            battles = member.pvp_battles or 0
            if filter_type != 'all' and battles < 100:
                continue

            data.append({
                'player_name': member.name,
                'pvp_battles': battles,
                'pvp_ratio': member.pvp_ratio or 0
            })

        return sorted(data, key=lambda row: row.get('pvp_battles', 0), reverse=True)

    cache_key = realm_cache_key(realm, f'clan:plot:v1:{clan_id}:{filter_type}')
    cached = cache.get(cache_key)
    try:
        clan = Clan.objects.get(clan_id=clan_id, realm=realm)
    except Clan.DoesNotExist:
        return cached if cached is not None else []

    members = clan.player_set.exclude(name='').all()
    member_count = members.count()
    needs_clan_refresh = not clan.members_count or clan_detail_needs_refresh(
        clan)
    needs_member_refresh = member_count == 0 or (
        clan.members_count and member_count < clan.members_count
    )

    if needs_clan_refresh:
        _dispatch_async_refresh(update_clan_data_task,
                                clan_id=clan_id, realm=realm)
    if needs_member_refresh:
        _dispatch_async_refresh(update_clan_members_task,
                                clan_id=clan_id, realm=realm)

    if cached is not None:
        if cached:
            return cached

        if member_count == 0:
            return []

        payload = build_plot_payload(members)
        cache.set(cache_key, payload, CLAN_PLOT_DATA_CACHE_TTL)
        return payload

    if member_count == 0:
        return []

    payload = build_plot_payload(members)
    cache.set(cache_key, payload, CLAN_PLOT_DATA_CACHE_TTL)
    return payload


def update_clan_tier_distribution(clan_id: str, realm: str = DEFAULT_REALM) -> list:
    """
    Computes an aggregated distribution of pvp_battles at each Ship Tier (1-11)
    for all active players in the specified clan.  Uses partial data when some
    members are still missing tiers_json — queues hydration for those players
    and caches a shorter TTL so the next poll picks up the completed data.
    Returns: [{'ship_tier': 1, 'pvp_battles': 240}, ... {'ship_tier': 11, 'pvp_battles': 105}]
    """
    cache_key = realm_cache_key(realm, f'clan:tiers:v3:{clan_id}')

    hydrating_count = 0
    hydrated_count = 0
    tier_aggregates = {tier: 0 for tier in range(1, 12)}

    players = Player.objects.filter(
        clan__clan_id=clan_id, realm=realm, is_hidden=False
    ).values_list('player_id', 'tiers_json')

    from warships.tasks import update_tiers_data_task

    for player_id, tiers_json in players:
        if not tiers_json:
            update_tiers_data_task.delay(player_id=player_id, realm=realm)
            hydrating_count += 1
            continue

        hydrated_count += 1
        for row in tiers_json:
            tier = row.get('ship_tier')
            battles = row.get('pvp_battles', 0)
            if isinstance(tier, int) and 1 <= tier <= 11 and isinstance(battles, int):
                tier_aggregates[tier] += battles

    # If zero members have data, return empty so frontend shows pending state
    if hydrated_count == 0:
        return []

    data = []
    for tier in range(11, 0, -1):
        data.append({
            'ship_tier': tier,
            'pvp_battles': tier_aggregates[tier]
        })

    pending_key = realm_cache_key(realm, f'clan:tiers:v3:{clan_id}:pending')
    if hydrating_count > 0:
        # Partial data — cache for 10 minutes so next poll picks up newly hydrated members
        cache.set(cache_key, data, 600)
        cache.set(pending_key, True, 600)
    else:
        # Complete data — cache for 24h
        cache.set(cache_key, data, 86400)
        cache.delete(pending_key)

    return data


def compute_clan_member_avg_tiers(clan_id: str, realm: str = DEFAULT_REALM) -> list:
    """
    Returns per-member weighted average tier and KDR for a clan.
    Each entry: {'player_id': int, 'name': str, 'avg_tier': float|null, 'kdr': float|null}
    Caches for 24h (full data) or 10 min (partial, some members missing tiers_json).
    """
    cache_key = realm_cache_key(realm, f'clan:member_tiers:v2:{clan_id}')
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    players = Player.objects.filter(
        clan__clan_id=clan_id, realm=realm, is_hidden=False
    ).values_list('player_id', 'name', 'tiers_json', 'pvp_frags', 'pvp_deaths')

    results = []
    missing_count = 0
    for player_id, name, tiers_json, pvp_frags, pvp_deaths in players:
        kdr = round(pvp_frags / pvp_deaths,
                    2) if pvp_deaths and pvp_deaths > 0 else None

        if not tiers_json:
            results.append({'player_id': player_id, 'name': name,
                           'avg_tier': None, 'kdr': kdr})
            missing_count += 1
            continue

        total_battles = 0
        weighted_sum = 0
        for row in tiers_json:
            tier = row.get('ship_tier')
            battles = row.get('pvp_battles', 0)
            if isinstance(tier, int) and 1 <= tier <= 11 and isinstance(battles, int) and battles > 0:
                weighted_sum += tier * battles
                total_battles += battles

        avg_tier = round(weighted_sum / total_battles,
                         1) if total_battles > 0 else None
        if avg_tier is None:
            missing_count += 1
        results.append({'player_id': player_id, 'name': name,
                       'avg_tier': avg_tier, 'kdr': kdr})

    if missing_count > 0 and missing_count < len(results):
        cache.set(cache_key, results, 600)
    elif len(results) > 0:
        cache.set(cache_key, results, 86400)

    return results


def warm_all_clan_tier_distributions(realm: str = DEFAULT_REALM, batch_size: int = 100) -> dict:
    """
    Recalculates and caches tier distribution data for every clan with members.
    Processes in batches with short sleeps to avoid monopolising DB connections.
    Runs on the background queue — typically completes in 15-30 minutes.
    """
    import time as _time

    clan_ids = list(
        Clan.objects.filter(realm=realm, members_count__gt=0)
        .values_list('clan_id', flat=True)
        .order_by('clan_id')
    )
    total = len(clan_ids)
    warmed = 0
    skipped = 0

    logging.info(
        "warm_all_clan_tier_distributions: starting %d clans (realm=%s)", total, realm
    )

    for i in range(0, total, batch_size):
        batch = clan_ids[i: i + batch_size]
        for clan_id in batch:
            try:
                result = update_clan_tier_distribution(
                    str(clan_id), realm=realm)
                if result:
                    warmed += 1
                else:
                    skipped += 1  # needs player hydration, returned []
            except Exception:
                logging.exception(
                    "warm_all_clan_tier_distributions: error on clan %s", clan_id
                )
                skipped += 1

        # Brief pause between batches to yield DB connections
        if i + batch_size < total:
            _time.sleep(0.5)

        if (i + batch_size) % 1000 == 0 or i + batch_size >= total:
            logging.info(
                "warm_all_clan_tier_distributions: progress %d/%d (warmed=%d, skipped=%d)",
                min(i + batch_size, total), total, warmed, skipped,
            )

    logging.info(
        "warm_all_clan_tier_distributions: done (warmed=%d, skipped=%d, total=%d)",
        warmed, skipped, total,
    )
    return {"warmed": warmed, "skipped": skipped, "total": total}


def update_randoms_data(player_id: str, realm: str = DEFAULT_REALM) -> None:
    player = Player.objects.get(player_id=player_id, realm=realm)
    player.randoms_json = extract_randoms_rows(player.battles_json, limit=20)
    player.randoms_updated_at = datetime.now()
    # Scoped: owns only randoms_* (bare-save audit, see update_tiers_data).
    player.save(update_fields=['randoms_json', 'randoms_updated_at'])

    logging.info(f'Updated randoms data for player {player.name}')


def update_clan_data(clan_id: str, realm: str = DEFAULT_REALM) -> None:
    # return if no clan_id is provided
    if not clan_id:
        return

    try:
        clan = Clan.objects.get(clan_id=clan_id, realm=realm)
    except Clan.DoesNotExist:
        logging.info(
            f"Clan {clan_id} not found\n")
        return

    if clan.last_fetch and datetime.now() - clan.last_fetch < timedelta(minutes=1440):
        logging.debug(
            f'{clan.name}: Clan data is fresh')
        return

    data = _fetch_clan_data(clan_id, realm=realm)
    if not data:
        logging.warning(
            "Skipping clan update because upstream returned no data for clan_id=%s", clan_id)
        return

    clan.members_count = data.get('members_count', 0)
    clan.tag = data.get('tag', '')
    clan.name = data.get('name', '')
    clan.description = data.get('description', '')
    clan.leader_id = data.get('leader_id', None)
    clan.leader_name = data.get('leader_name', '')
    clan.last_fetch = datetime.now()
    # Scoped: owns only these account/info fields. A bare save() would write back
    # this task's stale snapshot of the cached-aggregate columns that
    # refresh_clan_cached_aggregates writes concurrently (bare-save audit).
    clan.save(update_fields=[
        'members_count', 'tag', 'name', 'description', 'leader_id',
        'leader_name', 'last_fetch'])
    _invalidate_clan_battle_summary_cache(clan_id, realm=realm)
    cache.delete(realm_cache_key(realm, f'clan:members:{clan_id}'))
    invalidate_clan_detail_cache(int(clan_id), realm=realm)
    logging.info(
        f"Updated clan data: {clan.name} [{clan.tag}]: {clan.members_count} members")

    member_ids = _fetch_clan_member_ids(clan_id, realm=realm)
    for member_id in member_ids:
        try:
            player, created = get_or_create_canonical_player(
                member_id, realm=realm)
        except BlockedAccountError:
            logging.info(
                "Skipping blocked account %s during clan data update", member_id)
            continue
        if created:
            logging.info(
                f"Created new player: {player.player_id}\nPopulating data...")
            update_player_data(player)
        else:
            if player.clan != clan:
                player.clan = clan
                # Scoped: only the clan FK changed here. A bare save() during a
                # clan refresh would write back this loop's stale player snapshot,
                # reverting a concurrent per-player battle/ranked/account write
                # (bare-save audit, runbook-player-refresh-pill-clobber-2026-06-21 #2).
                player.save(update_fields=['clan'])

    reconcile_clan_departures(clan, member_ids, realm=realm)


def refresh_clan_cached_aggregates(clan_id: str, realm: str = DEFAULT_REALM) -> None:
    from django.db.models import Sum, Count, Q

    clan = Clan.objects.get(clan_id=clan_id, realm=realm)
    agg = Clan.objects.filter(clan_id=clan_id, realm=realm).aggregate(
        total_wins=Sum('player__pvp_wins'),
        total_battles=Sum('player__pvp_battles'),
        active_members=Count('player', filter=Q(
            player__days_since_last_battle__lte=30)),
    )
    total_wins = agg['total_wins'] or 0
    total_battles = agg['total_battles'] or 0
    clan.cached_total_wins = total_wins
    clan.cached_total_battles = total_battles
    clan.cached_active_member_count = agg['active_members'] or 0
    clan.cached_clan_wr = round(
        total_wins / total_battles * 100.0, 4) if total_battles > 0 else None
    clan.save(update_fields=[
        'cached_total_wins', 'cached_total_battles',
        'cached_active_member_count', 'cached_clan_wr',
    ])

    _invalidate_clan_battle_summary_cache(clan_id, realm=realm)
    cache.delete(realm_cache_key(realm, f'clan:members:{clan_id}'))


def reconcile_clan_departures(clan, live_member_ids, realm: str = DEFAULT_REALM) -> int:
    """Clear the clan FK on stored members no longer in the live WG roster.

    The roster-sync paths (daily clan crawl, on-view clan refresh) only ever ADD
    ``player.clan = clan``; nothing removes a member who has LEFT. Such a player
    lingers in ``clan.player_set`` (a "ghost" that inflates the member list)
    until their own profile is individually refreshed — and a departed-then-
    inactive player is never swept by the active-player observation floor, so
    ghosts accumulate indefinitely. This pass closes the gap using the member ids
    the caller already fetched (no extra WG calls).

    Guard: never reconcile against an empty/missing roster, so a transient
    upstream failure can't orphan an entire clan. Returns the number of
    departures cleared.
    """
    if not live_member_ids:
        return 0
    live_ids = {int(m) for m in live_member_ids}
    cleared = clan.player_set.exclude(player_id__in=live_ids).update(clan=None)
    if cleared:
        # Delete the *served* members key (v3 — see clan_members view); the
        # bare 'clan:members:{id}' key other call sites delete is a stale no-op.
        cache.delete(realm_cache_key(realm, f'clan:members:v3:{clan.clan_id}'))
        invalidate_clan_detail_cache(int(clan.clan_id), realm=realm)
        logging.info(
            "Reconciled %d departed member(s) out of clan %s [%s]",
            cleared, clan.name, clan.tag,
        )
    return cleared


def update_clan_members(clan_id: str, realm: str = DEFAULT_REALM) -> None:
    clan = Clan.objects.get(clan_id=clan_id, realm=realm)
    member_ids = _fetch_clan_member_ids(clan_id, realm=realm)

    if not member_ids and clan.members_count:
        logging.warning(
            "Skipping clan member refresh because upstream returned no member ids for clan_id=%s",
            clan_id,
        )
        return

    for member_id in member_ids:
        try:
            player, created = get_or_create_canonical_player(
                member_id, realm=realm)
        except BlockedAccountError:
            logging.info(
                "Skipping blocked account %s during clan member sync", member_id)
            continue
        if created:
            logging.info(
                f"Created new player: {player.player_id}")
            update_player_data(player)
            update_battle_data(player.player_id, realm=realm)

        else:
            if player.clan != clan:
                player.clan = clan
                # Scoped: only the clan FK changed here. A bare save() during a
                # clan refresh would write back this loop's stale player snapshot,
                # reverting a concurrent per-player battle/ranked/account write
                # (bare-save audit, runbook-player-refresh-pill-clobber-2026-06-21 #2).
                player.save(update_fields=['clan'])

        update_player_data(player)

    reconcile_clan_departures(clan, member_ids, realm=realm)
    refresh_clan_cached_aggregates(clan_id, realm=realm)


def update_player_data(player: Player, force_refresh: bool = False, realm: str | None = None) -> None:
    if realm is not None and player.realm != realm:
        player.realm = realm

    if not force_refresh and player.last_fetch and datetime.now() - player.last_fetch < timedelta(minutes=1400):
        logging.debug(
            f'Player data is fresh')
        return

    player_data = _fetch_player_personal_data(
        player.player_id, realm=player.realm)
    if not player_data:
        logging.warning(
            "Skipping player update because upstream returned no data for player_id=%s",
            player.player_id,
        )
        return

    # Map basic fields
    player.name = player_data.get("nickname", "")
    player.player_id = player_data.get("account_id", player.player_id)

    clan_membership = _fetch_clan_membership_for_player(
        player.player_id, realm=player.realm)
    clan_id = clan_membership.get("clan_id") or player_data.get("clan_id")
    if clan_id:
        clan, _ = Clan.objects.get_or_create(
            clan_id=clan_id, realm=player.realm)
        player.clan = clan
    else:
        player.clan = None

    created_at = player_data.get("created_at")
    player.creation_date = datetime.fromtimestamp(
        created_at, tz=timezone.utc) if created_at else None

    last_battle_time = player_data.get("last_battle_time")
    player.last_battle_date = datetime.fromtimestamp(
        last_battle_time, tz=timezone.utc).date() if last_battle_time else None

    # Calculate days since last battle
    if player.last_battle_date:
        player.days_since_last_battle = (datetime.now(
            timezone.utc).date() - player.last_battle_date).days

    # Check if the player's profile is hidden
    player.is_hidden = bool(player_data.get('hidden_profile'))

    # If the player is not hidden, map additional statistics
    if not player.is_hidden:
        # NOTE: do not write battles_updated_at here. This is an account/info
        # refresh — it does not fetch battle/ships data. battles_updated_at is
        # the "battle data was fetched" clock the live-refresh pill anchors on
        # (_player_refresh_signals), owned by update_battle_data (now()). Writing
        # WG's older account-level stats_updated_at moved it backwards and
        # re-armed the "Updating…" pill on cold (>23h) visits
        # (runbook-player-refresh-pill-clobber-2026-06-21).
        stats = player_data.get("statistics", {})
        player.total_battles = stats.get("battles", 0)
        pvp_stats = stats.get("pvp", {})
        player.pvp_battles = pvp_stats.get("battles", 0)
        player.pvp_wins = pvp_stats.get("wins", 0)
        player.pvp_losses = pvp_stats.get("losses", 0)
        player.pvp_frags = pvp_stats.get("frags", 0)
        player.pvp_survived_battles = pvp_stats.get("survived_battles", 0)
        player.pvp_deaths, player.actual_kdr = _calculate_actual_kdr(
            player.pvp_battles,
            player.pvp_frags,
            player.pvp_survived_battles,
        )

        # Calculate PvP ratios
        player.pvp_ratio = round(
            (player.pvp_wins / player.pvp_battles * 100), 2) if player.pvp_battles else 0
        player.pvp_survival_rate = round((pvp_stats.get(
            "survived_battles", 0) / player.pvp_battles) * 100, 2) if player.pvp_battles else 0
        player.wins_survival_rate = round((pvp_stats.get(
            "survived_wins", 0) / player.pvp_wins) * 100, 2) if player.pvp_wins else 0

        player.verdict = compute_player_verdict(
            pvp_battles=player.pvp_battles,
            pvp_ratio=player.pvp_ratio,
            pvp_survival_rate=player.pvp_survival_rate,
        )
    else:
        player.total_battles = 0
        player.pvp_battles = 0
        player.pvp_wins = 0
        player.pvp_losses = 0
        player.pvp_frags = 0
        player.pvp_survived_battles = 0
        player.pvp_deaths = 0
        player.pvp_ratio = None
        player.actual_kdr = None
        player.pvp_survival_rate = None
        player.wins_survival_rate = None
        player.battles_json = None
        player.battles_updated_at = None
        player.tiers_json = None
        player.tiers_updated_at = None
        player.activity_json = None
        player.activity_updated_at = None
        player.type_json = None
        player.type_updated_at = None
        player.randoms_json = None
        player.randoms_updated_at = None
        player.ranked_json = None
        player.ranked_updated_at = None
        player.efficiency_json = None
        player.efficiency_updated_at = None
        player.verdict = None

    player.last_fetch = datetime.now()
    if player.is_hidden or player._state.adding:
        # Hidden path legitimately owns wiping every battle/derived field
        # (battles_updated_at / *_json included); a brand-new row must be
        # inserted in full. Neither races concurrent battle/ranked writes.
        player.save()
    else:
        # Scoped to the account/info fields THIS refresh owns. A bare save()
        # writes back the task's stale snapshot of battles_json /
        # battles_updated_at / ranked_json / efficiency / tiers / type / randoms,
        # reverting the scoped writes that update_battle_data / update_ranked_data
        # land concurrently on the same cold (>23h) visit — a lost-update race
        # (runbook-player-refresh-pill-clobber-2026-06-21).
        player.save(update_fields=[
            'realm', 'name', 'player_id', 'clan', 'creation_date',
            'last_battle_date', 'days_since_last_battle', 'is_hidden',
            'total_battles', 'pvp_battles', 'pvp_wins', 'pvp_losses',
            'pvp_frags', 'pvp_survived_battles', 'pvp_deaths', 'actual_kdr',
            'pvp_ratio', 'pvp_survival_rate', 'wins_survival_rate', 'verdict',
            'last_fetch',
        ])
    if not player.is_hidden:
        update_player_efficiency_data(
            player, force_refresh=force_refresh, realm=player.realm)
    refresh_player_explorer_summary(player)
    invalidate_player_detail_cache(player.player_id, realm=player.realm)
    logging.info(f"Updated player personal data: {player.name}")


def _top_visited_entity_ids(entity_type: str, limit: int) -> list[int]:
    from warships.visit_analytics import get_top_entities

    try:
        rows = get_top_entities(entity_type, '7d', 'views_deduped', limit)
    except Exception as error:
        logging.warning(
            'Skipping top-visited %s lookup due to analytics error: %s',
            entity_type,
            error,
        )
        return []

    entity_ids: list[int] = []
    for row in rows:
        try:
            entity_id = int(row.get('entity_id') or 0)
        except (TypeError, ValueError):
            continue
        if entity_id > 0:
            entity_ids.append(entity_id)
    return entity_ids


def _get_pinned_player_ids(realm: str = DEFAULT_REALM) -> list[int]:
    if not HOT_ENTITY_PINNED_PLAYER_NAMES:
        return []
    return list(
        Player.objects.filter(
            realm=realm, name__in=HOT_ENTITY_PINNED_PLAYER_NAMES)
        .values_list('player_id', flat=True)
    )


def _get_hot_player_ids(limit: int = HOT_ENTITY_PLAYER_LIMIT, realm: str = DEFAULT_REALM) -> list[int]:
    candidate_ids: list[int] = list(_get_pinned_player_ids(realm=realm))
    candidate_ids.extend(_top_visited_entity_ids('player', limit))
    candidate_ids.extend(
        Player.objects.filter(realm=realm).exclude(name='').exclude(last_lookup__isnull=True).order_by(
            F('last_lookup').desc(nulls_last=True),
            'name',
        ).values_list('player_id', flat=True)[:limit]
    )
    candidate_ids.extend(
        Player.objects.filter(realm=realm).exclude(name='').filter(is_hidden=False).select_related('explorer_summary').order_by(
            F('explorer_summary__player_score').desc(nulls_last=True),
            F('pvp_ratio').desc(nulls_last=True),
            'name',
        ).values_list('player_id', flat=True)[:limit]
    )

    ordered_ids: list[int] = []
    seen_ids: set[int] = set()
    for raw_id in candidate_ids:
        try:
            player_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if player_id <= 0 or player_id in seen_ids:
            continue
        seen_ids.add(player_id)
        ordered_ids.append(player_id)
        if len(ordered_ids) >= limit:
            break
    return ordered_ids


def _get_hot_clan_ids(limit: int = HOT_ENTITY_CLAN_LIMIT, realm: str = DEFAULT_REALM) -> list[int]:
    candidate_ids: list[int] = []
    candidate_ids.extend(_top_visited_entity_ids('clan', limit))
    candidate_ids.extend(
        Clan.objects.filter(realm=realm).exclude(name__isnull=True).exclude(name='').exclude(last_lookup__isnull=True).order_by(
            F('last_lookup').desc(nulls_last=True),
            'name',
        ).values_list('clan_id', flat=True)[:limit]
    )
    # Rank by the denormalized cached_* columns, NOT a live SUM over every
    # member row: the aggregate ranking was 30.7 s/call on the 30-min warm
    # cycle — the single biggest cumulative DB consumer (audit F9.1). The
    # cached values refresh on clan refresh; staleness is immaterial for
    # choosing which clans to keep warm.
    candidate_ids.extend(
        Clan.objects.filter(realm=realm).exclude(name__isnull=True).exclude(name='').filter(
            cached_total_battles__gte=100000,
            cached_clan_wr__isnull=False,
        ).order_by(
            F('cached_clan_wr').desc(nulls_last=True),
            F('cached_total_battles').desc(nulls_last=True),
            'name',
        ).values_list('clan_id', flat=True)[:limit]
    )

    ordered_ids: list[int] = []
    seen_ids: set[int] = set()
    for raw_id in candidate_ids:
        try:
            clan_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if clan_id <= 0 or clan_id in seen_ids:
            continue
        seen_ids.add(clan_id)
        ordered_ids.append(clan_id)
        if len(ordered_ids) >= limit:
            break
    return ordered_ids


def warm_hot_entity_caches(
    player_limit: int = HOT_ENTITY_PLAYER_LIMIT,
    clan_limit: int = HOT_ENTITY_CLAN_LIMIT,
    force_refresh: bool = False,
    realm: str = DEFAULT_REALM,
) -> dict[str, Any]:
    pinned_ids = _get_pinned_player_ids(realm=realm)
    player_ids = _get_hot_player_ids(player_limit, realm=realm)
    clan_ids = _get_hot_clan_ids(clan_limit, realm=realm)
    if pinned_ids:
        logger.info("Hot entity warm includes %d pinned player(s): %s",
                    len(pinned_ids), pinned_ids)
    warmed_players = warm_player_entity_caches(
        player_ids,
        force_refresh=force_refresh,
    )
    warmed_clans = warm_clan_entity_caches(
        clan_ids,
        force_refresh=force_refresh,
    )

    return {
        'status': 'completed',
        'warmed': {
            'players': warmed_players,
            'clans': warmed_clans,
        },
        'candidate_counts': {
            'players': len(player_ids),
            'clans': len(clan_ids),
        },
    }


def warm_player_entity_caches(player_ids: Iterable[int], force_refresh: bool = False, realm: str = DEFAULT_REALM) -> int:
    warmed_players = 0

    player_ids = list(player_ids)
    players_by_id = {
        p.player_id: p
        for p in Player.objects.select_related('explorer_summary', 'clan')
        .filter(player_id__in=player_ids, realm=realm)
    }

    for player_id in player_ids:
        player = players_by_id.get(player_id)
        if player is None:
            continue

        refresh_player_detail_payloads(
            player,
            force_refresh=force_refresh,
            refresh_core=True,
        )
        player.refresh_from_db()
        if not player.is_hidden:
            fetch_player_clan_battle_seasons(player.player_id, realm=realm)
        warmed_players += 1

    return warmed_players


def warm_clan_entity_caches(clan_ids: Iterable[int], force_refresh: bool = False, realm: str = DEFAULT_REALM) -> int:
    warmed_clans = 0

    clan_ids = list(clan_ids)
    clans = {
        c.clan_id: c
        for c in Clan.objects.filter(clan_id__in=clan_ids, realm=realm).annotate(
            tracked_member_count=Count('player', filter=Q(player__name__gt=''))
        )
    }

    for clan_id in clan_ids:
        clan = clans.get(clan_id)
        if clan is None:
            continue

        if force_refresh or clan_detail_needs_refresh(clan):
            update_clan_data(str(clan_id), realm=realm)
            clan.refresh_from_db()

        if force_refresh or clan_members_missing_or_incomplete(clan, member_count=clan.tracked_member_count):
            update_clan_members(str(clan_id), realm=realm)

        refresh_clan_battle_seasons_cache(str(clan_id), realm=realm)
        fetch_clan_plot_data(str(clan_id), 'active', realm=realm)
        fetch_clan_plot_data(str(clan_id), 'all', realm=realm)
        update_clan_tier_distribution(str(clan_id), realm=realm)
        warmed_clans += 1

    return warmed_clans


RECENTLY_VIEWED_CACHE_KEY_BASE = 'recently_viewed:players:v1'
RECENTLY_VIEWED_PLAYER_LIMIT = max(
    1, int(os.getenv('RECENTLY_VIEWED_PLAYER_LIMIT', '100')))
RECENTLY_VIEWED_WARM_MINUTES = max(
    1, int(os.getenv('RECENTLY_VIEWED_WARM_MINUTES', '10')))

BULK_CACHE_TOP_PLAYER_LIMIT = max(
    1, int(os.getenv('BULK_CACHE_TOP_PLAYER_LIMIT', '50')))
BULK_CACHE_CLAN_LIMIT = max(1, int(os.getenv('BULK_CACHE_CLAN_LIMIT', '25')))
BULK_CACHE_CLAN_MEMBER_CLANS = max(
    1, int(os.getenv('BULK_CACHE_CLAN_MEMBER_CLANS', '25')))
BULK_CACHE_PLAYER_TTL = int(
    os.getenv('BULK_CACHE_PLAYER_TTL', str(24 * 60 * 60)))
BULK_CACHE_CLAN_TTL = int(os.getenv('BULK_CACHE_CLAN_TTL', str(24 * 60 * 60)))


def _bulk_cache_key_player(player_id: int, realm: str = DEFAULT_REALM) -> str:
    return realm_cache_key(realm, f'player:detail:v1:{player_id}')


def _bulk_cache_key_clan(clan_id: int, realm: str = DEFAULT_REALM) -> str:
    return realm_cache_key(realm, f'clan:detail:v1:{clan_id}')


def get_cached_player_detail(player_id: int, realm: str = DEFAULT_REALM) -> Optional[dict]:
    return cache.get(_bulk_cache_key_player(player_id, realm=realm))


def invalidate_player_detail_cache(player_id: int, realm: str = DEFAULT_REALM) -> None:
    cache.delete(_bulk_cache_key_player(player_id, realm=realm))


def invalidate_clan_detail_cache(clan_id: int, realm: str = DEFAULT_REALM) -> None:
    cache.delete(_bulk_cache_key_clan(clan_id, realm=realm))


# ---------------------------------------------------------------------------
# Recently-viewed player queue
# ---------------------------------------------------------------------------

def push_recently_viewed_player(player_id: int, realm: str = DEFAULT_REALM) -> None:
    """Add a player to the recently-viewed queue (most-recent-first).

    Best-effort: silently swallows errors so it never affects the request path.
    """
    try:
        rv_cache_key = realm_cache_key(realm, RECENTLY_VIEWED_CACHE_KEY_BASE)
        current: list[int] = cache.get(rv_cache_key) or []
        try:
            current.remove(player_id)
        except ValueError:
            pass
        current.insert(0, player_id)
        cache.set(
            rv_cache_key,
            current[:RECENTLY_VIEWED_PLAYER_LIMIT],
            timeout=None,
        )
    except Exception:
        logging.debug(
            "push_recently_viewed_player: failed for player %s", player_id, exc_info=True,
        )


def get_recently_viewed_player_ids(realm: str = DEFAULT_REALM) -> list[int]:
    """Return the list of recently-viewed player IDs, most-recent-first."""
    return cache.get(realm_cache_key(realm, RECENTLY_VIEWED_CACHE_KEY_BASE)) or []


def warm_recently_viewed_players(realm: str = DEFAULT_REALM) -> dict[str, Any]:
    """Re-cache recently-viewed players whose detail cache entry is missing.

    DB reads + serialization only — no WG API calls.
    """
    from warships.serializers import PlayerSerializer

    player_ids = get_recently_viewed_player_ids(realm=realm)
    if not player_ids:
        return {'status': 'completed', 'total': 0, 'hits': 0, 'misses': 0, 'warmed': 0}

    cache_keys = {pid: _bulk_cache_key_player(
        pid, realm=realm) for pid in player_ids}
    cached = cache.get_many(list(cache_keys.values()))

    missing_ids = [pid for pid, key in cache_keys.items() if key not in cached]
    hits = len(player_ids) - len(missing_ids)

    warmed = 0
    if missing_ids:
        players = (
            Player.objects
            .filter(player_id__in=missing_ids, realm=realm)
            .select_related('clan', 'explorer_summary')
        )
        serializer = PlayerSerializer()
        payloads: dict[str, dict] = {}
        for player in players:
            try:
                payloads[_bulk_cache_key_player(
                    player.player_id, realm=realm)] = serializer.to_representation(player)
            except Exception:
                logging.warning(
                    "warm_recently_viewed_players: failed to serialize player %s",
                    player.player_id, exc_info=True,
                )
        if payloads:
            cache.set_many(payloads, timeout=BULK_CACHE_PLAYER_TTL)
            warmed = len(payloads)

    logging.info(
        "warm_recently_viewed_players: total=%d hits=%d misses=%d warmed=%d",
        len(player_ids), hits, len(missing_ids), warmed,
    )
    return {
        'status': 'completed',
        'total': len(player_ids),
        'hits': hits,
        'misses': len(missing_ids),
        'warmed': warmed,
    }


CLAN_BATTLE_ACTIVITY_BADGE_WINDOW_DAYS = 365 * 3
CLAN_BATTLE_ACTIVITY_BADGE_MIN_SEASON_BATTLES = 20
CLAN_BATTLE_ACTIVITY_BADGE_MIN_PARTICIPANTS = 4
CLAN_BATTLE_ACTIVITY_BADGE_MIN_SEASON_PARTICIPATION_SHARE = 0.12
CLAN_BATTLE_ACTIVITY_BADGE_MIN_WEIGHTED_ACTIVE_SHARE = 0.25
CLAN_BATTLE_ACTIVITY_BADGE_MIN_WEIGHTED_PARTICIPATION_SHARE = 0.05
CLAN_BATTLE_ACTIVITY_BADGE_RECENCY_WEIGHTS = (
    (365, 1.0),
    (365 * 2, 0.6),
    (365 * 3, 0.35),
)


def _parse_clan_battle_meta_date(raw_value: Any) -> Optional[date]:
    if not raw_value:
        return None

    try:
        return datetime.strptime(str(raw_value), '%Y-%m-%d').date()
    except ValueError:
        return None


def _clan_battle_activity_badge_weight(age_days: int) -> float:
    for max_age_days, weight in CLAN_BATTLE_ACTIVITY_BADGE_RECENCY_WEIGHTS:
        if age_days <= max_age_days:
            return weight
    return 0.0


def _get_clan_battle_activity_badge_window(reference_date: Optional[date] = None) -> list[tuple[int, float, int]]:
    target_date = reference_date or django_timezone.now().date()
    season_window: list[tuple[int, float, int]] = []

    for season_id, meta in _get_clan_battle_seasons_metadata().items():
        end_date = _parse_clan_battle_meta_date(meta.get('end_date'))
        if end_date is None or end_date > target_date:
            continue

        age_days = max((target_date - end_date).days, 0)
        if age_days > CLAN_BATTLE_ACTIVITY_BADGE_WINDOW_DAYS:
            continue

        weight = _clan_battle_activity_badge_weight(age_days)
        if weight <= 0:
            continue

        season_window.append((int(season_id), weight, age_days))

    season_window.sort(key=lambda item: item[2])
    return season_window


def summarize_clan_battle_activity_badge(
    season_rows: Any,
    total_members: int = 0,
    reference_date: Optional[date] = None,
) -> dict[str, float | int | bool]:
    season_window = _get_clan_battle_activity_badge_window(reference_date)
    if not season_window:
        return {
            'is_clan_battle_active': False,
            'cb_activity_recent_active_seasons': 0,
            'cb_activity_total_active_seasons': 0,
            'cb_activity_weighted_active_share': 0.0,
            'cb_activity_weighted_participation_share': 0.0,
        }

    targeted_ids = {season_id for season_id,
                    _weight, _age_days in season_window}
    season_battles_by_id: dict[int, int] = {}
    season_participants_by_id: dict[int, int] = {}

    for row in season_rows or []:
        try:
            season_id = int(row.get('season_id') or 0)
        except (TypeError, ValueError):
            continue

        if season_id not in targeted_ids:
            continue

        battles = int(row.get('roster_battles', row.get('battles', 0)) or 0)
        participants = int(row.get('participants', 0) or 0)
        if battles <= 0 or participants <= 0:
            continue

        season_battles_by_id[season_id] = battles
        season_participants_by_id[season_id] = participants

    total_window_weight = sum(weight for _season_id,
                              weight, _age_days in season_window)
    if total_window_weight <= 0:
        return {
            'is_clan_battle_active': False,
            'cb_activity_recent_active_seasons': 0,
            'cb_activity_total_active_seasons': 0,
            'cb_activity_weighted_active_share': 0.0,
            'cb_activity_weighted_participation_share': 0.0,
        }

    weighted_active = 0.0
    weighted_participation = 0.0
    recent_active_seasons = 0
    total_active_seasons = 0
    member_floor = max(int(total_members or 0), 1)

    for season_id, weight, age_days in season_window:
        battles = season_battles_by_id.get(season_id, 0)
        participants = season_participants_by_id.get(season_id, 0)
        denominator = max(member_floor, participants, 1)
        participation_share = min(participants / denominator, 1.0)
        season_is_active = (
            battles >= CLAN_BATTLE_ACTIVITY_BADGE_MIN_SEASON_BATTLES
            and participants >= CLAN_BATTLE_ACTIVITY_BADGE_MIN_PARTICIPANTS
            and participation_share >= CLAN_BATTLE_ACTIVITY_BADGE_MIN_SEASON_PARTICIPATION_SHARE
        )

        weighted_participation += participation_share * weight
        if season_is_active:
            weighted_active += weight
            total_active_seasons += 1
            if age_days <= 365:
                recent_active_seasons += 1

    weighted_active_share = weighted_active / total_window_weight
    weighted_participation_share = weighted_participation / total_window_weight
    is_clan_battle_active = (
        recent_active_seasons > 0
        and weighted_active_share >= CLAN_BATTLE_ACTIVITY_BADGE_MIN_WEIGHTED_ACTIVE_SHARE
        and weighted_participation_share >= CLAN_BATTLE_ACTIVITY_BADGE_MIN_WEIGHTED_PARTICIPATION_SHARE
    )

    return {
        'is_clan_battle_active': is_clan_battle_active,
        'cb_activity_recent_active_seasons': recent_active_seasons,
        'cb_activity_total_active_seasons': total_active_seasons,
        'cb_activity_weighted_active_share': round(weighted_active_share, 4),
        'cb_activity_weighted_participation_share': round(weighted_participation_share, 4),
    }


def get_clan_battle_activity_badge(
    clan_id: int | str,
    total_members: int = 0,
    realm: str = DEFAULT_REALM,
    reference_date: Optional[date] = None,
    cache_only: bool = False,
) -> dict[str, float | int | bool]:
    normalized_clan_id = str(clan_id or '').strip()
    if not normalized_clan_id:
        return summarize_clan_battle_activity_badge([], total_members=total_members, reference_date=reference_date)

    cache_key = _get_clan_battle_summary_cache_key(
        normalized_clan_id, realm=realm)
    season_rows = cache.get(cache_key)
    if season_rows is None:
        if cache_only:
            # Caller is on a hot request path (e.g. landing render). Return a
            # default badge and let the caller queue an async refresh — never
            # fire a synchronous WG API fan-out from inside a request.
            badge = summarize_clan_battle_activity_badge(
                [], total_members=total_members, reference_date=reference_date)
            badge['cache_miss'] = True
            return badge
        season_rows = refresh_clan_battle_seasons_cache(
            normalized_clan_id, realm=realm)

    return summarize_clan_battle_activity_badge(
        season_rows,
        total_members=total_members,
        reference_date=reference_date,
    )


def bulk_load_player_cache(
    top_player_limit: int = BULK_CACHE_TOP_PLAYER_LIMIT,
    clan_member_clans: int = BULK_CACHE_CLAN_MEMBER_CLANS,
    realm: str = DEFAULT_REALM,
) -> dict[str, Any]:
    """Bulk-load player detail payloads into Redis from DB.

    Loads two cohorts into a single cache.set_many() call:
    1. Pinned players (always included)
    2. Recently-viewed players

    Single pass — no API calls, no Celery tasks. The former global-best +
    best-clan-member cohorts were removed with the Best/Popular landing boards
    (3.0); the view-driven recently-viewed cohort + the 30-min hot-entity warmer
    keep actually-viewed entities warm, unvisited pages hydrate lazily on view.
    """
    from warships.serializers import PlayerSerializer

    top_players: list = []
    seen_ids: set[int] = set()

    # Cohort 1: pinned players
    pinned_ids = _get_pinned_player_ids(realm=realm)
    missing_pinned = [pid for pid in pinned_ids if pid not in seen_ids]
    if missing_pinned:
        top_players.extend(
            Player.objects
            .filter(player_id__in=missing_pinned, realm=realm)
            .select_related('clan', 'explorer_summary')
        )
        seen_ids.update(missing_pinned)

    # Cohort 2: recently-viewed players
    rv_ids = get_recently_viewed_player_ids(realm=realm)
    missing_rv = [pid for pid in rv_ids if pid not in seen_ids]
    if missing_rv:
        rv_players = list(
            Player.objects
            .filter(player_id__in=missing_rv, realm=realm)
            .select_related('clan', 'explorer_summary')
        )
        top_players.extend(rv_players)
        seen_ids.update(p.player_id for p in rv_players)

    payloads: dict[str, dict] = {}
    serializer = PlayerSerializer()
    for player in top_players:
        try:
            data = serializer.to_representation(player)
            key = _bulk_cache_key_player(player.player_id, realm=realm)
            payloads[key] = data
        except Exception:
            logging.warning(
                "bulk_load_player_cache: failed to serialize player %s", player.player_id, exc_info=True)

    if payloads:
        cache.set_many(payloads, timeout=BULK_CACHE_PLAYER_TTL)

    logging.info(
        "bulk_load_player_cache: loaded %d player payloads (pinned=%d, recently_viewed=%d)",
        len(payloads), len(missing_pinned), len(missing_rv),
    )
    return {
        'status': 'completed',
        'loaded': len(payloads),
        'pinned_added': len(missing_pinned),
        'recently_viewed_added': len(missing_rv),
    }


def bulk_load_entity_caches(
    top_player_limit: int = BULK_CACHE_TOP_PLAYER_LIMIT,
    clan_member_clans: int = BULK_CACHE_CLAN_MEMBER_CLANS,
    clan_limit: int = BULK_CACHE_CLAN_LIMIT,
    realm: str = DEFAULT_REALM,
) -> dict[str, Any]:
    """Bulk-load player detail payloads into Redis. DB reads only, no tasks.

    The clan cohort was removed with the Best/Popular landing boards (3.0): it
    was entirely score_best_clans-driven and already gated off in prod. Viewed
    clans stay warm via the 30-min hot-entity warmer; cold clan pages hydrate
    lazily on first view.
    """
    player_result = bulk_load_player_cache(
        top_player_limit, clan_member_clans, realm=realm)
    return {
        'status': 'completed',
        'players': player_result,
    }


# Trailing window for the ship board / profile badges, recomputed nightly. The
# snapshot is overwritten each night, so this is just the lookback span; the
# evolution *speed* of the badges is governed by this length (longer = more
# stable night-to-night). Operator-tunable without a redeploy.
SHIP_LEADERBOARD_WINDOW_DAYS = int(os.getenv('SHIP_LEADERBOARD_WINDOW_DAYS', '30'))
SHIP_LEADERBOARD_CACHE_TTL = 900   # 15 min read-cache on the /ship endpoint


# --- Rolling ship-standings window (treemap + inline list) -------------------
# The realm treemap (compute_realm_top_ships) and the landing inline tier/type
# list (compute_realm_ships_by_tier_type) align 1:1 with the /ship/<id> player
# leaderboards: all three cover the SAME rolling trailing
# SHIP_LEADERBOARD_WINDOW_DAYS window the nightly ShipTopPlayerSnapshot was built
# over. The fixed 2-week "season" model that previously bucketed the treemap was
# retired 2026-06-15 when the badges/leaderboards moved to a nightly rolling
# window (see runbook-ship-badges-rolling-2026-06-14.md); the frontend mirror
# lives in client/app/lib/shipSeason.ts.


def latest_ship_snapshot_window(realm: str) -> tuple:
    """(`captured_on`, `window_start`, `window_end`) for a realm's ship standings.

    Anchors on the realm's most recent ``ShipTopPlayerSnapshot.captured_on`` — the
    exact rolling window the ``/ship`` leaderboards read — so the treemap and the
    inline tier/type list cover the identical date span as the player lists.
    ``window_end`` is that ``captured_on`` (exclusive end, a run date);
    ``window_start = window_end - SHIP_LEADERBOARD_WINDOW_DAYS``. Keying caches on
    ``window_end`` makes alignment self-heal the moment a new nightly snapshot
    lands (the key changes; the next request recomputes over the matching window).
    Falls back to a trailing window ending today when no snapshot exists yet
    (``captured_on`` is then ``None``).
    """
    from warships.models import ShipTopPlayerSnapshot

    realm = (realm or DEFAULT_REALM).lower().strip()
    captured_on = (
        ShipTopPlayerSnapshot.objects.filter(realm=realm)
        .order_by('-captured_on')
        .values_list('captured_on', flat=True)
        .first()
    )
    window_end = captured_on or django_timezone.now().date()
    window_start = window_end - timedelta(days=SHIP_LEADERBOARD_WINDOW_DAYS)
    return captured_on, window_start, window_end


def _season_window_datetimes(start: date, end: date) -> tuple:
    """Datetimes for a window's [start, end) date bounds (UTC midnight).

    Respects `USE_TZ`: aware UTC in production (matching `BattleEvent.detected_at`),
    naive under the sqlite test config — mirroring how the previous `since =
    django_timezone.now() - 14d` inherited awareness from settings.
    """
    start_dt = datetime.combine(start, datetime.min.time())
    end_dt = datetime.combine(end, datetime.min.time())
    if django_timezone.is_aware(django_timezone.now()):
        start_dt = django_timezone.make_aware(start_dt, timezone.utc)
        end_dt = django_timezone.make_aware(end_dt, timezone.utc)
    return start_dt, end_dt


def _pool_zscores(values: list) -> list:
    """Population z-scores for a list of numbers (mean 0, unit std within pool).

    Used to put the three ship-ranking signals (win rate, damage, kills) on a
    common scale before the weighted blend. Returns all-zeros when the pool has
    no spread (e.g. a metric that is uniform/absent across the pool), so that
    metric contributes nothing rather than NaN.
    """
    n = len(values)
    if n == 0:
        return []
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    std = math.sqrt(var)
    if std == 0:
        return [0.0] * n
    return [(v - mean) / std for v in values]


def compute_ship_top_player_snapshot(realm: str = DEFAULT_REALM, *,
                                     window_start: Optional[date] = None,
                                     window_end: Optional[date] = None,
                                     captured_on: Optional[date] = None) -> dict:
    """Per-realm ranked players for each Tier-N ship over a trailing window.

    Aggregates `BattleEvent` random-battle deltas over the window
    `[window_start, window_end)` (default: the **trailing
    `SHIP_LEADERBOARD_WINDOW_DAYS` (14) days ending today**), grouped by (ship,
    player) — the inverse grouping of `compute_realm_top_ships`. Keeps players
    with
    >= `SHIP_BADGE_MIN_BATTLES` battles; a ship is "ranked" only if its
    qualifying pool is >= `SHIP_BADGE_MIN_SHIP_POPULATION`. Players are ordered by
    a **volume-aware composite score** — the win proportion shrunk toward 50% by
    `SHIP_BADGE_PRIOR_BATTLES` pseudo-battles (empirical-Bayes), tiebreak raw
    battles — so a short hot streak doesn't outrank a high-volume player; the
    stored/displayed `win_rate` is still the raw rate. Players below
    `SHIP_BADGE_MIN_WIN_RATE` (default 50) are then trimmed from the board
    entirely (the population guard runs on the full pool first, so a thin ship is
    never delisted by the gate). For each ranked ship it
    writes the top `SHIP_BADGE_LIST_SIZE` players as `ShipTopPlayerSnapshot`
    rows (ranks 1..N) — the ship-page leaderboard — of which ranks
    1..`SHIP_BADGE_TOP_N` (3) are the gold/silver/bronze profile badges a player
    wears *only while they hold them*.

    `captured_on` is the **run date** (the snapshot identity), so the task runs
    nightly and a same-day re-run overwrites that day's rows — the badge set
    evolves each night and there is no durable award ledger (removed 2026-06-14).
    The heavy aggregation runs under `_elevated_work_mem()` to avoid a disk spill.
    Invalidates both the new top-3 winners' AND the previous run's top-3 holders'
    cached detail payloads, so a player who dropped out of the top-3 loses the
    badge immediately rather than at TTL.

    The `backfill_ship_seasons` command passes explicit historical windows.
    Thresholds are read from the environment at call time (not module load) so an
    operator can re-tune and re-run without a redeploy. See
    `agents/runbooks/runbook-ship-badges-rolling-2026-06-14.md`.
    """
    from warships.models import BattleEvent, ShipTopPlayerSnapshot

    min_battles = int(os.getenv('SHIP_BADGE_MIN_BATTLES', '15'))
    min_population = int(os.getenv('SHIP_BADGE_MIN_SHIP_POPULATION', '20'))
    # Carriers (CVs) are a low-volume class: plenty of players touch a CV, but
    # few grind >= min_battles on a *single* CV in a 2-week season, so the
    # universal population guard leaves most T10 CVs off the standings (NA: only
    # 3 of ~13 active CVs cleared pop=20). A class-specific, lower floor restores
    # CV coverage without loosening the guard for the populous classes. Applied
    # only to ship_type 'AirCarrier'; all other classes keep `min_population`.
    min_population_cv = int(os.getenv('SHIP_BADGE_MIN_SHIP_POPULATION_CV', '10'))
    # Submarines are the same shape of problem as CVs: a small hull roster where
    # few captains grind >= min_battles on one boat in a 2-week season, so the
    # universal pop=20 guard drops legit boards (NA T8: pools of 18/13 cut; only
    # 3 of 8 hulls cleared). A class-specific, lower floor restores sub coverage
    # without loosening the guard for the populous classes. Applied only to
    # ship_type 'Submarine'; all other (non-CV) classes keep `min_population`.
    min_population_sub = int(os.getenv('SHIP_BADGE_MIN_SHIP_POPULATION_SUB', '12'))
    top_n = int(os.getenv('SHIP_BADGE_TOP_N', '3'))
    list_size = int(os.getenv('SHIP_BADGE_LIST_SIZE', '15'))
    # Ship tiers in scope. `SHIP_BADGE_TIERS` is a comma list (e.g. "8,9,10");
    # falls back to the legacy single `SHIP_BADGE_TIER`. Each ship is ranked
    # within its own pool, so multi-tier is just a wider target set — no
    # cross-tier comparison. See the ship-badge runbook for the tier study.
    _tiers_env = os.getenv('SHIP_BADGE_TIERS') or os.getenv('SHIP_BADGE_TIER', '10')
    tiers = sorted({int(t) for t in _tiers_env.split(',') if t.strip()})
    # Only the latest captured_on is ever read, so retention just keeps a few
    # nights of history for debugging; the snapshot is rewritten nightly.
    retention_days = int(os.getenv('SHIP_BADGE_RETENTION_DAYS', '5'))
    # Volume-aware ranking: empirical-Bayes shrinkage of the win proportion
    # toward a baseline. `prior_battles` is the pseudo-sample weight (higher =
    # more shrinkage of small samples); `prior_wr` is the baseline (50%).
    # Defaults tuned against real NA data (sweep 2026-06-05): floor 15 + pop 20 +
    # prior 50 → ~73 of ~159 active T10 ships qualify, median #1 ≈ 41 battles, no
    # #1 under 15 battles. The floor caps the worst-case #1 sample; the prior
    # demotes short hot streaks; the population guard sets board depth.
    prior_battles = int(os.getenv('SHIP_BADGE_PRIOR_BATTLES', '50'))
    prior_wr = float(os.getenv('SHIP_BADGE_PRIOR_WR', '0.5'))
    # Composite ranking weights. Players are scored on a weighted blend of three
    # within-pool z-scores — win rate, damage/battle, kills/battle — each first
    # tempered by `prior_battles` pseudo-games so a small sample regresses toward
    # a baseline (win rate → `prior_wr`; damage/kills → the ship's pool mean).
    # High-volume players keep ~their true rate, so activity is never penalized.
    # Wins-led default (0.60/0.25/0.15): win rate dominates, damage a secondary,
    # kills a light tertiary (it overlaps damage). Raised from 0.50/0.35/0.15 on
    # 2026-06-29 because damage(.35)+kills(.15) summed to .50 — equal to win rate —
    # so a top-of-pool damage farmer could fully offset a bottom-of-pool win rate
    # (the essential_HT case). Modeled against the live NA pool: this nudge alone
    # demoted that case #7→#10 and cut sub-50% ranked rows 2.8%→2.1% with no
    # coverage loss. Paired with the `min_win_rate` hard gate below.
    w_wins = float(os.getenv('SHIP_BADGE_WEIGHT_WINS', '0.6'))
    w_damage = float(os.getenv('SHIP_BADGE_WEIGHT_DAMAGE', '0.25'))
    w_kills = float(os.getenv('SHIP_BADGE_WEIGHT_KILLS', '0.15'))
    # Hard win-rate gate: a player whose raw win rate is below this is dropped from
    # the ranked board entirely, regardless of damage/kills — so a damage farmer on
    # a losing record never appears on a ship's leaderboard or wears a badge. The
    # population guard below still counts the FULL qualifying pool, so a thin ship
    # is never delisted by the gate; only its sub-gate rows are trimmed from the
    # output. Default 50 (break-even kept; <50 cut). Set 0 to disable.
    min_win_rate = float(os.getenv('SHIP_BADGE_MIN_WIN_RATE', '50'))

    realm = (realm or DEFAULT_REALM).lower().strip()
    # Default to the trailing SHIP_LEADERBOARD_WINDOW_DAYS ending today; callers
    # (the backfill command) may pass an explicit historical window instead.
    # `captured_on` is the run date — the snapshot identity — so a same-day re-run
    # overwrites today's rows rather than minting a duplicate.
    if window_start is None or window_end is None:
        today = django_timezone.now().date()
        window_end = today
        window_start = today - timedelta(days=SHIP_LEADERBOARD_WINDOW_DAYS)
    if captured_on is None:
        captured_on = window_end
    since_dt, until_dt = _season_window_datetimes(window_start, window_end)

    # Target set = all in-scope-tier ships UNION the realm treemap's top-25
    # most-played ships (any tier), so every clickable treemap tile gets a
    # best-list while the full badge coverage for the scoped tiers is preserved.
    target_ids = set(
        Ship.objects.filter(tier__in=tiers).values_list('ship_id', flat=True))
    try:
        treemap = compute_realm_top_ships(realm, limit=25, mode='random')
        target_ids |= {s['ship_id'] for s in treemap.get('ships', [])}
    except Exception:  # treemap is best-effort enrichment, never fatal
        logger.exception("ship-badge snapshot realm=%s: treemap fetch failed", realm)
    target_ids = list(target_ids)
    if not target_ids:
        logger.info("ship-badge snapshot realm=%s: no target ships", realm)
        return {'realm': realm, 'captured_on': captured_on, 'ships_qualified': 0,
                'ships_total': 0, 'badges': 0, 'ranked_rows': 0}

    # Names for the snapshot rows; tiers to gate *badge eligibility*. The treemap
    # union can pull in off-scope tiers (any popular ship) so they get a /ship
    # board, but only in-scope tiers (`tiers`) mint profile badges + awards —
    # otherwise a popular T5/T6 ship would crown a "best player" we deliberately
    # excluded. The full ranked board is still written for every target ship.
    ship_rows_meta = {
        s.ship_id: (s.name, s.tier, s.ship_type)
        for s in Ship.objects.filter(ship_id__in=target_ids)
    }
    ship_names = {sid: name for sid, (name, _t, _ty) in ship_rows_meta.items()}
    tiers_set = set(tiers)

    agg = (
        BattleEvent.objects
        .filter(ship_id__in=target_ids, mode='random',
                detected_at__gte=since_dt, detected_at__lt=until_dt,
                player__realm=realm, player__is_hidden=False)
        # player_id is the FK PK (used for the snapshot FK); player__player_id
        # is the WG account id (used for cache invalidation) — keep both.
        .values('ship_id', 'player_id', 'player__player_id', 'player__name')
        .annotate(
            battles=Sum('battles_delta'),
            wins=Sum('wins_delta'),
            damage=Sum('damage_delta'),
            frags=Sum('frags_delta'),
            survived=Sum(Case(When(survived=True, then=1), default=0,
                              output_field=IntegerField())),
        )
        .filter(battles__gte=min_battles)
    )
    # Materialize the heavy group-aggregate under elevated work_mem so the
    # per-pool sort stays in memory (default work_mem spills it to disk — see the
    # prod sizing in runbook-ship-badges-rolling-2026-06-14.md). SET LOCAL needs a
    # transaction, hence the atomic wrapper.
    with transaction.atomic(), _elevated_work_mem():
        rows = list(agg)

    by_ship: dict = {}
    for r in rows:
        by_ship.setdefault(r['ship_id'], []).append(r)

    snapshot_rows = []
    invalidate_wg_ids = []
    qualified = 0
    badge_count = 0
    for ship_id, pool in by_ship.items():
        # CVs and subs each use a lower, class-specific population floor (see
        # min_population_cv / min_population_sub); all other classes keep the
        # universal `min_population`.
        _stype = ship_rows_meta.get(ship_id, (None, None, None))[2]
        if _stype == 'AirCarrier':
            floor = min_population_cv
        elif _stype == 'Submarine':
            floor = min_population_sub
        else:
            floor = min_population
        if len(pool) < floor:
            continue
        qualified += 1
        # Pool per-game baselines (battle-weighted) that damage/kills temper
        # toward. Win rate tempers toward `prior_wr` (the universal ~50% prior),
        # which damage/kills lack — hence the per-metric baseline split.
        pool_battles = sum((e['battles'] or 0) for e in pool)
        mean_dpb = (sum((e['damage'] or 0) for e in pool) / pool_battles) if pool_battles else 0.0
        mean_kpb = (sum((e['frags'] or 0) for e in pool) / pool_battles) if pool_battles else 0.0
        for entry in pool:
            b = entry['battles'] or 0
            w = entry['wins'] or 0
            entry['win_rate'] = (100.0 * w / b) if b else 0.0
            # Empirical-Bayes shrink each per-game signal by `prior_battles`
            # pseudo-games: a short hot streak regresses toward the baseline,
            # while a high-volume record stays at ~its true rate (never penalized
            # for activity — large n swamps the prior).
            denom = b + prior_battles
            entry['_shr_wr'] = (w + prior_battles * prior_wr) / denom if denom else 0.0
            entry['_shr_dpb'] = ((entry['damage'] or 0) + prior_battles * mean_dpb) / denom if denom else 0.0
            entry['_shr_kpb'] = ((entry['frags'] or 0) + prior_battles * mean_kpb) / denom if denom else 0.0
        # Put the three tempered signals on a common scale (within-pool z-score)
        # and blend by the configured weights. Display still uses raw win_rate.
        z_wr = _pool_zscores([e['_shr_wr'] for e in pool])
        z_dpb = _pool_zscores([e['_shr_dpb'] for e in pool])
        z_kpb = _pool_zscores([e['_shr_kpb'] for e in pool])
        for entry, zw, zd, zk in zip(pool, z_wr, z_dpb, z_kpb):
            entry['_score'] = w_wins * zw + w_damage * zd + w_kills * zk
        pool.sort(key=lambda e: (-e['_score'], -(e['battles'] or 0)))
        # Hard win-rate gate on the OUTPUT only: trim sub-`min_win_rate` players
        # from the board after scoring. The population guard above already passed
        # on the full qualifying pool, so the ship stays ranked — its board simply
        # excludes losing records, no matter how high their damage.
        ranked = ([e for e in pool if (e['win_rate'] or 0) >= min_win_rate]
                  if min_win_rate > 0 else pool)
        for rank, entry in enumerate(ranked[:list_size], start=1):
            snapshot_rows.append(ShipTopPlayerSnapshot(
                captured_on=captured_on,
                realm=realm,
                ship_id=ship_id,
                ship_name=ship_names.get(ship_id) or entry['player__name'] or '',
                rank=rank,
                player_id=entry['player_id'],
                win_rate=round(entry['win_rate'], 2),
                battles=entry['battles'] or 0,
                damage=entry['damage'] or 0,
                frags=entry['frags'] or 0,
                survived=entry['survived'] or 0,
            ))
            # Only the top-N rows of an *in-scope-tier* ship are profile badges →
            # only they change a player's cached detail payload. Off-scope
            # treemap-union ships still get the full ranked board above, but never
            # a badge.
            if rank <= top_n and ship_rows_meta.get(ship_id, (None, None, None))[1] in tiers_set:
                invalidate_wg_ids.append(entry['player__player_id'])
                badge_count += 1

    # The previous run's top-3 badge holders — invalidate them too so a player who
    # dropped out of the top-3 since last night loses the badge immediately
    # instead of carrying a stale one until their detail cache TTL. (Reading the
    # last captured_on < today; over-invalidating the off-scope treemap ships'
    # top-3 is harmless.) Read before the overwrite below.
    prev_captured = (
        ShipTopPlayerSnapshot.objects
        .filter(realm=realm, captured_on__lt=captured_on)
        .order_by('-captured_on')
        .values_list('captured_on', flat=True)
        .first()
    )
    if prev_captured is not None:
        invalidate_wg_ids += list(
            ShipTopPlayerSnapshot.objects
            .filter(realm=realm, captured_on=prev_captured, rank__lte=top_n)
            .values_list('player__player_id', flat=True)
        )

    with transaction.atomic():
        ShipTopPlayerSnapshot.objects.filter(
            realm=realm, captured_on=captured_on).delete()
        if snapshot_rows:
            ShipTopPlayerSnapshot.objects.bulk_create(snapshot_rows)
        prune_before = captured_on - timedelta(days=retention_days)
        ShipTopPlayerSnapshot.objects.filter(
            realm=realm, captured_on__lt=prune_before).delete()

    for wg_id in set(invalidate_wg_ids):
        invalidate_player_detail_cache(wg_id, realm=realm)

    logger.info(
        "ship-badge snapshot realm=%s tiers=%s window=%s..%s ships_qualified=%s/%s "
        "ranked_rows=%s badges=%s", realm, tiers, window_start, window_end,
        qualified, len(target_ids), len(snapshot_rows), badge_count,
    )
    return {'realm': realm, 'captured_on': captured_on, 'ships_qualified': qualified,
            'ships_total': len(target_ids), 'badges': badge_count,
            'ranked_rows': len(snapshot_rows),
            'window_start': window_start, 'window_end': window_end}


def _badge_tiers() -> set:
    """Tiers eligible for profile badges (`SHIP_BADGE_TIERS`; legacy single
    `SHIP_BADGE_TIER` fallback). The `/ship` board serves any tier (treemap tiles),
    but only these mint badges — so a popular off-tier treemap ship never surfaces
    a 'best player' we excluded. Mirrors the parse in compute.
    """
    env = os.getenv('SHIP_BADGE_TIERS') or os.getenv('SHIP_BADGE_TIER', '10')
    return {int(t) for t in env.split(',') if t.strip()}


def _ship_tier_map(ship_ids) -> dict:
    """`{ship_id: tier}` for the given ids — labels badges/awards with their tier.

    The snapshot/award rows don't denormalize tier, so the read paths look it up
    from `Ship` (one short query). Matters now that standings span tiers 8–10 and
    a T8 #1 must not read like a T10 #1.
    """
    ids = [s for s in set(ship_ids or []) if s is not None]
    if not ids:
        return {}
    return dict(
        Ship.objects.filter(ship_id__in=ids).values_list('ship_id', 'tier'))


def get_player_ship_badges(player: Player) -> list:
    """Current-window ship badges (ranks 1..SHIP_BADGE_TOP_N) for a player.

    Read path for the profile badge icons; one indexed lookup on
    `ship_badge_player_captured_idx`. Returns [] when the player holds none. The
    `rank <= top_n` filter keeps mid-list ranked finishes (4..50) — which appear
    on the ship page but are not badges — off the profile. Ordered tier-desc then
    rank so the most prestigious (T10) badge leads.
    """
    from warships.models import ShipTopPlayerSnapshot

    # A hidden account asked not to be shown — never surface its badges, even if a
    # snapshot row from when it was public still exists (the board is precomputed,
    # so a player who hid after the last run would otherwise linger until the next).
    if getattr(player, 'is_hidden', False):
        return []

    top_n = int(os.getenv('SHIP_BADGE_TOP_N', '3'))
    # Anchor on the REALM's current snapshot generation (the same captured_on the
    # /ship board reads via latest_ship_snapshot_window), NOT the player's own most
    # recent row. A player knocked off the board keeps a row for
    # SHIP_BADGE_RETENTION_DAYS; keying on their own latest captured_on would wear a
    # "1st place" badge the live board has already reassigned to someone else, until
    # that stale row prunes. Absent from the current generation → no badge.
    realm = (getattr(player, 'realm', '') or '').lower().strip()
    latest, _, _ = latest_ship_snapshot_window(realm)
    if latest is None:
        return []

    def _badge(r) -> dict:
        # Only avg damage is exposed: it's an accurate windowed delta. KDR and
        # survival% would need per-battle survival, which BattleEvent only records
        # for single-battle intervals (NULL for multi-battle), so they can't be
        # computed accurately for the window. The `frags`/`survived` columns stay
        # stored (dormant) in case accurate survival capture lands later.
        battles = r.battles or 0
        avg_damage = round((r.damage or 0) / battles) if battles else 0
        return {
            'ship_id': r.ship_id,
            'ship_name': r.ship_name,
            'rank': r.rank,
            'win_rate': r.win_rate,
            'battles': battles,
            'avg_damage': avg_damage,
            'window_days': SHIP_LEADERBOARD_WINDOW_DAYS,
            # Season-start date (captured_on) so the UI can label the badge with
            # its ISO week, e.g. "WK20".
            'window_start': r.captured_on.isoformat() if r.captured_on else None,
        }

    rows = list(
        ShipTopPlayerSnapshot.objects
        .filter(player=player, captured_on=latest, rank__lte=top_n)
        .order_by('rank', 'ship_name')
    )
    tier_by_ship = _ship_tier_map([r.ship_id for r in rows])
    eligible = _badge_tiers()
    # Filter to badge-eligible tiers: the snapshot also holds off-scope treemap
    # ships (for their /ship board), which must NOT become profile badges.
    badges = [{**_badge(r), 'tier': tier_by_ship.get(r.ship_id)} for r in rows
              if tier_by_ship.get(r.ship_id) in eligible]
    badges.sort(key=lambda b: (-(b['tier'] or 0), b['rank'], b['ship_name']))
    return badges


def get_players_ship_badges_bulk(player_pks, realm: Optional[str] = None) -> dict:
    """Bulk variant of `get_player_ship_badges` for player lists (avoids N+1).

    Returns ``{player_pk: [badge_dict, ...]}`` for the given players, each anchored
    on that player's realm's CURRENT snapshot generation (the `/ship` board's
    latest ``captured_on``) — NOT the player's own latest row, which lags and would
    surface a stale badge after they fall off the board (see `get_player_ship_badges`).
    Used by the landing and clan-member payloads so a 50-row list doesn't fan out
    to per-player lookups. ``player_pks`` are ``Player`` PKs (the snapshot FK), NOT
    WG account ids. ``realm`` is an optional narrowing filter (a player only has
    snapshots for their own realm, so it's safe to omit — the realms in play are
    derived from the candidates). Players absent from the current generation are
    absent from the result. Same badge shape as `get_player_ship_badges`.
    """
    from warships.models import ShipTopPlayerSnapshot

    pks = [pk for pk in (player_pks or []) if pk is not None]
    if not pks:
        return {}
    top_n = int(os.getenv('SHIP_BADGE_TOP_N', '3'))

    # Exclude now-hidden accounts (see get_player_ship_badges). `base` restricts to
    # the candidate players only to discover which realms they span; the
    # realm-current generation below is computed over ALL rows so a candidate who
    # fell off the board can't drag the anchor backward.
    base = ShipTopPlayerSnapshot.objects.filter(
        player_id__in=pks, player__is_hidden=False)
    if realm:
        base = base.filter(realm=(realm or '').lower().strip())
    realms = set(base.values_list('realm', flat=True).distinct())
    if not realms:
        return {}
    # Anchor on each realm's CURRENT snapshot generation (the /ship board's latest
    # captured_on), NOT each candidate's own latest row — a displaced ex-#1 must
    # drop the badge immediately, not wear a stale one until SHIP_BADGE_RETENTION_DAYS
    # prunes it. See get_player_ship_badges.
    latest_by_realm = {}
    for r in realms:
        cap, _, _ = latest_ship_snapshot_window(r)
        if cap is not None:
            latest_by_realm[r] = cap
    if not latest_by_realm:
        return {}
    gen_q = Q()
    for r, cap in latest_by_realm.items():
        gen_q |= Q(realm=r, captured_on=cap)

    result: dict = {}
    rows = list(
        ShipTopPlayerSnapshot.objects
        .filter(player_id__in=pks, player__is_hidden=False, rank__lte=top_n)
        .filter(gen_q)
        .order_by('rank', 'ship_name')
    )
    tier_by_ship = _ship_tier_map([r.ship_id for r in rows])
    eligible = _badge_tiers()
    for r in rows:
        # Off-scope treemap ships hold board rows but are not profile badges.
        if tier_by_ship.get(r.ship_id) not in eligible:
            continue
        battles = r.battles or 0
        result.setdefault(r.player_id, []).append({
            'ship_id': r.ship_id,
            'ship_name': r.ship_name,
            'rank': r.rank,
            'win_rate': r.win_rate,
            'battles': battles,
            'avg_damage': round((r.damage or 0) / battles) if battles else 0,
            'window_days': SHIP_LEADERBOARD_WINDOW_DAYS,
            'window_start': r.captured_on.isoformat() if r.captured_on else None,
            'tier': tier_by_ship.get(r.ship_id),
        })
    # Most prestigious tier first, then rank (same order as get_player_ship_badges).
    for pk in result:
        result[pk].sort(key=lambda b: (-(b['tier'] or 0), b['rank'], b['ship_name']))
    return result


def get_ship_leaderboard(realm: str, ship_id: int) -> Optional[dict]:
    """Latest trailing-window leaderboard for one ship on one realm (snapshot read).

    Powers the `/ship/<id>` page. Reads the most recent `captured_on`'s rows for
    the ship (precomputed nightly by `compute_ship_top_player_snapshot`, ≤
    `SHIP_BADGE_LIST_SIZE`), joins `Ship` for the header, and shapes a payload.
    Returns None when the ship_id is unknown; an empty `players` list when the
    ship was not "ranked" in the latest window (pool below the population guard).
    Cached by the view (`SHIP_LEADERBOARD_CACHE_TTL`). No live aggregation.
    """
    from warships.models import ShipTopPlayerSnapshot

    realm = (realm or DEFAULT_REALM).lower().strip()
    ship = Ship.objects.filter(ship_id=ship_id).first()
    if ship is None:
        return None

    latest = (
        ShipTopPlayerSnapshot.objects.filter(realm=realm, ship_id=ship_id)
        .order_by('-captured_on')
        .values_list('captured_on', flat=True)
        .first()
    )
    players = []
    if latest is not None:
        # Exclude now-hidden accounts (see get_player_ship_badges): the board is a
        # precomputed snapshot, so a player who hid after the run would otherwise
        # keep showing by name + stats until the next recompute. Their rank slot is
        # simply omitted (no re-ranking).
        rows = (
            ShipTopPlayerSnapshot.objects
            .filter(realm=realm, ship_id=ship_id, captured_on=latest,
                    player__is_hidden=False)
            .order_by('rank')
            .select_related('player')
        )
        players = [
            {
                'rank': r.rank,
                'player_name': r.player.name,
                'win_rate': r.win_rate,
                'battles': r.battles,
                # Windowed average damage and kills/battle — both accurate delta
                # sums (same basis as the profile ship badges). Survival%/KDR stay
                # omitted (per-battle survival isn't available for multi-battle
                # windows, so it would undercount).
                'avg_damage': round((r.damage or 0) / r.battles) if r.battles else 0,
                'kills_per_battle': round((r.frags or 0) / r.battles, 2) if r.battles else 0.0,
            }
            for r in rows
        ]

    # Rolling trailing window: the board is the latest `captured_on` (a run date),
    # covering [captured_on - window_days, captured_on). Recomputed nightly, so
    # there is no season boundary or "next window opens" countdown.
    window_start = (latest - timedelta(days=SHIP_LEADERBOARD_WINDOW_DAYS)) if latest else None
    return {
        'realm': realm,
        'window_days': SHIP_LEADERBOARD_WINDOW_DAYS,
        'captured_on': latest.isoformat() if latest else None,
        'window_start': window_start.isoformat() if window_start else None,
        'ship': {
            'ship_id': ship.ship_id,
            'name': ship.name,
            'tier': ship.tier,
            'ship_type': ship.ship_type,
            'nation': ship.nation,
            'is_premium': ship.is_premium,
            'shiptool_code': ship.shiptool_code or None,
        },
        'players': players,
    }


def _store_realm_ship_cache(fresh_key: str, published_key: str, payload: dict) -> dict:
    """Write a realm-ship payload to its window-keyed fresh key (26h TTL) and a
    window-**independent** durable ``:published`` key (no expiry).

    Write-new-then-overwrite — never delete-first — so a cold fresh key (a
    window-rotation gap after the nightly snapshot, or an ``allkeys-lru``
    eviction) can serve these numbers as last-good until the next warm replaces
    them. Mirrors ``landing._publish_landing_payload`` (the published-cache /
    durable-fallback idiom). The published key carries the request-shape
    discriminators (mode/limit or mode/tier/type) but **not** the window-end
    date, so it survives the rotation that the fresh key is designed to chase.
    """
    cache.set(fresh_key, payload, timeout=26 * 3600)
    cache.set(published_key, payload, timeout=None)
    return payload


def compute_realm_top_ships(realm, limit=25, mode="random", use_cache=True):
    """Most-played ships on a realm over the rolling ship-standings window.

    Sums ``BattleEvent.battles_delta`` per ship — filtered by realm + mode over the
    **rolling trailing ``SHIP_LEADERBOARD_WINDOW_DAYS`` window the ``/ship/<id>``
    leaderboards + profile medals read** (anchored on the latest
    ``ShipTopPlayerSnapshot.captured_on``; see ``latest_ship_snapshot_window``) —
    joins ``Ship`` for type/tier, and returns the top-``limit`` as a payload dict.
    The window advances each night with the snapshot, so the cache key carries the
    window-end date: when a new snapshot lands the key changes and the next request
    recomputes over the matching window (1:1 with the player lists). Shared by the
    ``realm_top_ships`` API view and the daily ``warm_realm_top_ships_task``
    warmer; pass ``use_cache=False`` to force a recompute.
    """
    from warships.models import BattleEvent

    realm = (realm or DEFAULT_REALM).lower().strip()
    mode = (str(mode) if mode is not None else "random").lower().strip()
    if mode not in ("random", "ranked"):
        mode = "random"
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 25
    limit = max(5, min(limit, 50))

    # Window: the rolling trailing window the /ship leaderboards read (anchored on
    # the latest snapshot's captured_on), so the treemap covers the identical date
    # span as the player lists. The cache key carries the window-end date, so when
    # a new nightly snapshot lands the key changes and the next request recomputes
    # over the matching window — alignment self-heals regardless of beat order.
    captured_on, window_start_d, window_end_d = latest_ship_snapshot_window(realm)
    window_start, window_end = _season_window_datetimes(window_start_d, window_end_d)

    cache_key = realm_cache_key(
        realm, f"top-ships:{mode}:win{window_end_d.isoformat()}:{limit}")
    published_key = realm_cache_key(realm, f"top-ships:published:{mode}:{limit}")
    if use_cache:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        # Fresh key cold (window rotated past the last warm, or evicted): serve
        # the durable last-good payload now and queue a warm to recompute the
        # new window — warm-before-evict. Never block the request on the heavy
        # BattleEvent aggregation. The brief misalignment vs the snapshot-backed
        # /ship boards (treemap shows the prior window for seconds–minutes) is
        # intentional and self-heals when the queued warm overwrites both keys.
        published = cache.get(published_key)
        if published is not None:
            from warships.tasks import queue_realm_top_ships_warm
            queue_realm_top_ships_warm(realm)
            return published

    rows = list(
        BattleEvent.objects
        .filter(detected_at__gte=window_start, detected_at__lt=window_end,
                player__realm=realm, mode=mode)
        .values("ship_id", "ship_name")
        .annotate(battles=Sum("battles_delta"))
        .filter(battles__gt=0)
        .order_by("-battles")[:limit]
    )
    ship_ids = [r["ship_id"] for r in rows]
    ships = {s.ship_id: s for s in Ship.objects.filter(ship_id__in=ship_ids)}

    payload_ships = [
        {
            "ship_id": r["ship_id"],
            "ship_name": (
                (ships[r["ship_id"]].name if r["ship_id"] in ships and ships[r["ship_id"]].name
                 else r["ship_name"]) or f"Ship {r['ship_id']}"
            ),
            "ship_type": ships[r["ship_id"]].ship_type if r["ship_id"] in ships else None,
            "tier": ships[r["ship_id"]].tier if r["ship_id"] in ships else None,
            "battles": int(r["battles"] or 0),
        }
        for r in rows
    ]

    payload = {
        "realm": realm,
        "window_days": SHIP_LEADERBOARD_WINDOW_DAYS,
        "captured_on": captured_on.isoformat() if captured_on else None,
        "window_start": window_start_d.isoformat(),  # date-only ISO (UTC midnight), inclusive
        "window_end": window_end_d.isoformat(),      # exclusive end (== captured_on)
        "mode": mode,
        "ships": payload_ships,
    }
    # Rolling window keyed by its end date; refreshed nightly by the warmer after
    # the snapshot lands. A 26h TTL bridges the daily warms without ever serving a
    # window the snapshot has already advanced past (the key changes at that point).
    # Also overwrite the durable published key so the next rotation gap serves
    # these numbers as last-good instead of blanking on a cold aggregation.
    return _store_realm_ship_cache(cache_key, published_key, payload)


# Raw WG ship-type strings as stored on `Ship.ship_type` (note: "AirCarrier",
# no space — the spelling the treemap and shipIdentity.ts both key on). The
# inline ship-leaderboard `type` filter accepts exactly these values.
SHIP_LEADERBOARD_TYPES = ('Battleship', 'Cruiser', 'Destroyer', 'AirCarrier', 'Submarine')
# Defensive secondary floor on the per-ship sample. The primary floor is the
# snapshot restriction (only ships that cleared the population guard are listed),
# so this just drops any oddly-thin row that still slipped through.
SHIP_LIST_MIN_BATTLES = int(os.getenv('SHIP_LIST_MIN_BATTLES', '50'))

# Win-rate-percentile views ("how are good/great players doing with these ships").
# When the inline ship list is filtered to the top 50% / 25% of each ship's
# players (by window win rate), the stats shown are re-pooled over that subset.
# These are the percentiles offered besides the default "all"; 100 is an internal
# hatch used only to assert the percentile path reproduces the cheap all-path.
SHIP_LIST_WR_PCTS = (50, 25)
# Per-PLAYER window-battle floor for the percentile ranking. A player needs at
# least this many battles in the window to enter the ranked population, so
# "top 25% by win rate" reflects players with a real sample rather than
# tiny-sample 100%-WR tourists (who would otherwise crowd out the genuinely good
# high-battle players). DISTINCT from SHIP_LIST_MIN_BATTLES, which gates whether a
# *ship* is listed (on full-population battles) and is unchanged by this filter.
SHIP_LIST_WR_PCT_PLAYER_MIN_BATTLES = int(
    os.getenv('SHIP_LIST_WR_PCT_PLAYER_MIN_BATTLES', '15'))
# The bucket the landing inline ship list opens on (mirrors ShipLeaderboard.tsx's
# initial tier/type). The list defaults to the top-50% WR view, so the daily
# top-ships warmer warms this one percentile bucket inline (instant primary view)
# and chains warm_realm_ships_pct_task, which pre-warms EVERY other tier×type pct
# bucket per realm (skip-if-warm, so the inline default isn't recomputed). The
# lazy queue+poll path remains only as a rare rotation-gap fallback.
SHIP_LIST_DEFAULT_TIER = int(os.getenv('SHIP_LIST_DEFAULT_TIER', '10'))
SHIP_LIST_DEFAULT_TYPE = os.getenv('SHIP_LIST_DEFAULT_TYPE', 'Battleship')


def _pool_player_rows(rows):
    """Sum (battles, wins, damage, frags) over per-player annotate dicts.

    damage/wins/frags are nullable Sums (coalesced to 0). Returns a 4-tuple.
    """
    battles = wins = damage = frags = 0
    for r in rows:
        battles += int(r["battles"] or 0)
        wins += int(r["wins"] or 0)
        damage += int(r["damage"] or 0)
        frags += int(r["frags"] or 0)
    return battles, wins, damage, frags


def _pct_ship_stats(player_rows, pct, player_floor):
    """Re-pool one ship's stats over the top-``pct``% of its players by win rate.

    ``player_rows`` is every player's window aggregate for the ship (annotate
    dicts with battles/wins/damage/frags + the ``player`` id). Players are ranked
    by win rate (descending; battles then player-id as deterministic tie-breaks)
    among those clearing ``player_floor`` window battles, and the top ``pct``%
    *by player count* (ceil, at least 1) are pooled into the displayed stats.

    Returns ``(battles, win_rate, avg_damage, kills_per_battle)`` or ``None`` when
    the ship has no positive-battle players at all. The ship is NEVER dropped for
    having too few players above the floor — if the floor leaves the ranked
    population empty, this falls back to the ship's full-population stats so the
    listed ship set stays identical to the all-view (the load-bearing constraint).
    ``pct >= 100`` ignores the floor entirely and pools every player, which makes
    the percentile path reproduce the cheap all-path exactly (equivalence hatch).
    """
    positive = [r for r in player_rows if int(r["battles"] or 0) > 0]
    if not positive:
        return None

    if pct >= 100:
        subset = positive
    else:
        ranked = [r for r in positive if int(r["battles"] or 0) >= player_floor]
        if not ranked:
            # Floor wiped out the population for this thin ship — keep it listed
            # with its full-population numbers rather than dropping it.
            subset = positive
        else:
            ranked.sort(key=lambda r: (
                -(int(r["wins"] or 0) / int(r["battles"])),
                -int(r["battles"]),
                r["player"],
            ))
            k = max(1, math.ceil(len(ranked) * pct / 100))
            subset = ranked[:k]

    battles, wins, damage, frags = _pool_player_rows(subset)
    if battles <= 0:
        return None
    return (
        battles,
        round(wins / battles * 100, 1),
        round(damage / battles),
        round(frags / battles, 2),
    )


def _ships_by_fresh_cache_key(realm, mode, window_end_d, tier, ship_type,
                              wr_pct=None):
    """Single source of truth for the window-keyed ship-list *fresh* cache key.

    Components must already be normalized exactly as
    ``compute_realm_ships_by_tier_type`` normalizes them (realm/mode lowercased,
    tier ``int``, ship_type stripped). ``wr_pct=None`` is the default all-view;
    50/25/100 is a win-rate-percentile view (suffixed ``:wr<pct>``). Both the
    writer (compute) and the warmer's warm-state check
    (:func:`ship_pct_bucket_cache_key`) build the key through here so the two can
    never drift — a duplicated key string is the bug that silently turns the
    warmer's skip-if-warm into "always recompute".
    """
    base = f"ships-by:{mode}:win{window_end_d.isoformat()}:t{tier}:{ship_type}"
    if wr_pct is not None:
        base = f"{base}:wr{wr_pct}"
    return realm_cache_key(realm, base)


def _pct_published_cache_key(realm, mode, tier, ship_type, wr_pct):
    """Durable window-independent ``:published`` key for a percentile bucket.

    Mirrors the all-view's published key with the pct discriminator appended,
    so each percentile view keeps a last-good copy across window rotations,
    Redis restarts, and starved warm chains (see ``_store_realm_ship_cache``).
    Components must already be normalized as ``compute_realm_ships_by_tier_type``
    normalizes them.
    """
    return realm_cache_key(
        realm, f"ships-by:published:{mode}:t{tier}:{ship_type}:wr{wr_pct}")


def _store_pct_ship_caches(realm, mode, window_end_d, tier, ship_type, payload,
                           requested_pct):
    """Write one payload to every offered percentile's fresh + published keys.

    Used by the warm path when a bucket has no candidate ships: the empty
    result must still be cached (all pcts share it) or the bucket stays
    permanently ``pending`` — the cold read path answers pending before ever
    reaching the compute, so an uncached empty pct bucket would poll forever
    (~48s client stall per view). Returns the requested pct's copy.
    """
    requested = None
    for pct in {requested_pct, *SHIP_LIST_WR_PCTS}:
        p = dict(payload)
        p["wr_pct"] = pct
        _store_realm_ship_cache(
            _ships_by_fresh_cache_key(
                realm, mode, window_end_d, tier, ship_type, pct),
            _pct_published_cache_key(realm, mode, tier, ship_type, pct),
            p)
        if pct == requested_pct:
            requested = p
    return requested


def ship_pct_bucket_cache_key(realm, tier, ship_type, mode="random", wr_pct=50):
    """Fresh cache key for a percentile ship-list bucket, normalized identically
    to :func:`compute_realm_ships_by_tier_type`.

    Exposed so ``warm_realm_ships_pct_task`` can check whether a bucket is already
    warm for the current window (and skip it) without re-running the heavy
    per-(ship,player) aggregation. Both 50 & 25 are written together by one
    compute, so a single percentile's presence implies the bucket is warm.
    """
    realm = (realm or DEFAULT_REALM).lower().strip()
    mode = (str(mode) if mode is not None else "random").lower().strip()
    if mode not in ("random", "ranked"):
        mode = "random"
    try:
        tier = int(tier)
    except (TypeError, ValueError):
        tier = None
    ship_type = (ship_type or "").strip()
    _, _, window_end_d = latest_ship_snapshot_window(realm)
    return _ships_by_fresh_cache_key(
        realm, mode, window_end_d, tier, ship_type, int(wr_pct))


def compute_realm_ships_by_tier_type(realm, tier, ship_type, mode="random",
                                     min_battles=None, wr_pct=None,
                                     player_min_battles=None, use_cache=True):
    """Ships of one tier+type on a realm, ranked by win rate over the rolling window.

    Powers the landing-page inline ship leaderboard (the filterable table under
    the treemap). Aggregates ``BattleEvent`` deltas per ship — realm + mode, over
    the **rolling trailing ``SHIP_LEADERBOARD_WINDOW_DAYS`` window the treemap, the
    ``/ship/<id>`` board and profile medals read** (anchored on the latest
    ``ShipTopPlayerSnapshot.captured_on``; see ``latest_ship_snapshot_window``) —
    to realm-wide win rate / avg damage / kills per battle, then orders by win rate
    descending.

    The candidate set is restricted to ships that hold a ``ShipTopPlayerSnapshot``
    row for the **latest** ``captured_on`` (i.e. ships that cleared the population
    guard in the current window and have a populated drill-down board), so every
    listed ship reliably opens a non-empty leaderboard and the snapshot's
    population guard doubles as the sample floor.

    The window advances nightly with the snapshot, so this is cached under a
    window-end-tagged key and refreshed by the daily warmer (mirrors
    ``compute_realm_top_ships``). Returns a payload dict; ``ships`` is ``[]`` when
    no ship in the bucket qualifies. ``tier``/``ship_type`` are assumed validated
    by the caller (the view rejects out-of-range values).

    ``wr_pct`` (one of ``SHIP_LIST_WR_PCTS`` — 50/25 — or ``None`` for the default
    "all") switches to the **win-rate-percentile view**: each listed ship's stats
    are re-pooled over only the top ``wr_pct``% of its players by window win rate
    (answering "how are good/great players doing with these ships"). The *listed
    ship set is unchanged* — membership still gates on full-population battles ≥
    ``min_battles`` — only the displayed numbers narrow. ``player_min_battles``
    floors which players enter the ranking. The percentile path runs a heavier
    per-(ship,player) aggregation, derives every offered percentile from one
    query, and caches each under its own window-keyed fresh key PLUS a durable
    window-independent ``:published`` fallback (``_pct_published_cache_key``) —
    a cold fresh key serves that last-good copy and queues a warm, exactly like
    the all-view, so viewers only ever see the `pending` stub on a bucket's
    first-ever computation. The nightly ``warm_realm_ships_pct_task`` pre-warms
    every tier×type percentile bucket per realm (skip-if-warm), so even the
    published fallback is a rotation-gap/restart exception, not the norm.
    """
    from warships.models import BattleEvent, ShipTopPlayerSnapshot

    realm = (realm or DEFAULT_REALM).lower().strip()
    mode = (str(mode) if mode is not None else "random").lower().strip()
    if mode not in ("random", "ranked"):
        mode = "random"
    try:
        tier = int(tier)
    except (TypeError, ValueError):
        tier = None
    ship_type = (ship_type or "").strip()
    if min_battles is None:
        min_battles = SHIP_LIST_MIN_BATTLES
    if player_min_battles is None:
        player_min_battles = SHIP_LIST_WR_PCT_PLAYER_MIN_BATTLES
    # Normalize the percentile selector. Anything outside the offered set (plus the
    # internal 100 equivalence hatch) collapses to None = the default all-view.
    if wr_pct is not None:
        try:
            wr_pct = int(wr_pct)
        except (TypeError, ValueError):
            wr_pct = None
        if wr_pct not in (*SHIP_LIST_WR_PCTS, 100):
            wr_pct = None
    is_pct = wr_pct is not None

    captured_on, window_start_d, window_end_d = latest_ship_snapshot_window(realm)
    window_start, window_end = _season_window_datetimes(window_start_d, window_end_d)

    if is_pct:
        # Percentile views are keyed by their pct. Like the all-view they carry
        # a window-independent durable `:published` fallback (written by the
        # same warm that fills the fresh keys), so a rotation gap, a starved
        # warm chain, or a Redis restart serves last-good numbers instead of a
        # pending stub. The nightly per-bucket pct warmer
        # (warm_realm_ships_pct_task) pre-fills the fresh keys so even that
        # fallback is the exception, not the first-view experience.
        cache_key = _ships_by_fresh_cache_key(
            realm, mode, window_end_d, tier, ship_type, wr_pct)
        published_key = _pct_published_cache_key(
            realm, mode, tier, ship_type, wr_pct)
    else:
        cache_key = _ships_by_fresh_cache_key(
            realm, mode, window_end_d, tier, ship_type)
        published_key = realm_cache_key(
            realm, f"ships-by:published:{mode}:t{tier}:{ship_type}")
    if use_cache:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        # Fresh key cold (window rotation gap / eviction / starved warm chain):
        # serve last-good and queue a warm — warm-before-evict, never block on
        # the aggregation. See compute_realm_top_ships for the full rationale +
        # the alignment note.
        published = cache.get(published_key)
        if published is not None:
            if is_pct:
                from warships.tasks import queue_ships_by_pct_warm
                queue_ships_by_pct_warm(realm, tier, ship_type, mode)
            else:
                from warships.tasks import queue_realm_top_ships_warm
                queue_realm_top_ships_warm(realm)
            return published
        # Cold PERCENTILE bucket with no last-good copy yet (first-ever view of
        # the bucket): the per-(ship,player) recompute is too heavy (~10–28s for
        # popular T10 buckets, over the client's 15s timeout) to run on the
        # request thread. Queue a background warm and return a `pending`
        # payload — the client polls (ttlMs:0) until the warm fills this
        # window-keyed fresh key. (Only once there's a snapshot/window to
        # compute against; with captured_on None we fall through to the empty
        # payload so the client doesn't poll forever.) See warm_ships_by_pct_task.
        if is_pct and captured_on is not None:
            from warships.tasks import queue_ships_by_pct_warm
            queue_ships_by_pct_warm(realm, tier, ship_type, mode)
            return {
                "realm": realm,
                "window_days": SHIP_LEADERBOARD_WINDOW_DAYS,
                "tier": tier,
                "ship_type": ship_type,
                "mode": mode,
                "wr_pct": wr_pct,
                "captured_on": captured_on.isoformat(),
                "window_start": window_start_d.isoformat(),
                "window_end": window_end_d.isoformat(),
                "total_battles": 0,
                "ships": [],
                "pending": True,
            }

    payload = {
        "realm": realm,
        "window_days": SHIP_LEADERBOARD_WINDOW_DAYS,
        "tier": tier,
        "ship_type": ship_type,
        "mode": mode,
        "captured_on": captured_on.isoformat() if captured_on else None,
        "window_start": window_start_d.isoformat(),
        "window_end": window_end_d.isoformat(),
        # None for the default all-view; 50/25 for a win-rate-percentile view.
        "wr_pct": wr_pct,
        # Total battles played across **every** ship of this tier+type in the
        # window — the whole-bucket denominator the client divides each ship's
        # battles into for its class/tier share %. Deliberately broader than the
        # listed ships: it counts low-population ships that miss the snapshot /
        # min-battles floor, so the per-ship shares sum to <100%. Filters mirror
        # the per-ship `rows` query below exactly (same window/realm/mode, and no
        # `is_hidden` filter — `rows` has none either, so numerator and
        # denominator share the same basis), broadened only to the full tier+type
        # ship set and dropping the min-battles floor.
        "total_battles": 0,
        "ships": [],
    }
    # Empty buckets (no snapshot, or no ships of this tier+type ranked) are not
    # cached on the read path — they re-run only the cheap candidate query, and
    # caching empty would let it clobber a good published payload during a cold
    # serve. On the **warm** path (use_cache=False) we DO cache + publish the
    # empty so a bucket that went empty this window clears its stale last-good —
    # and, for pct views, so the bucket isn't permanently `pending` (the pct
    # cold-read answers pending before ever reaching this compute, so an
    # uncached empty pct bucket would stall every viewer ~48s, forever).
    def _store_empty_bucket():
        if use_cache:
            return payload
        if is_pct:
            return _store_pct_ship_caches(
                realm, mode, window_end_d, tier, ship_type, payload, wr_pct)
        return _store_realm_ship_cache(cache_key, published_key, payload)

    if captured_on is None:
        return _store_empty_bucket()

    # Candidate ships: this tier+type AND ranked in the latest snapshot
    # (guarantees a populated drill-down board for every listed ship).
    snapshot_ids = set(
        ShipTopPlayerSnapshot.objects
        .filter(realm=realm, captured_on=captured_on)
        .values_list('ship_id', flat=True)
    )
    if not snapshot_ids:
        return _store_empty_bucket()
    ships = {
        s.ship_id: s
        for s in Ship.objects.filter(
            ship_id__in=snapshot_ids, tier=tier, ship_type=ship_type)
    }
    if not ships:
        return _store_empty_bucket()

    # Whole-bucket denominator: battles over **all** ships of this tier+type in
    # the window (not just the snapshot-ranked candidates), so each listed ship's
    # client-side class/tier share % is a true fraction of every game played in
    # the bucket. Same filters as the per-ship `rows` query (window/realm/mode,
    # no `is_hidden`); only the ship set is broadened and the min-battles floor
    # dropped. See the `total_battles` note on the payload above.
    bucket_ids = list(
        Ship.objects.filter(tier=tier, ship_type=ship_type)
        .values_list('ship_id', flat=True)
    )
    if bucket_ids:
        payload["total_battles"] = int(
            BattleEvent.objects
            .filter(detected_at__gte=window_start, detected_at__lt=window_end,
                    player__realm=realm, mode=mode, ship_id__in=bucket_ids)
            .aggregate(t=Sum("battles_delta"))["t"] or 0
        )

    def _ship_meta(ship_id, ship):
        """Static identity fields shared by the all-path and percentile rows."""
        return {
            "ship_id": ship_id,
            "ship_name": (ship.name if ship and ship.name else f"Ship {ship_id}"),
            "ship_type": ship.ship_type if ship else ship_type,
            "tier": ship.tier if ship else tier,
            "nation": ship.nation if ship else "",
            "is_premium": bool(ship.is_premium) if ship else False,
        }

    if is_pct:
        # Win-rate-percentile view. ONE per-(ship,player) aggregation feeds every
        # offered percentile (so a 50↔25 toggle never re-runs this heavier query);
        # each pct is built + cached under its own window-keyed fresh key.
        player_rows_by_ship = {}
        for r in (
            BattleEvent.objects
            .filter(detected_at__gte=window_start, detected_at__lt=window_end,
                    player__realm=realm, mode=mode, ship_id__in=ships.keys())
            .values("ship_id", "player")
            .annotate(
                battles=Sum("battles_delta"),
                wins=Sum("wins_delta"),
                damage=Sum("damage_delta"),
                frags=Sum("frags_delta"),
            )
        ):
            player_rows_by_ship.setdefault(r["ship_id"], []).append(r)

        requested = None
        for pct in {wr_pct, *SHIP_LIST_WR_PCTS}:
            ships_out = []
            for ship_id, prows in player_rows_by_ship.items():
                # Membership mirrors the all-path EXACTLY: gate on the ship's
                # FULL-population battles (never the subset), so the listed ship
                # set is identical to the all-view — the load-bearing constraint.
                full_battles, *_ = _pool_player_rows(prows)
                if full_battles < min_battles:
                    continue
                stats = _pct_ship_stats(prows, pct, player_min_battles)
                if stats is None:
                    continue
                battles, win_rate, avg_damage, kpb = stats
                row = _ship_meta(ship_id, ships.get(ship_id))
                row.update({
                    "battles": battles,
                    "win_rate": win_rate,
                    "avg_damage": avg_damage,
                    "kills_per_battle": kpb,
                })
                ships_out.append(row)
            ships_out.sort(key=lambda s: (-s["win_rate"], -s["battles"]))
            p = dict(payload)
            p["wr_pct"] = pct
            p["ships"] = ships_out
            _store_realm_ship_cache(
                _ships_by_fresh_cache_key(
                    realm, mode, window_end_d, tier, ship_type, pct),
                _pct_published_cache_key(realm, mode, tier, ship_type, pct),
                p)
            if pct == wr_pct:
                requested = p
        return requested

    # ship_id is a plain BigIntegerField (no FK), so there is no ORM join to
    # Ship — aggregate the candidate ids directly, then attach metadata in Python.
    rows = (
        BattleEvent.objects
        .filter(detected_at__gte=window_start, detected_at__lt=window_end,
                player__realm=realm, mode=mode, ship_id__in=ships.keys())
        .values("ship_id")
        .annotate(
            battles=Sum("battles_delta"),
            wins=Sum("wins_delta"),
            # damage_delta is nullable — Sum can return None; coalesced below.
            damage=Sum("damage_delta"),
            frags=Sum("frags_delta"),
        )
        .filter(battles__gte=min_battles)
    )

    payload_ships = []
    for r in rows:
        battles = int(r["battles"] or 0)
        if battles <= 0:
            continue
        wins = int(r["wins"] or 0)
        damage = int(r["damage"] or 0)
        frags = int(r["frags"] or 0)
        row = _ship_meta(r["ship_id"], ships.get(r["ship_id"]))
        row.update({
            "battles": battles,
            "win_rate": round(wins / battles * 100, 1),
            "avg_damage": round(damage / battles),
            "kills_per_battle": round(frags / battles, 2),
        })
        payload_ships.append(row)

    # Win rate descending; battles as a deterministic tie-break.
    payload_ships.sort(key=lambda s: (-s["win_rate"], -s["battles"]))
    payload["ships"] = payload_ships

    # Rolling window keyed by its end date; refreshed nightly by the warmer. The
    # 26h TTL bridges the daily warms; the key changes once a new snapshot lands.
    # Also overwrite the durable published key (last-good fallback for the next
    # rotation gap). See compute_realm_top_ships / _store_realm_ship_cache.
    return _store_realm_ship_cache(cache_key, published_key, payload)


# ---------------------------------------------------------------------------
# Ship combat comparison (ShipStats component)
# ---------------------------------------------------------------------------
# Operationalizes the untapped per-ship combat fields documented in
# runbook-battle-history-data-operationalization-2026-06-16.md. For one ship,
# compares a player's CAREER per-ship profile (full coverage, read from the
# latest BattleObservation.ships_stats_json) against the ship's 30-day
# POPULATION average (summed across all players' PlayerDailyShipStats for that
# ship_id + realm). Per-battle rates and accuracy ratios are window-robust, so
# career-vs-30d is a coherent "you vs how this ship is being played" comparison
# with full user coverage. Metrics whose population denominator is ~0 (e.g.
# secondaries on a DD, torpedoes on most BBs) are OMITTED — the frontend renders
# only what is meaningful for the ship.

SHIP_COMBAT_WINDOW_DAYS = 30
_SHIP_COMBAT_POP_CACHE_TTL = 3600  # 1h — population aggregate is player-independent

# The widened per-day columns summed for the population aggregate. ships_stats_json
# calls damage `damage_dealt`; PlayerDailyShipStats calls it `damage` — normalized
# to `damage` for both sides below.
_SHIP_COMBAT_SUM_FIELDS = (
    'battles', 'wins', 'losses', 'frags', 'damage', 'xp', 'planes_killed',
    'survived_battles',
    'main_shots', 'main_hits', 'main_frags',
    'secondary_shots', 'secondary_hits', 'secondary_frags',
    'torpedo_shots', 'torpedo_hits', 'torpedo_frags',
    'damage_scouting', 'ships_spotted',
    'capture_points', 'dropped_capture_points', 'team_capture_points',
)


def _ship_combat_safe_div(numerator, denominator):
    if not denominator:
        return None
    return numerator / denominator


# Metric catalogue. `value(totals)` derives a per-battle rate or accuracy ratio
# from a totals dict. `gate(pop)` (optional) returns False when the ship's
# population sample can't support the metric → it is dropped. `better` drives
# the frontend's above/below-average coloring.
#
# RELIABILITY SCOPING (important): only metrics that PlayerDailyShipStats can
# aggregate trustworthily across the population are surfaced. The original core
# counters (battles / wins / frags / damage / xp / planes_killed) are complete
# on every daily row, and accuracy RATIOS (hits / shots) self-normalize over the
# rows that carry gunnery. The Phase-7 WIDENED per-battle counters — survival,
# spotting, scouting, capture play — are captured on only a small fraction of
# daily rows (~6% in spot checks), so their per-battle population averages are
# badly biased and are intentionally NOT surfaced. A faithful comparison for
# those needs a precomputed career-population aggregate from ships_stats_json
# (the runbook's recommended full-coverage source) — tracked as a follow-up.
_SHIP_COMBAT_MIN_SHOTS = 100  # accuracy ratios need a stable population sample

_SHIP_COMBAT_METRICS = (
    dict(key='win_rate', label='Win rate', cluster='Outcomes', unit='%', better='high',
         value=lambda t: _ship_combat_safe_div(t['wins'] * 100.0, t['battles'])),
    dict(key='damage_pb', label='Damage', cluster='Combat output', unit='/battle', better='high',
         value=lambda t: _ship_combat_safe_div(t['damage'], t['battles'])),
    dict(key='frags_pb', label='Frags', cluster='Combat output', unit='/battle', better='high',
         value=lambda t: _ship_combat_safe_div(t['frags'], t['battles'])),
    dict(key='xp_pb', label='XP', cluster='Combat output', unit='/battle', better='high',
         value=lambda t: _ship_combat_safe_div(t['xp'], t['battles'])),
    # Accuracy ratios use the player's CAREER totals (user_basis='career'): the
    # 30-day daily rows seldom capture gunnery, and hit% is a stable skill better
    # judged over a career than a few recent battles. The population average
    # stays 30-day (over the rows that did capture shots).
    dict(key='main_hit_rate', label='Main battery hit %', cluster='Accuracy', unit='%', better='high',
         user_basis='career',
         gate=lambda p: p['main_shots'] >= _SHIP_COMBAT_MIN_SHOTS,
         value=lambda t: _ship_combat_safe_div(t['main_hits'] * 100.0, t['main_shots'])),
    dict(key='secondary_hit_rate', label='Secondary hit %', cluster='Accuracy', unit='%', better='high',
         user_basis='career',
         gate=lambda p: p['secondary_shots'] >= _SHIP_COMBAT_MIN_SHOTS,
         value=lambda t: _ship_combat_safe_div(t['secondary_hits'] * 100.0, t['secondary_shots'])),
    dict(key='torpedo_hit_rate', label='Torpedo hit %', cluster='Accuracy', unit='%', better='high',
         user_basis='career',
         gate=lambda p: p['torpedo_shots'] >= _SHIP_COMBAT_MIN_SHOTS,
         value=lambda t: _ship_combat_safe_div(t['torpedo_hits'] * 100.0, t['torpedo_shots'])),
)


# --- Ship population avg-damage baseline (battle-history damage treemap) ---
# Realm-wide per-ship average damage over the trailing SHIP_COMBAT_WINDOW_DAYS
# of random battles — the same population/window convention as the ShipStats
# panel above, reduced to the one number the damage treemap colors against.
# The per-ship aggregate takes SECONDS on popular ships (realm-wide PDSS scan),
# so it is NEVER computed on the request thread: the battle-history view
# attaches from this cache only and queues warm_ship_pop_avg_damage_task for
# misses (tasks.py). Ships below the population floor cache 0 (a "computed,
# no usable baseline" sentinel — attach translates it to None and does NOT
# re-queue).
SHIP_POP_AVG_MIN_BATTLES = 20
_SHIP_POP_AVG_CACHE_TTL = 26 * 3600  # day-scoped key; TTL is just a backstop

# --- ShipPopDailyAgg rollup (DB-audit lever F9.2) ---
# Per-(realm, mode, ship, day) aggregate of PlayerDailyShipStats, so the
# nightly bulk warm sums ~30 tiny rows per ship instead of re-scanning the
# 7M+ row PDSS table (~34s/realm measured). PDSS `date` comes from
# `detected_at.date()`, so a past realm-day is frozen once the UTC day ends
# (only the manual rebuild repair op rewrites history) — the catch-up
# therefore fills missing dates and re-rolls only the trailing
# SHIP_POP_ROLLUP_REFRESH_DAYS: the current UTC day is still accruing, and a
# commit straddling midnight can land its detected-yesterday rows just after
# the day flips.
SHIP_POP_ROLLUP_RETENTION_DAYS = 100  # self-bounding; pruned inside the rollup
SHIP_POP_ROLLUP_REFRESH_DAYS = 2      # today + yesterday always re-rolled

# (PDSS source column, ShipPopDailyAgg column) — the sums the ship-population
# consumers need: avg-damage baseline (battles, damage_sum) + the ship-combat
# metric catalogue's per-battle rates and hit ratios incl. their shot gates.
_SHIP_POP_ROLLUP_FIELDS = (
    ('battles', 'battles'),
    ('wins', 'wins'),
    ('frags', 'frags'),
    ('damage', 'damage_sum'),
    ('xp', 'xp'),
    ('main_shots', 'main_shots'),
    ('main_hits', 'main_hits'),
    ('secondary_shots', 'secondary_shots'),
    ('secondary_hits', 'secondary_hits'),
    ('torpedo_shots', 'torpedo_shots'),
    ('torpedo_hits', 'torpedo_hits'),
)


def rollup_ship_pop_daily(realm: str, on_date) -> int:
    """Recompute ONE realm-day of ShipPopDailyAgg from PlayerDailyShipStats.

    Idempotent delete-and-replace upsert inside one transaction (a realm-day
    is only a few hundred (ship, mode) rows). Also prunes the realm's rows
    older than SHIP_POP_ROLLUP_RETENTION_DAYS so the table stays
    self-bounding without a dedicated timer. Returns the number of agg rows
    written for the day."""
    from warships.models import PlayerDailyShipStats as _PDSS, ShipPopDailyAgg

    sum_kwargs = {agg: Sum(src) for src, agg in _SHIP_POP_ROLLUP_FIELDS}
    with transaction.atomic(), _elevated_work_mem():
        rows = list(
            _PDSS.objects
            .filter(date=on_date, player__realm=realm)
            .values('ship_id', 'mode')
            .annotate(**sum_kwargs)
        )
        ShipPopDailyAgg.objects.filter(realm=realm, date=on_date).delete()
        ShipPopDailyAgg.objects.bulk_create([
            ShipPopDailyAgg(
                realm=realm, date=on_date,
                ship_id=row['ship_id'], mode=row['mode'],
                **{agg: int(row[agg] or 0)
                   for _, agg in _SHIP_POP_ROLLUP_FIELDS},
            )
            for row in rows
        ])
    prune_before = (django_timezone.now().date()
                    - timedelta(days=SHIP_POP_ROLLUP_RETENTION_DAYS))
    ShipPopDailyAgg.objects.filter(
        realm=realm, date__lt=prune_before).delete()
    return len(rows)


def rollup_ship_pop_daily_catchup(
        realm: str, window_days: int = SHIP_COMBAT_WINDOW_DAYS) -> int:
    """Bring the trailing window's ShipPopDailyAgg up to date for a realm.

    Rolls up (a) every window date with no agg rows yet — on first deploy
    this backfills the whole window, afterwards only genuine gaps — and
    (b) always the trailing SHIP_POP_ROLLUP_REFRESH_DAYS (still accruing).
    A frozen day already rolled up is skipped, which is what makes the
    nightly warm cheap after day one. Returns the number of days rolled."""
    from warships.models import ShipPopDailyAgg

    today = django_timezone.now().date()
    cutoff = today - timedelta(days=window_days)
    rolled_dates = set(
        ShipPopDailyAgg.objects
        .filter(realm=realm, date__gte=cutoff)
        .values_list('date', flat=True)
        .distinct()
    )
    days = 0
    for offset in range(window_days + 1):
        day = cutoff + timedelta(days=offset)
        in_refresh_window = (today - day).days < SHIP_POP_ROLLUP_REFRESH_DAYS
        if in_refresh_window or day not in rolled_dates:
            rollup_ship_pop_daily(realm, day)
            days += 1
    return days


def _ship_pop_avg_damage_cache_key(realm: str, ship_id: int) -> str:
    day = django_timezone.now().date().isoformat()
    return f"ship_pop_avgdmg:v1:{realm}:{int(ship_id)}:{day}"


def compute_ship_pop_avg_damage(realm: str, ship_id: int) -> int:
    """Compute + cache one ship's realm-wide 30d random avg damage. Returns
    the cached value (0 when the population is below the floor). Task-side
    only — seconds per popular ship."""
    from warships.models import PlayerDailyShipStats as _PDSS

    cutoff = django_timezone.now().date() - timedelta(
        days=SHIP_COMBAT_WINDOW_DAYS)
    with transaction.atomic(), _elevated_work_mem():
        row = (
            _PDSS.objects
            .filter(ship_id=int(ship_id), mode=_PDSS.MODE_RANDOM,
                    date__gte=cutoff, player__realm=realm)
            .aggregate(b=Sum('battles'), d=Sum('damage'))
        )
    battles = int(row['b'] or 0)
    value = (
        int(round((row['d'] or 0) / battles))
        if battles >= SHIP_POP_AVG_MIN_BATTLES else 0
    )
    cache.set(_ship_pop_avg_damage_cache_key(realm, ship_id), value,
              _SHIP_POP_AVG_CACHE_TTL)
    return value


def compute_all_ship_pop_avg_damage(realm: str) -> dict:
    """Bulk variant of compute_ship_pop_avg_damage: computes + caches EVERY
    ship's realm-wide 30d random avg damage, including the 0 below-floor
    sentinels. Nightly pre-warm path; the per-ship request-driven warm
    survives only as the gap fallback (day-key rotation at UTC midnight →
    first viewers before the snapshot chain fires). Task-side only.

    F9.2 rework: instead of ONE grouped scan of the 7M+ row PDSS window
    (~34s/realm measured 2026-07-13), first bring the ShipPopDailyAgg daily
    rollup up to date (first call backfills the window; afterwards only the
    trailing refresh days + genuine gaps compute), then sum the window from
    the small agg table (~30 rows/ship). Output is IDENTICAL to the legacy
    scan — same cache keys, values, floor, and sentinel semantics — because
    per-day sums compose associatively into the same window totals."""
    from warships.models import PlayerDailyShipStats as _PDSS, ShipPopDailyAgg

    rollup_ship_pop_daily_catchup(realm)
    cutoff = django_timezone.now().date() - timedelta(
        days=SHIP_COMBAT_WINDOW_DAYS)
    rows = list(
        ShipPopDailyAgg.objects
        .filter(realm=realm, mode=_PDSS.MODE_RANDOM, date__gte=cutoff)
        .values('ship_id')
        .annotate(b=Sum('battles'), d=Sum('damage_sum'))
    )
    payload = {}
    for row in rows:
        battles = int(row['b'] or 0)
        payload[_ship_pop_avg_damage_cache_key(realm, row['ship_id'])] = (
            int(round((row['d'] or 0) / battles))
            if battles >= SHIP_POP_AVG_MIN_BATTLES else 0
        )
    if payload:
        cache.set_many(payload, _SHIP_POP_AVG_CACHE_TTL)
    return {"ships": len(payload)}


def get_cached_ship_pop_avg_damage(realm: str, ship_ids) -> tuple[dict, list]:
    """Read-only bulk cache probe. Returns ({ship_id: cached value}, [missing
    ship_ids]) — 0-sentinel values are 'hits' (computed, below floor) so the
    caller never re-queues them."""
    ids = sorted({int(s) for s in ship_ids})
    if not ids:
        return {}, []
    keys = {sid: _ship_pop_avg_damage_cache_key(realm, sid) for sid in ids}
    cached = cache.get_many(list(keys.values()))
    hits = {sid: cached[k] for sid, k in keys.items() if k in cached}
    missing = [sid for sid in ids if sid not in hits]
    return hits, missing


# Skill brackets, ranked by overall account random win rate (Player.pvp_ratio).
# `all` is the whole window population; `top50`/`top25` are the better-skilled
# halves/quarters of it (relative percentiles of THIS ship's players, not fixed
# global cutoffs). Mirrors shiptool's account-WR bracketing in spirit.
_SHIP_COMBAT_BRACKETS = ('all', 'top50', 'top25')
_SHIP_COMBAT_BRACKET_FRACTION = {'all': 1.0, 'top50': 0.50, 'top25': 0.25}
# Exclude low-sample accounts from the skill ranking (shiptool uses 200).
_SHIP_COMBAT_MIN_ACCOUNT_BATTLES = 200


def _ship_population_brackets_30d(ship_id, realm, window_days=SHIP_COMBAT_WINDOW_DAYS):
    """Per-skill-bracket summed PlayerDailyShipStats (random) for one ship over
    the trailing window. Players are aggregated individually, ranked by overall
    account random win rate (Player.pvp_ratio, accounts with >=200 pvp battles),
    then summed into the All / top-50% / top-25% brackets. Cached per
    (realm, ship, day)."""
    import math
    from warships.models import PlayerDailyShipStats

    cache_key = (
        f"ship_combat_pop:v2:{realm}:{ship_id}:{window_days}:"
        f"{django_timezone.now().date().isoformat()}"
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    cutoff = django_timezone.now().date() - timedelta(days=window_days)
    sum_kwargs = {f: Sum(f) for f in _SHIP_COMBAT_SUM_FIELDS}
    with transaction.atomic(), _elevated_work_mem():
        per_player = list(
            PlayerDailyShipStats.objects
            .filter(ship_id=ship_id, mode='random', date__gte=cutoff,
                    player__realm=realm,
                    player__pvp_battles__gte=_SHIP_COMBAT_MIN_ACCOUNT_BATTLES,
                    player__pvp_ratio__isnull=False)
            .values('player_id', 'player__pvp_ratio')
            .annotate(**sum_kwargs)
        )

    # Rank by account win rate (desc) and take the leading N% for each bracket.
    per_player.sort(key=lambda r: r['player__pvp_ratio'], reverse=True)
    total = len(per_player)

    def _sum_rows(rows):
        totals = {f: 0 for f in _SHIP_COMBAT_SUM_FIELDS}
        for r in rows:
            for f in _SHIP_COMBAT_SUM_FIELDS:
                totals[f] += int(r.get(f) or 0)
        totals['players'] = len(rows)
        return totals

    result = {}
    for bracket, fraction in _SHIP_COMBAT_BRACKET_FRACTION.items():
        count = total if fraction >= 1.0 else (max(1, math.ceil(total * fraction)) if total else 0)
        result[bracket] = _sum_rows(per_player[:count])

    cache.set(cache_key, result, _SHIP_COMBAT_POP_CACHE_TTL)
    return result


def _ship_combat_user_totals(player, ship_id, window_days=SHIP_COMBAT_WINDOW_DAYS):
    """The player's own totals for one ship over the SAME trailing window and
    source (PlayerDailyShipStats, random) as the population — so the panel is
    consistent with the Battle History table the user clicked from and with the
    "30d" framing. Returns None when the player has no battles on the ship in the
    window."""
    from warships.models import PlayerDailyShipStats

    cutoff = django_timezone.now().date() - timedelta(days=window_days)
    row = (
        PlayerDailyShipStats.objects
        .filter(player=player, ship_id=ship_id, mode='random', date__gte=cutoff)
        .aggregate(**{f: Sum(f) for f in _SHIP_COMBAT_SUM_FIELDS})
    )
    totals = {f: int(row.get(f) or 0) for f in _SHIP_COMBAT_SUM_FIELDS}
    if totals['battles'] <= 0:
        return None
    return totals


def _ship_combat_user_career_totals(player, ship_id):
    """The player's CAREER totals for one ship from the latest
    BattleObservation.ships_stats_json — complete for every field. Used for the
    accuracy ratios, whose 30-day daily rows are too sparse (gunnery is captured
    on few rows) and which are stable career traits anyway. Returns None if no
    observation row for the ship."""
    from warships.models import BattleObservation

    obs = (
        BattleObservation.objects
        .filter(player=player, ships_stats_json__isnull=False)
        .order_by('-observed_at')
        .first()
    )
    if not obs or not obs.ships_stats_json:
        return None
    row = next((r for r in obs.ships_stats_json
                if r.get('ship_id') == ship_id), None)
    if row is None:
        return None
    totals = {f: int(row.get(f, 0) or 0) for f in _SHIP_COMBAT_SUM_FIELDS}
    totals['damage'] = int(row.get('damage_dealt', row.get('damage', 0)) or 0)
    return totals


def compute_ship_combat_comparison(player, ship_id, realm,
                                   window_days=SHIP_COMBAT_WINDOW_DAYS):
    """Build the ShipStats payload: per-metric {user, averages:{all,top50,top25}}
    clustered by combat role, with role-irrelevant metrics omitted (population
    denominator ~0). Both `user` and each `averages` entry are 30-day random-
    battle rates (same window/source), so the panel stays consistent with the
    Battle History table; `averages` is bracketed by account-WR skill. Any side
    may be None (no battles in the window / empty bracket)."""
    ship_id = int(ship_id)
    brackets = _ship_population_brackets_30d(ship_id, realm, window_days)
    pop_all = brackets['all']
    # 30d window totals for the core per-battle metrics (match the table); career
    # totals for the accuracy ratios (30d gunnery is too sparse — see specs).
    user_window = _ship_combat_user_totals(player, ship_id, window_days)
    user_career = _ship_combat_user_career_totals(player, ship_id)
    user_totals_for = {'window': user_window, 'career': user_career}

    ship = Ship.objects.filter(ship_id=ship_id).first()

    clusters: dict[str, list] = {}
    cluster_order: list[str] = []
    pop_has_battles = pop_all['battles'] > 0

    for spec in _SHIP_COMBAT_METRICS:
        gate = spec.get('gate')
        # Whether to surface the metric is decided on the full population; the
        # per-bracket averages may still be None for a thin top-25% slice.
        if not pop_has_battles:
            continue
        if gate is not None and not gate(pop_all):
            continue
        if spec['value'](pop_all) is None:
            continue

        averages = {}
        for bracket in _SHIP_COMBAT_BRACKETS:
            value = spec['value'](brackets[bracket])
            averages[bracket] = round(value, 2) if value is not None else None
        user_totals = user_totals_for[spec.get('user_basis', 'window')]
        user_value = spec['value'](user_totals) if user_totals is not None else None

        cluster = spec['cluster']
        if cluster not in clusters:
            clusters[cluster] = []
            cluster_order.append(cluster)
        clusters[cluster].append({
            'key': spec['key'],
            'label': spec['label'],
            'unit': spec['unit'],
            'better': spec['better'],
            'user': round(user_value, 2) if user_value is not None else None,
            'averages': averages,
        })

    return {
        'ship_id': ship_id,
        'ship_name': (ship.name if ship else '') or (player and ''),
        'ship_tier': ship.tier if ship else None,
        'ship_type': ship.ship_type if ship else None,
        'window_days': window_days,
        'min_account_battles': _SHIP_COMBAT_MIN_ACCOUNT_BATTLES,
        'brackets': {
            bracket: {'players': brackets[bracket]['players'],
                      'battles': brackets[bracket]['battles']}
            for bracket in _SHIP_COMBAT_BRACKETS
        },
        'user_battles': (user_window or {}).get('battles', 0),
        'has_user_data': user_window is not None or user_career is not None,
        'clusters': [{'name': name, 'metrics': clusters[name]}
                     for name in cluster_order],
    }
