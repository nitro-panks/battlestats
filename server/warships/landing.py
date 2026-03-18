import random
from datetime import timedelta
import math

from django.core.cache import cache
from django.db.models import Case, Count, F, FloatField, Q, Sum, Value, When
from django.db.models.functions import Cast
from django.utils import timezone

from warships.data import _calculate_tier_filtered_pvp_record, _get_published_efficiency_rank_payload, get_highest_ranked_league_name, get_player_clan_battle_summaries, is_clan_battle_enjoyer, is_pve_player, is_ranked_player, is_sleepy_player
from warships.models import Clan, Player


LANDING_CACHE_TTL = 60
LANDING_CLAN_CACHE_TTL = 60 * 60
LANDING_PLAYER_CACHE_TTL = 60 * 60
LANDING_CLANS_CACHE_KEY = 'landing:clans:v3'
LANDING_CLANS_CACHE_METADATA_KEY = 'landing:clans:v3:meta'
LANDING_RECENT_CLANS_CACHE_KEY = 'landing:recent_clans:last_lookup:v2'
LANDING_RECENT_PLAYERS_CACHE_KEY = 'landing:recent_players:last_lookup:v5'
LANDING_PLAYERS_CACHE_NAMESPACE_KEY = 'landing:players:v11:namespace'
LANDING_CLAN_FEATURED_COUNT = 40
LANDING_CLAN_MIN_TOTAL_BATTLES = 100000
LANDING_PLAYER_LIMIT = 40
LANDING_PLAYER_RANDOM_MIN_PVP_BATTLES = 500
LANDING_PLAYER_BEST_MIN_PVP_BATTLES = 2500
LANDING_PLAYER_BEST_MIN_HIGH_TIER_PVP_BATTLES = 500
LANDING_PLAYER_BEST_TARGET_HIGH_TIER_PVP_BATTLES = 5000
LANDING_PLAYER_BEST_CANDIDATE_LIMIT = 1200
LANDING_PLAYER_SIGMA_MIN_PVP_BATTLES = 500
LANDING_PLAYER_MODES = ('random', 'best', 'sigma')
LANDING_PLAYER_BEST_WR_WEIGHT = 0.40
LANDING_PLAYER_BEST_PLAYER_SCORE_WEIGHT = 0.22
LANDING_PLAYER_BEST_EFFICIENCY_WEIGHT = 0.18
LANDING_PLAYER_BEST_VOLUME_WEIGHT = 0.10
LANDING_PLAYER_BEST_RANKED_WEIGHT = 0.06
LANDING_PLAYER_BEST_CLAN_WEIGHT = 0.04
LANDING_PLAYER_BEST_EFFICIENCY_NEUTRAL = 0.35
LANDING_PLAYER_BEST_COMPETITIVE_SHARE_FLOOR = 0.55


def _player_score_ordering(secondary_field: str):
    return (
        F('explorer_summary__player_score').desc(nulls_last=True),
        F(secondary_field).desc(nulls_last=True),
        'name',
    )


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def _normalize_best_wr_score(value: float | None) -> float:
    if value is None:
        return 0.0
    return _clamp((float(value) - 45.0) / 20.0, 0.0, 1.0)


def _normalize_best_player_score(value: float | None) -> float:
    if value is None:
        return 0.0
    return _clamp(float(value) / 10.0, 0.0, 1.0)


def _normalize_best_efficiency_score(percentile: float | None, shrunken_strength: float | None) -> float:
    if percentile is not None:
        return _clamp(float(percentile), 0.0, 1.0)
    if shrunken_strength is not None:
        return _clamp(float(shrunken_strength), 0.0, 1.0)
    return LANDING_PLAYER_BEST_EFFICIENCY_NEUTRAL


def _normalize_best_volume_score(high_tier_battles: int | None) -> float:
    battles = max(int(high_tier_battles or 0), 0)
    if battles <= 0:
        return 0.0
    return _clamp(
        math.log10(battles + 1) /
        math.log10(LANDING_PLAYER_BEST_TARGET_HIGH_TIER_PVP_BATTLES + 1),
        0.0,
        1.0,
    )


