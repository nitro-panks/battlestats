import logging
import random
import time
from datetime import timedelta
import math

from django.core.cache import cache
from django.db.models import Case, Count, F, FloatField, Q, Sum, Value, When
from django.db.models.functions import Cast, Coalesce
from django.utils import timezone

from warships.data import _calculate_tier_filtered_pvp_record, get_highest_ranked_league_name, is_clan_battle_enjoyer, is_pve_player, is_ranked_player, is_sleepy_player, score_best_clans
from warships.models import Clan, DEFAULT_REALM, Player, realm_cache_key
from warships.visit_analytics import get_top_entities


logger = logging.getLogger(__name__)


def _clan_agg_annotations():
    """Clan stats annotations: use cached fields, fall back to live aggregation."""
    return dict(
        total_wins=Coalesce(F('cached_total_wins'), Sum('player__pvp_wins')),
        total_battles=Coalesce(F('cached_total_battles'),
                               Sum('player__pvp_battles')),
        active_members=Coalesce(
            F('cached_active_member_count'),
            Count('player', filter=Q(player__days_since_last_battle__lte=30)),
        ),
    )


def _clan_wr_annotation():
    """Clan win-rate annotation: use cached value, fall back to live computation."""
    return dict(
        clan_wr=Case(
            When(cached_clan_wr__isnull=False, then=F('cached_clan_wr')),
            When(
                total_battles__gt=0,
                then=Cast(F('total_wins'), FloatField()) /
                Cast(F('total_battles'), FloatField()) * Value(100.0),
            ),
            default=None,
            output_field=FloatField(),
        ),
    )


LANDING_CACHE_TTL = 60 * 60 * 12
LANDING_CLAN_CACHE_TTL = 60 * 60 * 12
LANDING_PLAYER_CACHE_TTL = 60 * 60 * 12
LANDING_CLANS_CACHE_KEY = 'landing:clans:v4'
LANDING_CLANS_CACHE_METADATA_KEY = 'landing:clans:v4:meta'
LANDING_CLANS_PUBLISHED_CACHE_KEY = 'landing:clans:v4:published'
LANDING_CLANS_PUBLISHED_METADATA_KEY = 'landing:clans:v4:published:meta'
LANDING_CLANS_BEST_CACHE_KEY = 'landing:clans:best:v1'
LANDING_CLANS_BEST_CACHE_METADATA_KEY = 'landing:clans:best:v1:meta'
LANDING_CLANS_BEST_PUBLISHED_CACHE_KEY = 'landing:clans:best:v1:published'
LANDING_CLANS_BEST_PUBLISHED_METADATA_KEY = 'landing:clans:best:v1:published:meta'
LANDING_RECENT_CLANS_CACHE_KEY = 'landing:recent_clans:last_lookup:v2'
LANDING_RECENT_PLAYERS_CACHE_KEY = 'landing:recent_players:last_lookup:v6'
LANDING_PLAYERS_CACHE_NAMESPACE_KEY = 'landing:players:v12:namespace'
LANDING_CLANS_DIRTY_KEY = 'landing:clans:dirty:v1'
LANDING_PLAYERS_DIRTY_KEY = 'landing:players:dirty:v1'
LANDING_RECENT_CLANS_DIRTY_KEY = 'landing:recent_clans:dirty:v1'
LANDING_RECENT_PLAYERS_DIRTY_KEY = 'landing:recent_players:dirty:v1'
LANDING_CLAN_FEATURED_COUNT = 30
LANDING_CLAN_MIN_TOTAL_BATTLES = 100000
LANDING_CLAN_MODES = ('random', 'best')
LANDING_PLAYER_LIMIT = 25
LANDING_PLAYER_RANDOM_MIN_PVP_BATTLES = 500
LANDING_PLAYER_BEST_MIN_PVP_BATTLES = 2500
LANDING_PLAYER_BEST_MIN_HIGH_TIER_PVP_BATTLES = 500
LANDING_PLAYER_BEST_TARGET_HIGH_TIER_PVP_BATTLES = 5000
LANDING_PLAYER_BEST_CANDIDATE_LIMIT = 1200
LANDING_PLAYER_SIGMA_MIN_PVP_BATTLES = 500
LANDING_PLAYER_MODES = ('random', 'best', 'sigma', 'popular')
LANDING_PLAYER_BEST_WR_WEIGHT = 0.40
LANDING_PLAYER_BEST_PLAYER_SCORE_WEIGHT = 0.22
LANDING_PLAYER_BEST_EFFICIENCY_WEIGHT = 0.18
LANDING_PLAYER_BEST_VOLUME_WEIGHT = 0.10
LANDING_PLAYER_BEST_RANKED_WEIGHT = 0.06
LANDING_PLAYER_BEST_CLAN_WEIGHT = 0.04
LANDING_PLAYER_BEST_EFFICIENCY_NEUTRAL = 0.35
LANDING_PLAYER_BEST_COMPETITIVE_SHARE_FLOOR = 0.55
LANDING_RANDOM_PLAYER_QUEUE_KEY = 'landing:queue:players:random:v1'
LANDING_RANDOM_PLAYER_QUEUE_ELIGIBLE_KEY = 'landing:queue:players:random:eligible:v1'
LANDING_RANDOM_PLAYER_QUEUE_LOCK_KEY = 'landing:queue:players:random:lock:v1'
LANDING_RANDOM_PLAYER_QUEUE_TARGET_SIZE = 100
LANDING_RANDOM_PLAYER_QUEUE_REFILL_SIZE = 25
LANDING_RANDOM_PLAYER_QUEUE_REFILL_THRESHOLD = 60
LANDING_RANDOM_PLAYER_QUEUE_LOCK_TIMEOUT = 30
LANDING_RANDOM_PLAYER_QUEUE_ELIGIBLE_TTL = 10 * 60
LANDING_RANDOM_CLAN_QUEUE_KEY = 'landing:queue:clans:random:v1'
LANDING_RANDOM_CLAN_QUEUE_ELIGIBLE_KEY = 'landing:queue:clans:random:eligible:v1'
LANDING_RANDOM_CLAN_QUEUE_LOCK_KEY = 'landing:queue:clans:random:lock:v1'
LANDING_RANDOM_CLAN_QUEUE_PREVIEW_KEY = 'landing:queue:clans:random:preview:v1'
LANDING_RANDOM_CLAN_QUEUE_TARGET_SIZE = 100
LANDING_RANDOM_CLAN_QUEUE_REFILL_SIZE = 30
LANDING_RANDOM_CLAN_QUEUE_REFILL_THRESHOLD = 60
LANDING_RANDOM_CLAN_QUEUE_LOCK_TIMEOUT = 30
LANDING_RANDOM_CLAN_QUEUE_ELIGIBLE_TTL = 10 * 60


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
        return rows[:sample_size]

    featured = random.sample(eligible, k=min(sample_size, len(eligible)))
    featured.sort(key=lambda row: (
        row.get('clan_wr') if row.get('clan_wr') is not None else float('inf'),
        (row.get('name') or '').lower(),
        row.get('clan_id') or 0,
    ))

    return featured


