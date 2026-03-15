from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, Optional, Iterable
from datetime import datetime, timezone, timedelta, date
import logging
import math
import os
from django.core.cache import cache
from django.db.models import Count, Q
from django.db.models.functions import TruncMonth
from django.utils import timezone as django_timezone
from warships.models import Player, Snapshot, Clan, PlayerExplorerSummary, Ship
from warships.api.ships import _fetch_ship_stats_for_player, _fetch_ship_info, _fetch_ranked_ship_stats_for_player, _fetch_efficiency_badges_for_player, build_ship_chart_name
from warships.api.players import _fetch_snapshot_data, _fetch_player_personal_data, _fetch_ranked_account_info
from warships.api.clans import _fetch_clan_data, _fetch_clan_member_ids, _fetch_clan_membership_for_player, \
    _fetch_clan_battle_seasons_info, _fetch_clan_battle_season_stats
from warships.tasks import update_tiers_data_task, update_type_data_task

logging.basicConfig(level=logging.INFO)


PLAYSTYLE_RECRUIT_BATTLES_THRESHOLD = 100
PLAYSTYLE_SUPER_UNICUM_WR_THRESHOLD = 65.0
PLAYSTYLE_UNICUM_WR_THRESHOLD = 60.0
PLAYSTYLE_GREAT_WR_THRESHOLD = 56.0
PLAYSTYLE_GOOD_WR_THRESHOLD = 54.0
PLAYSTYLE_ABOVE_AVERAGE_WR_THRESHOLD = 52.0
PLAYSTYLE_AVERAGE_WR_THRESHOLD = 50.0
PLAYSTYLE_BELOW_AVERAGE_WR_THRESHOLD = 45.0
PLAYSTYLE_LOW_SURVIVABILITY_THRESHOLD = 33.0
KILL_RATIO_LOW_TIER_WEIGHT = 0.15
KILL_RATIO_MID_TIER_WEIGHT = 0.65
KILL_RATIO_HIGH_TIER_WEIGHT = 1.0
KILL_RATIO_SMOOTHING_BATTLES = 12.0
KILL_RATIO_PRIOR = 0.7
PLAYER_SCORE_WR_WEIGHT = 0.36
PLAYER_SCORE_KDR_WEIGHT = 0.24
PLAYER_SCORE_SURVIVAL_WEIGHT = 0.14
PLAYER_SCORE_BATTLES_WEIGHT = 0.10
PLAYER_SCORE_ACTIVITY_WEIGHT = 0.16
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
CLAN_RANKED_HYDRATION_STALE_AFTER = timedelta(hours=24)
PLAYER_EFFICIENCY_STALE_AFTER = timedelta(hours=24)
EFFICIENCY_BADGE_CLASS_LABELS = {
    1: 'Expert',
    2: 'Grade I',
    3: 'Grade II',
    4: 'Grade III',
}
CLAN_RANKED_HYDRATION_MAX_IN_FLIGHT = max(
    1, int(os.getenv('CLAN_RANKED_HYDRATION_MAX_IN_FLIGHT', '8')))


def compute_player_verdict(pvp_battles: int, pvp_ratio: Optional[float], pvp_survival_rate: Optional[float]) -> Optional[str]:
    if pvp_battles < PLAYSTYLE_RECRUIT_BATTLES_THRESHOLD:
        return 'Recruit'

    if pvp_ratio is None:
        return None

    if pvp_ratio > PLAYSTYLE_SUPER_UNICUM_WR_THRESHOLD:
        return 'Sealord'

    if pvp_survival_rate is None:
        return None

    is_low_survivability = pvp_survival_rate < PLAYSTYLE_LOW_SURVIVABILITY_THRESHOLD

    if pvp_ratio >= PLAYSTYLE_UNICUM_WR_THRESHOLD:
        return 'Kraken' if is_low_survivability else 'Assassin'

    if pvp_ratio >= PLAYSTYLE_GREAT_WR_THRESHOLD:
        return 'Daredevil' if is_low_survivability else 'Stalwart'

    if pvp_ratio >= PLAYSTYLE_GOOD_WR_THRESHOLD:
        return 'Raider' if is_low_survivability else 'Warrior'

    if pvp_ratio >= PLAYSTYLE_ABOVE_AVERAGE_WR_THRESHOLD:
        return 'Jetsam' if is_low_survivability else 'Survivor'

    if pvp_ratio >= PLAYSTYLE_AVERAGE_WR_THRESHOLD:
        return 'Drifter' if is_low_survivability else 'Flotsam'

    if pvp_ratio >= PLAYSTYLE_BELOW_AVERAGE_WR_THRESHOLD:
        return 'Potato' if is_low_survivability else 'Pirate'

    return 'Leroy Jenkins' if is_low_survivability else 'Hot Potato'