def _ranked_league_score(league: str | None) -> float:
    return {
        'Bronze': 0.35,
        'Silver': 0.65,
        'Gold': 1.0,
    }.get(str(league or '').strip(), 0.0)


def _normalize_best_ranked_score(latest_ranked_battles: int | None, highest_ranked_league: str | None) -> float:
    battles = max(int(latest_ranked_battles or 0), 0)
    if battles <= 0 and not highest_ranked_league:
        return 0.0

    volume_score = _clamp(math.log1p(battles) / math.log1p(40), 0.0, 1.0)
    league_score = _ranked_league_score(highest_ranked_league)
    return round((0.65 * league_score) + (0.35 * volume_score), 4)


def _normalize_best_clan_score(is_clan_battle_player: bool | None, clan_battle_win_rate: float | None) -> float:
    if not is_clan_battle_player:
        return 0.0

    win_rate_score = _normalize_best_wr_score(clan_battle_win_rate)
    return round(0.35 + (0.65 * win_rate_score), 4)


def _competitive_share_multiplier(pvp_battles: int | None, high_tier_battles: int | None) -> float:
    total_battles = max(int(pvp_battles or 0), 0)
    competitive_battles = max(int(high_tier_battles or 0), 0)
    if total_battles <= 0:
        return LANDING_PLAYER_BEST_COMPETITIVE_SHARE_FLOOR

    share = _clamp(competitive_battles / total_battles, 0.0, 1.0)
    if share <= 0.2:
        return LANDING_PLAYER_BEST_COMPETITIVE_SHARE_FLOOR
    if share >= 0.8:
        return 1.0

    normalized_share = (share - 0.2) / 0.6
    return round(
        LANDING_PLAYER_BEST_COMPETITIVE_SHARE_FLOOR +
        ((1.0 - LANDING_PLAYER_BEST_COMPETITIVE_SHARE_FLOOR)
         * math.sqrt(_clamp(normalized_share, 0.0, 1.0))),
        4,
    )


def _calculate_landing_best_score(row: dict) -> float:
    base_score = (
        LANDING_PLAYER_BEST_WR_WEIGHT * _normalize_best_wr_score(
            row.get('high_tier_pvp_ratio')) +
        LANDING_PLAYER_BEST_PLAYER_SCORE_WEIGHT * _normalize_best_player_score(
            row.get('player_score')) +
        LANDING_PLAYER_BEST_EFFICIENCY_WEIGHT * _normalize_best_efficiency_score(
            row.get('efficiency_rank_percentile'), row.get('shrunken_efficiency_strength')) +
        LANDING_PLAYER_BEST_VOLUME_WEIGHT * _normalize_best_volume_score(
            row.get('high_tier_pvp_battles')) +
        LANDING_PLAYER_BEST_RANKED_WEIGHT * _normalize_best_ranked_score(
            row.get('latest_ranked_battles'), row.get('highest_ranked_league_recent')) +
        LANDING_PLAYER_BEST_CLAN_WEIGHT * _normalize_best_clan_score(
            row.get('is_clan_battle_player'), row.get('clan_battle_win_rate'))
    )

    return round(
        base_score * _competitive_share_multiplier(
            row.get('pvp_battles'), row.get('high_tier_pvp_battles')),
        6,
    )


def _prioritize_landing_clans(rows, sample_size: int = LANDING_CLAN_FEATURED_COUNT, min_total_battles: int = LANDING_CLAN_MIN_TOTAL_BATTLES):
    eligible = [
        row for row in rows
        if (row.get('total_battles') or 0) >= min_total_battles and row.get('clan_wr') is not None
    ]
    if not eligible:
        return rows

    featured = random.sample(eligible, k=min(sample_size, len(eligible)))
    featured.sort(key=lambda row: (
        row.get('clan_wr') if row.get('clan_wr') is not None else float('inf'),
        (row.get('name') or '').lower(),
        row.get('clan_id') or 0,
    ))

    featured_ids = {row.get('clan_id') for row in featured}
    remainder = [row for row in rows if row.get('clan_id') not in featured_ids]
    return featured + remainder