def _get_landing_players_cache_namespace(realm: str = DEFAULT_REALM) -> int:
    ns_key = realm_cache_key(realm, LANDING_PLAYERS_CACHE_NAMESPACE_KEY)
    namespace = cache.get(ns_key)
    if namespace is None:
        cache.add(ns_key, 1, timeout=None)
        namespace = cache.get(ns_key)

    try:
        return int(namespace)
    except (TypeError, ValueError):
        cache.set(ns_key, 1, timeout=None)
        return 1


def _bump_landing_players_cache_namespace(realm: str = DEFAULT_REALM) -> int:
    ns_key = realm_cache_key(realm, LANDING_PLAYERS_CACHE_NAMESPACE_KEY)
    current_namespace = _get_landing_players_cache_namespace(realm=realm)
    try:
        return int(cache.incr(ns_key))
    except ValueError:
        next_namespace = current_namespace + 1
        cache.set(ns_key, next_namespace, timeout=None)
        return next_namespace


def landing_player_cache_key(mode: str, limit: int, realm: str = DEFAULT_REALM) -> str:
    namespace = _get_landing_players_cache_namespace(realm=realm)
    return realm_cache_key(realm, f'landing:players:v12:n{namespace}:{mode}:{limit}')


def landing_player_cache_metadata_key(mode: str, limit: int, realm: str = DEFAULT_REALM) -> str:
    namespace = _get_landing_players_cache_namespace(realm=realm)
    return realm_cache_key(realm, f'landing:players:v12:n{namespace}:{mode}:{limit}:meta')


def landing_player_published_cache_key(mode: str, limit: int, realm: str = DEFAULT_REALM) -> str:
    return realm_cache_key(realm, f'landing:players:v12:published:{mode}:{limit}')


def landing_player_published_metadata_key(mode: str, limit: int, realm: str = DEFAULT_REALM) -> str:
    return realm_cache_key(realm, f'landing:players:v12:published:{mode}:{limit}:meta')


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


def _mark_cache_family_dirty(*dirty_keys: str) -> None:
    dirty_at = timezone.now().isoformat()
    for dirty_key in dirty_keys:
        cache.set(dirty_key, dirty_at, timeout=None)


def _clear_cache_family_dirty(*dirty_keys: str) -> None:
    if dirty_keys:
        cache.delete_many(list(dirty_keys))


def _queue_landing_republish(realm: str = DEFAULT_REALM) -> None:
    from warships.tasks import queue_landing_page_warm

    queue_landing_page_warm(realm=realm)


def _publish_landing_payload(
    cache_key: str,
    metadata_key: str,
    published_cache_key: str,
    published_metadata_key: str,
    payload: list[dict],
    metadata: dict[str, str | int],
    ttl_seconds: int,
) -> None:
    cache.set(cache_key, payload, ttl_seconds)
    cache.set(metadata_key, metadata, ttl_seconds)
    cache.set(published_cache_key, payload, timeout=None)
    cache.set(published_metadata_key, metadata, timeout=None)


def _get_cached_landing_payload_with_fallback(
    cache_key: str,
    metadata_key: str,
    published_cache_key: str,
    published_metadata_key: str,
    ttl_seconds: int,
    force_refresh: bool,
    realm: str = DEFAULT_REALM,
) -> tuple[list[dict] | None, dict[str, str | int]]:
    payload = None if force_refresh else cache.get(cache_key)
    metadata = _normalize_landing_player_cache_metadata(
        None if force_refresh else cache.get(metadata_key), ttl_seconds)

    if payload is not None:
        if cache.get(metadata_key) is None:
            cache.set(metadata_key, metadata, ttl_seconds)
        cache.set(published_cache_key, payload, timeout=None)
        cache.set(published_metadata_key, metadata, timeout=None)
        return payload, metadata

    published_payload = None if force_refresh else cache.get(
        published_cache_key)
    if published_payload is not None:
        published_metadata = _normalize_landing_player_cache_metadata(
            cache.get(published_metadata_key), ttl_seconds)
        cache.set(published_metadata_key, published_metadata, timeout=None)
        _queue_landing_republish(realm=realm)
        return published_payload, published_metadata

    return None, metadata


def landing_clan_cache_metadata_key(realm: str = DEFAULT_REALM) -> str:
    return realm_cache_key(realm, LANDING_CLANS_CACHE_METADATA_KEY)


def get_landing_clans_payload_with_cache_metadata(force_refresh: bool = False, realm: str = DEFAULT_REALM) -> tuple[list[dict], dict[str, str | int]]:
    ttl_seconds = LANDING_CLAN_CACHE_TTL
    cache_key = realm_cache_key(realm, LANDING_CLANS_CACHE_KEY)
    metadata_key = landing_clan_cache_metadata_key(realm=realm)
    published_cache_key = realm_cache_key(
        realm, LANDING_CLANS_PUBLISHED_CACHE_KEY)
    published_metadata_key = realm_cache_key(
        realm, LANDING_CLANS_PUBLISHED_METADATA_KEY)

    payload, metadata = _get_cached_landing_payload_with_fallback(
        cache_key,
        metadata_key,
        published_cache_key,
        published_metadata_key,
        ttl_seconds,
        force_refresh,
        realm=realm,
    )

    if payload is None:
        payload = _build_landing_clans(realm=realm)
        metadata = _build_landing_player_cache_metadata(ttl_seconds)
        _publish_landing_payload(
            cache_key,
            metadata_key,
            published_cache_key,
            published_metadata_key,
            payload,
            metadata,
            ttl_seconds,
        )

    return payload, metadata


def normalize_landing_player_mode(mode: str | None) -> str:
    normalized_mode = (mode or 'random').strip().lower()
    if normalized_mode not in LANDING_PLAYER_MODES:
        raise ValueError('mode must be one of: random, best, sigma, popular')
    return normalized_mode


def normalize_landing_clan_mode(mode: str | None) -> str:
    normalized_mode = (mode or 'random').strip().lower()
    if normalized_mode not in LANDING_CLAN_MODES:
        raise ValueError('mode must be one of: random, best')
    return normalized_mode


def normalize_landing_player_limit(requested_limit: int | None) -> int:
    try:
        parsed_limit = int(requested_limit or LANDING_PLAYER_LIMIT)
    except (TypeError, ValueError):
        parsed_limit = LANDING_PLAYER_LIMIT

    return max(1, min(parsed_limit, LANDING_PLAYER_LIMIT))


def normalize_landing_clan_limit(requested_limit: int | None) -> int:
    try:
        parsed_limit = int(requested_limit or LANDING_CLAN_FEATURED_COUNT)
    except (TypeError, ValueError):
        parsed_limit = LANDING_CLAN_FEATURED_COUNT

    return max(1, min(parsed_limit, LANDING_CLAN_FEATURED_COUNT))


def invalidate_landing_clan_caches(realm: str = DEFAULT_REALM) -> None:
    _mark_cache_family_dirty(
        realm_cache_key(realm, LANDING_CLANS_DIRTY_KEY),
        realm_cache_key(realm, LANDING_RECENT_CLANS_DIRTY_KEY),
    )
    _queue_landing_republish(realm=realm)


RECENT_PLAYERS_INVALIDATE_COOLDOWN = 30  # seconds — coalesce rapid lookups