def _coerce_activity_rows(activity_rows: Any) -> list[dict]:
    if not isinstance(activity_rows, list):
        return []

    rows = []
    for row in activity_rows:
        if not isinstance(row, dict):
            continue

        rows.append({
            'date': row.get('date'),
            'battles': int(row.get('battles', 0) or 0),
            'wins': int(row.get('wins', 0) or 0),
        })

    return rows


def _coerce_ranked_rows(ranked_rows: Any) -> list[dict]:
    if not isinstance(ranked_rows, list):
        return []

    rows = [row for row in ranked_rows if isinstance(row, dict)]
    return sorted(rows, key=lambda row: int(row.get('season_id', 0) or 0), reverse=True)


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

    if player.efficiency_json is None:
        return True

    updated_at = player.efficiency_updated_at
    if updated_at is None:
        return True

    return django_timezone.now() - updated_at >= stale_after


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


def update_player_efficiency_data(player: Player, force_refresh: bool = False) -> list[dict[str, Any]]:
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
        _fetch_efficiency_badges_for_player(player.player_id)
    )
    player.efficiency_json = rows
    player.efficiency_updated_at = django_timezone.now()
    player.save(update_fields=['efficiency_json', 'efficiency_updated_at'])
    return rows


def queue_clan_ranked_hydration(players: Iterable[Player]) -> dict[str, Any]:
    from warships.tasks import is_ranked_data_refresh_pending, queue_ranked_data_refresh

    eligible_players = [
        player for player in players if clan_ranked_hydration_needs_refresh(player)
    ]
    pending_player_ids: set[int] = set()
    queued_player_ids: set[int] = set()
    deferred_player_ids: set[int] = set()

    for player in eligible_players:
        if is_ranked_data_refresh_pending(player.player_id):
            pending_player_ids.add(player.player_id)

    available_slots = max(
        0, CLAN_RANKED_HYDRATION_MAX_IN_FLIGHT - len(pending_player_ids))

    for player in eligible_players:
        if player.player_id in pending_player_ids:
            continue

        if available_slots <= 0:
            deferred_player_ids.add(player.player_id)
            continue

        enqueue_result = queue_ranked_data_refresh(player.player_id)
        if enqueue_result.get("status") == "queued":
            pending_player_ids.add(player.player_id)
            queued_player_ids.add(player.player_id)
            available_slots -= 1

    pending_player_ids.update(deferred_player_ids)

    return {
        'pending_player_ids': pending_player_ids,
        'queued_player_ids': queued_player_ids,
        'deferred_player_ids': deferred_player_ids,
        'eligible_player_ids': {player.player_id for player in eligible_players},
        'max_in_flight': CLAN_RANKED_HYDRATION_MAX_IN_FLIGHT,
    }


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


def _coerce_battle_rows(battles_rows: Any) -> list[dict]:
    if not isinstance(battles_rows, list):
        return []

    return [row for row in battles_rows if isinstance(row, dict)]


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


def is_pve_player(total_battles: Optional[int], pvp_battles: Optional[int]) -> bool:
    total = max(int(total_battles or 0), 0)
    pvp = max(int(pvp_battles or 0), 0)
    pve = calculate_pve_battle_count(total, pvp)
    return total > 500 and (pve > (0.75 * pvp) or pve >= 4000)