def _get_landing_players_cache_namespace() -> int:
    namespace = cache.get(LANDING_PLAYERS_CACHE_NAMESPACE_KEY)
    if namespace is None:
        cache.add(LANDING_PLAYERS_CACHE_NAMESPACE_KEY, 1, timeout=None)
        namespace = cache.get(LANDING_PLAYERS_CACHE_NAMESPACE_KEY)

    try:
        return int(namespace)
    except (TypeError, ValueError):
        cache.set(LANDING_PLAYERS_CACHE_NAMESPACE_KEY, 1, timeout=None)
        return 1


def _bump_landing_players_cache_namespace() -> int:
    current_namespace = _get_landing_players_cache_namespace()
    try:
        return int(cache.incr(LANDING_PLAYERS_CACHE_NAMESPACE_KEY))
    except ValueError:
        next_namespace = current_namespace + 1
        cache.set(LANDING_PLAYERS_CACHE_NAMESPACE_KEY,
                  next_namespace, timeout=None)
        return next_namespace


def landing_player_cache_key(mode: str, limit: int) -> str:
    namespace = _get_landing_players_cache_namespace()
    return f'landing:players:v11:n{namespace}:{mode}:{limit}'


def landing_player_cache_metadata_key(mode: str, limit: int) -> str:
    namespace = _get_landing_players_cache_namespace()
    return f'landing:players:v11:n{namespace}:{mode}:{limit}:meta'


def landing_player_cache_ttl(mode: str) -> int:
    if mode in LANDING_PLAYER_MODES:
        return LANDING_PLAYER_CACHE_TTL
    return LANDING_CACHE_TTL


def _build_landing_player_cache_metadata(ttl_seconds: int) -> dict[str, str | int]:
    cached_at = timezone.now()
    expires_at = cached_at + timedelta(seconds=ttl_seconds)
    return {
        'cached_at': cached_at.isoformat(),
        'expires_at': expires_at.isoformat(),
        'ttl_seconds': ttl_seconds,
    }


def _normalize_landing_player_cache_metadata(metadata: dict | None, ttl_seconds: int) -> dict[str, str | int]:
    if not isinstance(metadata, dict):
        return _build_landing_player_cache_metadata(ttl_seconds)

    expires_at = metadata.get('expires_at')
    cached_at = metadata.get('cached_at')
    stored_ttl = metadata.get('ttl_seconds')
    if isinstance(expires_at, str) and isinstance(cached_at, str) and isinstance(stored_ttl, int):
        return {
            'cached_at': cached_at,
            'expires_at': expires_at,
            'ttl_seconds': stored_ttl,
        }

    return _build_landing_player_cache_metadata(ttl_seconds)


def landing_clan_cache_metadata_key() -> str:
    return LANDING_CLANS_CACHE_METADATA_KEY


def get_landing_clans_payload_with_cache_metadata(force_refresh: bool = False) -> tuple[list[dict], dict[str, str | int]]:
    ttl_seconds = LANDING_CLAN_CACHE_TTL
    cache_key = LANDING_CLANS_CACHE_KEY
    metadata_key = landing_clan_cache_metadata_key()

    payload = None if force_refresh else cache.get(cache_key)
    metadata = _normalize_landing_player_cache_metadata(
        None if force_refresh else cache.get(metadata_key), ttl_seconds)

    if payload is None:
        payload = _build_landing_clans()
        metadata = _build_landing_player_cache_metadata(ttl_seconds)
        cache.set(cache_key, payload, ttl_seconds)
        cache.set(metadata_key, metadata, ttl_seconds)
    elif cache.get(metadata_key) is None:
        cache.set(metadata_key, metadata, ttl_seconds)

    return payload, metadata