def invalidate_landing_recent_player_cache(realm: str = DEFAULT_REALM) -> None:
    cooldown_key = realm_cache_key(
        realm, 'landing:recent_players:invalidate_cooldown')
    _mark_cache_family_dirty(realm_cache_key(
        realm, LANDING_RECENT_PLAYERS_DIRTY_KEY))
    if not cache.add(cooldown_key, 1, timeout=RECENT_PLAYERS_INVALIDATE_COOLDOWN):
        return
    _queue_landing_republish(realm=realm)


def invalidate_landing_player_caches(include_recent: bool = False, realm: str = DEFAULT_REALM) -> None:
    dirty_keys = [realm_cache_key(realm, LANDING_PLAYERS_DIRTY_KEY)]
    if include_recent:
        dirty_keys.append(realm_cache_key(
            realm, LANDING_RECENT_PLAYERS_DIRTY_KEY))
    _mark_cache_family_dirty(*dirty_keys)
    _queue_landing_republish(realm=realm)


def _normalize_cached_id_list(raw_value) -> list[int]:
    if not isinstance(raw_value, list):
        return []

    normalized_ids: list[int] = []
    seen_ids: set[int] = set()
    for value in raw_value:
        try:
            parsed_value = int(value)
        except (TypeError, ValueError):
            continue

        if parsed_value in seen_ids:
            continue

        seen_ids.add(parsed_value)
        normalized_ids.append(parsed_value)

    return normalized_ids


def _get_random_landing_player_queue(realm: str = DEFAULT_REALM) -> list[int]:
    return _normalize_cached_id_list(cache.get(realm_cache_key(realm, LANDING_RANDOM_PLAYER_QUEUE_KEY)))


def _set_random_landing_player_queue(player_ids: list[int], realm: str = DEFAULT_REALM) -> None:
    cache.set(
        realm_cache_key(realm, LANDING_RANDOM_PLAYER_QUEUE_KEY),
        _normalize_cached_id_list(player_ids),
        timeout=None,
    )


def _build_random_landing_player_eligible_ids(realm: str = DEFAULT_REALM) -> list[int]:
    return list(
        Player.objects.exclude(name='').filter(
            realm=realm,
            is_hidden=False,
            days_since_last_battle__lte=180,
            pvp_battles__gt=LANDING_PLAYER_RANDOM_MIN_PVP_BATTLES,
        ).exclude(
            last_battle_date__isnull=True,
        ).values_list('player_id', flat=True)
    )


def _get_cached_random_landing_player_eligible_ids(force_refresh: bool = False, realm: str = DEFAULT_REALM) -> list[int]:
    eligible_key = realm_cache_key(
        realm, LANDING_RANDOM_PLAYER_QUEUE_ELIGIBLE_KEY)
    cached_ids = None if force_refresh else cache.get(eligible_key)
    normalized_ids = _normalize_cached_id_list(cached_ids)
    if normalized_ids:
        return normalized_ids

    eligible_ids = _build_random_landing_player_eligible_ids(realm=realm)
    cache.set(
        eligible_key,
        eligible_ids,
        LANDING_RANDOM_PLAYER_QUEUE_ELIGIBLE_TTL,
    )
    return eligible_ids


def _acquire_random_landing_player_queue_lock(attempts: int = 5, sleep_seconds: float = 0.02, realm: str = DEFAULT_REALM) -> bool:
    lock_key = realm_cache_key(realm, LANDING_RANDOM_PLAYER_QUEUE_LOCK_KEY)
    for attempt in range(attempts):
        if cache.add(lock_key, 'locked', timeout=LANDING_RANDOM_PLAYER_QUEUE_LOCK_TIMEOUT):
            return True
        if attempt < attempts - 1:
            time.sleep(sleep_seconds)
    return False


def _release_random_landing_player_queue_lock(realm: str = DEFAULT_REALM) -> None:
    cache.delete(realm_cache_key(realm, LANDING_RANDOM_PLAYER_QUEUE_LOCK_KEY))


def _get_random_landing_clan_queue(realm: str = DEFAULT_REALM) -> list[int]:
    return _normalize_cached_id_list(cache.get(realm_cache_key(realm, LANDING_RANDOM_CLAN_QUEUE_KEY)))


def _set_random_landing_clan_queue(clan_ids: list[int], realm: str = DEFAULT_REALM) -> None:
    cache.set(
        realm_cache_key(realm, LANDING_RANDOM_CLAN_QUEUE_KEY),
        _normalize_cached_id_list(clan_ids),
        timeout=None,
    )


def _build_random_landing_clan_eligible_ids(realm: str = DEFAULT_REALM) -> list[int]:
    return list(
        Clan.objects.exclude(name__isnull=True).exclude(name='').filter(
            realm=realm,
        ).annotate(
            **_clan_agg_annotations(),
        ).annotate(
            **_clan_wr_annotation(),
        ).filter(
            total_battles__gte=LANDING_CLAN_MIN_TOTAL_BATTLES,
            clan_wr__isnull=False,
        ).values_list('clan_id', flat=True)
    )


def _get_cached_random_landing_clan_eligible_ids(force_refresh: bool = False, realm: str = DEFAULT_REALM) -> list[int]:
    eligible_key = realm_cache_key(
        realm, LANDING_RANDOM_CLAN_QUEUE_ELIGIBLE_KEY)
    cached_ids = None if force_refresh else cache.get(eligible_key)
    normalized_ids = _normalize_cached_id_list(cached_ids)
    if normalized_ids:
        return normalized_ids

    eligible_ids = _build_random_landing_clan_eligible_ids(realm=realm)
    cache.set(
        eligible_key,
        eligible_ids,
        LANDING_RANDOM_CLAN_QUEUE_ELIGIBLE_TTL,
    )
    return eligible_ids


def _acquire_random_landing_clan_queue_lock(attempts: int = 5, sleep_seconds: float = 0.02, realm: str = DEFAULT_REALM) -> bool:
    lock_key = realm_cache_key(realm, LANDING_RANDOM_CLAN_QUEUE_LOCK_KEY)
    for attempt in range(attempts):
        if cache.add(lock_key, 'locked', timeout=LANDING_RANDOM_CLAN_QUEUE_LOCK_TIMEOUT):
            return True
        if attempt < attempts - 1:
            time.sleep(sleep_seconds)
    return False


def _release_random_landing_clan_queue_lock(realm: str = DEFAULT_REALM) -> None:
    cache.delete(realm_cache_key(realm, LANDING_RANDOM_CLAN_QUEUE_LOCK_KEY))


def _extend_random_landing_clan_queue(
    queue_ids: list[int],
    *,
    batch_size: int,
    target_size: int,
    force_eligible_refresh: bool = False,
    realm: str = DEFAULT_REALM,
) -> tuple[list[int], int]:
    normalized_queue = _normalize_cached_id_list(queue_ids)
    max_additions = min(batch_size, max(
        target_size - len(normalized_queue), 0))
    if max_additions <= 0:
        return normalized_queue, 0

    eligible_ids = _get_cached_random_landing_clan_eligible_ids(
        force_refresh=force_eligible_refresh, realm=realm)
    queued_ids = set(normalized_queue)
    available_ids = [
        clan_id for clan_id in eligible_ids if clan_id not in queued_ids]

    if not available_ids and not force_eligible_refresh:
        eligible_ids = _get_cached_random_landing_clan_eligible_ids(
            force_refresh=True, realm=realm)
        available_ids = [
            clan_id for clan_id in eligible_ids if clan_id not in queued_ids]

    if not available_ids:
        return normalized_queue, 0

    random.shuffle(available_ids)
    additions = available_ids[:max_additions]
    normalized_queue.extend(additions)
    return normalized_queue, len(additions)