def is_ranked_player(ranked_rows: Any, minimum_ranked_battles: int = 100) -> bool:
    total_battles, _win_rate = _calculate_ranked_record(ranked_rows)
    return total_battles > minimum_ranked_battles


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
    activity_rows: Any,
    days_since_last_battle: Optional[int],
    battle_rows: list[dict],
) -> Optional[float]:
    component_values = [
        (PLAYER_SCORE_WR_WEIGHT, _normalize_wr_score(pvp_ratio)),
        (PLAYER_SCORE_KDR_WEIGHT, _normalize_kdr_score(kill_ratio)),
        (PLAYER_SCORE_SURVIVAL_WEIGHT, _normalize_survival_score(pvp_survival_rate)),
        (PLAYER_SCORE_BATTLES_WEIGHT, _normalize_battle_volume_score(
            total_battles, battle_rows)),
        (PLAYER_SCORE_ACTIVITY_WEIGHT, _calculate_recent_activity_score(
            activity_rows, days_since_last_battle)),
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
    score = round(
        score * _calculate_competitive_tier_factor(battle_rows, total_battles), 2)

    inactivity_cap = _inactivity_score_cap(days_since_last_battle)
    if inactivity_cap is not None:
        score = min(score, inactivity_cap)

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
        activity_rows=player.activity_json,
        days_since_last_battle=player.days_since_last_battle,
        battle_rows=battle_rows,
    )

    if explorer_summary.ships_played_total != expected_ships_played_total:
        return True
    if explorer_summary.kill_ratio != expected_kill_ratio:
        return True
    if explorer_summary.player_score != expected_player_score:
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
        'ranked_seasons_participated': None,
        'latest_ranked_battles': None,
        'highest_ranked_league_recent': None,
    }
    normalized_battles_rows = _coerce_battle_rows(
        battles_rows if battles_rows is not None else player.battles_json
    )
    has_battle_data = _summary_has_battle_data(player, normalized_battles_rows)
    summary['kill_ratio'] = _calculate_player_kill_ratio(
        normalized_battles_rows) if has_battle_data else None

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
            'ranked_seasons_participated': explorer_summary.ranked_seasons_participated,
            'latest_ranked_battles': explorer_summary.latest_ranked_battles,
            'highest_ranked_league_recent': explorer_summary.highest_ranked_league_recent,
        })
        return summary

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
            activity_rows=normalized_activity_rows,
            days_since_last_battle=player.days_since_last_battle,
            battle_rows=normalized_battles_rows,
        ),
        'ships_played_total': len(played_rows) if has_battle_data else None,
        'ship_type_spread': ship_type_spread if has_battle_data else None,
        'tier_spread': tier_spread if has_battle_data else None,
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
            'battles_last_29_days': summary['battles_last_29_days'],
            'wins_last_29_days': summary['wins_last_29_days'],
            'active_days_last_29_days': summary['active_days_last_29_days'],
            'recent_win_rate': summary['recent_win_rate'],
            'activity_trend_direction': summary['activity_trend_direction'],
            'player_score': summary['player_score'],
            'ships_played_total': summary['ships_played_total'],
            'ship_type_spread': summary['ship_type_spread'],
            'tier_spread': summary['tier_spread'],
            'ranked_seasons_participated': summary['ranked_seasons_participated'],
            'latest_ranked_battles': summary['latest_ranked_battles'],
            'highest_ranked_league_recent': summary['highest_ranked_league_recent'],
            'kill_ratio': summary['kill_ratio'],
        },
    )

    player.explorer_summary = explorer_summary
    return explorer_summary


def fetch_player_summary(player_id: str) -> dict:
    player = Player.objects.get(player_id=player_id)
    activity_rows = fetch_activity_data(player_id)
    ranked_rows = fetch_ranked_data(player_id)

    if not player.is_hidden and not player.battles_json:
        update_battle_data(player_id)

    player.refresh_from_db()
    refresh_player_explorer_summary(
        player,
        activity_rows=activity_rows,
        ranked_rows=ranked_rows,
        battles_rows=player.battles_json,
    )
    return build_player_summary(player)


def fetch_player_explorer_rows(
    query: str = '',
    hidden: str = 'all',
    activity_bucket: str = 'all',
    ranked: str = 'all',
    min_pvp_battles: int = 0,
) -> list[dict]:
    players = Player.objects.exclude(
        name='').select_related('explorer_summary').all()

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

    rows = []
    for player in players:
        if _explorer_summary_needs_refresh(player):
            refresh_player_explorer_summary(player)
        rows.append(build_player_summary(player))

    if ranked == 'yes':
        rows = [row for row in rows if (
            row.get('ranked_seasons_participated') or 0) > 0]
    elif ranked == 'no':
        rows = [row for row in rows if (
            row.get('ranked_seasons_participated') or 0) == 0]

    return rows


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


def update_battle_data(player_id: str) -> None:
    """
    Updates the battle data for a given player.

    This function fetches the latest battle data for a player from an external API if the cached data is older than 15 minutes.
    The fetched data is then processed and saved back to the player's record in the database.

    Args:
        player_id (str): The ID of the player whose battle data needs to be updated.

    Returns:
        None
    """
    player = Player.objects.get(player_id=player_id)

    # Check if the cached data is less than 15 minutes old
    if player.battles_json and player.battles_updated_at and datetime.now() - player.battles_updated_at < timedelta(minutes=15):
        logging.debug(
            f'Cache exists and is fresh: returning cached data')
        return player.battles_json

    logging.info(
        f'Battles data empty or outdated: fetching new data for {player.name}')

    # Fetch ship stats for the player
    ship_data = _fetch_ship_stats_for_player(player_id)
    if not ship_data:
        logging.warning(
            f'No ship stats returned for player_id={player_id}; leaving battles_json unchanged.'
        )
        return player.battles_json

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
    refresh_player_explorer_summary(player, battles_rows=sorted_data)
    logging.info(f"Updated battles_json data: {player.name}")