def normalize_landing_player_mode(mode: str | None) -> str:
    normalized_mode = (mode or 'random').strip().lower()
    if normalized_mode not in LANDING_PLAYER_MODES:
        raise ValueError('mode must be one of: random, best, sigma')
    return normalized_mode


def normalize_landing_player_limit(requested_limit: int | None) -> int:
    try:
        parsed_limit = int(requested_limit or LANDING_PLAYER_LIMIT)
    except (TypeError, ValueError):
        parsed_limit = LANDING_PLAYER_LIMIT

    return max(1, min(parsed_limit, LANDING_PLAYER_LIMIT))


def invalidate_landing_clan_caches() -> None:
    cache.delete_many(
        [LANDING_CLANS_CACHE_KEY, LANDING_CLANS_CACHE_METADATA_KEY, LANDING_RECENT_CLANS_CACHE_KEY])


def invalidate_landing_recent_player_cache() -> None:
    cache.delete(LANDING_RECENT_PLAYERS_CACHE_KEY)


def invalidate_landing_player_caches(include_recent: bool = False) -> None:
    _bump_landing_players_cache_namespace()
    if include_recent:
        invalidate_landing_recent_player_cache()


def _serialize_landing_player_rows(rows: list[dict]) -> list[dict]:
    player_ids = [int(row.get('player_id') or 0)
                  for row in rows if row.get('player_id') is not None]
    clan_battle_summaries = get_player_clan_battle_summaries(
        player_ids,
        allow_fetch=False,
    )
    players_by_id = {
        player.player_id: player
        for player in Player.objects.filter(player_id__in=player_ids).select_related('explorer_summary').only(
            'player_id',
            'is_hidden',
            'pvp_battles',
            'efficiency_updated_at',
            'battles_updated_at',
            'explorer_summary__efficiency_rank_percentile',
            'explorer_summary__efficiency_rank_tier',
            'explorer_summary__has_efficiency_rank_icon',
            'explorer_summary__efficiency_rank_population_size',
            'explorer_summary__efficiency_rank_updated_at',
            'explorer_summary__eligible_ship_count',
            'explorer_summary__efficiency_badge_rows_total',
            'explorer_summary__badge_rows_unmapped',
        )
    }

    for row in rows:
        player_id = int(row.get('player_id') or 0)
        high_tier_battles, high_tier_ratio = _calculate_tier_filtered_pvp_record(
            row.pop('battles_json', None),
            minimum_tier=5,
        )
        ranked_rows = row.pop('ranked_json', None)
        clan_battle_summary = clan_battle_summaries.get(
            player_id,
            {'total_battles': 0, 'seasons_participated': 0, 'win_rate': None},
        )
        row['high_tier_pvp_battles'] = high_tier_battles
        row['high_tier_pvp_ratio'] = high_tier_ratio
        row['is_pve_player'] = is_pve_player(
            row.get('total_battles'), row.get('pvp_battles'))
        row['is_sleepy_player'] = is_sleepy_player(
            row.get('days_since_last_battle'))
        row['is_ranked_player'] = is_ranked_player(ranked_rows)
        row['is_clan_battle_player'] = is_clan_battle_enjoyer(
            clan_battle_summary['total_battles'], clan_battle_summary['seasons_participated'])
        row['clan_battle_win_rate'] = clan_battle_summary['win_rate']
        row['highest_ranked_league'] = get_highest_ranked_league_name(
            ranked_rows)
        row.update(_get_published_efficiency_rank_payload(
            players_by_id.get(player_id)))
        row.pop('player_id', None)
        row.pop('days_since_last_battle', None)

    return rows