def ensure_random_landing_clan_queue_ready(
    minimum_size: int = LANDING_CLAN_FEATURED_COUNT,
    target_size: int = LANDING_RANDOM_CLAN_QUEUE_TARGET_SIZE,
    realm: str = DEFAULT_REALM,
) -> int:
    current_queue = _get_random_landing_clan_queue(realm=realm)
    if len(current_queue) >= minimum_size:
        return len(current_queue)

    if not _acquire_random_landing_clan_queue_lock(realm=realm):
        return len(_get_random_landing_clan_queue(realm=realm))

    try:
        current_queue = _get_random_landing_clan_queue(realm=realm)
        if len(current_queue) < minimum_size:
            current_queue, _ = _extend_random_landing_clan_queue(
                current_queue,
                batch_size=target_size,
                target_size=target_size,
                force_eligible_refresh=not current_queue,
                realm=realm,
            )
            _set_random_landing_clan_queue(current_queue, realm=realm)
        return len(current_queue)
    finally:
        _release_random_landing_clan_queue_lock(realm=realm)


def peek_random_landing_clan_ids(limit: int = LANDING_CLAN_FEATURED_COUNT, realm: str = DEFAULT_REALM) -> tuple[list[int], int]:
    normalized_limit = normalize_landing_clan_limit(limit)
    ensure_random_landing_clan_queue_ready(
        minimum_size=normalized_limit, realm=realm)
    queue_ids = _get_random_landing_clan_queue(realm=realm)
    return queue_ids[:normalized_limit], len(queue_ids)


def pop_random_landing_clan_ids(limit: int = LANDING_CLAN_FEATURED_COUNT, realm: str = DEFAULT_REALM) -> tuple[list[int], int]:
    normalized_limit = normalize_landing_clan_limit(limit)
    ensure_random_landing_clan_queue_ready(
        minimum_size=normalized_limit, realm=realm)

    if not _acquire_random_landing_clan_queue_lock(realm=realm):
        queue_ids = _get_random_landing_clan_queue(realm=realm)
        served_ids = queue_ids[:normalized_limit]
        remaining_count = max(len(queue_ids) - len(served_ids), 0)
        return served_ids, remaining_count

    try:
        queue_ids = _get_random_landing_clan_queue(realm=realm)
        if len(queue_ids) < normalized_limit:
            queue_ids, _ = _extend_random_landing_clan_queue(
                queue_ids,
                batch_size=LANDING_RANDOM_CLAN_QUEUE_TARGET_SIZE,
                target_size=LANDING_RANDOM_CLAN_QUEUE_TARGET_SIZE,
                force_eligible_refresh=not queue_ids,
                realm=realm,
            )

        served_ids = queue_ids[:normalized_limit]
        remaining_ids = queue_ids[normalized_limit:]
        _set_random_landing_clan_queue(remaining_ids, realm=realm)
        return served_ids, len(remaining_ids)
    finally:
        _release_random_landing_clan_queue_lock(realm=realm)


def refill_random_landing_clan_queue(
    batch_size: int = LANDING_RANDOM_CLAN_QUEUE_REFILL_SIZE,
    target_size: int = LANDING_RANDOM_CLAN_QUEUE_TARGET_SIZE,
    realm: str = DEFAULT_REALM,
) -> dict[str, int | str]:
    lock_key = realm_cache_key(realm, LANDING_RANDOM_CLAN_QUEUE_LOCK_KEY)
    if not cache.add(lock_key, 'locked', timeout=LANDING_RANDOM_CLAN_QUEUE_LOCK_TIMEOUT):
        return {
            'status': 'skipped',
            'reason': 'already-running',
            'added': 0,
            'queue_depth': len(_get_random_landing_clan_queue(realm=realm)),
        }

    try:
        queue_ids = _get_random_landing_clan_queue(realm=realm)
        queue_ids, added_count = _extend_random_landing_clan_queue(
            queue_ids,
            batch_size=batch_size,
            target_size=target_size,
            realm=realm,
        )
        _set_random_landing_clan_queue(queue_ids, realm=realm)
        return {
            'status': 'completed',
            'added': added_count,
            'queue_depth': len(queue_ids),
        }
    finally:
        _release_random_landing_clan_queue_lock(realm=realm)


def resolve_landing_clans_by_id_order(clan_ids: list[int], realm: str = DEFAULT_REALM) -> list[dict]:
    normalized_ids = _normalize_cached_id_list(clan_ids)
    if not normalized_ids:
        return []

    selected_order = {
        clan_id: index for index, clan_id in enumerate(normalized_ids)
    }
    rows = list(
        Clan.objects.exclude(name__isnull=True).exclude(name='').filter(
            realm=realm,
            clan_id__in=normalized_ids,
        ).annotate(
            **_clan_agg_annotations(),
        ).annotate(
            **_clan_wr_annotation(),
        ).filter(
            total_battles__gte=LANDING_CLAN_MIN_TOTAL_BATTLES,
            clan_wr__isnull=False,
        ).values(
            'clan_id', 'name', 'tag', 'members_count', 'clan_wr', 'total_battles', 'active_members'
        )
    )
    rows.sort(key=lambda row: selected_order.get(
        int(row.get('clan_id') or 0), len(selected_order)))
    return rows


def _build_best_landing_clans(limit: int = LANDING_CLAN_FEATURED_COUNT, realm: str = DEFAULT_REALM) -> list[dict]:
    best_clan_ids = score_best_clans(limit=limit, realm=realm)
    if not best_clan_ids:
        return []

    rows = list(
        Clan.objects.filter(realm=realm, clan_id__in=best_clan_ids).annotate(
            **_clan_agg_annotations(),
        ).annotate(
            **_clan_wr_annotation(),
        ).values(
            'clan_id', 'name', 'tag', 'members_count', 'clan_wr', 'total_battles', 'active_members'
        )
    )

    # Preserve the score_best_clans ordering
    id_order = {cid: i for i, cid in enumerate(best_clan_ids)}
    rows.sort(key=lambda row: id_order.get(row['clan_id'], len(best_clan_ids)))
    return rows


def get_landing_best_clans_payload_with_cache_metadata(force_refresh: bool = False, realm: str = DEFAULT_REALM) -> tuple[list[dict], dict[str, str | int]]:
    ttl_seconds = LANDING_CLAN_CACHE_TTL
    cache_key = realm_cache_key(realm, LANDING_CLANS_BEST_CACHE_KEY)
    metadata_key = realm_cache_key(
        realm, LANDING_CLANS_BEST_CACHE_METADATA_KEY)
    published_cache_key = realm_cache_key(
        realm, LANDING_CLANS_BEST_PUBLISHED_CACHE_KEY)
    published_metadata_key = realm_cache_key(
        realm, LANDING_CLANS_BEST_PUBLISHED_METADATA_KEY)

    payload, metadata = _get_cached_landing_payload_with_fallback(
        cache_key,
        metadata_key,
        published_cache_key,
        published_metadata_key,
        ttl_seconds,
        force_refresh,
        realm=realm,
    )

    if payload is None:
        payload = _build_best_landing_clans(
            LANDING_CLAN_FEATURED_COUNT, realm=realm)
        metadata = _build_landing_player_cache_metadata(ttl_seconds)
        _publish_landing_payload(
            cache_key,
            metadata_key,
            published_cache_key,
            published_metadata_key,
            payload,
            metadata,
            ttl_seconds,
        )

    return payload, metadata


