import random

from django.core.cache import cache
from django.db.models import Case, Count, F, FloatField, Q, Sum, Value, When
from django.db.models.functions import Cast

from warships.data import _calculate_tier_filtered_pvp_record, get_highest_ranked_league_name, get_player_clan_battle_summaries, is_clan_battle_enjoyer, is_pve_player, is_ranked_player, is_sleepy_player
from warships.models import Clan, Player


LANDING_CACHE_TTL = 60
LANDING_CLANS_CACHE_KEY = 'landing:clans:v3'
LANDING_RECENT_CLANS_CACHE_KEY = 'landing:recent_clans:last_lookup:v2'
LANDING_RECENT_PLAYERS_CACHE_KEY = 'landing:recent_players:last_lookup:v4'
LANDING_PLAYERS_CACHE_NAMESPACE_KEY = 'landing:players:v10:namespace'
LANDING_CLAN_FEATURED_COUNT = 40
LANDING_CLAN_MIN_TOTAL_BATTLES = 100000
LANDING_PLAYER_LIMIT = 40
LANDING_PLAYER_RANDOM_MIN_PVP_BATTLES = 500
LANDING_PLAYER_BEST_MIN_PVP_BATTLES = 2500
LANDING_PLAYER_BEST_CANDIDATE_LIMIT = 400
LANDING_PLAYER_MODES = ('random', 'best')


def _player_score_ordering(secondary_field: str):
    return (
        F('explorer_summary__player_score').desc(nulls_last=True),
        F(secondary_field).desc(nulls_last=True),
        'name',
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
    return f'landing:players:v10:n{namespace}:{mode}:{limit}'


def normalize_landing_player_mode(mode: str | None) -> str:
    normalized_mode = (mode or 'random').strip().lower()
    if normalized_mode not in LANDING_PLAYER_MODES:
        raise ValueError('mode must be one of: random, best')
    return normalized_mode


def normalize_landing_player_limit(requested_limit: int | None) -> int:
    try:
        parsed_limit = int(requested_limit or LANDING_PLAYER_LIMIT)
    except (TypeError, ValueError):
        parsed_limit = LANDING_PLAYER_LIMIT

    return max(1, min(parsed_limit, LANDING_PLAYER_LIMIT))


def invalidate_landing_clan_caches() -> None:
    cache.delete_many(
        [LANDING_CLANS_CACHE_KEY, LANDING_RECENT_CLANS_CACHE_KEY])


def invalidate_landing_recent_player_cache() -> None:
    cache.delete(LANDING_RECENT_PLAYERS_CACHE_KEY)


def invalidate_landing_player_caches(include_recent: bool = False) -> None:
    _bump_landing_players_cache_namespace()
    if include_recent:
        invalidate_landing_recent_player_cache()


def _serialize_landing_player_rows(rows: list[dict]) -> list[dict]:
    clan_battle_summaries = get_player_clan_battle_summaries(
        [row.get('player_id') for row in rows],
        allow_fetch=False,
    )

    for row in rows:
        high_tier_battles, high_tier_ratio = _calculate_tier_filtered_pvp_record(
            row.pop('battles_json', None),
            minimum_tier=5,
        )
        ranked_rows = row.pop('ranked_json', None)
        clan_battle_summary = clan_battle_summaries.get(
            int(row.get('player_id') or 0),
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


def get_landing_clans_payload() -> list[dict]:
    return cache.get_or_set(LANDING_CLANS_CACHE_KEY, _build_landing_clans, LANDING_CACHE_TTL)


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
        ).values(
            'name', 'player_id', 'pvp_ratio', 'is_hidden', 'days_since_last_battle', 'total_battles', 'pvp_battles', 'battles_json', 'ranked_json'
        ).order_by(
            F('pvp_ratio').desc(nulls_last=True),
            F('last_battle_date').desc(nulls_last=True),
            'name',
        )[:LANDING_PLAYER_BEST_CANDIDATE_LIMIT]
    )
    rows = _serialize_landing_player_rows(candidate_rows)
    rows = [
        {
            **row,
            'pvp_ratio': (
                row.get('high_tier_pvp_ratio')
                if (row.get('high_tier_pvp_battles') or 0) > LANDING_PLAYER_BEST_MIN_PVP_BATTLES and row.get('high_tier_pvp_ratio') is not None
                else row.get('pvp_ratio')
            ),
        }
        for row in rows
        if (row.get('pvp_battles') or 0) > LANDING_PLAYER_BEST_MIN_PVP_BATTLES
    ]
    rows.sort(key=lambda row: (
        -(row.get('pvp_ratio') if row.get('pvp_ratio')
          is not None else float('-inf')),
        row.get('name') or '',
    ))
    return rows[:limit]


def get_landing_players_payload(mode: str = 'random', limit: int = LANDING_PLAYER_LIMIT) -> list[dict]:
    normalized_mode = normalize_landing_player_mode(mode)
    normalized_limit = normalize_landing_player_limit(limit)
    cache_key = landing_player_cache_key(normalized_mode, normalized_limit)
    builder = _build_best_landing_players if normalized_mode == 'best' else _build_random_landing_players
    return cache.get_or_set(cache_key, lambda: builder(normalized_limit), LANDING_CACHE_TTL)


def _build_recent_players() -> list[dict]:
    rows = list(
        Player.objects.exclude(name='').exclude(
            last_lookup__isnull=True
        ).values('name', 'player_id', 'pvp_ratio', 'days_since_last_battle', 'total_battles', 'pvp_battles', 'ranked_json').order_by(
            F('last_lookup').desc(nulls_last=True),
            'name',
        )[:40]
    )
    clan_battle_summaries = get_player_clan_battle_summaries(
        [row.get('player_id') for row in rows],
        allow_fetch=False,
    )

    for row in rows:
        ranked_rows = row.pop('ranked_json', None)
        clan_battle_summary = clan_battle_summaries.get(
            int(row.get('player_id') or 0),
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
        row.pop('player_id', None)
        row.pop('days_since_last_battle', None)

    return rows


def get_landing_recent_players_payload() -> list[dict]:
    return cache.get_or_set(LANDING_RECENT_PLAYERS_CACHE_KEY, _build_recent_players, LANDING_CACHE_TTL)


def warm_landing_page_content() -> dict:
    warmed = {
        'clans': len(get_landing_clans_payload()),
        'recent_clans': len(get_landing_recent_clans_payload()),
        'players_random': len(get_landing_players_payload('random', LANDING_PLAYER_LIMIT)),
        'players_best': len(get_landing_players_payload('best', LANDING_PLAYER_LIMIT)),
        'recent_players': len(get_landing_recent_players_payload()),
    }
    return {'status': 'completed', 'warmed': warmed}