def _build_landing_clans() -> list[dict]:
    qs = Clan.objects.exclude(name__isnull=True).exclude(name='').annotate(
        total_wins=Sum('player__pvp_wins'),
        total_battles=Sum('player__pvp_battles'),
        active_members=Count('player', filter=Q(
            player__days_since_last_battle__lte=30)),
    ).annotate(
        clan_wr=Case(
            When(total_battles__gt=0, then=Cast(F('total_wins'), FloatField(
            )) / Cast(F('total_battles'), FloatField()) * Value(100.0)),
            default=None,
            output_field=FloatField(),
        ),
    ).values(
        'clan_id', 'name', 'tag', 'members_count', 'clan_wr', 'total_battles', 'active_members'
    ).order_by(F('last_lookup').desc(nulls_last=True))
    return _prioritize_landing_clans(list(qs))


def get_landing_clans_payload(force_refresh: bool = False) -> list[dict]:
    payload, _ = get_landing_clans_payload_with_cache_metadata(
        force_refresh=force_refresh)
    return payload


def _build_recent_clans() -> list[dict]:
    return list(
        Clan.objects.exclude(name__isnull=True).exclude(name='').exclude(
            last_lookup__isnull=True
        ).annotate(
            total_wins=Sum('player__pvp_wins'),
            total_battles=Sum('player__pvp_battles'),
        ).annotate(
            clan_wr=Case(
                When(total_battles__gt=0, then=Cast(F('total_wins'), FloatField(
                )) / Cast(F('total_battles'), FloatField()) * Value(100.0)),
                default=None,
                output_field=FloatField(),
            ),
        ).values(
            'clan_id', 'name', 'tag', 'members_count', 'clan_wr', 'total_battles'
        ).order_by(
            F('last_lookup').desc(nulls_last=True),
            'name',
        )[:40]
    )


def get_landing_recent_clans_payload() -> list[dict]:
    return cache.get_or_set(LANDING_RECENT_CLANS_CACHE_KEY, _build_recent_clans, LANDING_CACHE_TTL)


def _build_random_landing_players(limit: int) -> list[dict]:
    eligible_ids = list(
        Player.objects.exclude(name='').filter(
            is_hidden=False,
            days_since_last_battle__lte=180,
            pvp_battles__gt=LANDING_PLAYER_RANDOM_MIN_PVP_BATTLES,
        ).exclude(
            last_battle_date__isnull=True
        ).values_list('player_id', flat=True)
    )
    if not eligible_ids:
        return []

    selected_ids = random.sample(
        eligible_ids, k=min(limit, len(eligible_ids)))
    selected_order = {player_id: index for index,
                      player_id in enumerate(selected_ids)}
    rows = list(
        Player.objects.filter(player_id__in=selected_ids).values(
            'name', 'player_id', 'pvp_ratio', 'is_hidden', 'days_since_last_battle', 'total_battles', 'pvp_battles', 'battles_json', 'ranked_json'
        )
    )
    rows.sort(key=lambda row: selected_order.get(
        int(row.get('player_id') or 0), len(selected_order)))
    return _serialize_landing_player_rows(rows)