def _get_random_landing_clan_preview(realm: str = DEFAULT_REALM) -> dict | None:
    preview = cache.get(realm_cache_key(
        realm, LANDING_RANDOM_CLAN_QUEUE_PREVIEW_KEY))
    if not isinstance(preview, dict):
        return None
    return preview


def warm_random_landing_clan_queue_preview(limit: int = LANDING_CLAN_FEATURED_COUNT, realm: str = DEFAULT_REALM) -> tuple[list[dict], dict[str, str | int | bool]]:
    clan_ids, queue_remaining = peek_random_landing_clan_ids(
        limit, realm=realm)
    payload = resolve_landing_clans_by_id_order(clan_ids, realm=realm)
    metadata = _build_landing_player_cache_metadata(0)
    metadata.update({
        'queue_remaining': queue_remaining,
        'served_count': len(payload),
        'refill_scheduled': False,
    })
    cache.set(
        realm_cache_key(realm, LANDING_RANDOM_CLAN_QUEUE_PREVIEW_KEY),
        {
            'ids': clan_ids,
            'payload': payload,
            'metadata': metadata,
        },
        LANDING_CACHE_TTL,
    )
    return payload, metadata


def get_random_landing_clan_queue_payload(
    limit: int = LANDING_CLAN_FEATURED_COUNT,
    *,
    pop: bool,
    schedule_refill: bool = True,
    warm_preview: bool = False,
    realm: str = DEFAULT_REALM,
) -> tuple[list[dict], dict[str, str | int | bool]]:
    normalized_limit = normalize_landing_clan_limit(limit)
    if pop:
        clan_ids, queue_remaining = pop_random_landing_clan_ids(
            normalized_limit, realm=realm)
    else:
        clan_ids, queue_remaining = peek_random_landing_clan_ids(
            normalized_limit, realm=realm)

    refill_scheduled = False
    if schedule_refill and queue_remaining < LANDING_RANDOM_CLAN_QUEUE_REFILL_THRESHOLD:
        from warships.tasks import queue_random_landing_clan_queue_refill

        refill_result = queue_random_landing_clan_queue_refill(realm=realm)
        refill_scheduled = refill_result.get('status') == 'queued'

    payload = None
    preview = _get_random_landing_clan_preview(realm=realm)
    if preview and _normalize_cached_id_list(preview.get('ids')) == clan_ids:
        payload = preview.get('payload')

    if not isinstance(payload, list):
        payload = resolve_landing_clans_by_id_order(clan_ids, realm=realm)

    metadata = _build_landing_player_cache_metadata(0)
    metadata.update({
        'queue_remaining': queue_remaining,
        'served_count': len(payload),
        'refill_scheduled': refill_scheduled,
    })

    if warm_preview and not pop:
        cache.set(
            realm_cache_key(realm, LANDING_RANDOM_CLAN_QUEUE_PREVIEW_KEY),
            {
                'ids': clan_ids,
                'payload': payload,
                'metadata': metadata,
            },
            LANDING_CACHE_TTL,
        )

    return payload, metadata


def _extend_random_landing_player_queue(
    queue_ids: list[int],
    *,
    batch_size: int,
    target_size: int,
    force_eligible_refresh: bool = False,
    realm: str = DEFAULT_REALM,
) -> tuple[list[int], int]:
    normalized_queue = _normalize_cached_id_list(queue_ids)
    max_additions = min(batch_size, max(
        target_size - len(normalized_queue), 0))
    if max_additions <= 0:
        return normalized_queue, 0

    eligible_ids = _get_cached_random_landing_player_eligible_ids(
        force_refresh=force_eligible_refresh, realm=realm)
    queued_ids = set(normalized_queue)
    available_ids = [
        player_id for player_id in eligible_ids if player_id not in queued_ids]

    if not available_ids and not force_eligible_refresh:
        eligible_ids = _get_cached_random_landing_player_eligible_ids(
            force_refresh=True, realm=realm)
        available_ids = [
            player_id for player_id in eligible_ids if player_id not in queued_ids]

    if not available_ids:
        return normalized_queue, 0

    random.shuffle(available_ids)
    additions = available_ids[:max_additions]
    normalized_queue.extend(additions)
    return normalized_queue, len(additions)


def ensure_random_landing_player_queue_ready(
    minimum_size: int = LANDING_PLAYER_LIMIT,
    target_size: int = LANDING_RANDOM_PLAYER_QUEUE_TARGET_SIZE,
    realm: str = DEFAULT_REALM,
) -> int:
    current_queue = _get_random_landing_player_queue(realm=realm)
    if len(current_queue) >= minimum_size:
        return len(current_queue)

    if not _acquire_random_landing_player_queue_lock(realm=realm):
        return len(_get_random_landing_player_queue(realm=realm))

    try:
        current_queue = _get_random_landing_player_queue(realm=realm)
        if len(current_queue) < minimum_size:
            current_queue, _ = _extend_random_landing_player_queue(
                current_queue,
                batch_size=target_size,
                target_size=target_size,
                force_eligible_refresh=not current_queue,
                realm=realm,
            )
            _set_random_landing_player_queue(current_queue, realm=realm)
        return len(current_queue)
    finally:
        _release_random_landing_player_queue_lock(realm=realm)


def peek_random_landing_player_ids(limit: int = LANDING_PLAYER_LIMIT, realm: str = DEFAULT_REALM) -> tuple[list[int], int]:
    normalized_limit = normalize_landing_player_limit(limit)
    ensure_random_landing_player_queue_ready(
        minimum_size=normalized_limit, realm=realm)
    queue_ids = _get_random_landing_player_queue(realm=realm)
    return queue_ids[:normalized_limit], len(queue_ids)


def pop_random_landing_player_ids(limit: int = LANDING_PLAYER_LIMIT, realm: str = DEFAULT_REALM) -> tuple[list[int], int]:
    normalized_limit = normalize_landing_player_limit(limit)
    ensure_random_landing_player_queue_ready(
        minimum_size=normalized_limit, realm=realm)

    if not _acquire_random_landing_player_queue_lock(realm=realm):
        queue_ids = _get_random_landing_player_queue(realm=realm)
        served_ids = queue_ids[:normalized_limit]
        remaining_count = max(len(queue_ids) - len(served_ids), 0)
        return served_ids, remaining_count

    try:
        queue_ids = _get_random_landing_player_queue(realm=realm)
        if len(queue_ids) < normalized_limit:
            queue_ids, _ = _extend_random_landing_player_queue(
                queue_ids,
                batch_size=LANDING_RANDOM_PLAYER_QUEUE_TARGET_SIZE,
                target_size=LANDING_RANDOM_PLAYER_QUEUE_TARGET_SIZE,
                force_eligible_refresh=not queue_ids,
                realm=realm,
            )

        served_ids = queue_ids[:normalized_limit]
        remaining_ids = queue_ids[normalized_limit:]
        _set_random_landing_player_queue(remaining_ids, realm=realm)
        return served_ids, len(remaining_ids)
    finally:
        _release_random_landing_player_queue_lock(realm=realm)