def fetch_tier_data(player_id: str) -> list:
    """
    Fetches and processes tier data for a given player. Tier data is a subset of battle data.

    This function updates the battle data for a player and then processes it to calculate the number of battles,
    wins, and win ratio for each ship tier. The processed data is saved back to the player's record in the database.

    Args:
        player_id (str): The ID of the player whose tier data needs to be fetched.

    Returns:
        str: A JSON response containing the processed tier data.
    """
    try:
        player = Player.objects.get(player_id=player_id)
        if not player.battles_json:
            update_battle_data(player_id)
    except Player.DoesNotExist:
        return []

    player = Player.objects.get(player_id=player_id)

    if player.tiers_json:
        if not player.tiers_updated_at or datetime.now() - player.tiers_updated_at > timedelta(days=1):
            update_tiers_data_task.delay(player_id)
        return player.tiers_json
    else:
        update_tiers_data(player_id)
        player = Player.objects.get(player_id=player_id)
        return player.tiers_json


def update_tiers_data(player_id: str) -> list:
    player = Player.objects.get(player_id=player_id)
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


def update_snapshot_data(player_id: int) -> None:
    """
    Records today's cumulative PvP stats as a Snapshot and computes
    daily interval_battles / interval_wins from successive snapshots.

    The WoWS account/statsbydate endpoint no longer returns pvp data,
    so we use the Player model's pvp_battles / pvp_wins (kept current
    by update_player_data via account/info) as today's cumulative values.
    """
    player = Player.objects.get(player_id=player_id)
    player.last_lookup = datetime.now()
    player.save()

    # Ensure the player model has fresh stats
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

        snap.save(update_fields=['interval_battles', 'interval_wins'])
        previous_battles = snap.battles
        previous_wins = snap.wins

    logging.info(f'Updated snapshot data for player {player.name}')


def fetch_activity_data(player_id: str) -> list:
    player = Player.objects.get(player_id=player_id)

    def _is_empty_activity(activity_rows: Any) -> bool:
        if not isinstance(activity_rows, list) or not activity_rows:
            return True
        return all((row.get('battles', 0) or 0) == 0 for row in activity_rows)

    def _looks_like_cumulative_spike(activity_rows: Any) -> bool:
        if not isinstance(activity_rows, list) or not activity_rows:
            return False
        non_zero_days = [row for row in activity_rows if (
            row.get('battles', 0) or 0) > 0]
        total_battles = sum((row.get('battles', 0) or 0)
                            for row in activity_rows)
        return len(non_zero_days) == 1 and total_battles > 1000

    if player.activity_json:
        logging.info(f'Activity data exists for player {player.name}')
        is_stale = not player.activity_updated_at or datetime.now(
        ) - player.activity_updated_at > timedelta(minutes=15)
        is_empty = _is_empty_activity(player.activity_json)
        is_cumulative_spike = _looks_like_cumulative_spike(
            player.activity_json)

        if is_stale or is_empty or is_cumulative_spike:
            logging.info(
                f'Activity data refresh required (stale={is_stale}, empty={is_empty}, cumulative_spike={is_cumulative_spike}) for {player.name} : {player.player_id}')
            update_snapshot_data(player.player_id)
            update_activity_data(player.player_id)
            player.refresh_from_db()
        else:
            logging.info(
                f'Activity fetch datetime is fresh: returning cached data for player {player.name}')
        return player.activity_json
    else:
        update_snapshot_data(player_id)
        update_activity_data(player_id)
        player = Player.objects.get(player_id=player_id)
        return player.activity_json


def update_activity_data(player_id: int) -> None:
    player = Player.objects.get(player_id=player_id)
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

PLAYER_DISTRIBUTION_CACHE_TTL = 3600  # 1 hour
PLAYER_CORRELATION_CACHE_TTL = 3600  # 1 hour
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
}