def _build_best_landing_players(limit: int) -> list[dict]:
    candidate_rows = list(
        Player.objects.exclude(name='').filter(
            is_hidden=False,
            days_since_last_battle__lte=180,
            pvp_battles__gt=LANDING_PLAYER_BEST_MIN_PVP_BATTLES,
        ).exclude(
            last_battle_date__isnull=True
        ).annotate(
            player_score=F('explorer_summary__player_score'),
            efficiency_rank_percentile=F(
                'explorer_summary__efficiency_rank_percentile'),
            shrunken_efficiency_strength=F(
                'explorer_summary__shrunken_efficiency_strength'),
            latest_ranked_battles=F('explorer_summary__latest_ranked_battles'),
            highest_ranked_league_recent=F(
                'explorer_summary__highest_ranked_league_recent'),
        ).values(
            'name',
            'player_id',
            'pvp_ratio',
            'is_hidden',
            'days_since_last_battle',
            'total_battles',
            'pvp_battles',
            'battles_json',
            'ranked_json',
            'player_score',
            'efficiency_rank_percentile',
            'shrunken_efficiency_strength',
            'latest_ranked_battles',
            'highest_ranked_league_recent',
        ).order_by(
            F('explorer_summary__player_score').desc(nulls_last=True),
            F('pvp_ratio').desc(nulls_last=True),
            F('last_battle_date').desc(nulls_last=True),
            'name',
        )[:LANDING_PLAYER_BEST_CANDIDATE_LIMIT]
    )
    serialized_rows = _serialize_landing_player_rows(candidate_rows)
    rows = []
    for row in serialized_rows:
        high_tier_battles = int(row.get('high_tier_pvp_battles') or 0)
        if high_tier_battles < LANDING_PLAYER_BEST_MIN_HIGH_TIER_PVP_BATTLES:
            continue

        row['pvp_ratio'] = (
            row.get('high_tier_pvp_ratio')
            if row.get('high_tier_pvp_ratio') is not None
            else row.get('pvp_ratio')
        )
        row['best_competitive_score'] = _calculate_landing_best_score(row)
        rows.append(row)

    rows.sort(key=lambda row: (
        -(row.get('best_competitive_score') if row.get('best_competitive_score')
          is not None else float('-inf')),
        -(row.get('high_tier_pvp_ratio') if row.get('high_tier_pvp_ratio')
          is not None else float('-inf')),
        -(row.get('player_score') if row.get('player_score')
          is not None else float('-inf')),
        -(row.get('efficiency_rank_percentile') if row.get('efficiency_rank_percentile')
          is not None else (row.get('shrunken_efficiency_strength') if row.get('shrunken_efficiency_strength') is not None else float('-inf'))),
        row.get('name') or '',
    ))

    for row in rows:
        row.pop('best_competitive_score', None)
        row.pop('player_score', None)
        row.pop('shrunken_efficiency_strength', None)
        row.pop('latest_ranked_battles', None)
        row.pop('highest_ranked_league_recent', None)

    return rows[:limit]


def _build_sigma_landing_players(limit: int) -> list[dict]:
    candidate_rows = list(
        Player.objects.exclude(name='').filter(
            is_hidden=False,
            days_since_last_battle__lte=180,
            pvp_battles__gt=LANDING_PLAYER_SIGMA_MIN_PVP_BATTLES,
            explorer_summary__efficiency_rank_percentile__isnull=False,
        ).exclude(
            last_battle_date__isnull=True,
        ).annotate(
            player_score=F('explorer_summary__player_score'),
        ).values(
            'name',
            'player_id',
            'pvp_ratio',
            'is_hidden',
            'days_since_last_battle',
            'total_battles',
            'pvp_battles',
            'battles_json',
            'ranked_json',
            'player_score',
        )
    )

    rows = _serialize_landing_player_rows(candidate_rows)
    rows = [row for row in rows if row.get(
        'efficiency_rank_percentile') is not None]
    rows.sort(key=lambda row: (
        -(row.get('efficiency_rank_percentile')
          if row.get('efficiency_rank_percentile') is not None else float('-inf')),
        -(row.get('player_score') if row.get('player_score')
          is not None else float('-inf')),
        -(row.get('pvp_ratio') if row.get('pvp_ratio')
          is not None else float('-inf')),
        row.get('name') or '',
    ))

    for row in rows:
        row.pop('player_score', None)

    return rows[:limit]