def refill_random_landing_player_queue(
    batch_size: int = LANDING_RANDOM_PLAYER_QUEUE_REFILL_SIZE,
    target_size: int = LANDING_RANDOM_PLAYER_QUEUE_TARGET_SIZE,
    realm: str = DEFAULT_REALM,
) -> dict[str, int | str]:
    lock_key = realm_cache_key(realm, LANDING_RANDOM_PLAYER_QUEUE_LOCK_KEY)
    if not cache.add(lock_key, 'locked', timeout=LANDING_RANDOM_PLAYER_QUEUE_LOCK_TIMEOUT):
        return {
            'status': 'skipped',
            'reason': 'already-running',
            'added': 0,
            'queue_depth': len(_get_random_landing_player_queue(realm=realm)),
        }

    try:
        queue_ids = _get_random_landing_player_queue(realm=realm)
        queue_ids, added_count = _extend_random_landing_player_queue(
            queue_ids,
            batch_size=batch_size,
            target_size=target_size,
            realm=realm,
        )
        _set_random_landing_player_queue(queue_ids, realm=realm)
        return {
            'status': 'completed',
            'added': added_count,
            'queue_depth': len(queue_ids),
        }
    finally:
        _release_random_landing_player_queue_lock(realm=realm)


def resolve_landing_players_by_id_order(player_ids: list[int], realm: str = DEFAULT_REALM) -> list[dict]:
    normalized_ids = _normalize_cached_id_list(player_ids)
    if not normalized_ids:
        return []

    selected_order = {
        player_id: index for index, player_id in enumerate(normalized_ids)
    }
    rows = list(
        Player.objects.exclude(name='').filter(
            realm=realm,
            player_id__in=normalized_ids,
            is_hidden=False,
            days_since_last_battle__lte=180,
            pvp_battles__gt=LANDING_PLAYER_RANDOM_MIN_PVP_BATTLES,
        ).exclude(
            last_battle_date__isnull=True,
        ).values(
            'name', 'player_id', 'pvp_ratio', 'is_hidden', 'days_since_last_battle', 'total_battles', 'pvp_battles', 'battles_json', 'ranked_json'
        )
    )
    rows.sort(key=lambda row: selected_order.get(
        int(row.get('player_id') or 0), len(selected_order)))
    return _serialize_landing_player_rows(rows)


def get_random_landing_player_queue_payload(
    limit: int = LANDING_PLAYER_LIMIT,
    *,
    pop: bool,
    schedule_refill: bool = True,
    realm: str = DEFAULT_REALM,
) -> tuple[list[dict], dict[str, str | int | bool]]:
    normalized_limit = normalize_landing_player_limit(limit)
    if pop:
        player_ids, queue_remaining = pop_random_landing_player_ids(
            normalized_limit, realm=realm)
    else:
        player_ids, queue_remaining = peek_random_landing_player_ids(
            normalized_limit, realm=realm)

    refill_scheduled = False
    if schedule_refill and queue_remaining < LANDING_RANDOM_PLAYER_QUEUE_REFILL_THRESHOLD:
        from warships.tasks import queue_random_landing_player_queue_refill

        refill_result = queue_random_landing_player_queue_refill(realm=realm)
        refill_scheduled = refill_result.get('status') == 'queued'

    payload = resolve_landing_players_by_id_order(player_ids, realm=realm)
    metadata = _build_landing_player_cache_metadata(0)
    metadata.update({
        'queue_remaining': queue_remaining,
        'served_count': len(payload),
        'refill_scheduled': refill_scheduled,
    })
    return payload, metadata


def _serialize_landing_player_rows(rows: list[dict]) -> list[dict]:
    player_ids = [int(row.get('player_id') or 0)
                  for row in rows if row.get('player_id') is not None]
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
            'explorer_summary__clan_battle_seasons_participated',
            'explorer_summary__clan_battle_total_battles',
            'explorer_summary__clan_battle_overall_win_rate',
            'explorer_summary__clan_battle_summary_updated_at',
        )
    }

    for row in rows:
        player_id = int(row.get('player_id') or 0)
        high_tier_battles, high_tier_ratio = _calculate_tier_filtered_pvp_record(
            row.pop('battles_json', None),
            minimum_tier=5,
        )
        ranked_rows = row.pop('ranked_json', None)
        player_obj = players_by_id.get(player_id)
        es = getattr(player_obj, 'explorer_summary',
                     None) if player_obj else None
        row['high_tier_pvp_battles'] = high_tier_battles
        row['high_tier_pvp_ratio'] = high_tier_ratio
        row['is_pve_player'] = is_pve_player(
            row.get('total_battles'), row.get('pvp_battles'))
        row['is_sleepy_player'] = is_sleepy_player(
            row.get('days_since_last_battle'))
        row['is_ranked_player'] = is_ranked_player(ranked_rows)
        row['is_clan_battle_player'] = is_clan_battle_enjoyer(
            getattr(es, 'clan_battle_total_battles', None),
            getattr(es, 'clan_battle_seasons_participated', None),
        )
        row['clan_battle_win_rate'] = getattr(
            es, 'clan_battle_overall_win_rate', None)
        row['highest_ranked_league'] = get_highest_ranked_league_name(
            ranked_rows)
        # Use stored percentile directly — landing surfaces tolerate minor
        # input-data drift (unlike player detail, which uses the stricter
        # _get_published_efficiency_rank_payload freshness gate).
        if es and es.efficiency_rank_percentile is not None:
            row['efficiency_rank_percentile'] = es.efficiency_rank_percentile
            row['efficiency_rank_tier'] = es.efficiency_rank_tier
            row['has_efficiency_rank_icon'] = bool(es.has_efficiency_rank_icon)
            row['efficiency_rank_population_size'] = es.efficiency_rank_population_size
            row['efficiency_rank_updated_at'] = es.efficiency_rank_updated_at
        else:
            row['efficiency_rank_percentile'] = None
            row['efficiency_rank_tier'] = None
            row['has_efficiency_rank_icon'] = False
            row['efficiency_rank_population_size'] = None
            row['efficiency_rank_updated_at'] = None
        row.pop('player_id', None)
        row.pop('days_since_last_battle', None)

    return rows


def _build_landing_clans(realm: str = DEFAULT_REALM) -> list[dict]:
    qs = Clan.objects.exclude(name__isnull=True).exclude(name='').filter(realm=realm).annotate(
        **_clan_agg_annotations(),
    ).annotate(
        **_clan_wr_annotation(),
    ).values(
        'clan_id', 'name', 'tag', 'members_count', 'clan_wr', 'total_battles', 'active_members'
    ).order_by(F('last_lookup').desc(nulls_last=True))
    return _prioritize_landing_clans(list(qs))


def get_landing_clans_payload(force_refresh: bool = False, realm: str = DEFAULT_REALM) -> list[dict]:
    payload, _ = get_landing_clans_payload_with_cache_metadata(
        force_refresh=force_refresh, realm=realm)
    return payload


