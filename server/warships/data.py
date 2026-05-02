from warships.tasks import update_activity_data_task, update_battle_data_task, update_clan_data_task, update_clan_members_task, update_randoms_data_task, update_snapshot_data_task, update_tiers_data_task, update_type_data_task
from warships.api.clans import _fetch_clan_data, _fetch_clan_member_ids, _fetch_clan_membership_for_player, \
    _fetch_clan_battle_seasons_info, _fetch_clan_battle_season_stats
from warships.api.players import _fetch_snapshot_data, _fetch_player_personal_data, _fetch_ranked_account_info, _fetch_player_achievements
from warships.api.ships import _fetch_ship_stats_for_player, _fetch_ship_info, _fetch_ranked_ship_stats_for_player, _fetch_efficiency_badges_for_player, build_ship_chart_name
from warships.achievements_catalog import get_achievement_catalog_entry
from warships.player_analytics import compute_player_verdict
from warships.data_support import _coerce_activity_rows, _coerce_battle_rows, _coerce_efficiency_rows, _coerce_ranked_rows, _has_newer_source_timestamp, _is_stale_timestamp, _queue_limited_player_hydration, _timestamped_payload_needs_refresh
from warships.player_records import BlockedAccountError, get_or_create_canonical_player
from warships.models import PlayerAchievementStat, MvPlayerDistributionStats
from warships.models import DEFAULT_REALM, realm_cache_key, Player, Snapshot, Clan, PlayerExplorerSummary, Ship
from django.utils import timezone as django_timezone
from django.db.models.functions import Cast, Lower, TruncMonth
from django.db.models import Avg, Case, Count, F, FloatField, IntegerField, Q, Sum, Value, When
from django.db import connection, transaction
from django.core.cache import cache
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from typing import Dict, Any, Optional, Iterable
from datetime import datetime, timezone, timedelta, date
import logging
import math
import os

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
PLAYER_EXPLORER_ON_READ_BACKFILL_MAX = 200
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
HOT_ENTITY_PINNED_PLAYER_NAMES = [
    n.strip() for n in os.getenv('HOT_ENTITY_PINNED_PLAYER_NAMES', 'lil_boots').split(',') if n.strip()
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


def player_derived_chart_data_needs_refresh(
    updated_at: Optional[datetime],
    stale_after: timedelta = PLAYER_DERIVED_DATA_STALE_AFTER,
) -> bool:
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


def _get_published_efficiency_rank_payload(player: Player) -> dict[str, Any]:
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


def _efficiency_rank_tier_from_percentile(percentile: Optional[float]) -> Optional[str]:
    if percentile is None:
        return None
    if percentile >= EFFICIENCY_RANK_EXPERT_PERCENTILE:
        return 'E'
    if percentile >= EFFICIENCY_RANK_GRADE_I_PERCENTILE:
        return 'I'
    if percentile >= EFFICIENCY_RANK_GRADE_II_PERCENTILE:
        return 'II'
    if percentile >= EFFICIENCY_RANK_MIN_VISIBLE_PERCENTILE:
        return 'III'
    return None


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


def _calculate_shrunken_efficiency_strength(
    normalized_badge_strength: float,
    eligible_ship_count: int,
    field_mean_strength: float,
    shrinkage_k: float = EFFICIENCY_RANK_SHRINKAGE_K,
) -> float:
    if eligible_ship_count <= 0:
        return field_mean_strength

    weight = eligible_ship_count / (eligible_ship_count + shrinkage_k)
    shrunken_strength = (
        weight * normalized_badge_strength +
        ((1.0 - weight) * field_mean_strength)
    )
    return round(shrunken_strength, 6)


def _score_percentile_from_rank(average_rank: float, population_size: int) -> float:
    if population_size <= 1:
        return 1.0

    return round((population_size - average_rank) / (population_size - 1), 6)


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


def _count_suppressed_players(min_pvp, min_ships, unmapped_limit):
    suppressed_sql = """
        SELECT
            SUM(CASE WHEN COALESCE(p.pvp_battles, 0) < %s THEN 1 ELSE 0 END),
            SUM(CASE WHEN COALESCE(es.eligible_ship_count, 0) < %s THEN 1 ELSE 0 END),
            SUM(CASE WHEN COALESCE(es.efficiency_badge_rows_total, 0) > 0
                      AND es.normalized_badge_strength IS NOT NULL
                      AND COALESCE(es.eligible_ship_count, 0) >= %s
                      AND COALESCE(p.pvp_battles, 0) >= %s
                      AND (COALESCE(es.badge_rows_unmapped, 0)::float
                           / es.efficiency_badge_rows_total) > %s
                 THEN 1 ELSE 0 END)
        FROM warships_playerexplorersummary es
        JOIN warships_player p ON p.id = es.player_id
        WHERE p.is_hidden = false
    """
    with connection.cursor() as cursor:
        cursor.execute(suppressed_sql, [
            min_pvp, min_ships, min_ships, min_pvp, unmapped_limit,
        ])
        row = cursor.fetchone()
    counts = {}
    if row:
        if row[0]:
            counts['low_battles'] = row[0]
        if row[1]:
            counts['low_ships'] = row[1]
        if row[2]:
            counts['unmapped_badge_gate'] = row[2]
    return counts


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


def _calculate_tier_filtered_pvp_record(
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


def get_highest_ranked_league_name(ranked_rows: Any) -> Optional[str]:
    normalized_rows = _coerce_ranked_rows(ranked_rows)
    best_league: Optional[int] = None

    for row in normalized_rows:
        if int(row.get('total_battles', 0) or 0) <= 0:
            continue

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
            continue

        if best_league is None or league < best_league:
            best_league = league

    if best_league is None:
        return None

    return LEAGUE_NAMES.get(best_league)


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


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _normalize_wr_score(pvp_ratio: Optional[float]) -> Optional[float]:
    if pvp_ratio is None:
        return None

    return _clamp((float(pvp_ratio) - 45.0) / 20.0, 0.0, 1.0)


def _normalize_kdr_score(kill_ratio: Optional[float]) -> Optional[float]:
    if kill_ratio is None:
        return None

    return _clamp((float(kill_ratio) - 0.4) / 1.6, 0.0, 1.0)


def _normalize_survival_score(pvp_survival_rate: Optional[float]) -> Optional[float]:
    if pvp_survival_rate is None:
        return None

    return _clamp((float(pvp_survival_rate) - 25.0) / 25.0, 0.0, 1.0)


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
        competitive_share = _clamp(weighted_battles / total_battles, 0.0, 1.0)
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

    return _clamp(math.log10(battles + 1) / 4.0, 0.0, 1.0)


def _calculate_competitive_tier_factor(battle_rows: list[dict], fallback_battles: Optional[int]) -> float:
    total_battles = 0
    weighted_battles = 0.0

    for row in battle_rows:
        battles = max(int(row.get('pvp_battles', 0) or 0), 0)
        if battles <= 0:
            continue

        total_battles += battles
        weighted_battles += battles * \
            _player_score_tier_weight(row.get('ship_tier'))

    if total_battles <= 0:
        fallback_total = max(int(fallback_battles or 0), 0)
        return 1.0 if fallback_total > 0 else 1.0

    competitive_share = _clamp(weighted_battles / total_battles, 0.0, 1.0)
    return round(
        _clamp(
            PLAYER_SCORE_LOW_TIER_FLOOR +
                ((1.0 - PLAYER_SCORE_LOW_TIER_FLOOR)
                 * math.sqrt(competitive_share)),
            PLAYER_SCORE_LOW_TIER_FLOOR,
            1.0,
        ),
        4,
    )


def _fibonacci_activity_weight(day_age: int) -> float:
    if day_age <= 1:
        return 34.0
    if day_age <= 3:
        return 21.0
    if day_age <= 7:
        return 13.0
    if day_age <= 13:
        return 8.0
    if day_age <= 21:
        return 5.0
    if day_age <= 34:
        return 3.0
    if day_age <= 55:
        return 2.0
    if day_age <= 89:
        return 1.0
    if day_age <= 144:
        return 0.55
    if day_age <= 233:
        return 0.34
    if day_age <= 365:
        return 0.21
    return 0.08


def _smoothstep(progress: float) -> float:
    normalized = _clamp(progress, 0.0, 1.0)
    return normalized * normalized * (3.0 - (2.0 * normalized))


def _inactivity_score_cap(days_since_last_battle: Optional[int]) -> Optional[float]:
    if days_since_last_battle is None:
        return None

    days = max(int(days_since_last_battle), 0)
    if days <= PLAYER_SCORE_INACTIVITY_GRACE_DAYS:
        return PLAYER_SCORE_MAX

    if days <= 180:
        progress = (days - PLAYER_SCORE_INACTIVITY_GRACE_DAYS) / \
            (180.0 - PLAYER_SCORE_INACTIVITY_GRACE_DAYS)
        return round(
            PLAYER_SCORE_MAX -
            ((PLAYER_SCORE_MAX - PLAYER_SCORE_180_DAY_CAP) * _smoothstep(progress)),
            2,
        )

    if days <= 365:
        progress = (days - 180) / 185.0
        return round(
            PLAYER_SCORE_180_DAY_CAP -
            ((PLAYER_SCORE_180_DAY_CAP - PLAYER_SCORE_365_DAY_CAP)
             * _smoothstep(progress)),
            2,
        )

    overflow_days = days - 365
    return round(
        max(
            PLAYER_SCORE_DORMANT_MIN,
            PLAYER_SCORE_365_DAY_CAP *
            math.exp(-overflow_days / PLAYER_SCORE_POST_YEAR_DECAY_DAYS),
        ),
        2,
    )


def _inactivity_activity_multiplier(days_since_last_battle: Optional[int]) -> float:
    if days_since_last_battle is None:
        return 1.0

    if days_since_last_battle <= 34:
        return 1.0
    if days_since_last_battle <= 55:
        return 0.92
    if days_since_last_battle <= 89:
        return 0.75
    if days_since_last_battle <= 144:
        return 0.55
    if days_since_last_battle <= 233:
        return 0.35
    if days_since_last_battle <= 365:
        return 0.18
    return 0.06


def _calculate_recent_activity_score(activity_rows: Any, days_since_last_battle: Optional[int]) -> float:
    normalized_rows = _coerce_activity_rows(activity_rows)
    today = datetime.now().date()
    weighted_intensity = 0.0

    for row in normalized_rows:
        row_date = row.get('date')
        if not row_date:
            continue

        try:
            age_days = (
                today - datetime.fromisoformat(str(row_date)).date()).days
        except ValueError:
            continue

        if age_days < 0:
            continue

        battles = int(row.get('battles', 0) or 0)
        if battles <= 0:
            continue

        day_intensity = _clamp(
            math.log1p(battles) /
            math.log1p(PLAYER_SCORE_ACTIVITY_SATURATION_BATTLES),
            0.0,
            1.0,
        )
        weighted_intensity += day_intensity * \
            _fibonacci_activity_weight(age_days)

    max_recent_weight = sum(_fibonacci_activity_weight(day_age)
                            for day_age in range(29))
    recent_score = weighted_intensity / \
        max_recent_weight if max_recent_weight > 0 else 0.0
    return round(_clamp(recent_score, 0.0, 1.0) * _inactivity_activity_multiplier(days_since_last_battle), 4)


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


def _explorer_summary_needs_refresh(player: Player) -> bool:
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
        'days_since_last_battle': player.days_since_last_battle,
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

    if getattr(player, 'explorer_summary', None) is None and (
        player.battles_json is not None or player.activity_json is not None or player.ranked_json is not None
    ):
        refresh_player_explorer_summary(player)

    return build_player_summary(player)


def fetch_player_explorer_rows(
    query: str = '',
    hidden: str = 'all',
    activity_bucket: str = 'all',
    ranked: str = 'all',
    min_pvp_battles: int = 0,
    realm: str = DEFAULT_REALM,
) -> list[dict]:
    players = _build_player_explorer_queryset(
        query=query,
        hidden=hidden,
        activity_bucket=activity_bucket,
        ranked=ranked,
        min_pvp_battles=min_pvp_battles,
        realm=realm,
    )

    rows = []
    for player in players:
        if getattr(player, 'explorer_summary', None) is None:
            refresh_player_explorer_summary(player)
        rows.append(build_player_summary(player))

    if ranked == 'yes':
        rows = [row for row in rows if (
            row.get('ranked_seasons_participated') or 0) > 0]
    elif ranked == 'no':
        rows = [row for row in rows if (
            row.get('ranked_seasons_participated') or 0) == 0]

    return rows


def _player_explorer_base_queryset(realm: str = DEFAULT_REALM):
    return Player.objects.filter(realm=realm).exclude(name='').select_related('explorer_summary').only(
        'id',
        'name',
        'player_id',
        'is_hidden',
        'days_since_last_battle',
        'last_battle_date',
        'creation_date',
        'pvp_ratio',
        'pvp_battles',
        'pvp_survival_rate',
        'explorer_summary__id',
        'explorer_summary__player_id',
        'explorer_summary__battles_last_29_days',
        'explorer_summary__wins_last_29_days',
        'explorer_summary__active_days_last_29_days',
        'explorer_summary__recent_win_rate',
        'explorer_summary__activity_trend_direction',
        'explorer_summary__kill_ratio',
        'explorer_summary__player_score',
        'explorer_summary__ships_played_total',
        'explorer_summary__ship_type_spread',
        'explorer_summary__tier_spread',
        'explorer_summary__eligible_ship_count',
        'explorer_summary__efficiency_badge_rows_total',
        'explorer_summary__badge_rows_unmapped',
        'explorer_summary__expert_count',
        'explorer_summary__grade_i_count',
        'explorer_summary__grade_ii_count',
        'explorer_summary__grade_iii_count',
        'explorer_summary__raw_badge_points',
        'explorer_summary__normalized_badge_strength',
        'explorer_summary__ranked_seasons_participated',
        'explorer_summary__latest_ranked_battles',
        'explorer_summary__highest_ranked_league_recent',
    )


def _build_player_explorer_queryset(
    query: str = '',
    hidden: str = 'all',
    activity_bucket: str = 'all',
    ranked: str = 'all',
    min_pvp_battles: int = 0,
    apply_ranked_filter: bool = True,
    realm: str = DEFAULT_REALM,
):
    players = _player_explorer_base_queryset(realm=realm)

    if query:
        players = players.filter(name__icontains=query)

    if hidden == 'visible':
        players = players.filter(is_hidden=False)
    elif hidden == 'hidden':
        players = players.filter(is_hidden=True)

    if min_pvp_battles > 0:
        players = players.filter(pvp_battles__gte=min_pvp_battles)

    if activity_bucket == '7d':
        players = players.filter(days_since_last_battle__lte=7)
    elif activity_bucket == '30d':
        players = players.filter(days_since_last_battle__lte=30)
    elif activity_bucket == '90d':
        players = players.filter(days_since_last_battle__lte=90)
    elif activity_bucket == 'dormant90plus':
        players = players.filter(days_since_last_battle__gt=90)

    if apply_ranked_filter:
        if ranked == 'yes':
            players = players.filter(
                explorer_summary__ranked_seasons_participated__gt=0)
        elif ranked == 'no':
            players = players.filter(
                Q(explorer_summary__ranked_seasons_participated__isnull=True)
                | Q(explorer_summary__ranked_seasons_participated=0)
            )

    return players


def _player_explorer_sort_uses_summary(sort: str) -> bool:
    return sort in {
        'kill_ratio',
        'player_score',
        'battles_last_29_days',
        'active_days_last_29_days',
        'ships_played_total',
        'ranked_seasons_participated',
    }


def _backfill_player_explorer_summaries(players) -> None:
    for player in players.filter(explorer_summary__isnull=True):
        refresh_player_explorer_summary(player)


def _build_player_explorer_ordering(sort: str, direction: str):
    if sort == 'account_age_days':
        creation_date_order = F('creation_date').asc(nulls_last=True)
        if direction == 'asc':
            creation_date_order = F('creation_date').desc(nulls_last=True)
        return [creation_date_order, Lower('name').asc(), 'player_id']

    field_map = {
        'name': 'name',
        'days_since_last_battle': 'days_since_last_battle',
        'pvp_ratio': 'pvp_ratio',
        'pvp_battles': 'pvp_battles',
        'pvp_survival_rate': 'pvp_survival_rate',
        'kill_ratio': 'explorer_summary__kill_ratio',
        'player_score': 'explorer_summary__player_score',
        'battles_last_29_days': 'explorer_summary__battles_last_29_days',
        'active_days_last_29_days': 'explorer_summary__active_days_last_29_days',
        'ships_played_total': 'explorer_summary__ships_played_total',
        'ranked_seasons_participated': 'explorer_summary__ranked_seasons_participated',
    }
    field_name = field_map[sort]
    primary_order = F(field_name).asc(nulls_last=True)
    if direction == 'desc':
        primary_order = F(field_name).desc(nulls_last=True)

    if sort == 'name':
        return [Lower('name').desc() if direction == 'desc' else Lower('name').asc(), 'player_id']

    return [primary_order, Lower('name').asc(), 'player_id']


def fetch_player_explorer_page(
    query: str = '',
    hidden: str = 'all',
    activity_bucket: str = 'all',
    ranked: str = 'all',
    min_pvp_battles: int = 0,
    sort: str = 'player_score',
    direction: str = 'desc',
    page: int = 1,
    page_size: int = 25,
    realm: str = DEFAULT_REALM,
) -> tuple[int, list[dict]]:
    players = _build_player_explorer_queryset(
        query=query,
        hidden=hidden,
        activity_bucket=activity_bucket,
        ranked=ranked,
        min_pvp_battles=min_pvp_battles,
        apply_ranked_filter=False,
        realm=realm,
    )

    should_backfill = ranked != 'all' or _player_explorer_sort_uses_summary(
        sort)
    if should_backfill and players.count() <= PLAYER_EXPLORER_ON_READ_BACKFILL_MAX:
        _backfill_player_explorer_summaries(players)

    if ranked == 'yes':
        players = players.filter(
            explorer_summary__ranked_seasons_participated__gt=0)
    elif ranked == 'no':
        players = players.filter(
            Q(explorer_summary__ranked_seasons_participated__isnull=True)
            | Q(explorer_summary__ranked_seasons_participated=0)
        )

    players = players.order_by(
        *_build_player_explorer_ordering(sort, direction))

    total_count = players.count()
    start = (page - 1) * page_size
    end = start + page_size

    rows = []
    for player in players[start:end]:
        if getattr(player, 'explorer_summary', None) is None:
            refresh_player_explorer_summary(player)
        rows.append(build_player_summary(player))

    return total_count, rows


def _extract_randoms_rows(battles_json: Any, limit: Optional[int] = 20) -> list[dict]:
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


def update_battle_data(player_id: str, realm: str = DEFAULT_REALM) -> None:
    """
    Updates the battle data for a given player.

    This function fetches the latest battle data for a player from an external API if the cached data is older than 15 minutes.
    The fetched data is then processed and saved back to the player's record in the database.

    Args:
        player_id (str): The ID of the player whose battle data needs to be updated.
        realm (str): The realm to scope the query to.

    Returns:
        None
    """
    player = Player.objects.get(player_id=player_id, realm=realm)

    # Check if the cached data is less than 15 minutes old
    if player.battles_json and player.battles_updated_at and datetime.now() - player.battles_updated_at < timedelta(minutes=15):
        logging.debug(
            f'Cache exists and is fresh: returning cached data')
        return player.battles_json

    logging.info(
        f'Battles data empty or outdated: fetching new data for {player.name}')

    # Fetch ship stats for the player
    ship_data = _fetch_ship_stats_for_player(player_id, realm=realm)
    if not ship_data:
        logging.warning(
            f'No ship stats returned for player_id={player_id}; recording empty battles_json to avoid re-selection.'
        )
        player.battles_json = []
        player.battles_updated_at = datetime.now()
        player.save(update_fields=['battles_json', 'battles_updated_at'])
        return

    prepared_data = []

    for ship in ship_data:
        ship_model = _fetch_ship_info(ship['ship_id'])
        ship_metadata = _build_ship_row_metadata(
            ship.get('ship_id'), ship_model)
        if ship_model is None:
            logging.warning(
                'Falling back to placeholder ship metadata for ship_id=%s while updating player_id=%s',
                ship.get('ship_id'),
                player_id,
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
            'kdr': round(frags / pvp_battles, 2) if pvp_battles > 0 else 0
        }

        prepared_data.append(ship_info)

    # Sort the data by "pvp_battles" in descending order
    sorted_data = sorted(prepared_data, key=lambda x: x.get(
        'pvp_battles', 0), reverse=True)

    player.battles_updated_at = datetime.now()
    player.battles_json = sorted_data
    player.save()
    update_tiers_data(player.player_id, realm=realm)
    update_type_data(player.player_id, realm=realm)
    update_randoms_data(player.player_id, realm=realm)
    refresh_player_explorer_summary(player, battles_rows=sorted_data)
    logging.info(f"Updated battles_json data: {player.name}")

    # Battle-history capture hook (Phase 2 of the playerbase rollout).
    # Runbook: agents/runbooks/runbook-battle-history-rollout-2026-04-28.md
    # Off in production until BATTLE_HISTORY_CAPTURE_ENABLED=1 is set.
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
    player.save()


def update_snapshot_data(player_id: int, realm: str = DEFAULT_REALM, refresh_player: bool = True) -> None:
    """
    Records today's cumulative PvP stats as a Snapshot and computes
    daily interval_battles / interval_wins from successive snapshots.

    The WoWS account/statsbydate endpoint no longer returns pvp data,
    so we use the Player model's pvp_battles / pvp_wins (kept current
    by update_player_data via account/info) as today's cumulative values.
    """
    player = Player.objects.get(player_id=player_id, realm=realm)

    # Ensure the player model has fresh stats
    if refresh_player:
        from warships.data import update_player_data
        update_player_data(player, force_refresh=True)
        player.refresh_from_db()

    today = datetime.now().date()
    start_date = today - timedelta(days=28)

    # Purge stale zero-value snapshots left by the broken statsbydate API
    Snapshot.objects.filter(
        player=player, battles=0, wins=0
    ).exclude(date=today).delete()

    # Upsert today's snapshot with current cumulative totals
    snapshot, _ = Snapshot.objects.get_or_create(player=player, date=today)
    snapshot.battles = player.pvp_battles or 0
    snapshot.wins = player.pvp_wins or 0
    snapshot.last_fetch = datetime.now()
    snapshot.save()

    # Recompute intervals for the whole 28-day window
    snapshots = list(Snapshot.objects.filter(
        player=player, date__gte=start_date, date__lte=today).order_by('date'))

    previous_battles = None
    previous_wins = None
    for snap in snapshots:
        if previous_battles is None or previous_wins is None:
            snap.interval_battles = 0
            snap.interval_wins = 0
        else:
            snap.interval_battles = max(
                0, int(snap.battles or 0) - int(previous_battles or 0))
            snap.interval_wins = max(
                0, int(snap.wins or 0) - int(previous_wins or 0))

        previous_battles = snap.battles
        previous_wins = snap.wins

    Snapshot.objects.bulk_update(
        snapshots, ['interval_battles', 'interval_wins'])

    update_activity_data(player_id, realm=realm)
    logging.info(f'Updated snapshot data for player {player.name}')


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
    snapshots = list(Snapshot.objects.filter(player=player).order_by('date'))

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
    player.save()
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
LANDING_ACTIVITY_ATTRITION_CACHE_TTL = 900
LANDING_ACTIVITY_ATTRITION_MONTHS = 18
LANDING_ACTIVITY_ATTRITION_COMPARE_WINDOW = 6
LANDING_ACTIVITY_ACTIVE_DAYS = 30
LANDING_ACTIVITY_COOLING_DAYS = 90


def _shift_month_start(month_start: date, month_delta: int) -> date:
    absolute_month = (month_start.year * 12) + \
        month_start.month - 1 + month_delta
    shifted_year = absolute_month // 12
    shifted_month = (absolute_month % 12) + 1
    return date(shifted_year, shifted_month, 1)


def _classify_population_signal(recent_active_avg: float, prior_active_avg: float) -> tuple[str, Optional[float]]:
    if prior_active_avg <= 0:
        if recent_active_avg <= 0:
            return 'stable', None
        return 'growing', None

    delta_pct = round(
        ((recent_active_avg - prior_active_avg) / prior_active_avg) * 100, 1)
    if delta_pct >= 8.0:
        return 'growing', delta_pct
    if delta_pct <= -8.0:
        return 'shrinking', delta_pct
    return 'stable', delta_pct


def fetch_landing_activity_attrition(realm: str = DEFAULT_REALM) -> dict:
    cache_key = realm_cache_key(realm, 'landing:activity_attrition:v1')
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    today = datetime.now(timezone.utc).date()
    current_month_start = today.replace(day=1)
    latest_complete_month = _shift_month_start(current_month_start, -1)
    earliest_month = _shift_month_start(
        latest_complete_month,
        -(LANDING_ACTIVITY_ATTRITION_MONTHS - 1),
    )

    cohort_rows = list(
        Player.objects.filter(
            realm=realm,
            is_hidden=False,
            creation_date__isnull=False,
            creation_date__gte=earliest_month,
            creation_date__lt=current_month_start,
        ).annotate(
            cohort_month=TruncMonth('creation_date'),
        ).values('cohort_month').annotate(
            total_players=Count('id'),
            active_players=Count('id', filter=Q(
                days_since_last_battle__lte=LANDING_ACTIVITY_ACTIVE_DAYS)),
            cooling_players=Count(
                'id',
                filter=Q(
                    days_since_last_battle__gt=LANDING_ACTIVITY_ACTIVE_DAYS,
                    days_since_last_battle__lte=LANDING_ACTIVITY_COOLING_DAYS,
                ),
            ),
            dormant_players=Count('id', filter=Q(
                days_since_last_battle__gt=LANDING_ACTIVITY_COOLING_DAYS)),
        ).order_by('cohort_month')
    )

    rows_by_month = {
        row['cohort_month'].date(): row
        for row in cohort_rows
        if row.get('cohort_month') is not None
    }

    months = []
    cursor = earliest_month
    while cursor <= latest_complete_month:
        row = rows_by_month.get(cursor, {})
        total_players = int(row.get('total_players', 0) or 0)
        active_players = int(row.get('active_players', 0) or 0)
        cooling_players = int(row.get('cooling_players', 0) or 0)
        dormant_players = int(row.get('dormant_players', 0) or 0)

        months.append({
            'month': cursor.isoformat(),
            'total_players': total_players,
            'active_players': active_players,
            'cooling_players': cooling_players,
            'dormant_players': dormant_players,
            'active_share': round((active_players / total_players) * 100, 1) if total_players > 0 else 0.0,
        })
        cursor = _shift_month_start(cursor, 1)

    recent_window = months[-LANDING_ACTIVITY_ATTRITION_COMPARE_WINDOW:]
    prior_window = months[-(LANDING_ACTIVITY_ATTRITION_COMPARE_WINDOW * 2):-LANDING_ACTIVITY_ATTRITION_COMPARE_WINDOW]
    recent_active_avg = round(
        sum(row['active_players'] for row in recent_window) / len(recent_window), 1) if recent_window else 0.0
    prior_active_avg = round(
        sum(row['active_players'] for row in prior_window) / len(prior_window), 1) if prior_window else 0.0
    recent_new_avg = round(
        sum(row['total_players'] for row in recent_window) / len(recent_window), 1) if recent_window else 0.0
    prior_new_avg = round(
        sum(row['total_players'] for row in prior_window) / len(prior_window), 1) if prior_window else 0.0
    population_signal, signal_delta_pct = _classify_population_signal(
        recent_active_avg,
        prior_active_avg,
    )

    payload = {
        'metric': 'landing_activity_attrition',
        'label': 'Player Activity and Attrition',
        'x_label': 'Account Creation Month',
        'y_label': 'Players Observed',
        'tracked_population': sum(row['total_players'] for row in months),
        'months': months,
        'summary': {
            'latest_month': latest_complete_month.isoformat(),
            'population_signal': population_signal,
            'signal_delta_pct': signal_delta_pct,
            'recent_active_avg': recent_active_avg,
            'prior_active_avg': prior_active_avg,
            'recent_new_avg': recent_new_avg,
            'prior_new_avg': prior_new_avg,
            'months_compared': LANDING_ACTIVITY_ATTRITION_COMPARE_WINDOW,
        },
    }
    cache.set(cache_key, payload, LANDING_ACTIVITY_ATTRITION_CACHE_TTL)
    return payload


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
        try:
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


def _fetch_player_tier_type_population_correlation(realm: str = DEFAULT_REALM, *, allow_rebuild: bool = True) -> dict:
    cache_key = _player_correlation_cache_key(
        PLAYER_TIER_TYPE_CACHE_VERSION, realm=realm)
    published_cache_key = _player_correlation_published_cache_key(
        PLAYER_TIER_TYPE_CACHE_VERSION, realm=realm)
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
    tile_counts: dict[tuple[str, int], int] = {}
    trend_tier_weighted_sum: dict[str, float] = {}
    trend_battles: dict[str, int] = {}
    observed_ship_types: set[str] = set()
    tracked_population = 0

    with transaction.atomic(), _elevated_work_mem():
        rows = Player.objects.filter(
            realm=realm,
            is_hidden=False,
            pvp_battles__gte=config['min_population_battles'],
            battles_json__isnull=False,
        ).values_list('battles_json', flat=True)

        for battles_json in rows.iterator(chunk_size=1000):
            normalized_rows = _extract_tier_type_battle_rows(battles_json)
            if not normalized_rows:
                continue

            tracked_population += 1
            for row in normalized_rows:
                ship_type = str(row['ship_type'])
                ship_tier = int(row['ship_tier'])
                pvp_battles = int(row['pvp_battles'])
                observed_ship_types.add(ship_type)

                tile_counts[(ship_type, ship_tier)] = tile_counts.get(
                    (ship_type, ship_tier), 0) + pvp_battles
                trend_tier_weighted_sum[ship_type] = trend_tier_weighted_sum.get(
                    ship_type, 0.0) + (ship_tier * pvp_battles)
                trend_battles[ship_type] = trend_battles.get(
                    ship_type, 0) + pvp_battles

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
    """Force-rebuild the tier-type population correlation cache."""
    cache_key = _player_correlation_cache_key(
        PLAYER_TIER_TYPE_CACHE_VERSION, realm=realm)
    cache.delete(cache_key)
    return _fetch_player_tier_type_population_correlation(realm=realm)


def warm_player_wr_survival_correlation(realm: str = DEFAULT_REALM) -> dict:
    """Force-rebuild the win-rate vs survival correlation cache."""
    cache_key = _player_correlation_cache_key('win_rate_survival', realm=realm)
    cache.delete(cache_key)
    return fetch_player_wr_survival_correlation(realm=realm)


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
        realm=realm)

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

    if not player.battles_json:
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


def fetch_player_wr_survival_correlation(realm: str = DEFAULT_REALM) -> dict:
    cache_key = _player_correlation_cache_key('win_rate_survival', realm=realm)
    published_cache_key = _player_correlation_published_cache_key(
        'win_rate_survival', realm=realm)
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
        try:
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


def _fetch_player_ranked_wr_battles_population_correlation(realm: str = DEFAULT_REALM) -> dict:
    cache_key = _player_correlation_cache_key(
        PLAYER_RANKED_WR_BATTLES_CORRELATION_CACHE_VERSION, realm=realm)
    published_cache_key = _player_correlation_published_cache_key(
        PLAYER_RANKED_WR_BATTLES_CORRELATION_CACHE_VERSION, realm=realm)
    cached = cache.get(cache_key)
    if cached is not None:
        cache.set(published_cache_key, cached, timeout=None)
        return cached

    published = cache.get(published_cache_key)
    if published is not None:
        return published

    payload = _build_player_ranked_wr_battles_population_correlation_payload(
        realm=realm)
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
CLAN_BATTLE_PLAYER_STATS_CACHE_TTL = 21600
CLAN_BATTLE_SUMMARY_CACHE_TTL = 3600


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


def _get_ranked_seasons_metadata() -> dict:
    """Return season_id → {name, label, start_date, end_date}. Cached for 24h in Redis."""
    from warships.api.players import _fetch_ranked_seasons_info

    cached = cache.get(RANKED_SEASONS_CACHE_KEY)
    if cached is not None:
        return cached

    raw = _fetch_ranked_seasons_info()
    if not raw:
        return {}  # nothing to cache

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

    cache.set(RANKED_SEASONS_CACHE_KEY, result, RANKED_SEASONS_CACHE_TTL)
    return result


def _get_clan_battle_seasons_metadata() -> dict:
    """Return season_id -> clan battle season metadata. Cached for 24h."""
    cached = cache.get(CLAN_BATTLE_SEASONS_CACHE_KEY)
    if cached is not None:
        return cached

    raw = _fetch_clan_battle_seasons_info()
    if not raw:
        return {}

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

    cache.set(CLAN_BATTLE_SEASONS_CACHE_KEY,
              result, CLAN_BATTLE_SEASONS_CACHE_TTL)
    return result


def _get_player_clan_battle_season_stats(account_id: int, realm: str = DEFAULT_REALM) -> list:
    """Return cached clan battle season stats for a player."""
    cache_key = realm_cache_key(realm, f'clan_battles:player:{account_id}')
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    raw = _fetch_clan_battle_season_stats(account_id, realm=realm)
    seasons = raw.get('seasons', []) if raw else []
    cache.set(cache_key, seasons, CLAN_BATTLE_PLAYER_STATS_CACHE_TTL)
    return seasons


def get_player_clan_battle_summary(account_id: Optional[int], allow_fetch: bool = True, realm: str = DEFAULT_REALM) -> dict[str, Any]:
    if not account_id:
        return summarize_clan_battle_seasons([])

    player_account_id = int(account_id)
    if allow_fetch:
        seasons = _get_player_clan_battle_season_stats(
            player_account_id, realm=realm)
    else:
        seasons = cache.get(realm_cache_key(
            realm, f'clan_battles:player:{player_account_id}')) or []

    return summarize_clan_battle_seasons(seasons)


def _persist_player_clan_battle_summary(
    account_id: int,
    summary: dict[str, Any],
    realm: str = DEFAULT_REALM,
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
    explorer_summary.clan_battle_summary_updated_at = django_timezone.now()
    explorer_summary.save(update_fields=[
        'realm',
        'clan_battle_total_battles',
        'clan_battle_seasons_participated',
        'clan_battle_overall_win_rate',
        'clan_battle_summary_updated_at',
    ])

    if payload_changed:
        from warships.landing import invalidate_landing_player_caches

        invalidate_landing_player_caches(include_recent=True)


def fetch_player_clan_battle_seasons(account_id: int, realm: str = DEFAULT_REALM) -> list:
    """Return a single player's clan battle seasons enriched with season metadata."""
    if not account_id:
        return []

    season_meta = _get_clan_battle_seasons_metadata()
    seasons = _get_player_clan_battle_season_stats(
        int(account_id), realm=realm)
    _persist_player_clan_battle_summary(
        int(account_id),
        summarize_clan_battle_seasons(seasons),
        realm=realm,
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

    with ThreadPoolExecutor(max_workers=8) as executor:
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

    logging.info(f'Fetching ranked data for {player.name}')
    update_ranked_data(player_id, realm=realm)
    player.refresh_from_db()
    return player.ranked_json or []


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
        player.save()
        return

    requested_season_ids = sorted(
        [int(season_id)
         for season_id in rank_info.keys() if str(season_id).isdigit()]
    )
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
    player.save()
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
    player.save()

    logging.info(f'Updated type data for player {player.name}')


def fetch_randoms_data(player_id: str, realm: str = DEFAULT_REALM) -> list:
    try:
        player = Player.objects.get(player_id=player_id, realm=realm)
        if not player.battles_json:
            _dispatch_async_refresh(
                update_battle_data_task, player_id=player_id, realm=realm)
            return _extract_randoms_rows(player.randoms_json, limit=20)
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
        return _extract_randoms_rows(player.randoms_json, limit=20)

    extracted_battle_rows = _extract_randoms_rows(
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
    player.randoms_json = _extract_randoms_rows(player.battles_json, limit=20)
    player.randoms_updated_at = datetime.now()
    player.save()

    logging.info(f'Updated randoms data for player {player.name}')


def update_clan_data(clan_id: str, realm: str = DEFAULT_REALM) -> None:
    from warships.landing import invalidate_landing_clan_caches

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
    clan.save()
    invalidate_landing_clan_caches()
    _invalidate_clan_battle_summary_cache(clan_id, realm=realm)
    cache.delete(realm_cache_key(realm, f'clan:members:{clan_id}'))
    invalidate_clan_detail_cache(int(clan_id), realm=realm)
    logging.info(
        f"Updated clan data: {clan.name} [{clan.tag}]: {clan.members_count} members")

    for member_id in _fetch_clan_member_ids(clan_id, realm=realm):
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
                player.save()


def refresh_clan_cached_aggregates(clan_id: str, realm: str = DEFAULT_REALM) -> None:
    from warships.landing import invalidate_landing_clan_caches
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

    invalidate_landing_clan_caches(realm=realm)
    _invalidate_clan_battle_summary_cache(clan_id, realm=realm)
    cache.delete(realm_cache_key(realm, f'clan:members:{clan_id}'))


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
                player.save()

        update_player_data(player)

    refresh_clan_cached_aggregates(clan_id, realm=realm)


def update_player_data(player: Player, force_refresh: bool = False, realm: str | None = None) -> None:
    from warships.landing import invalidate_landing_player_caches

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
        stats_updated_at = player_data.get("stats_updated_at")
        player.battles_updated_at = datetime.fromtimestamp(
            stats_updated_at, tz=timezone.utc) if stats_updated_at else None
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
    player.save()
    if not player.is_hidden:
        update_player_efficiency_data(
            player, force_refresh=force_refresh, realm=player.realm)
    refresh_player_explorer_summary(player)
    invalidate_landing_player_caches(include_recent=True)
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
    candidate_ids.extend(
        Clan.objects.filter(realm=realm).exclude(name__isnull=True).exclude(name='').annotate(
            total_wins=Sum('player__pvp_wins'),
            total_battles=Sum('player__pvp_battles'),
            clan_wr=Case(
                When(total_battles__gt=0, then=Cast(F('total_wins'), FloatField(
                )) / Cast(F('total_battles'), FloatField()) * Value(100.0)),
                default=None,
                output_field=FloatField(),
            ),
        ).filter(
            total_battles__gte=100000,
            clan_wr__isnull=False,
        ).order_by(
            F('clan_wr').desc(nulls_last=True),
            F('total_battles').desc(nulls_last=True),
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


def warm_landing_best_entity_caches(
    player_limit: int = 25,
    clan_limit: int = 25,
    force_refresh: bool = False,
    realm: str = DEFAULT_REALM,
) -> dict[str, Any]:
    from warships.landing import get_landing_best_clans_payload, get_landing_players_payload, normalize_landing_clan_limit, normalize_landing_player_best_sort, normalize_landing_player_limit

    normalized_player_limit = normalize_landing_player_limit(player_limit)
    normalized_clan_limit = min(normalize_landing_clan_limit(clan_limit), 25)
    best_player_ids: list[int] = []
    seen_player_ids: set[int] = set()
    for player_sort in ('overall', 'ranked', 'efficiency', 'wr', 'cb'):
        normalized_sort = normalize_landing_player_best_sort(player_sort)
        best_player_rows = get_landing_players_payload(
            'best',
            normalized_player_limit,
            sort=normalized_sort,
            realm=realm,
        )
        for row in best_player_rows:
            try:
                player_id = int(row.get('player_id') or 0)
            except (TypeError, ValueError):
                continue
            if player_id <= 0 or player_id in seen_player_ids:
                continue
            seen_player_ids.add(player_id)
            best_player_ids.append(player_id)
    best_clan_rows = get_landing_best_clans_payload(
        realm=realm)[:normalized_clan_limit]

    clan_ids = [
        int(row.get('clan_id') or 0)
        for row in best_clan_rows
        if row.get('clan_id') is not None
    ]

    warmed_players = warm_player_entity_caches(
        best_player_ids,
        force_refresh=force_refresh,
        realm=realm,
    )
    warmed_clans = warm_clan_entity_caches(
        clan_ids,
        force_refresh=force_refresh,
        realm=realm,
    )

    return {
        'status': 'completed',
        'realm': realm,
        'warmed': {
            'players': warmed_players,
            'clans': warmed_clans,
        },
        'candidate_counts': {
            'players': len(best_player_ids),
            'clans': len(clan_ids),
        },
    }


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


def get_cached_clan_detail(clan_id: int, realm: str = DEFAULT_REALM) -> Optional[dict]:
    return cache.get(_bulk_cache_key_clan(clan_id, realm=realm))


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


BEST_CLAN_MIN_MEMBERS = 10
BEST_CLAN_MIN_TRACKED = 5
BEST_CLAN_MIN_ACTIVE_SHARE = 0.40
BEST_CLAN_MIN_TOTAL_BATTLES = 50_000
BEST_CLAN_EXCLUDED_IDS: set[int] = {
    int(x) for x in os.environ.get('BEST_CLAN_EXCLUDED_IDS', '').split(',') if x.strip()
} if os.environ.get('BEST_CLAN_EXCLUDED_IDS') else set()

BEST_CLAN_W_WR = 0.30
BEST_CLAN_W_ACTIVITY = 0.25
BEST_CLAN_W_MEMBER_SCORE = 0.20
BEST_CLAN_W_CB_RECENCY = 0.15
BEST_CLAN_W_VOLUME = 0.10
BEST_CLAN_WR_MIN_CB_BATTLES = 10.0
BEST_CLAN_WR_CB_BATTLES_SATURATION = 200.0
BEST_CLAN_WR_ACTIVE_MEMBERS_TARGET = 25.0
BEST_CLAN_WR_MEMBER_SCORE_TARGET = 6.0
BEST_CLAN_WR_CB_LIFT_WEIGHT = 0.40
BEST_CLAN_CB_SUCCESS_BASELINE = 50.0
BEST_CLAN_CB_ACTIVE_MEMBERS_TARGET = 25.0
BEST_CLAN_CB_MEMBER_SCORE_TARGET = 5.0
BEST_CLAN_CB_WINDOW_COMPLETED_SEASONS = 10
BEST_CLAN_CB_WINDOW_SEASON_BATTLES_TARGET = 30
BEST_CLAN_CB_WINDOW_SHORTLIST_MULTIPLIER = 4
BEST_CLAN_CB_WINDOW_SHORTLIST_MAX = 120
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
BEST_CLAN_SORTS = ('overall', 'wr')


def _minmax_normalize(values: list[float]) -> list[float]:
    """Min-max normalize a list of floats to [0, 1]. Returns 0.5 for constant lists."""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        return [0.5] * len(values)
    span = hi - lo
    return [(v - lo) / span for v in values]


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


def score_best_clans(limit: int = BULK_CACHE_CLAN_MEMBER_CLANS, realm: str = DEFAULT_REALM, sort: str = 'overall') -> tuple[list[int], dict[int, dict]]:
    """Score and rank clans using the composite Best Clan eligibility criteria.

    Returns a tuple of (clan_ids, cb_metrics_by_clan) where clan_ids is a list
    of the top `limit` clan IDs sorted by composite score descending, and
    cb_metrics_by_clan maps each clan ID to its CB sub-sort fields
    (avg_cb_battles, avg_cb_wr, cb_recency_days).
    """
    normalized_sort = (sort or 'overall').strip().lower()
    if normalized_sort not in BEST_CLAN_SORTS:
        raise ValueError(f"sort must be one of: {', '.join(BEST_CLAN_SORTS)}")

    now = django_timezone.now()

    # Hard filters via ORM annotation
    candidates = list(
        Clan.objects.filter(realm=realm)
        .exclude(name__isnull=True).exclude(name='')
        .exclude(clan_id__in=BEST_CLAN_EXCLUDED_IDS)
        .filter(
            members_count__gt=BEST_CLAN_MIN_MEMBERS,
            cached_total_battles__gte=BEST_CLAN_MIN_TOTAL_BATTLES,
            cached_clan_wr__isnull=False,
            cached_active_member_count__isnull=False,
        )
        .annotate(
            tracked_count=Count(
                'player', filter=Q(player__name__gt=''),
            ),
        )
        .filter(tracked_count__gte=BEST_CLAN_MIN_TRACKED)
        .values(
            'clan_id', 'name', 'cached_clan_wr', 'cached_active_member_count',
            'members_count', 'cached_total_battles', 'tracked_count',
        )
    )

    # Activity ratio hard filter (Python-side — ratio not annotatable cleanly)
    candidates = [
        row for row in candidates
        if ((row['cached_active_member_count'] or 0) / max(row['members_count'] or 0, 1)) >= BEST_CLAN_MIN_ACTIVE_SHARE
    ]

    if not candidates:
        logging.warning(
            "score_best_clans: no clans passed hard filters for sort=%s", normalized_sort)
        return [], {}

    clan_ids = [int(row['clan_id']) for row in candidates]

    # Gather per-clan average member score and CB recency via a single query
    member_stats = list(
        PlayerExplorerSummary.objects
        .filter(player__clan__clan_id__in=clan_ids)
        .exclude(player__name='')
        .values('player__clan__clan_id')
        .annotate(
            avg_score=Avg('player_score'),
            avg_cb_battles=Avg('clan_battle_total_battles'),
            avg_cb_wr=Avg('clan_battle_overall_win_rate'),
        )
    )
    member_stats_by_clan: dict[int, dict] = {
        row['player__clan__clan_id']: row for row in member_stats
    }

    # CB recency: average clan_battle_summary_updated_at per clan
    # Done separately because Avg on DateTimeField isn't straightforward
    from django.db.models import Max
    cb_recency = dict(
        PlayerExplorerSummary.objects
        .filter(
            player__clan__clan_id__in=clan_ids,
            clan_battle_summary_updated_at__isnull=False,
        )
        .exclude(player__name='')
        .values_list('player__clan__clan_id')
        .annotate(latest_cb=Max('clan_battle_summary_updated_at'))
        .values_list('player__clan__clan_id', 'latest_cb')
    )

    # Build raw component arrays
    raw_wr = []
    raw_activity = []
    raw_member_score = []
    raw_cb = []
    raw_volume = []
    candidate_rows: list[dict[str, Any]] = []

    for row in candidates:
        clan_id = int(row['clan_id'])
        clan_name = row.get('name') or ''
        clan_wr = row.get('cached_clan_wr') or 0.0
        active_count = row.get('cached_active_member_count') or 0
        total_members = row.get('members_count') or 0
        total_battles = row.get('cached_total_battles') or 0

        raw_wr.append(clan_wr)
        raw_activity.append(active_count / max(total_members, 1))

        stats = member_stats_by_clan.get(clan_id, {})
        avg_member_score = stats.get('avg_score') or 0.0
        raw_member_score.append(avg_member_score)

        # CB recency-weighted score
        avg_cb_battles = stats.get('avg_cb_battles') or 0.0
        avg_cb_wr = stats.get('avg_cb_wr') or 0.0
        latest_cb = cb_recency.get(clan_id)
        if latest_cb and avg_cb_battles:
            years_since = max((now - latest_cb).days, 0) / 365.25
            recency_factor = 1.0 / (1.0 + years_since)
        else:
            recency_factor = 0.0
        cb_support_factor = (
            min(active_count / BEST_CLAN_CB_ACTIVE_MEMBERS_TARGET, 1.0)
            * min(avg_member_score / BEST_CLAN_CB_MEMBER_SCORE_TARGET, 1.0)
            if recency_factor > 0
            else 0.0
        )
        cb_success_margin = max(avg_cb_wr - BEST_CLAN_CB_SUCCESS_BASELINE, 0.0)
        cb_sort_score = avg_cb_battles * cb_success_margin * \
            recency_factor * cb_support_factor

        raw_cb.append(cb_sort_score)

        raw_volume.append(math.log(max(total_battles, 1)))

        candidate_rows.append({
            'clan_id': clan_id,
            'clan_name': str(clan_name).lower(),
            'clan_wr': float(clan_wr),
            'members_count': int(total_members),
            'active_members': int(active_count),
            'activity_ratio': active_count / max(total_members, 1),
            'avg_member_score': float(avg_member_score),
            'avg_cb_battles': float(avg_cb_battles),
            'avg_cb_wr': float(avg_cb_wr),
            'cb_sort_score': float(cb_sort_score),
            'cb_success_margin': float(cb_success_margin),
            'cb_support_factor': float(cb_support_factor),
            'total_battles': int(total_battles),
            'recency_factor': float(recency_factor),
            'cb_recency_days': max((now - latest_cb).days, 0) if latest_cb else None,
        })

    # Normalize and score
    n_wr = _minmax_normalize(raw_wr)
    n_activity = _minmax_normalize(raw_activity)
    n_member_score = _minmax_normalize(raw_member_score)
    n_cb = _minmax_normalize(raw_cb)
    n_volume = _minmax_normalize(raw_volume)

    # Collect per-clan CB metrics for sub-sort support
    cb_metrics_by_clan: dict[int, dict] = {}
    ranked_rows: list[dict[str, Any]] = []
    for i, candidate in enumerate(candidate_rows):
        clan_id = candidate['clan_id']
        overall_score = (
            BEST_CLAN_W_WR * n_wr[i]
            + BEST_CLAN_W_ACTIVITY * n_activity[i]
            + BEST_CLAN_W_MEMBER_SCORE * n_member_score[i]
            + BEST_CLAN_W_CB_RECENCY * n_cb[i]
            + BEST_CLAN_W_VOLUME * n_volume[i]
        )
        wr_uses_cb_sample = (
            candidate['avg_cb_battles'] >= BEST_CLAN_WR_MIN_CB_BATTLES
            and candidate['avg_cb_wr'] > 0
        )
        wr_support_factor = (
            min(candidate['avg_cb_battles'] /
                BEST_CLAN_WR_CB_BATTLES_SATURATION, 1.0)
            * min(candidate['active_members'] / BEST_CLAN_WR_ACTIVE_MEMBERS_TARGET, 1.0)
            * min(candidate['avg_member_score'] / BEST_CLAN_WR_MEMBER_SCORE_TARGET, 1.0)
            if wr_uses_cb_sample
            else 0.0
        )
        wr_cb_lift = (
            max(candidate['avg_cb_wr'] - candidate['clan_wr'], 0.0)
            * BEST_CLAN_WR_CB_LIFT_WEIGHT
            * wr_support_factor
        )
        composite_wr = candidate['clan_wr'] + wr_cb_lift
        wr_sort_avg_cb_wr = (
            candidate['avg_cb_wr'] if wr_cb_lift > 0 else 0.0
        )

        avg_cb_b = candidate['avg_cb_battles']
        avg_cb_w = candidate['avg_cb_wr']
        cb_metrics_by_clan[clan_id] = {
            'avg_cb_battles': round(avg_cb_b, 1) if avg_cb_b else None,
            'avg_cb_wr': round(avg_cb_w, 1) if avg_cb_w else None,
            'cb_recency_days': candidate['cb_recency_days'],
        }

        ranked_rows.append({
            **candidate,
            'overall_score': float(overall_score),
            'composite_wr': float(composite_wr),
            'wr_cb_lift': float(wr_cb_lift),
            'wr_support_factor': float(wr_support_factor),
            'wr_sort_avg_cb_wr': float(wr_sort_avg_cb_wr),
        })

    if normalized_sort == 'overall':
        ranked_rows.sort(key=lambda row: (
            -row['overall_score'],
            -row['composite_wr'],
            -row['clan_wr'],
            -row['cb_sort_score'],
            -row['total_battles'],
            row['clan_name'],
            row['clan_id'],
        ))
    elif normalized_sort == 'wr':
        ranked_rows.sort(key=lambda row: (
            -row['composite_wr'],
            -row['clan_wr'],
            -row['wr_cb_lift'],
            -row['avg_member_score'],
            -row['activity_ratio'],
            -row['overall_score'],
            -row['total_battles'],
            row['clan_name'],
            row['clan_id'],
        ))
    else:
        raise ValueError(f"sort must be one of: {', '.join(BEST_CLAN_SORTS)}")

    top = ranked_rows[:limit]

    if top:
        logging.info(
            "score_best_clans: top %d clans for sort=%s from %d candidates",
            len(top), normalized_sort, len(candidates),
        )

    return [int(row['clan_id']) for row in top], cb_metrics_by_clan


def bulk_load_player_cache(
    top_player_limit: int = BULK_CACHE_TOP_PLAYER_LIMIT,
    clan_member_clans: int = BULK_CACHE_CLAN_MEMBER_CLANS,
    realm: str = DEFAULT_REALM,
) -> dict[str, Any]:
    """Bulk-load player detail payloads into Redis from DB.

    Loads three cohorts into a single cache.set_many() call:
    1. Top N players by player_score (global best)
    2. All members of the top M clans by composite Best score
    3. Pinned players (always included)

    Single pass — no API calls, no Celery tasks.
    """
    from warships.serializers import PlayerSerializer

    from warships.landing import get_landing_players_payload, normalize_landing_player_best_sort

    # Cohort 1: union of shipped Best-player sub-sort cohorts
    best_player_ids: list[int] = []
    seen_best_ids: set[int] = set()
    for player_sort in ('overall', 'ranked', 'efficiency', 'wr', 'cb'):
        normalized_sort = normalize_landing_player_best_sort(player_sort)
        for row in get_landing_players_payload(
            'best',
            top_player_limit,
            sort=normalized_sort,
            realm=realm,
        ):
            try:
                player_id = int(row.get('player_id') or 0)
            except (TypeError, ValueError):
                continue
            if player_id <= 0 or player_id in seen_best_ids:
                continue
            seen_best_ids.add(player_id)
            best_player_ids.append(player_id)

    top_players = list(
        Player.objects
        .filter(realm=realm, player_id__in=best_player_ids)
        .exclude(name='')
        .filter(is_hidden=False)
        .select_related('clan', 'explorer_summary')
    )
    top_players.sort(
        key=lambda player: best_player_ids.index(player.player_id)
        if player.player_id in seen_best_ids else len(best_player_ids)
    )
    seen_ids = {p.player_id for p in top_players}

    # Cohort 2: members of the best clans (composite scoring)
    best_clan_ids, _ = score_best_clans(limit=clan_member_clans, realm=realm)
    clan_members = list(
        Player.objects
        .filter(realm=realm, clan_id__in=best_clan_ids)
        .exclude(name='')
        .exclude(player_id__in=seen_ids)
        .select_related('clan', 'explorer_summary')
    )
    top_players.extend(clan_members)
    seen_ids.update(p.player_id for p in clan_members)

    # Cohort 3: pinned players
    pinned_ids = _get_pinned_player_ids(realm=realm)
    missing_pinned = [pid for pid in pinned_ids if pid not in seen_ids]
    if missing_pinned:
        top_players.extend(
            Player.objects
            .filter(player_id__in=missing_pinned, realm=realm)
            .select_related('clan', 'explorer_summary')
        )
        seen_ids.update(missing_pinned)

    # Cohort 4: recently-viewed players
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
        "bulk_load_player_cache: loaded %d player payloads (best_union=%d, clan_members=%d, clans=%d, recently_viewed=%d)",
        len(payloads), len(best_player_ids), len(
            clan_members), len(best_clan_ids), len(missing_rv),
    )
    return {
        'status': 'completed',
        'loaded': len(payloads),
        'top_players': len(best_player_ids),
        'clan_member_clans': len(best_clan_ids),
        'clan_members_added': len(clan_members),
        'recently_viewed_added': len(missing_rv),
    }


def bulk_load_clan_cache(limit: int = BULK_CACHE_CLAN_LIMIT, realm: str = DEFAULT_REALM) -> dict[str, Any]:
    """Bulk-load top clan detail payloads into Redis from DB."""
    from warships.serializers import ClanSerializer

    best_clan_ids, _ = score_best_clans(limit=limit, realm=realm)
    clans = (
        Clan.objects
        .filter(clan_id__in=best_clan_ids, realm=realm)
    )

    payloads: dict[str, dict] = {}
    serializer = ClanSerializer()
    for clan in clans:
        try:
            data = serializer.to_representation(clan)
            key = _bulk_cache_key_clan(clan.clan_id, realm=realm)
            payloads[key] = data
        except Exception:
            logging.warning(
                "bulk_load_clan_cache: failed to serialize clan %s", clan.clan_id, exc_info=True)

    if payloads:
        cache.set_many(payloads, timeout=BULK_CACHE_CLAN_TTL)

    logging.info("bulk_load_clan_cache: loaded %d clan detail payloads into cache (limit=%d)", len(
        payloads), limit)
    return {
        'status': 'completed',
        'loaded': len(payloads),
        'limit': limit,
    }


def bulk_load_entity_caches(
    top_player_limit: int = BULK_CACHE_TOP_PLAYER_LIMIT,
    clan_member_clans: int = BULK_CACHE_CLAN_MEMBER_CLANS,
    clan_limit: int = BULK_CACHE_CLAN_LIMIT,
    realm: str = DEFAULT_REALM,
) -> dict[str, Any]:
    """Bulk-load player and clan detail payloads into Redis. DB reads only, no tasks."""
    player_result = bulk_load_player_cache(
        top_player_limit, clan_member_clans, realm=realm)
    clan_result = bulk_load_clan_cache(clan_limit, realm=realm)
    return {
        'status': 'completed',
        'players': player_result,
        'clans': clan_result,
    }