PLAYER_WR_SURVIVAL_CORRELATION_CONFIG = {
    'label': 'Win Rate vs Survival',
    'x_label': 'Win Rate',
    'y_label': 'Survival Rate',
    'min_population_battles': 100,
    'x_min': 35.0,
    'x_max': 75.0,
    'x_bin_width': 1.0,
    'y_min': 15.0,
    'y_max': 75.0,
    'y_bin_width': 1.5,
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


def fetch_landing_activity_attrition() -> dict:
    cache_key = 'landing:activity_attrition:v1'
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


def _player_distribution_cache_key(metric: str) -> str:
    return f'players:distribution:v2:{metric}'


def _player_correlation_cache_key(metric: str) -> str:
    return f'players:correlation:v2:{metric}'


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
    bins: list[dict] = []
    current = value_min

    while current < value_max:
        upper = round(current + bin_width, 6)
        count = qs.filter(**{
            f'{field_name}__gte': current,
            f'{field_name}__lt': upper,
        }).count()
        bins.append({
            'bin_min': round(current, 4),
            'bin_max': round(upper, 4),
            'count': count,
        })
        current = upper

    return bins


def _build_explicit_distribution_bins(qs, field_name: str, bin_edges: list[int]) -> list[dict]:
    bins: list[dict] = []

    for index, lower in enumerate(bin_edges[:-1]):
        upper = bin_edges[index + 1]
        filters = {
            f'{field_name}__gte': lower,
            f'{field_name}__lt': upper,
        }

        if index == len(bin_edges) - 2:
            filters = {
                f'{field_name}__gte': lower,
                f'{field_name}__lte': upper,
            }

        bins.append({
            'bin_min': lower,
            'bin_max': upper,
            'count': qs.filter(**filters).count(),
        })

    return bins


def fetch_player_population_distribution(metric: str) -> dict:
    config = PLAYER_DISTRIBUTION_CONFIGS.get(metric)
    if config is None:
        raise ValueError(f'Unsupported player distribution metric: {metric}')

    cache_key = _player_distribution_cache_key(metric)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    field_name = config['field_name']
    qs = Player.objects.filter(
        is_hidden=False,
        pvp_battles__gte=config['min_population_battles'],
        **{f'{field_name}__isnull': False},
    )

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
        'tracked_population': qs.count(),
        'bins': bins,
    }

    cache.set(cache_key, payload, PLAYER_DISTRIBUTION_CACHE_TTL)
    return payload


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

        normalized_rows.append({
            'ship_type': ship_type.strip(),
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


def _fetch_player_tier_type_population_correlation() -> dict:
    cache_key = _player_correlation_cache_key('tier_type_population')
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    config = PLAYER_TIER_TYPE_CORRELATION_CONFIG
    tile_counts: dict[tuple[str, int], int] = {}
    trend_tier_weighted_sum: dict[str, float] = {}
    trend_battles: dict[str, int] = {}
    tracked_population = 0

    rows = Player.objects.filter(
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

            tile_counts[(ship_type, ship_tier)] = tile_counts.get(
                (ship_type, ship_tier), 0) + pvp_battles
            trend_tier_weighted_sum[ship_type] = trend_tier_weighted_sum.get(
                ship_type, 0.0) + (ship_tier * pvp_battles)
            trend_battles[ship_type] = trend_battles.get(
                ship_type, 0) + pvp_battles

    tiles = [
        {
            'ship_type': ship_type,
            'ship_tier': ship_tier,
            'count': count,
        }
        for (ship_type, ship_tier), count in sorted(
            tile_counts.items(),
            key=lambda item: _tier_type_sort_key(item[0][0], item[0][1]),
        )
    ]

    trend = [
        {
            'ship_type': ship_type,
            'avg_tier': round(trend_tier_weighted_sum[ship_type] / total_battles, 4),
            'count': total_battles,
        }
        for ship_type, total_battles in sorted(
            trend_battles.items(),
            key=lambda item: _tier_type_sort_key(item[0]),
        )
        if total_battles > 0
    ]

    payload = {
        'metric': 'tier_type',
        'label': config['label'],
        'x_label': config['x_label'],
        'y_label': config['y_label'],
        'tracked_population': tracked_population,
        'tiles': tiles,
        'trend': trend,
    }
    cache.set(cache_key, payload, PLAYER_CORRELATION_CACHE_TTL)
    return payload


def fetch_player_tier_type_correlation(player_id: str) -> dict:
    player = Player.objects.get(player_id=player_id)
    if not player.battles_json:
        update_battle_data(player_id)
        player.refresh_from_db(fields=['battles_json'])

    population_payload = _fetch_player_tier_type_population_correlation()
    return {
        **population_payload,
        'player_cells': _build_tier_type_player_cells(player.battles_json),
    }


def fetch_player_wr_survival_correlation() -> dict:
    cache_key = _player_correlation_cache_key('win_rate_survival')
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

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

    rows = Player.objects.filter(
        is_hidden=False,
        pvp_battles__gte=config['min_population_battles'],
        pvp_ratio__isnull=False,
        pvp_survival_rate__isnull=False,
    ).values_list('pvp_ratio', 'pvp_survival_rate')

    for win_rate, survival_rate in rows.iterator(chunk_size=5000):
        if win_rate is None or survival_rate is None:
            continue

        x_value = float(win_rate)
        y_value = float(survival_rate)

        tracked_population += 1
        sum_x += x_value
        sum_y += y_value
        sum_xy += x_value * y_value
        sum_x2 += x_value * x_value
        sum_y2 += y_value * y_value

        x_clamped = _clamp_to_open_upper_bound(x_value, x_min, x_max)
        y_clamped = _clamp_to_open_upper_bound(y_value, y_min, y_max)

        x_index = min(int((x_clamped - x_min) / x_bin_width), x_bin_count - 1)
        y_index = min(int((y_clamped - y_min) / y_bin_width), y_bin_count - 1)

        tile_counts[(x_index, y_index)] = tile_counts.get(
            (x_index, y_index), 0) + 1
        trend_sum_y[x_index] += y_value
        trend_counts[x_index] += 1

    tiles = []
    for (x_index, y_index), count in sorted(tile_counts.items()):
        tiles.append({
            'x_min': round(x_min + (x_index * x_bin_width), 4),
            'x_max': round(x_min + ((x_index + 1) * x_bin_width), 4),
            'y_min': round(y_min + (y_index * y_bin_width), 4),
            'y_max': round(y_min + ((y_index + 1) * y_bin_width), 4),
            'count': count,
        })

    trend = []
    for index, count in enumerate(trend_counts):
        if count == 0:
            continue

        trend.append({
            'x': round(x_min + (index * x_bin_width) + (x_bin_width / 2), 4),
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
    return payload


def _fetch_player_ranked_wr_battles_population_correlation() -> dict:
    cache_key = _player_correlation_cache_key(
        PLAYER_RANKED_WR_BATTLES_CORRELATION_CACHE_VERSION)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    config = PLAYER_RANKED_WR_BATTLES_CORRELATION_CONFIG
    y_min = config['y_min']
    y_max = config['y_max']
    y_bin_width = config['y_bin_width']
    y_bin_count = int((y_max - y_min) / y_bin_width)

    records: list[tuple[int, float]] = []
    max_battles = config['min_battles']
    rows = Player.objects.filter(
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
            'x_min': float(x_edges[x_index]),
            'x_max': float(x_edges[x_index + 1]),
            'y_min': round(y_min + (y_index * y_bin_width), 4),
            'y_max': round(y_min + ((y_index + 1) * y_bin_width), 4),
            'count': count,
        })

    trend = []
    for index, count in enumerate(trend_counts):
        if count == 0:
            continue

        trend.append({
            'x': round(math.sqrt(x_edges[index] * x_edges[index + 1]), 4),
            'y': round(trend_sum_y[index] / count, 4),
            'count': count,
        })

    payload = {
        'metric': 'ranked_wr_battles',
        'label': config['label'],
        'x_label': config['x_label'],
        'y_label': config['y_label'],
        'x_scale': config['x_scale'],
        'y_scale': config['y_scale'],
        'x_ticks': major_x_ticks,
        'tracked_population': tracked_population,
        'correlation': round(_pearson_correlation(tracked_population, sum_x, sum_y, sum_xy, sum_x2, sum_y2), 4) if tracked_population > 1 else None,
        'x_domain': {
            'min': float(x_edges[0]),
            'max': float(x_edges[-1]),
            'bin_width': None,
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
    return payload


def fetch_player_ranked_wr_battles_correlation(player_id: str) -> dict:
    player = Player.objects.get(player_id=player_id)
    ranked_rows = fetch_ranked_data(player_id)
    total_battles, win_rate = _calculate_ranked_record(ranked_rows)
    population_payload = _fetch_player_ranked_wr_battles_population_correlation()

    return {
        **population_payload,
        'player_point': {
            'x': float(total_battles),
            'y': win_rate,
            'label': player.name,
        } if total_battles > 0 and win_rate is not None else None,
    }


def fetch_wr_distribution() -> list[dict]:
    """Return a histogram of player WR distribution, cached for 1 hour."""
    payload = fetch_player_population_distribution('win_rate')
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


def _get_clan_battle_summary_cache_key(clan_id: str) -> str:
    return f'clan_battles:summary:v2:{clan_id}'


def has_clan_battle_summary_cache(clan_id: str) -> bool:
    return cache.get(_get_clan_battle_summary_cache_key(clan_id)) is not None


def _invalidate_clan_battle_summary_cache(clan_id: str) -> None:
    cache.delete(_get_clan_battle_summary_cache_key(clan_id))


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


def _get_player_clan_battle_season_stats(account_id: int) -> list:
    """Return cached clan battle season stats for a player."""
    cache_key = f'clan_battles:player:{account_id}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    raw = _fetch_clan_battle_season_stats(account_id)
    seasons = raw.get('seasons', []) if raw else []
    cache.set(cache_key, seasons, CLAN_BATTLE_PLAYER_STATS_CACHE_TTL)
    return seasons


def fetch_clan_battle_seasons(clan_id: str) -> list:
    """Return cached clan battle summary, enqueueing background refresh on misses."""
    if not clan_id:
        return []

    cache_key = _get_clan_battle_summary_cache_key(clan_id)
    cached = cache.get(cache_key)
    if cached is not None:
        if cached:
            return cached

        try:
            clan = Clan.objects.get(clan_id=clan_id)
        except Clan.DoesNotExist:
            return []

        has_populated_roster = clan.members_count > 0 and clan.player_set.exclude(
            name='').exclude(player_id__isnull=True).exists()
        if has_populated_roster:
            return refresh_clan_battle_seasons_cache(clan_id)

        return cached

    from warships.tasks import update_clan_battle_summary_task

    update_clan_battle_summary_task.delay(clan_id=clan_id)
    return []


def refresh_clan_battle_seasons_cache(clan_id: str) -> list:
    """Aggregate clan battle season stats across the clan's current roster and cache them."""
    if not clan_id:
        return []

    cache_key = _get_clan_battle_summary_cache_key(clan_id)

    try:
        clan = Clan.objects.get(clan_id=clan_id)
    except Clan.DoesNotExist:
        return []

    members = list(
        clan.player_set.exclude(name='').exclude(
            player_id__isnull=True).values('player_id', 'name')
    )

    if not members and clan.members_count:
        update_clan_members(clan_id=clan_id)
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
            executor.submit(_get_player_clan_battle_season_stats, member['player_id']): member
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
                })

                summary['participants'] += 1
                summary['roster_battles'] += battles
                summary['roster_wins'] += int(season.get('wins', 0) or 0)
                summary['roster_losses'] += int(season.get('losses', 0) or 0)

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


def fetch_ranked_data(player_id: str) -> list:
    """Fetch ranked battles data for a player. Caches as ranked_json."""
    try:
        player = Player.objects.get(player_id=player_id)
    except Player.DoesNotExist:
        return []

    # Return cached if fresh
    if player.ranked_json and player.ranked_updated_at and \
            datetime.now() - player.ranked_updated_at < timedelta(hours=1):
        logging.info(f'Ranked data cache fresh for {player.name}')
        return player.ranked_json

    logging.info(f'Fetching ranked data for {player.name}')
    update_ranked_data(player_id)
    player.refresh_from_db()
    return player.ranked_json or []


def update_ranked_data(player_id) -> None:
    """Fetch ranked data from WG API, aggregate, and cache on Player model."""
    player = Player.objects.get(player_id=player_id)

    # Get season metadata (cached globally)
    season_meta = _get_ranked_seasons_metadata()

    # Get player's rank_info
    account_data = _fetch_ranked_account_info(int(player_id))
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
        int(player_id), season_ids=requested_season_ids)
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


def fetch_type_data(player_id: str) -> list:
    try:
        player = Player.objects.get(player_id=player_id)
        if not player.battles_json:
            update_battle_data(player_id)
    except Player.DoesNotExist:
        return []

    if player.type_json:
        if not player.type_updated_at or datetime.now() - player.type_updated_at > timedelta(days=1):
            update_type_data_task.delay(player_id)
        return player.type_json
    else:
        update_type_data(player_id)
        player = Player.objects.get(player_id=player_id)
        return player.type_json


def update_type_data(player_id: str) -> list:
    player = Player.objects.get(player_id=player_id)
    player.type_json = _aggregate_battles_by_key(
        player.battles_json, 'ship_type')
    player.type_updated_at = datetime.now()
    player.save()

    logging.info(f'Updated type data for player {player.name}')


def fetch_randoms_data(player_id: str) -> list:
    try:
        player = Player.objects.get(player_id=player_id)
        if not player.battles_json:
            update_battle_data(player_id)
    except Player.DoesNotExist:
        return []

    if player.randoms_json:
        has_required_fields = isinstance(player.randoms_json, list) and all(
            isinstance(row, dict) and 'ship_type' in row and 'ship_tier' in row
            for row in player.randoms_json
        )

        if not has_required_fields:
            update_randoms_data(player_id)
            player = Player.objects.get(player_id=player_id)
            return _extract_randoms_rows(player.randoms_json, limit=20)

        randoms_stale = not player.randoms_updated_at or datetime.now(
        ) - player.randoms_updated_at > timedelta(days=1)
        battles_stale = not player.battles_updated_at or datetime.now(
        ) - player.battles_updated_at > timedelta(minutes=15)
        if randoms_stale or battles_stale:
            update_battle_data(player_id)
            update_randoms_data(player_id)
            player = Player.objects.get(player_id=player_id)
        return _extract_randoms_rows(player.randoms_json, limit=20)
    else:
        update_randoms_data(player_id)
        player = Player.objects.get(player_id=player_id)
        return _extract_randoms_rows(player.randoms_json, limit=20)


def fetch_clan_plot_data(clan_id: str, filter_type: str = 'active') -> list:
    try:
        clan = Clan.objects.get(clan_id=clan_id)
    except Clan.DoesNotExist:
        return []

    if not clan.members_count:
        update_clan_data(clan_id)
        clan.refresh_from_db()

    members = clan.player_set.exclude(name='').all()

    if not members.exists() or (clan.members_count and members.count() < clan.members_count):
        update_clan_members(clan_id)
        members = clan.player_set.exclude(name='').all()

    data = []
    for member in members:
        battles = member.pvp_battles or 0
        if filter_type != 'all' and battles < 100:
            continue

        data.append({
            'player_name': member.name,
            'pvp_battles': battles,
            'pvp_ratio': member.pvp_ratio or 0
        })

    return sorted(data, key=lambda row: row.get('pvp_battles', 0), reverse=True)


def update_randoms_data(player_id: str) -> None:
    player = Player.objects.get(player_id=player_id)
    player.randoms_json = _extract_randoms_rows(player.battles_json, limit=20)
    player.randoms_updated_at = datetime.now()
    player.save()

    logging.info(f'Updated randoms data for player {player.name}')


def update_clan_data(clan_id: str) -> None:

    # return if no clan_id is provided
    if not clan_id:
        return

    try:
        clan = Clan.objects.get(clan_id=clan_id)
    except Clan.DoesNotExist:
        logging.info(
            f"Clan {clan_id} not found\n")
        return

    if clan.last_fetch and datetime.now() - clan.last_fetch < timedelta(minutes=1440):
        logging.debug(
            f'{clan.name}: Clan data is fresh')
        return

    data = _fetch_clan_data(clan_id)
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
    cache.delete('landing:clans')
    _invalidate_clan_battle_summary_cache(clan_id)
    logging.info(
        f"Updated clan data: {clan.name} [{clan.tag}]: {clan.members_count} members")

    for member_id in _fetch_clan_member_ids(clan_id):
        player, created = Player.objects.get_or_create(player_id=member_id)
        if created:
            player.player_id = member_id
            player.save()
            logging.info(
                f"Created new player: {player.player_id}\nPopulating data...")
            update_player_data(player)
        else:
            if player.clan != clan:
                player.clan = clan
                player.save()


def update_clan_members(clan_id: str) -> None:
    clan = Clan.objects.get(clan_id=clan_id)
    member_ids = _fetch_clan_member_ids(clan_id)

    if not member_ids and clan.members_count:
        logging.warning(
            "Skipping clan member refresh because upstream returned no member ids for clan_id=%s",
            clan_id,
        )
        return

    for member_id in member_ids:
        player, created = Player.objects.get_or_create(player_id=member_id)
        if created:
            player.player_id = member_id
            player.save()
            logging.info(
                f"Created new player: {player.player_id}")
            update_player_data(player)
            update_battle_data(player.player_id)

        else:
            if player.clan != clan:
                player.clan = clan
                player.save()

        update_player_data(player)

    cache.delete('landing:clans')
    _invalidate_clan_battle_summary_cache(clan_id)


def update_player_data(player: Player, force_refresh: bool = False) -> None:
    if not force_refresh and player.last_fetch and datetime.now() - player.last_fetch < timedelta(minutes=1400):
        logging.debug(
            f'Player data is fresh')
        return

    player_data = _fetch_player_personal_data(player.player_id)
    if not player_data:
        logging.warning(
            "Skipping player update because upstream returned no data for player_id=%s",
            player.player_id,
        )
        return

    # Map basic fields
    player.name = player_data.get("nickname", "")
    player.player_id = player_data.get("account_id", player.player_id)

    clan_membership = _fetch_clan_membership_for_player(player.player_id)
    clan_id = clan_membership.get("clan_id") or player_data.get("clan_id")
    if clan_id:
        clan, _ = Clan.objects.get_or_create(clan_id=clan_id)
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
        player.pvp_ratio = None
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
        update_player_efficiency_data(player, force_refresh=force_refresh)
    refresh_player_explorer_summary(player)
    cache.delete('landing:players')
    logging.info(f"Updated player personal data: {player.name}")


def preload_battles_json() -> None:
    logging.info("Preloading battles_json data for all players")
    players = Player.objects.all()
    for player in players:
        if not player.battles_json:
            update_battle_data(player.player_id)
        logging.info(f"Preloaded battles json for player: {player.name}")
    logging.info("Preloading complete")


def preload_activity_data() -> None:
    # because this function isn't calling update_snapshot_data, it's just creating
    # an empty data structure for the player's activity_json field, which helps the
    # front end to render the activity faster, while it loads the actual data in the background
    logging.info("Preloading activity data for all players")
    players = Player.objects.all()
    for player in players:
        if not player.activity_json:
            update_activity_data(player.player_id)
        logging.info(f"Preloaded activity data for player: {player.name}")
    logging.info("Preloading complete")