def get_landing_best_clans_payload(force_refresh: bool = False, realm: str = DEFAULT_REALM) -> list[dict]:
    payload, _ = get_landing_best_clans_payload_with_cache_metadata(
        force_refresh=force_refresh, realm=realm)
    return payload


def _build_recent_clans(realm: str = DEFAULT_REALM) -> list[dict]:
    return list(
        Clan.objects.exclude(name__isnull=True).exclude(name='').filter(
            realm=realm,
        ).exclude(
            last_lookup__isnull=True
        ).annotate(
            **_clan_agg_annotations(),
        ).annotate(
            **_clan_wr_annotation(),
        ).values(
            'clan_id', 'name', 'tag', 'members_count', 'clan_wr', 'total_battles'
        ).order_by(
            F('last_lookup').desc(nulls_last=True),
            'name',
        )[:40]
    )


def get_landing_recent_clans_payload(force_refresh: bool = False, realm: str = DEFAULT_REALM) -> list[dict]:
    dirty_key = realm_cache_key(realm, LANDING_RECENT_CLANS_DIRTY_KEY)
    cache_key = realm_cache_key(realm, LANDING_RECENT_CLANS_CACHE_KEY)
    is_dirty = not force_refresh and cache.get(dirty_key) is not None
    payload = None if force_refresh or is_dirty else cache.get(cache_key)
    if payload is None:
        payload = _build_recent_clans(realm=realm)
        cache.set(cache_key, payload, LANDING_CACHE_TTL)
        if is_dirty:
            cache.delete(dirty_key)
    return payload