def get_landing_players_payload_with_cache_metadata(mode: str = 'random', limit: int = LANDING_PLAYER_LIMIT, force_refresh: bool = False) -> tuple[list[dict], dict[str, str | int]]:
    normalized_mode = normalize_landing_player_mode(mode)
    normalized_limit = normalize_landing_player_limit(limit)
    cache_key = landing_player_cache_key(normalized_mode, normalized_limit)
    metadata_key = landing_player_cache_metadata_key(
        normalized_mode, normalized_limit)
    ttl_seconds = landing_player_cache_ttl(normalized_mode)

    if normalized_mode == 'best':
        builder = _build_best_landing_players
    elif normalized_mode == 'sigma':
        builder = _build_sigma_landing_players
    else:
        builder = _build_random_landing_players

    payload = None if force_refresh else cache.get(cache_key)
    metadata = _normalize_landing_player_cache_metadata(
        None if force_refresh else cache.get(metadata_key), ttl_seconds)

    if payload is None:
        payload = builder(normalized_limit)
        metadata = _build_landing_player_cache_metadata(ttl_seconds)
        cache.set(cache_key, payload, ttl_seconds)
        cache.set(metadata_key, metadata, ttl_seconds)
    elif cache.get(metadata_key) is None:
        cache.set(metadata_key, metadata, ttl_seconds)

    return payload, metadata


def get_landing_players_payload(mode: str = 'random', limit: int = LANDING_PLAYER_LIMIT, force_refresh: bool = False) -> list[dict]:
    payload, _ = get_landing_players_payload_with_cache_metadata(
        mode=mode,
        limit=limit,
        force_refresh=force_refresh,
    )
    return payload


def _build_recent_players() -> list[dict]:
    rows = list(
        Player.objects.exclude(name='').exclude(
            last_lookup__isnull=True
        ).values('name', 'player_id', 'pvp_ratio', 'days_since_last_battle', 'total_battles', 'pvp_battles', 'ranked_json').order_by(
            F('last_lookup').desc(nulls_last=True),
            'name',
        )[:40]
    )
    player_ids = [int(row.get('player_id') or 0)
                  for row in rows if row.get('player_id') is not None]
    clan_battle_summaries = get_player_clan_battle_summaries(
        player_ids,
        allow_fetch=False,
    )
    players_by_id = {
        player.player_id: player
        for player in Player.objects.filter(player_id__in=player_ids).select_related('explorer_summary')
    }

    for row in rows:
        player_id = int(row.get('player_id') or 0)
        ranked_rows = row.pop('ranked_json', None)
        clan_battle_summary = clan_battle_summaries.get(
            player_id,
            {'total_battles': 0, 'seasons_participated': 0, 'win_rate': None},
        )
        row['is_pve_player'] = is_pve_player(
            row.get('total_battles'), row.get('pvp_battles'))
        row['is_sleepy_player'] = is_sleepy_player(
            row.get('days_since_last_battle'))
        row['is_ranked_player'] = is_ranked_player(ranked_rows)
        row['is_clan_battle_player'] = is_clan_battle_enjoyer(
            clan_battle_summary['total_battles'], clan_battle_summary['seasons_participated'])
        row['clan_battle_win_rate'] = clan_battle_summary['win_rate']
        row['highest_ranked_league'] = get_highest_ranked_league_name(
            ranked_rows)
        row.update(_get_published_efficiency_rank_payload(
            players_by_id.get(player_id)))
        row.pop('player_id', None)
        row.pop('days_since_last_battle', None)

    return rows


def get_landing_recent_players_payload() -> list[dict]:
    return cache.get_or_set(LANDING_RECENT_PLAYERS_CACHE_KEY, _build_recent_players, LANDING_CACHE_TTL)


def warm_landing_page_content(force_refresh: bool = False) -> dict:
    warmed = {
        'clans': len(get_landing_clans_payload(force_refresh=force_refresh)),
        'recent_clans': len(get_landing_recent_clans_payload()),
        'players_random': len(get_landing_players_payload('random', LANDING_PLAYER_LIMIT, force_refresh=force_refresh)),
        'players_best': len(get_landing_players_payload('best', LANDING_PLAYER_LIMIT, force_refresh=force_refresh)),
        'players_sigma': len(get_landing_players_payload('sigma', LANDING_PLAYER_LIMIT, force_refresh=force_refresh)),
        'recent_players': len(get_landing_recent_players_payload()),
    }
    return {'status': 'completed', 'warmed': warmed}