def _build_random_landing_players(limit: int, realm: str = DEFAULT_REALM) -> list[dict]:
    eligible_ids = list(
        Player.objects.exclude(name='').filter(
            realm=realm,
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


def _build_best_landing_players(limit: int, realm: str = DEFAULT_REALM) -> list[dict]:
    candidate_rows = list(
        Player.objects.exclude(name='').filter(
            realm=realm,
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


def _build_sigma_landing_players(limit: int, realm: str = DEFAULT_REALM) -> list[dict]:
    players = list(
        Player.objects.exclude(name='').filter(
            realm=realm,
            is_hidden=False,
            days_since_last_battle__lte=180,
            pvp_battles__gt=LANDING_PLAYER_SIGMA_MIN_PVP_BATTLES,
            explorer_summary__efficiency_rank_percentile__isnull=False,
        ).exclude(
            last_battle_date__isnull=True,
        ).select_related('explorer_summary').only(
            'name',
            'player_id',
            'pvp_ratio',
            'is_hidden',
            'days_since_last_battle',
            'total_battles',
            'pvp_battles',
            'explorer_summary__player_score',
            'explorer_summary__efficiency_rank_percentile',
            'explorer_summary__efficiency_rank_tier',
            'explorer_summary__has_efficiency_rank_icon',
            'explorer_summary__efficiency_rank_population_size',
            'explorer_summary__efficiency_rank_updated_at',
            'explorer_summary__eligible_ship_count',
            'explorer_summary__efficiency_badge_rows_total',
            'explorer_summary__badge_rows_unmapped',
            'explorer_summary__latest_ranked_battles',
            'explorer_summary__highest_ranked_league_recent',
            'explorer_summary__clan_battle_seasons_participated',
            'explorer_summary__clan_battle_total_battles',
            'explorer_summary__clan_battle_overall_win_rate',
            'explorer_summary__clan_battle_summary_updated_at',
        ).order_by(
            F('explorer_summary__efficiency_rank_percentile').desc(nulls_last=True),
            F('explorer_summary__player_score').desc(nulls_last=True),
            F('pvp_ratio').desc(nulls_last=True),
            'name',
        )[:limit]
    )

    rows = []
    for player in players:
        explorer_summary = getattr(player, 'explorer_summary', None)
        if explorer_summary is None:
            continue

        # Use stored percentile directly — the landing surface tolerates
        # minor input-data drift (unlike individual player pages, which
        # use the stricter _get_published_efficiency_rank_payload check).
        percentile = explorer_summary.efficiency_rank_percentile
        if percentile is None:
            continue

        rows.append({
            'name': player.name,
            'pvp_ratio': player.pvp_ratio,
            'is_hidden': player.is_hidden,
            'pvp_battles': player.pvp_battles,
            'total_battles': player.total_battles,
            'days_since_last_battle': player.days_since_last_battle,
            'is_pve_player': is_pve_player(player.total_battles, player.pvp_battles),
            'is_sleepy_player': is_sleepy_player(player.days_since_last_battle),
            'is_ranked_player': max(int(getattr(explorer_summary, 'latest_ranked_battles', 0) or 0), 0) > 0,
            'highest_ranked_league': getattr(explorer_summary, 'highest_ranked_league_recent', None),
            'is_clan_battle_player': is_clan_battle_enjoyer(
                getattr(explorer_summary, 'clan_battle_total_battles', None),
                getattr(explorer_summary,
                        'clan_battle_seasons_participated', None),
            ),
            'clan_battle_win_rate': getattr(explorer_summary, 'clan_battle_overall_win_rate', None),
            'efficiency_rank_percentile': percentile,
            'efficiency_rank_tier': explorer_summary.efficiency_rank_tier,
            'has_efficiency_rank_icon': bool(explorer_summary.has_efficiency_rank_icon),
            'efficiency_rank_population_size': explorer_summary.efficiency_rank_population_size,
            'efficiency_rank_updated_at': explorer_summary.efficiency_rank_updated_at,
        })

    return rows


def _build_popular_landing_players(limit: int, realm: str = DEFAULT_REALM) -> list[dict]:
    candidate_limit = max(limit * 4, limit)

    try:
        rows = get_top_entities(
            'player', '7d', 'views_deduped', candidate_limit)
    except Exception as error:
        logger.warning(
            'Falling back to empty popular landing players due to analytics error: %s', error)
        return []

    ordered_player_ids: list[int] = []
    seen_player_ids: set[int] = set()
    for row in rows:
        try:
            player_id = int(row.get('entity_id') or 0)
        except (TypeError, ValueError):
            continue
        if player_id <= 0 or player_id in seen_player_ids:
            continue
        seen_player_ids.add(player_id)
        ordered_player_ids.append(player_id)

    return resolve_landing_players_by_id_order(ordered_player_ids, realm=realm)[:limit]


def get_landing_players_payload_with_cache_metadata(mode: str = 'random', limit: int = LANDING_PLAYER_LIMIT, force_refresh: bool = False, realm: str = DEFAULT_REALM) -> tuple[list[dict], dict[str, str | int]]:
    normalized_mode = normalize_landing_player_mode(mode)
    normalized_limit = normalize_landing_player_limit(limit)
    cache_key = landing_player_cache_key(
        normalized_mode, normalized_limit, realm=realm)
    metadata_key = landing_player_cache_metadata_key(
        normalized_mode, normalized_limit, realm=realm)
    published_cache_key = landing_player_published_cache_key(
        normalized_mode, normalized_limit, realm=realm)
    published_metadata_key = landing_player_published_metadata_key(
        normalized_mode, normalized_limit, realm=realm)
    ttl_seconds = landing_player_cache_ttl(normalized_mode)

    if normalized_mode == 'best':
        def builder(lim): return _build_best_landing_players(lim, realm=realm)
    elif normalized_mode == 'sigma':
        def builder(lim): return _build_sigma_landing_players(lim, realm=realm)
    elif normalized_mode == 'popular':
        def builder(lim): return _build_popular_landing_players(
            lim, realm=realm)
    else:
        def builder(lim): return _build_random_landing_players(
            lim, realm=realm)

    payload, metadata = _get_cached_landing_payload_with_fallback(
        cache_key,
        metadata_key,
        published_cache_key,
        published_metadata_key,
        ttl_seconds,
        force_refresh,
        realm=realm,
    )

    if payload is None:
        payload = builder(normalized_limit)
        metadata = _build_landing_player_cache_metadata(ttl_seconds)
        _publish_landing_payload(
            cache_key,
            metadata_key,
            published_cache_key,
            published_metadata_key,
            payload,
            metadata,
            ttl_seconds,
        )

    return payload, metadata


def get_landing_players_payload(mode: str = 'random', limit: int = LANDING_PLAYER_LIMIT, force_refresh: bool = False, realm: str = DEFAULT_REALM) -> list[dict]:
    payload, _ = get_landing_players_payload_with_cache_metadata(
        mode=mode,
        limit=limit,
        force_refresh=force_refresh,
        realm=realm,
    )
    return payload


def _build_recent_players(realm: str = DEFAULT_REALM) -> list[dict]:
    # Single query: fetch lightweight columns + ranked_json, joined with
    # explorer_summary to avoid N+1.
    players = list(
        Player.objects.exclude(name='').filter(
            realm=realm).exclude(last_lookup__isnull=True)
        .select_related('explorer_summary')
        .only(
            'player_id', 'name', 'pvp_ratio', 'days_since_last_battle',
            'total_battles', 'pvp_battles', 'ranked_json',
            'explorer_summary__clan_battle_total_battles',
            'explorer_summary__clan_battle_seasons_participated',
            'explorer_summary__clan_battle_overall_win_rate',
            'explorer_summary__efficiency_rank_percentile',
            'explorer_summary__efficiency_rank_tier',
            'explorer_summary__has_efficiency_rank_icon',
            'explorer_summary__efficiency_rank_population_size',
            'explorer_summary__efficiency_rank_updated_at',
        )
        .order_by(F('last_lookup').desc(nulls_last=True), 'name')[:40]
    )

    rows = []
    for player_obj in players:
        ranked_rows = player_obj.ranked_json
        es = getattr(player_obj, 'explorer_summary', None)
        row = {
            'name': player_obj.name,
            'pvp_ratio': player_obj.pvp_ratio,
            'total_battles': player_obj.total_battles,
            'pvp_battles': player_obj.pvp_battles,
            'is_pve_player': is_pve_player(
                player_obj.total_battles, player_obj.pvp_battles),
            'is_sleepy_player': is_sleepy_player(
                player_obj.days_since_last_battle),
            'is_ranked_player': is_ranked_player(ranked_rows),
            'is_clan_battle_player': is_clan_battle_enjoyer(
                getattr(es, 'clan_battle_total_battles', None),
                getattr(es, 'clan_battle_seasons_participated', None),
            ),
            'clan_battle_win_rate': getattr(
                es, 'clan_battle_overall_win_rate', None),
            'highest_ranked_league': get_highest_ranked_league_name(
                ranked_rows),
        }
        if es and es.efficiency_rank_percentile is not None:
            row['efficiency_rank_percentile'] = es.efficiency_rank_percentile
            row['efficiency_rank_tier'] = es.efficiency_rank_tier
            row['has_efficiency_rank_icon'] = bool(es.has_efficiency_rank_icon)
            row['efficiency_rank_population_size'] = es.efficiency_rank_population_size
            row['efficiency_rank_updated_at'] = es.efficiency_rank_updated_at
        else:
            row['efficiency_rank_percentile'] = None
            row['efficiency_rank_tier'] = None
            row['has_efficiency_rank_icon'] = False
            row['efficiency_rank_population_size'] = None
            row['efficiency_rank_updated_at'] = None
        rows.append(row)

    return rows


def get_landing_recent_players_payload(force_refresh: bool = False, realm: str = DEFAULT_REALM) -> list[dict]:
    dirty_key = realm_cache_key(realm, LANDING_RECENT_PLAYERS_DIRTY_KEY)
    cache_key = realm_cache_key(realm, LANDING_RECENT_PLAYERS_CACHE_KEY)
    is_dirty = not force_refresh and cache.get(dirty_key) is not None
    payload = None if force_refresh or is_dirty else cache.get(cache_key)
    if payload is None:
        payload = _build_recent_players(realm=realm)
        cache.set(cache_key, payload, LANDING_CACHE_TTL)
        if is_dirty:
            cache.delete(dirty_key)
    return payload


def warm_landing_page_content(force_refresh: bool = False, include_recent: bool = True, realm: str = DEFAULT_REALM) -> dict:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    surfaces = {
        'players_random': lambda: len(get_landing_players_payload('random', LANDING_PLAYER_LIMIT, force_refresh=force_refresh, realm=realm)),
        'players_best': lambda: len(get_landing_players_payload('best', LANDING_PLAYER_LIMIT, force_refresh=force_refresh, realm=realm)),
        'players_sigma': lambda: len(get_landing_players_payload('sigma', LANDING_PLAYER_LIMIT, force_refresh=force_refresh, realm=realm)),
        'players_popular': lambda: len(get_landing_players_payload('popular', LANDING_PLAYER_LIMIT, force_refresh=force_refresh, realm=realm)),
        'clans': lambda: len(get_landing_clans_payload(force_refresh=force_refresh, realm=realm)),
        'clans_best': lambda: len(get_landing_best_clans_payload(force_refresh=force_refresh, realm=realm)),
        'recent_clans': lambda: len(get_landing_recent_clans_payload(force_refresh=force_refresh if include_recent else False, realm=realm)),
        'recent_players': lambda: len(get_landing_recent_players_payload(force_refresh=force_refresh if include_recent else False, realm=realm)),
    }

    def _run_surface(fn):
        from django.db import close_old_connections
        close_old_connections()
        try:
            return fn()
        finally:
            close_old_connections()

    warmed = {}
    from django.conf import settings
    parallel = getattr(settings, 'LANDING_WARM_PARALLEL', True)
    if parallel:
        with ThreadPoolExecutor(max_workers=4) as pool:
            future_to_name = {pool.submit(
                _run_surface, fn): name for name, fn in surfaces.items()}
            for future in as_completed(future_to_name):
                name = future_to_name[future]
                try:
                    warmed[name] = future.result()
                except Exception:
                    logging.exception(
                        "Failed to warm landing surface %s", name)
                    warmed[name] = 0
    else:
        for name, fn in surfaces.items():
            warmed[name] = fn()

    _clear_cache_family_dirty(
        realm_cache_key(realm, LANDING_CLANS_DIRTY_KEY),
        realm_cache_key(realm, LANDING_PLAYERS_DIRTY_KEY),
        realm_cache_key(realm, LANDING_RECENT_CLANS_DIRTY_KEY),
        realm_cache_key(realm, LANDING_RECENT_PLAYERS_DIRTY_KEY),
    )
    return {'status': 'completed', 'warmed': warmed}
