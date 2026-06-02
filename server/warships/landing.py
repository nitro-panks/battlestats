import gc
import json
import logging
import os
import random
import time
from datetime import timedelta
import math

from django.core.cache import cache
from django.db import connection
from django.db.models import Case, Count, F, FloatField, Q, Sum, Value, When
from django.db.models.functions import Cast, Coalesce
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from warships.data import calculate_tier_filtered_pvp_record, get_clan_battle_activity_badge, get_highest_ranked_league_name, is_clan_battle_enjoyer, is_pve_player, is_ranked_player, is_sleepy_player, score_best_clans
from warships.data_support import clamp
from warships.models import Clan, DEFAULT_REALM, LandingPlayerBestSnapshot, LandingRecentPlayersSnapshot, Player, realm_cache_key
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


def _attach_clan_battle_activity_badges(rows: list[dict], realm: str = DEFAULT_REALM) -> list[dict]:
    pending_refresh: list[object] = []
    for row in rows:
        clan_id = row.get('clan_id')
        if clan_id is None:
            row['is_clan_battle_active'] = False
            continue

        badge = get_clan_battle_activity_badge(
            clan_id,
            total_members=int(row.get('members_count') or 0),
            realm=realm,
            cache_only=True,
        )
        row['is_clan_battle_active'] = bool(badge.get('is_clan_battle_active'))
        if badge.get('cache_miss'):
            pending_refresh.append(clan_id)

    if pending_refresh:
        # Cache miss on a hot path: defer the WG API fan-out to the celery
        # background queue so the request thread never blocks. Each clan's
        # refresh has its own dispatch dedup lock inside the helper.
        from warships.tasks import queue_clan_battle_summary_refresh
        for clan_id in pending_refresh:
            try:
                queue_clan_battle_summary_refresh(clan_id, realm=realm)
            except Exception as error:
                logger.warning(
                    'Failed to queue clan battle summary refresh for clan_id=%s: %s',
                    clan_id, error,
                )

    return rows


LANDING_CACHE_TTL = 60 * 60 * 6
LANDING_CLAN_CACHE_TTL = 60 * 60 * 6
LANDING_PLAYER_CACHE_TTL = 60 * 60 * 6
# Recent players is now a 7-day "most-active" rollup over PlayerDailyShipStats,
# rebuilt out-of-band every 3h by `warm_landing_recent_players_task`. The cache
# is durable (no TTL) so reads never trigger rebuilds — that keeps page latency
# flat even while the warmer is computing the next snapshot.
LANDING_RECENT_PLAYERS_CACHE_TTL = None
LANDING_RECENT_PLAYERS_LOOKBACK_DAYS = 7
LANDING_RECENT_PLAYERS_LIMIT = 25
# Activity floor: a player must have played strictly more than this many
# random battles in the trailing lookback window to qualify. Filters out
# "logged in for 3 matches" sessions while keeping anyone who had a real
# session in the last 7 days. See
# `agents/runbooks/runbook-recent-players-recency-filter-2026-05-04.md`.
LANDING_RECENT_PLAYERS_MIN_WEEK_BATTLES = 10
# Sanity ceiling for the trailing 7-day random-battle tally. Even the most
# committed grinders rarely exceed ~60 randoms/day, so anything above this
# cap is almost certainly a "first-observation phantom" emitted by the
# BattleEvent diff lane (where a first observation pair can dump the
# player's lifetime totals into a single huge delta). Filter these out so
# the surface doesn't surface implausible counts to users.
LANDING_RECENT_PLAYERS_MAX_WEEK_BATTLES = 1500
# Tolerance band: a small lag between `pvp_battles` (refreshed by the
# tiered crawler) and the rollup's running sum is expected, so accept
# `week_battles` up to `pvp_battles + slack`. Beyond that the row is
# definitionally bogus (you can't play more battles in a week than you've
# played in your entire account history).
LANDING_RECENT_PLAYERS_PVP_SLACK = 50
LANDING_CLANS_CACHE_KEY = 'landing:clans:v4'
LANDING_CLANS_CACHE_METADATA_KEY = 'landing:clans:v4:meta'
LANDING_CLANS_PUBLISHED_CACHE_KEY = 'landing:clans:v4:published'
LANDING_CLANS_PUBLISHED_METADATA_KEY = 'landing:clans:v4:published:meta'
LANDING_CLANS_BEST_CACHE_KEY = 'landing:clans:best:v2:overall'
LANDING_CLANS_BEST_CACHE_METADATA_KEY = 'landing:clans:best:v2:overall:meta'
LANDING_CLANS_BEST_PUBLISHED_CACHE_KEY = 'landing:clans:best:v2:overall:published'
LANDING_CLANS_BEST_PUBLISHED_METADATA_KEY = 'landing:clans:best:v2:overall:published:meta'
LANDING_RECENT_CLANS_CACHE_KEY = 'landing:recent_clans:last_lookup:v2'
LANDING_RECENT_PLAYERS_CACHE_KEY = 'landing:recent_players:recent25:v1'
LANDING_PLAYERS_CACHE_NAMESPACE_KEY = 'landing:players:v13:namespace'
LANDING_CLANS_DIRTY_KEY = 'landing:clans:dirty:v1'
LANDING_PLAYERS_DIRTY_KEY = 'landing:players:dirty:v1'
LANDING_RECENT_CLANS_DIRTY_KEY = 'landing:recent_clans:dirty:v1'

# Debounce window for invalidation-driven landing republishes. Every clan/
# player write calls _queue_landing_republish, as does the published-fallback
# path on every request while the cache is dirty — so under the clan crawl +
# landing traffic, a warm was enqueued on essentially every event, flooding
# the background queue. This coalesces those triggers into at most one warm
# per window per realm. The scheduled beat warmer (LANDING_PAGE_WARM_MINUTES)
# is unaffected — it dispatches the task directly, not via this path.
# See agents/runbooks/runbook-db-cpu-saturation-2026-05-24.md.
# Raised 120s -> 600s on 2026-05-27: a multi-day clan crawl re-dirties the
# cache continuously, so the 120s floor still let ~30 republish warms/hour
# through; 600s caps that at ~6/hr while the 55-min beat warmer guarantees
# steady-state freshness. See runbook-db-cpu-saturation-2026-05-24.md.
LANDING_REPUBLISH_COOLDOWN_SECONDS = int(
    os.environ.get('LANDING_REPUBLISH_COOLDOWN_SECONDS', '600'))
LANDING_REPUBLISH_COOLDOWN_KEY = 'landing:republish:cooldown:v1'
LANDING_CLAN_FEATURED_COUNT = 30
LANDING_CLAN_MIN_TOTAL_BATTLES = 100000
LANDING_CLAN_MODES = ('best',)
LANDING_CLAN_BEST_SORTS = ('overall', 'wr')
LANDING_PLAYER_LIMIT = 25
LANDING_PLAYER_BEST_SORTS = (
    'overall', 'ranked', 'efficiency', 'wr', 'cb')
LANDING_PLAYER_BEST_SNAPSHOT_LIMIT = LANDING_PLAYER_LIMIT
LANDING_PLAYER_BEST_MIN_PVP_BATTLES = 2500
LANDING_PLAYER_BEST_MIN_HIGH_TIER_PVP_BATTLES = 500
LANDING_PLAYER_BEST_TARGET_HIGH_TIER_PVP_BATTLES = 5000
LANDING_PLAYER_BEST_CANDIDATE_LIMIT = 1200
LANDING_PLAYER_SIGMA_MIN_PVP_BATTLES = 500
LANDING_PLAYER_MODES = ('best', 'sigma', 'popular')
LANDING_PLAYER_BEST_WR_WEIGHT = 0.40
LANDING_PLAYER_BEST_PLAYER_SCORE_WEIGHT = 0.22
LANDING_PLAYER_BEST_EFFICIENCY_WEIGHT = 0.18
LANDING_PLAYER_BEST_VOLUME_WEIGHT = 0.10
LANDING_PLAYER_BEST_CLAN_WEIGHT = 0.10
LANDING_PLAYER_BEST_RANKED_BOOST = 0.15
LANDING_PLAYER_BEST_RANKED_QUALITY_LEAGUE_WEIGHT = 0.35
LANDING_PLAYER_BEST_RANKED_QUALITY_WR_WEIGHT = 0.25
LANDING_PLAYER_BEST_RANKED_QUALITY_DEPTH_WEIGHT = 0.25
LANDING_PLAYER_BEST_RANKED_QUALITY_VOLUME_WEIGHT = 0.15
LANDING_PLAYER_BEST_RANKED_QUALITY_MAX_SEASONS = 15
LANDING_PLAYER_BEST_RANKED_QUALITY_MAX_BATTLES = 50
LANDING_PLAYER_BEST_EFFICIENCY_NEUTRAL = 0.35
LANDING_PLAYER_BEST_COMPETITIVE_SHARE_FLOOR = 0.55
LANDING_PLAYER_RANKED_SORT_VOLUME_WEIGHT = 0.20
LANDING_PLAYER_RANKED_SORT_RECENT_WR_WEIGHT = 0.40
LANDING_PLAYER_RANKED_SORT_PLAYER_SCORE_WEIGHT = 0.25
LANDING_PLAYER_RANKED_SORT_WR_WEIGHT = 0.15
LANDING_PLAYER_RANKED_SORT_WR_LOW_CONFIDENCE_BATTLES = 8
LANDING_PLAYER_RANKED_SORT_WR_FULL_CONFIDENCE_BATTLES = 28
LANDING_PLAYER_RANKED_SORT_FRESHNESS_GRACE_DAYS = 14
LANDING_PLAYER_RANKED_SORT_FRESHNESS_STALE_DAYS = 90
LANDING_PLAYER_RANKED_SORT_FRESHNESS_FLOOR = 0.82
LANDING_PLAYER_CB_SORT_WILSON_Z = 1.2815515655446004
LANDING_PLAYER_CB_SORT_WR_WEIGHT = 0.80
LANDING_PLAYER_CB_SORT_VOLUME_WEIGHT = 0.15
LANDING_PLAYER_CB_SORT_SEASON_DEPTH_WEIGHT = 0.05
LANDING_PLAYER_CB_SORT_MAX_BATTLES = 4000
LANDING_PLAYER_CB_SORT_MAX_SEASONS = 10



def _normalize_best_wr_score(value: float | None) -> float:
    if value is None:
        return 0.0
    return clamp((float(value) - 45.0) / 20.0, 0.0, 1.0)


def _normalize_best_player_score(value: float | None) -> float:
    if value is None:
        return 0.0
    return clamp(float(value) / 10.0, 0.0, 1.0)


def _normalize_best_efficiency_score(percentile: float | None, shrunken_strength: float | None) -> float:
    if percentile is not None:
        return clamp(float(percentile), 0.0, 1.0)
    if shrunken_strength is not None:
        return clamp(float(shrunken_strength), 0.0, 1.0)
    return LANDING_PLAYER_BEST_EFFICIENCY_NEUTRAL


def _normalize_best_volume_score(high_tier_battles: int | None) -> float:
    battles = max(int(high_tier_battles or 0), 0)
    if battles <= 0:
        return 0.0
    return clamp(
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


def _normalize_ranked_volume_score(latest_ranked_battles: int | None) -> float:
    battles = max(int(latest_ranked_battles or 0), 0)
    if battles <= 0:
        return 0.0

    return round(clamp(math.log1p(battles) / math.log1p(40), 0.0, 1.0), 4)


def _summarize_ranked_medal_history(ranked_rows) -> dict[str, int | float | None]:
    rows = ranked_rows if isinstance(ranked_rows, list) else []
    gold_count = 0
    silver_count = 0
    bronze_count = 0
    total_wins = 0
    total_battles = 0

    for row in rows:
        if not isinstance(row, dict):
            continue

        league_name = str(row.get('highest_league_name') or '').strip()
        if league_name == 'Gold':
            gold_count += 1
        elif league_name == 'Silver':
            silver_count += 1
        elif league_name == 'Bronze':
            bronze_count += 1

        try:
            battles = max(int(row.get('total_battles') or 0), 0)
            wins = max(int(row.get('total_wins') or 0), 0)
        except (TypeError, ValueError):
            battles = 0
            wins = 0

        total_battles += battles
        total_wins += min(wins, battles) if battles > 0 else 0

    ranked_win_rate = round((total_wins / total_battles)
                            * 100.0, 2) if total_battles > 0 else None
    return {
        'gold_medal_count': gold_count,
        'silver_medal_count': silver_count,
        'bronze_medal_count': bronze_count,
        'ranked_total_battles': total_battles,
        'ranked_total_wins': total_wins,
        'ranked_overall_win_rate': ranked_win_rate,
    }


def _ranked_freshness_multiplier(ranked_updated_at) -> float:
    if ranked_updated_at is None:
        return LANDING_PLAYER_RANKED_SORT_FRESHNESS_FLOOR

    age_delta = timezone.now() - ranked_updated_at
    age_days = max(age_delta.total_seconds() / 86400.0, 0.0)
    if age_days <= LANDING_PLAYER_RANKED_SORT_FRESHNESS_GRACE_DAYS:
        return 1.0
    if age_days >= LANDING_PLAYER_RANKED_SORT_FRESHNESS_STALE_DAYS:
        return LANDING_PLAYER_RANKED_SORT_FRESHNESS_FLOOR

    decay_progress = clamp(
        (
            age_days - LANDING_PLAYER_RANKED_SORT_FRESHNESS_GRACE_DAYS
        ) /
        (
            LANDING_PLAYER_RANKED_SORT_FRESHNESS_STALE_DAYS -
            LANDING_PLAYER_RANKED_SORT_FRESHNESS_GRACE_DAYS
        ),
        0.0,
        1.0,
    )
    return round(
        1.0 - ((1.0 - LANDING_PLAYER_RANKED_SORT_FRESHNESS_FLOOR) * decay_progress),
        4,
    )



def _normalize_best_clan_score(is_clan_battle_player: bool | None, clan_battle_win_rate: float | None) -> float:
    if not is_clan_battle_player:
        return 0.0

    win_rate_score = _normalize_best_wr_score(clan_battle_win_rate)
    return round(0.35 + (0.65 * win_rate_score), 4)


def _ranked_quality_score(row: dict) -> float:
    seasons = max(int(row.get('ranked_seasons_participated') or 0), 0)
    latest_battles = max(int(row.get('latest_ranked_battles') or 0), 0)
    league = row.get('highest_ranked_league_recent')
    ranked_wr = row.get('ranked_overall_win_rate')

    if seasons == 0 and latest_battles == 0:
        return 0.0

    league_score = _ranked_league_score(league)
    wr_score = _normalize_best_wr_score(
        ranked_wr) if ranked_wr is not None else 0.0
    depth_score = clamp(
        seasons / LANDING_PLAYER_BEST_RANKED_QUALITY_MAX_SEASONS, 0.0, 1.0)
    volume_score = clamp(
        math.log1p(latest_battles) / math.log1p(
            LANDING_PLAYER_BEST_RANKED_QUALITY_MAX_BATTLES),
        0.0, 1.0,
    )

    return round(
        LANDING_PLAYER_BEST_RANKED_QUALITY_LEAGUE_WEIGHT * league_score +
        LANDING_PLAYER_BEST_RANKED_QUALITY_WR_WEIGHT * wr_score +
        LANDING_PLAYER_BEST_RANKED_QUALITY_DEPTH_WEIGHT * depth_score +
        LANDING_PLAYER_BEST_RANKED_QUALITY_VOLUME_WEIGHT * volume_score,
        4,
    )


def _competitive_share_multiplier(pvp_battles: int | None, high_tier_battles: int | None) -> float:
    total_battles = max(int(pvp_battles or 0), 0)
    competitive_battles = max(int(high_tier_battles or 0), 0)
    if total_battles <= 0:
        return LANDING_PLAYER_BEST_COMPETITIVE_SHARE_FLOOR

    share = clamp(competitive_battles / total_battles, 0.0, 1.0)
    if share <= 0.2:
        return LANDING_PLAYER_BEST_COMPETITIVE_SHARE_FLOOR
    if share >= 0.8:
        return 1.0

    normalized_share = (share - 0.2) / 0.6
    return round(
        LANDING_PLAYER_BEST_COMPETITIVE_SHARE_FLOOR +
        ((1.0 - LANDING_PLAYER_BEST_COMPETITIVE_SHARE_FLOOR)
         * math.sqrt(clamp(normalized_share, 0.0, 1.0))),
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
        LANDING_PLAYER_BEST_CLAN_WEIGHT * _normalize_best_clan_score(
            row.get('is_clan_battle_player'), row.get('clan_battle_win_rate'))
    )

    ranked_multiplier = 1.0 + (
        LANDING_PLAYER_BEST_RANKED_BOOST * _ranked_quality_score(row))

    return round(
        base_score * ranked_multiplier * _competitive_share_multiplier(
            row.get('pvp_battles'), row.get('high_tier_pvp_battles')),
        6,
    )


def _calculate_landing_ranked_sort_score(row: dict) -> float:
    medal_weighted_score = (
        (1000 * max(int(row.get('gold_medal_count') or 0), 0)) +
        (100 * max(int(row.get('silver_medal_count') or 0), 0)) +
        (10 * max(int(row.get('bronze_medal_count') or 0), 0))
    )
    ranked_wr = float(row.get('ranked_overall_win_rate') or 0.0)
    freshness_multiplier = _ranked_freshness_multiplier(
        row.get('ranked_updated_at'))
    return round((medal_weighted_score + ranked_wr) * freshness_multiplier, 6)


def _calculate_wilson_lower_bound(success_rate: float | None, sample_size: int | None, z_score: float = LANDING_PLAYER_CB_SORT_WILSON_Z) -> float:
    battles = max(int(sample_size or 0), 0)
    if battles <= 0 or success_rate is None:
        return 0.0

    proportion = clamp(float(success_rate) / 100.0, 0.0, 1.0)
    z_squared = z_score * z_score
    denominator = 1.0 + (z_squared / battles)
    center = proportion + (z_squared / (2.0 * battles))
    margin = z_score * math.sqrt(
        ((proportion * (1.0 - proportion)) +
         (z_squared / (4.0 * battles))) / battles
    )
    lower_bound = (center - margin) / denominator
    return round(clamp(lower_bound, 0.0, 1.0), 6)


def _calculate_landing_cb_sort_score(row: dict) -> float:
    battles = max(int(row.get('clan_battle_total_battles') or 0), 0)
    credible_wr_score = _calculate_wilson_lower_bound(
        row.get('clan_battle_win_rate'),
        battles,
    )
    volume_score = clamp(
        battles / LANDING_PLAYER_CB_SORT_MAX_BATTLES,
        0.0,
        1.0,
    )
    season_depth_score = clamp(
        max(int(row.get('clan_battle_seasons_participated') or 0), 0) /
        LANDING_PLAYER_CB_SORT_MAX_SEASONS,
        0.0,
        1.0,
    )
    return round(
        (LANDING_PLAYER_CB_SORT_WR_WEIGHT * credible_wr_score) +
        (LANDING_PLAYER_CB_SORT_VOLUME_WEIGHT * volume_score) +
        (LANDING_PLAYER_CB_SORT_SEASON_DEPTH_WEIGHT * season_depth_score),
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


def _canonical_landing_player_mode_and_sort(mode: str | None, sort: str | None) -> tuple[str, str | None]:
    normalized_mode = normalize_landing_player_mode(mode)
    if normalized_mode == 'sigma':
        return 'best', 'efficiency'
    if normalized_mode == 'best':
        return 'best', normalize_landing_player_best_sort(sort)
    return normalized_mode, None


def landing_player_cache_key(mode: str, limit: int, realm: str = DEFAULT_REALM, sort: str | None = None) -> str:
    namespace = _get_landing_players_cache_namespace(realm=realm)
    canonical_mode, canonical_sort = _canonical_landing_player_mode_and_sort(
        mode, sort)
    if canonical_mode == 'best' and canonical_sort is not None:
        return realm_cache_key(realm, f'landing:players:v13:n{namespace}:best:{canonical_sort}:{limit}')
    return realm_cache_key(realm, f'landing:players:v13:n{namespace}:{canonical_mode}:{limit}')


def landing_player_cache_metadata_key(mode: str, limit: int, realm: str = DEFAULT_REALM, sort: str | None = None) -> str:
    namespace = _get_landing_players_cache_namespace(realm=realm)
    canonical_mode, canonical_sort = _canonical_landing_player_mode_and_sort(
        mode, sort)
    if canonical_mode == 'best' and canonical_sort is not None:
        return realm_cache_key(realm, f'landing:players:v13:n{namespace}:best:{canonical_sort}:{limit}:meta')
    return realm_cache_key(realm, f'landing:players:v13:n{namespace}:{canonical_mode}:{limit}:meta')


def landing_player_published_cache_key(mode: str, limit: int, realm: str = DEFAULT_REALM, sort: str | None = None) -> str:
    namespace = _get_landing_players_cache_namespace(realm=realm)
    canonical_mode, canonical_sort = _canonical_landing_player_mode_and_sort(
        mode, sort)
    if canonical_mode == 'best' and canonical_sort is not None:
        return realm_cache_key(realm, f'landing:players:v13:n{namespace}:published:best:{canonical_sort}:{limit}')
    return realm_cache_key(realm, f'landing:players:v13:n{namespace}:published:{canonical_mode}:{limit}')


def landing_player_published_metadata_key(mode: str, limit: int, realm: str = DEFAULT_REALM, sort: str | None = None) -> str:
    namespace = _get_landing_players_cache_namespace(realm=realm)
    canonical_mode, canonical_sort = _canonical_landing_player_mode_and_sort(
        mode, sort)
    if canonical_mode == 'best' and canonical_sort is not None:
        return realm_cache_key(realm, f'landing:players:v13:n{namespace}:published:best:{canonical_sort}:{limit}:meta')
    return realm_cache_key(realm, f'landing:players:v13:n{namespace}:published:{canonical_mode}:{limit}:meta')


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


def _queue_landing_republish(realm: str = DEFAULT_REALM, scope: str = 'all') -> None:
    from warships.tasks import queue_landing_page_warm

    # Debounce: coalesce bursts of invalidations into at most one warm per
    # LANDING_REPUBLISH_COOLDOWN_SECONDS per realm. cache.add only succeeds
    # when no cooldown key exists; the key expires on its own and is NOT
    # cleared by the warm task (unlike queue_landing_page_warm's dispatch
    # key, which the task deletes on completion — that let republishes
    # re-fire immediately after every warm). See
    # agents/runbooks/runbook-db-cpu-saturation-2026-05-24.md.
    if LANDING_REPUBLISH_COOLDOWN_SECONDS > 0:
        cooldown_key = realm_cache_key(realm, LANDING_REPUBLISH_COOLDOWN_KEY)
        if not cache.add(cooldown_key, '1', LANDING_REPUBLISH_COOLDOWN_SECONDS):
            return

    # include_recent=False: invalidation comes from clan/player writes, which
    # do not change the recent-players 7-day rollup. Rebuilding it here re-runs
    # the 25s `week_battles` aggregate on every crawl-driven republish — the
    # 2026-05-27 DB-CPU saturation. The recent surfaces are kept fresh by their
    # dedicated beat warmers (recent-players-warmer / the 55-min landing warmer
    # which dispatches the task directly with include_recent=True).
    # `scope` narrows the warm to the family that was invalidated (clan write ->
    # 'clans', player write -> 'players') so a clan write doesn't rebuild the
    # player surfaces and vice versa.
    queue_landing_page_warm(realm=realm, include_recent=False, scope=scope)


def _publish_landing_payload(
    cache_key: str,
    metadata_key: str,
    published_cache_key: str,
    published_metadata_key: str,
    payload: list[dict],
    metadata: dict[str, str | int],
    ttl_seconds: int,
    dirty_keys: tuple[str, ...] = (),
    publish_empty_payload: bool = True,
) -> None:
    cache.set(cache_key, payload, ttl_seconds)
    cache.set(metadata_key, metadata, ttl_seconds)
    if publish_empty_payload or payload:
        cache.set(published_cache_key, payload, timeout=None)
        cache.set(published_metadata_key, metadata, timeout=None)
    _clear_cache_family_dirty(*dirty_keys)


def _get_cached_landing_payload_with_fallback(
    cache_key: str,
    metadata_key: str,
    published_cache_key: str,
    published_metadata_key: str,
    ttl_seconds: int,
    force_refresh: bool,
    realm: str = DEFAULT_REALM,
    dirty_keys: tuple[str, ...] = (),
    use_published_fallback_when_dirty: bool = False,
    prefer_published_non_empty_payload: bool = False,
    publish_empty_primary_payload: bool = True,
    republish_scope: str = 'all',
) -> tuple[list[dict] | None, dict[str, str | int]]:
    is_dirty = (not force_refresh) and any(
        cache.get(dirty_key) is not None for dirty_key in dirty_keys
    )
    payload = None if force_refresh or is_dirty else cache.get(cache_key)
    metadata = _normalize_landing_player_cache_metadata(
        None if force_refresh or is_dirty else cache.get(metadata_key), ttl_seconds)

    if payload is not None:
        if prefer_published_non_empty_payload and not payload:
            published_payload = cache.get(published_cache_key)
            if published_payload:
                published_metadata = _normalize_landing_player_cache_metadata(
                    cache.get(published_metadata_key), ttl_seconds)
                cache.set(published_metadata_key,
                          published_metadata, timeout=None)
                _queue_landing_republish(realm=realm, scope=republish_scope)
                return published_payload, published_metadata
        if cache.get(metadata_key) is None:
            cache.set(metadata_key, metadata, ttl_seconds)
        if publish_empty_primary_payload or payload:
            cache.set(published_cache_key, payload, timeout=None)
            cache.set(published_metadata_key, metadata, timeout=None)
        return payload, metadata

    published_payload = None if force_refresh or (is_dirty and not use_published_fallback_when_dirty) else cache.get(
        published_cache_key)
    if published_payload is not None:
        published_metadata = _normalize_landing_player_cache_metadata(
            cache.get(published_metadata_key), ttl_seconds)
        cache.set(published_metadata_key, published_metadata, timeout=None)
        _queue_landing_republish(realm=realm, scope=republish_scope)
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
        dirty_keys=(realm_cache_key(realm, LANDING_CLANS_DIRTY_KEY),),
        republish_scope='clans',
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
            dirty_keys=(realm_cache_key(realm, LANDING_CLANS_DIRTY_KEY),),
        )

    return payload, metadata


def normalize_landing_player_mode(mode: str | None) -> str:
    normalized_mode = (mode or 'best').strip().lower()
    if normalized_mode not in LANDING_PLAYER_MODES:
        raise ValueError('mode must be one of: best, sigma, popular')
    return normalized_mode


def normalize_landing_player_best_sort(sort: str | None) -> str:
    normalized_sort = (sort or 'overall').strip().lower()
    if normalized_sort not in LANDING_PLAYER_BEST_SORTS:
        raise ValueError(
            'sort must be one of: overall, ranked, efficiency, wr, cb')
    return normalized_sort


def normalize_landing_clan_mode(mode: str | None) -> str:
    normalized_mode = (mode or 'best').strip().lower()
    if normalized_mode not in LANDING_CLAN_MODES:
        raise ValueError('mode must be one of: best')
    return normalized_mode


def normalize_landing_clan_best_sort(sort: str | None) -> str:
    normalized_sort = (sort or 'overall').strip().lower()
    if normalized_sort not in LANDING_CLAN_BEST_SORTS:
        raise ValueError('sort must be one of: overall, wr')
    return normalized_sort


def landing_best_clan_cache_key(sort: str, realm: str = DEFAULT_REALM) -> str:
    normalized_sort = normalize_landing_clan_best_sort(sort)
    return realm_cache_key(realm, f'landing:clans:best:v2:{normalized_sort}')


def landing_best_clan_cache_metadata_key(sort: str, realm: str = DEFAULT_REALM) -> str:
    normalized_sort = normalize_landing_clan_best_sort(sort)
    return realm_cache_key(realm, f'landing:clans:best:v2:{normalized_sort}:meta')


def landing_best_clan_published_cache_key(sort: str, realm: str = DEFAULT_REALM) -> str:
    normalized_sort = normalize_landing_clan_best_sort(sort)
    return realm_cache_key(realm, f'landing:clans:best:v2:{normalized_sort}:published')


def landing_best_clan_published_metadata_key(sort: str, realm: str = DEFAULT_REALM) -> str:
    normalized_sort = normalize_landing_clan_best_sort(sort)
    return realm_cache_key(realm, f'landing:clans:best:v2:{normalized_sort}:published:meta')


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


def invalidate_landing_clan_caches(realm: str = DEFAULT_REALM, queue_republish: bool = True) -> None:
    _mark_cache_family_dirty(
        realm_cache_key(realm, LANDING_CLANS_DIRTY_KEY),
        realm_cache_key(realm, LANDING_RECENT_CLANS_DIRTY_KEY),
    )
    if queue_republish:
        _queue_landing_republish(realm=realm, scope='clans')


def invalidate_landing_player_caches(include_recent: bool = False, realm: str = DEFAULT_REALM, queue_republish: bool = True, bump_namespace: bool = False) -> None:
    # bump_namespace=True is reserved for deploy-time schema changes. Per-row
    # invalidations must NOT bump, because the bump orphans the published
    # fallback at the new namespace and forces every subsequent landing
    # request to run the slow inline rebuild until the warmer catches up.
    # See agents/runbooks/runbook-landing-random-cold-queue-2026-04-07.md
    if bump_namespace:
        _bump_landing_players_cache_namespace(realm=realm)
    dirty_keys = [realm_cache_key(realm, LANDING_PLAYERS_DIRTY_KEY)]
    # `include_recent` is a no-op now that Recent is a 7-day rollup rebuilt
    # by a dedicated 3h periodic warmer; the parameter is kept for callsite
    # compatibility but no dirty key needs flipping.
    _mark_cache_family_dirty(*dirty_keys)
    if queue_republish:
        _queue_landing_republish(realm=realm, scope='players')


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




def _build_best_landing_clans(limit: int = LANDING_CLAN_FEATURED_COUNT, realm: str = DEFAULT_REALM, sort: str = 'overall') -> list[dict]:
    normalized_sort = normalize_landing_clan_best_sort(sort)
    best_clan_ids, cb_metrics = score_best_clans(
        limit=limit, realm=realm, sort=normalized_sort)
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

    # Merge CB metrics for sub-sort support
    for row in rows:
        metrics = cb_metrics.get(row['clan_id'], {})
        row['avg_cb_battles'] = metrics.get('avg_cb_battles')
        row['avg_cb_wr'] = metrics.get('avg_cb_wr')
        row['cb_recency_days'] = metrics.get('cb_recency_days')

    # Preserve the score_best_clans ordering
    id_order = {cid: i for i, cid in enumerate(best_clan_ids)}
    rows.sort(key=lambda row: id_order.get(row['clan_id'], len(best_clan_ids)))
    return _attach_clan_battle_activity_badges(rows, realm=realm)


def get_landing_best_clans_payload_with_cache_metadata(force_refresh: bool = False, realm: str = DEFAULT_REALM, sort: str = 'overall') -> tuple[list[dict], dict[str, str | int]]:
    normalized_sort = normalize_landing_clan_best_sort(sort)
    ttl_seconds = LANDING_CLAN_CACHE_TTL
    cache_key = landing_best_clan_cache_key(normalized_sort, realm=realm)
    metadata_key = landing_best_clan_cache_metadata_key(
        normalized_sort, realm=realm)
    published_cache_key = landing_best_clan_published_cache_key(
        normalized_sort, realm=realm)
    published_metadata_key = landing_best_clan_published_metadata_key(
        normalized_sort, realm=realm)

    payload, metadata = _get_cached_landing_payload_with_fallback(
        cache_key,
        metadata_key,
        published_cache_key,
        published_metadata_key,
        ttl_seconds,
        force_refresh,
        realm=realm,
        dirty_keys=(realm_cache_key(realm, LANDING_CLANS_DIRTY_KEY),),
        use_published_fallback_when_dirty=True,
        prefer_published_non_empty_payload=True,
        publish_empty_primary_payload=False,
        republish_scope='clans',
    )

    if payload is None:
        payload = _build_best_landing_clans(
            LANDING_CLAN_FEATURED_COUNT, realm=realm, sort=normalized_sort)
        metadata = _build_landing_player_cache_metadata(ttl_seconds)
        _publish_landing_payload(
            cache_key,
            metadata_key,
            published_cache_key,
            published_metadata_key,
            payload,
            metadata,
            ttl_seconds,
            dirty_keys=(realm_cache_key(realm, LANDING_CLANS_DIRTY_KEY),),
            publish_empty_payload=False,
        )

    return payload, metadata




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



def _serialize_landing_player_rows(rows: list[dict]) -> list[dict]:
    player_ids = [int(row.get('player_id') or 0)
                  for row in rows if row.get('player_id') is not None]
    players_by_id = {
        player.player_id: player
        for player in Player.objects.filter(player_id__in=player_ids).select_related('explorer_summary').only(
            'player_id',
            'is_hidden',
            'is_streamer',
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
        high_tier_battles, high_tier_ratio = calculate_tier_filtered_pvp_record(
            row.pop('battles_json', None),
            minimum_tier=5,
        )
        ranked_rows = row.pop('ranked_json', None)
        ranked_medal_summary = _summarize_ranked_medal_history(
            ranked_rows or [])
        player_obj = players_by_id.get(player_id)
        es = getattr(player_obj, 'explorer_summary',
                     None) if player_obj else None
        row['is_streamer'] = bool(getattr(player_obj, 'is_streamer', False))
        latest_ranked_battles = max(
            int(row.get('latest_ranked_battles') or 0), 0)
        highest_ranked_league_recent = row.get('highest_ranked_league_recent')
        row['high_tier_pvp_battles'] = high_tier_battles
        row['high_tier_pvp_ratio'] = high_tier_ratio
        row['ranked_overall_win_rate'] = ranked_medal_summary.get(
            'ranked_overall_win_rate')
        row['is_pve_player'] = is_pve_player(
            row.get('total_battles'), row.get('pvp_battles'))
        row['is_sleepy_player'] = is_sleepy_player(
            row.get('days_since_last_battle'))
        row['is_ranked_player'] = is_ranked_player(
            ranked_rows) or latest_ranked_battles > 0 or bool(highest_ranked_league_recent)
        row['is_clan_battle_player'] = is_clan_battle_enjoyer(
            getattr(es, 'clan_battle_total_battles', None),
            getattr(es, 'clan_battle_seasons_participated', None),
        )
        row['clan_battle_total_battles'] = getattr(
            es, 'clan_battle_total_battles', None)
        row['clan_battle_seasons_participated'] = getattr(
            es, 'clan_battle_seasons_participated', None)
        row['clan_battle_win_rate'] = getattr(
            es, 'clan_battle_overall_win_rate', None)
        row['highest_ranked_league'] = get_highest_ranked_league_name(
            ranked_rows) or highest_ranked_league_recent
        latest_ranked_row = ranked_rows[0] if ranked_rows else None
        row['latest_ranked_win_rate'] = (
            float(latest_ranked_row.get('win_rate'))
            if isinstance(latest_ranked_row, dict)
            and latest_ranked_row.get('win_rate') is not None
            else None
        )
        row['ranked_updated_at'] = getattr(
            player_obj, 'ranked_updated_at', None)
        # Use stored percentile directly — landing surfaces tolerate minor
        # input-data drift (unlike player detail, which uses the stricter
        # get_published_efficiency_rank_payload freshness gate).
        if player_obj and not player_obj.is_hidden and es and es.efficiency_rank_percentile is not None:
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
    return _attach_clan_battle_activity_badges(_prioritize_landing_clans(list(qs)), realm=realm)


def get_landing_clans_payload(force_refresh: bool = False, realm: str = DEFAULT_REALM) -> list[dict]:
    payload, _ = get_landing_clans_payload_with_cache_metadata(
        force_refresh=force_refresh, realm=realm)
    return payload


def get_landing_best_clans_payload(force_refresh: bool = False, realm: str = DEFAULT_REALM, sort: str = 'overall') -> list[dict]:
    payload, _ = get_landing_best_clans_payload_with_cache_metadata(
        force_refresh=force_refresh, realm=realm, sort=sort)
    return payload


def _build_recent_clans(realm: str = DEFAULT_REALM) -> list[dict]:
    return _attach_clan_battle_activity_badges(list(
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
    ), realm=realm)


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


_LANDING_BUILD_LOCK_TIMEOUT = 30
_LANDING_BUILD_LOCK_WAIT_SECONDS = 5
_LANDING_BUILD_LOCK_POLL_INTERVAL = 0.1


def _build_landing_payload_with_lock(cache_key, builder, normalized_limit, realm=DEFAULT_REALM):
    """Run `builder` under a per-cache-key Redis lock to prevent thundering-herd
    rebuilds. Concurrent waiters poll the cache for up to a few seconds and
    return the lock-holder's result."""
    lock_key = f"{cache_key}:build_lock"
    if cache.add(lock_key, "building", timeout=_LANDING_BUILD_LOCK_TIMEOUT):
        try:
            return builder(normalized_limit)
        finally:
            cache.delete(lock_key)

    deadline = time.monotonic() + _LANDING_BUILD_LOCK_WAIT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(_LANDING_BUILD_LOCK_POLL_INTERVAL)
        cached_payload = cache.get(cache_key)
        if cached_payload is not None:
            return cached_payload
    # Lock holder is taking too long; build anyway rather than block the user.
    logger.warning(
        "Landing build lock wait expired for %s; building inline", cache_key)
    return builder(normalized_limit)



def _best_landing_player_candidate_rows(
    *,
    realm: str,
    min_pvp_battles: int,
    order_by: tuple,
    limit: int = LANDING_PLAYER_BEST_CANDIDATE_LIMIT,
    extra_filters: dict | None = None,
    apply_recency_cap: bool = True,
) -> list[dict]:
    base_filters = dict(
        realm=realm,
        is_hidden=False,
        pvp_battles__gt=min_pvp_battles,
    )
    if apply_recency_cap:
        base_filters['days_since_last_battle__lte'] = 180
    qs = Player.objects.exclude(name='').filter(
        **base_filters,
    ).exclude(
        last_battle_date__isnull=True,
    ).annotate(
        player_score=F('explorer_summary__player_score'),
        efficiency_rank_percentile=F(
            'explorer_summary__efficiency_rank_percentile'),
        shrunken_efficiency_strength=F(
            'explorer_summary__shrunken_efficiency_strength'),
        latest_ranked_battles=F('explorer_summary__latest_ranked_battles'),
        highest_ranked_league_recent=F(
            'explorer_summary__highest_ranked_league_recent'),
        ranked_seasons_participated=F(
            'explorer_summary__ranked_seasons_participated'),
        clan_battle_total_battles=F(
            'explorer_summary__clan_battle_total_battles'),
        clan_battle_seasons_participated=F(
            'explorer_summary__clan_battle_seasons_participated'),
        clan_battle_overall_win_rate=F(
            'explorer_summary__clan_battle_overall_win_rate'),
    )
    if extra_filters:
        qs = qs.filter(**extra_filters)
    return list(
        qs.values(
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
            'ranked_seasons_participated',
            'clan_battle_total_battles',
            'clan_battle_seasons_participated',
            'clan_battle_overall_win_rate',
        ).order_by(*order_by)[:limit]
    )


def _finalize_best_player_payload(rows: list[dict], limit: int) -> list[dict]:
    for row in rows:
        row.pop('player_score', None)
        row.pop('shrunken_efficiency_strength', None)
        row.pop('latest_ranked_battles', None)
        row.pop('latest_ranked_win_rate', None)
        row.pop('gold_medal_count', None)
        row.pop('silver_medal_count', None)
        row.pop('bronze_medal_count', None)
        row.pop('ranked_overall_win_rate', None)
        row.pop('ranked_total_battles', None)
        row.pop('ranked_total_wins', None)
        row.pop('highest_ranked_league_recent', None)
        row.pop('ranked_seasons_participated', None)
        row.pop('ranked_updated_at', None)
    return rows[:limit]


def _clone_landing_player_payload(payload: list[dict] | None, limit: int | None = None) -> list[dict]:
    if not isinstance(payload, list):
        return []

    rows: list[dict] = []
    max_rows = None if limit is None else max(int(limit), 0)
    for row in payload:
        if not isinstance(row, dict):
            continue
        rows.append(dict(row))
        if max_rows is not None and len(rows) >= max_rows:
            break
    return rows


def _normalize_landing_player_snapshot_payload(payload: list[dict]) -> list[dict]:
    return json.loads(json.dumps(payload, cls=DjangoJSONEncoder))


def _get_landing_player_best_snapshot(sort: str, realm: str = DEFAULT_REALM) -> LandingPlayerBestSnapshot | None:
    normalized_sort = normalize_landing_player_best_sort(sort)
    return LandingPlayerBestSnapshot.objects.filter(
        realm=realm,
        sort=normalized_sort,
    ).only('payload_json', 'generated_at').first()


def _build_best_landing_player_snapshot_payload(sort: str, realm: str = DEFAULT_REALM) -> list[dict]:
    normalized_sort = normalize_landing_player_best_sort(sort)
    if normalized_sort == 'ranked':
        return _build_best_ranked_landing_players(LANDING_PLAYER_BEST_SNAPSHOT_LIMIT, realm=realm)
    if normalized_sort == 'efficiency':
        return _build_best_efficiency_landing_players(LANDING_PLAYER_BEST_SNAPSHOT_LIMIT, realm=realm)
    if normalized_sort == 'wr':
        return _build_best_wr_landing_players(LANDING_PLAYER_BEST_SNAPSHOT_LIMIT, realm=realm)
    if normalized_sort == 'cb':
        return _build_best_cb_landing_players(LANDING_PLAYER_BEST_SNAPSHOT_LIMIT, realm=realm)
    return _build_best_overall_landing_players(LANDING_PLAYER_BEST_SNAPSHOT_LIMIT, realm=realm)


def materialize_landing_player_best_snapshot(sort: str, realm: str = DEFAULT_REALM) -> dict[str, object]:
    normalized_sort = normalize_landing_player_best_sort(sort)
    payload = _normalize_landing_player_snapshot_payload(
        _build_best_landing_player_snapshot_payload(
            normalized_sort,
            realm=realm,
        )
    )
    snapshot, _created = LandingPlayerBestSnapshot.objects.update_or_create(
        realm=realm,
        sort=normalized_sort,
        defaults={'payload_json': payload},
    )
    return {
        'realm': realm,
        'sort': normalized_sort,
        'count': len(payload),
        'generated_at': snapshot.generated_at.isoformat(),
    }


def materialize_landing_player_best_snapshots(realm: str = DEFAULT_REALM, sorts: tuple[str, ...] | list[str] | None = None) -> dict[str, object]:
    normalized_sorts = [
        normalize_landing_player_best_sort(sort)
        for sort in (sorts or LANDING_PLAYER_BEST_SORTS)
    ]
    # Each sort loads up to LANDING_PLAYER_BEST_CANDIDATE_LIMIT (1200) player
    # rows including their `battles_json` and `ranked_json` columns, which can
    # peak at 100-500 MB of transient garbage per sort. On the 4 GB droplet
    # this is enough to push the box into OOM territory if we don't release
    # references between iterations. Run as an explicit loop and gc.collect()
    # after each sort so the candidate row list, the serialized rows, and the
    # snapshot payload all get reaped before the next sort begins.
    results: list[dict[str, object]] = []
    for sort in normalized_sorts:
        results.append(
            materialize_landing_player_best_snapshot(sort, realm=realm)
        )
        gc.collect()
    return {
        'status': 'completed',
        'realm': realm,
        'results': results,
    }


def _get_materialized_best_landing_players(limit: int, realm: str = DEFAULT_REALM, sort: str = 'overall') -> list[dict]:
    normalized_sort = normalize_landing_player_best_sort(sort)
    snapshot = _get_landing_player_best_snapshot(normalized_sort, realm=realm)
    if snapshot is None:
        materialize_landing_player_best_snapshot(normalized_sort, realm=realm)
        snapshot = _get_landing_player_best_snapshot(
            normalized_sort, realm=realm)

    if snapshot is None:
        return []

    return _clone_landing_player_payload(snapshot.payload_json, limit=limit)


def _build_best_overall_landing_players(limit: int, realm: str = DEFAULT_REALM) -> list[dict]:
    candidate_rows = _best_landing_player_candidate_rows(
        realm=realm,
        min_pvp_battles=LANDING_PLAYER_BEST_MIN_PVP_BATTLES,
        order_by=(
            F('explorer_summary__player_score').desc(nulls_last=True),
            F('pvp_ratio').desc(nulls_last=True),
            F('last_battle_date').desc(nulls_last=True),
            'name',
        ),
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

    return _finalize_best_player_payload(rows, limit)


def _build_best_ranked_landing_players(limit: int, realm: str = DEFAULT_REALM) -> list[dict]:
    players = list(
        Player.objects.exclude(name='').filter(
            realm=realm,
            is_hidden=False,
            days_since_last_battle__lte=180,
            pvp_battles__gt=LANDING_PLAYER_BEST_MIN_PVP_BATTLES,
            explorer_summary__ranked_seasons_participated__gt=0,
        ).exclude(
            last_battle_date__isnull=True,
        ).select_related('explorer_summary').only(
            'name',
            'player_id',
            'pvp_ratio',
            'is_hidden',
            'is_streamer',
            'days_since_last_battle',
            'total_battles',
            'pvp_battles',
            'ranked_json',
            'ranked_updated_at',
            'explorer_summary__player_score',
            'explorer_summary__latest_ranked_battles',
            'explorer_summary__highest_ranked_league_recent',
            'explorer_summary__ranked_seasons_participated',
            'explorer_summary__clan_battle_seasons_participated',
            'explorer_summary__clan_battle_total_battles',
            'explorer_summary__clan_battle_overall_win_rate',
            'explorer_summary__efficiency_rank_percentile',
            'explorer_summary__efficiency_rank_tier',
            'explorer_summary__has_efficiency_rank_icon',
            'explorer_summary__efficiency_rank_population_size',
            'explorer_summary__efficiency_rank_updated_at',
        )
    )
    ranked_rows = []
    for player in players:
        explorer_summary = getattr(player, 'explorer_summary', None)
        if explorer_summary is None:
            continue

        ranked_history = player.ranked_json or []
        medal_summary = _summarize_ranked_medal_history(ranked_history)
        if (
            medal_summary['gold_medal_count'] == 0
            and medal_summary['silver_medal_count'] == 0
            and medal_summary['bronze_medal_count'] == 0
        ):
            continue

        row = {
            'player_id': player.player_id,
            'name': player.name,
            'pvp_ratio': player.pvp_ratio,
            'is_hidden': player.is_hidden,
            'is_streamer': player.is_streamer,
            'pvp_battles': player.pvp_battles,
            'total_battles': player.total_battles,
            'is_pve_player': is_pve_player(player.total_battles, player.pvp_battles),
            'is_sleepy_player': is_sleepy_player(player.days_since_last_battle),
            'is_ranked_player': True,
            'highest_ranked_league': get_highest_ranked_league_name(ranked_history) or getattr(explorer_summary, 'highest_ranked_league_recent', None),
            'is_clan_battle_player': is_clan_battle_enjoyer(
                getattr(explorer_summary, 'clan_battle_total_battles', None),
                getattr(explorer_summary,
                        'clan_battle_seasons_participated', None),
            ),
            'clan_battle_total_battles': getattr(explorer_summary, 'clan_battle_total_battles', None),
            'clan_battle_seasons_participated': getattr(explorer_summary, 'clan_battle_seasons_participated', None),
            'clan_battle_win_rate': getattr(explorer_summary, 'clan_battle_overall_win_rate', None),
            'efficiency_rank_percentile': getattr(explorer_summary, 'efficiency_rank_percentile', None),
            'efficiency_rank_tier': getattr(explorer_summary, 'efficiency_rank_tier', None),
            'has_efficiency_rank_icon': bool(getattr(explorer_summary, 'has_efficiency_rank_icon', False)),
            'efficiency_rank_population_size': getattr(explorer_summary, 'efficiency_rank_population_size', None),
            'efficiency_rank_updated_at': getattr(explorer_summary, 'efficiency_rank_updated_at', None),
            'player_score': getattr(explorer_summary, 'player_score', None),
            'latest_ranked_battles': getattr(explorer_summary, 'latest_ranked_battles', None),
            'latest_ranked_win_rate': ranked_history[0].get('win_rate') if isinstance(ranked_history, list) and ranked_history and isinstance(ranked_history[0], dict) else None,
            'ranked_updated_at': player.ranked_updated_at,
            **medal_summary,
        }
        row['ranked_sort_score'] = _calculate_landing_ranked_sort_score(row)
        ranked_rows.append(row)

    ranked_rows.sort(key=lambda row: (
        -max(int(row.get('gold_medal_count') or 0), 0),
        -(float(row.get('ranked_overall_win_rate'))
          if row.get('ranked_overall_win_rate') is not None else float('-inf')),
        -max(int(row.get('silver_medal_count') or 0), 0),
        -max(int(row.get('bronze_medal_count') or 0), 0),
        -(_ranked_freshness_multiplier(row.get('ranked_updated_at')) if row.get('ranked_updated_at')
          is not None else LANDING_PLAYER_RANKED_SORT_FRESHNESS_FLOOR),
        -(row.get('latest_ranked_battles')
          if row.get('latest_ranked_battles') is not None else float('-inf')),
        -(row.get('player_score') if row.get('player_score')
          is not None else float('-inf')),
        row.get('name') or '',
    ))

    for row in ranked_rows:
        row.pop('ranked_sort_score', None)

    return _finalize_best_player_payload(ranked_rows, limit)


def _build_best_efficiency_landing_players(limit: int, realm: str = DEFAULT_REALM) -> list[dict]:
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
            'is_streamer',
            'days_since_last_battle',
            'total_battles',
            'pvp_battles',
            'explorer_summary__player_score',
            'explorer_summary__efficiency_rank_percentile',
            'explorer_summary__efficiency_rank_tier',
            'explorer_summary__has_efficiency_rank_icon',
            'explorer_summary__efficiency_rank_population_size',
            'explorer_summary__efficiency_rank_updated_at',
            'explorer_summary__latest_ranked_battles',
            'explorer_summary__highest_ranked_league_recent',
            'explorer_summary__clan_battle_seasons_participated',
            'explorer_summary__clan_battle_total_battles',
            'explorer_summary__clan_battle_overall_win_rate',
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
        if explorer_summary is None or explorer_summary.efficiency_rank_percentile is None:
            continue

        rows.append({
            'player_id': player.player_id,
            'name': player.name,
            'pvp_ratio': player.pvp_ratio,
            'is_hidden': player.is_hidden,
            'is_streamer': player.is_streamer,
            'pvp_battles': player.pvp_battles,
            'total_battles': player.total_battles,
            'is_pve_player': is_pve_player(player.total_battles, player.pvp_battles),
            'is_sleepy_player': is_sleepy_player(player.days_since_last_battle),
            'is_ranked_player': max(int(getattr(explorer_summary, 'latest_ranked_battles', 0) or 0), 0) > 0,
            'highest_ranked_league': getattr(explorer_summary, 'highest_ranked_league_recent', None),
            'is_clan_battle_player': is_clan_battle_enjoyer(
                getattr(explorer_summary, 'clan_battle_total_battles', None),
                getattr(explorer_summary,
                        'clan_battle_seasons_participated', None),
            ),
            'clan_battle_total_battles': getattr(explorer_summary, 'clan_battle_total_battles', None),
            'clan_battle_seasons_participated': getattr(explorer_summary, 'clan_battle_seasons_participated', None),
            'clan_battle_win_rate': getattr(explorer_summary, 'clan_battle_overall_win_rate', None),
            'efficiency_rank_percentile': explorer_summary.efficiency_rank_percentile,
            'efficiency_rank_tier': explorer_summary.efficiency_rank_tier,
            'has_efficiency_rank_icon': bool(explorer_summary.has_efficiency_rank_icon),
            'efficiency_rank_population_size': explorer_summary.efficiency_rank_population_size,
            'efficiency_rank_updated_at': explorer_summary.efficiency_rank_updated_at,
        })

    return rows[:limit]


def _build_best_wr_landing_players(limit: int, realm: str = DEFAULT_REALM) -> list[dict]:
    candidate_rows = _best_landing_player_candidate_rows(
        realm=realm,
        min_pvp_battles=LANDING_PLAYER_BEST_MIN_PVP_BATTLES,
        order_by=(
            F('pvp_ratio').desc(nulls_last=True),
            F('explorer_summary__player_score').desc(nulls_last=True),
            'name',
        ),
    )
    rows = _serialize_landing_player_rows(candidate_rows)
    wr_rows = []
    for row in rows:
        high_tier_battles = int(row.get('high_tier_pvp_battles') or 0)
        if high_tier_battles < LANDING_PLAYER_BEST_MIN_HIGH_TIER_PVP_BATTLES:
            continue
        row['pvp_ratio'] = (
            row.get('high_tier_pvp_ratio')
            if row.get('high_tier_pvp_ratio') is not None
            else row.get('pvp_ratio')
        )
        wr_rows.append(row)

    wr_rows.sort(key=lambda row: (
        -(row.get('high_tier_pvp_ratio') if row.get('high_tier_pvp_ratio')
          is not None else float('-inf')),
        -(row.get('high_tier_pvp_battles')
          if row.get('high_tier_pvp_battles') is not None else float('-inf')),
        -(row.get('player_score') if row.get('player_score')
          is not None else float('-inf')),
        -(row.get('efficiency_rank_percentile')
          if row.get('efficiency_rank_percentile') is not None else float('-inf')),
        row.get('name') or '',
    ))

    return _finalize_best_player_payload(wr_rows, limit)


def _build_best_cb_landing_players(limit: int, realm: str = DEFAULT_REALM) -> list[dict]:
    candidate_rows = _best_landing_player_candidate_rows(
        realm=realm,
        min_pvp_battles=LANDING_PLAYER_BEST_MIN_PVP_BATTLES,
        order_by=(
            F('explorer_summary__clan_battle_total_battles').desc(nulls_last=True),
            F('explorer_summary__clan_battle_overall_win_rate').desc(nulls_last=True),
            F('explorer_summary__player_score').desc(nulls_last=True),
            'name',
        ),
        extra_filters={'explorer_summary__clan_battle_total_battles__gt': 0},
    )
    rows = _serialize_landing_player_rows(candidate_rows)
    cb_rows = []
    for row in rows:
        if not row.get('is_clan_battle_player'):
            continue
        row['cb_sort_score'] = _calculate_landing_cb_sort_score(row)
        cb_rows.append(row)

    cb_rows.sort(key=lambda row: (
        -(row.get('cb_sort_score') if row.get('cb_sort_score')
          is not None else float('-inf')),
        -(row.get('clan_battle_win_rate') if row.get('clan_battle_win_rate')
          is not None else float('-inf')),
        -(row.get('clan_battle_total_battles')
          if row.get('clan_battle_total_battles') is not None else float('-inf')),
        -(row.get('clan_battle_seasons_participated')
          if row.get('clan_battle_seasons_participated') is not None else float('-inf')),
        row.get('name') or '',
    ))

    for row in cb_rows:
        row.pop('cb_sort_score', None)

    return _finalize_best_player_payload(cb_rows, limit)


def _build_best_landing_players(limit: int, realm: str = DEFAULT_REALM, sort: str = 'overall') -> list[dict]:
    return _get_materialized_best_landing_players(limit, realm=realm, sort=sort)



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


def get_landing_players_payload_with_cache_metadata(mode: str = 'best', limit: int = LANDING_PLAYER_LIMIT, force_refresh: bool = False, realm: str = DEFAULT_REALM, sort: str | None = None) -> tuple[list[dict], dict[str, str | int]]:
    normalized_mode = normalize_landing_player_mode(mode)
    normalized_limit = normalize_landing_player_limit(limit)
    canonical_mode, canonical_sort = _canonical_landing_player_mode_and_sort(
        normalized_mode,
        sort,
    )
    cache_key = landing_player_cache_key(
        normalized_mode, normalized_limit, realm=realm, sort=sort)
    metadata_key = landing_player_cache_metadata_key(
        normalized_mode, normalized_limit, realm=realm, sort=sort)
    published_cache_key = landing_player_published_cache_key(
        normalized_mode, normalized_limit, realm=realm, sort=sort)
    published_metadata_key = landing_player_published_metadata_key(
        normalized_mode, normalized_limit, realm=realm, sort=sort)
    ttl_seconds = landing_player_cache_ttl(canonical_mode)

    # 'sigma' canonicalizes to 'best' and 'best' is the default; only 'popular'
    # branches separately. The pre-2026-05-07 'random' fallback is gone.
    if canonical_mode == 'popular':
        def builder(lim): return _build_popular_landing_players(
            lim, realm=realm)
    else:
        def builder(lim): return _build_best_landing_players(
            lim, realm=realm, sort=canonical_sort or 'overall')

    payload, metadata = _get_cached_landing_payload_with_fallback(
        cache_key,
        metadata_key,
        published_cache_key,
        published_metadata_key,
        ttl_seconds,
        force_refresh,
        realm=realm,
        dirty_keys=(realm_cache_key(realm, LANDING_PLAYERS_DIRTY_KEY),),
        republish_scope='players',
    )

    if payload is None:
        payload = _build_landing_payload_with_lock(
            cache_key, builder, normalized_limit, realm=realm,
        )
        metadata = _build_landing_player_cache_metadata(ttl_seconds)
        _publish_landing_payload(
            cache_key,
            metadata_key,
            published_cache_key,
            published_metadata_key,
            payload,
            metadata,
            ttl_seconds,
            dirty_keys=(realm_cache_key(realm, LANDING_PLAYERS_DIRTY_KEY),),
        )

    return payload, metadata


def get_landing_players_payload(mode: str = 'best', limit: int = LANDING_PLAYER_LIMIT, force_refresh: bool = False, realm: str = DEFAULT_REALM, sort: str | None = None) -> list[dict]:
    payload, _ = get_landing_players_payload_with_cache_metadata(
        mode=mode,
        limit=limit,
        force_refresh=force_refresh,
        realm=realm,
        sort=sort,
    )
    return payload


_LANDING_PLAYER_ROW_ONLY_FIELDS = (
    'player_id', 'name', 'pvp_ratio', 'is_hidden', 'is_streamer', 'days_since_last_battle',
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


def _serialize_landing_player_row(player_obj) -> dict:
    """Shared row builder for the Recent and other landing surfaces.

    Recent now sources its ordering from the trailing 7-day random-battle
    rollup over PlayerDailyShipStats; the row shape is unchanged.
    """
    ranked_rows = player_obj.ranked_json
    es = getattr(player_obj, 'explorer_summary', None)
    row = {
        'player_id': player_obj.player_id,
        'name': player_obj.name,
        'pvp_ratio': player_obj.pvp_ratio,
        'is_hidden': player_obj.is_hidden,
        'is_streamer': player_obj.is_streamer,
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
        'highest_ranked_league': get_highest_ranked_league_name(ranked_rows),
    }
    if not player_obj.is_hidden and es and es.efficiency_rank_percentile is not None:
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
    return row


def _build_recent_players(realm: str = DEFAULT_REALM) -> list[dict]:
    # Surface contract: the LANDING_RECENT_PLAYERS_LIMIT most-recently-active
    # random-battle players who have crossed the >MIN_WEEK_BATTLES floor over
    # the trailing LANDING_RECENT_PLAYERS_LOOKBACK_DAYS-day window. Eligibility
    # is computed against the PlayerDailyShipStats rollup (random mode); the
    # final order is `Player.last_random_battle_at` desc — that column is
    # maintained by the BattleEvent capture hook so it tracks "really played
    # most recently" rather than "polled most recently". See
    # `agents/runbooks/runbook-recent-players-recency-filter-2026-05-04.md`.
    from warships.models import PlayerDailyShipStats

    lookback_floor = (
        timezone.now().date() - timedelta(days=LANDING_RECENT_PLAYERS_LOOKBACK_DAYS)
    )
    eligibility = (
        PlayerDailyShipStats.objects
        .filter(
            mode=PlayerDailyShipStats.MODE_RANDOM,
            date__gte=lookback_floor,
            player__realm=realm,
            player__is_hidden=False,
        )
        .values('player')
        .annotate(week_battles=Sum('battles'))
        .filter(week_battles__gt=LANDING_RECENT_PLAYERS_MIN_WEEK_BATTLES,
                week_battles__lte=LANDING_RECENT_PLAYERS_MAX_WEEK_BATTLES)
    )
    eligibility_rows = list(eligibility)
    if not eligibility_rows:
        return []

    battle_counts = {r['player']: r['week_battles'] for r in eligibility_rows}
    eligible_pks = list(battle_counts.keys())

    # Over-fetch so the implausible-row filter below can drop phantom
    # first-observation rows without shrinking the final list below LIMIT.
    overfetch_cap = LANDING_RECENT_PLAYERS_LIMIT * 4
    candidates = list(
        Player.objects
        .filter(
            pk__in=eligible_pks,
            realm=realm,
            is_hidden=False,
            last_random_battle_at__isnull=False,
        )
        .exclude(name='')
        .select_related('explorer_summary')
        .only(*_LANDING_PLAYER_ROW_ONLY_FIELDS, 'last_random_battle_at')
        .order_by(F('last_random_battle_at').desc(nulls_last=True), 'name')[:overfetch_cap]
    )

    payload = []
    for player_obj in candidates:
        week_battles = int(battle_counts.get(player_obj.pk, 0))
        # Definitional bound: a 7-day count cannot exceed lifetime battles.
        # Allow a small slack so a refresh-lag mismatch doesn't drop a real
        # active player.
        pvp_battles = int(getattr(player_obj, 'pvp_battles', 0) or 0)
        if week_battles > pvp_battles + LANDING_RECENT_PLAYERS_PVP_SLACK:
            continue
        row = _serialize_landing_player_row(player_obj)
        row['week_battles'] = week_battles
        payload.append(row)
        if len(payload) >= LANDING_RECENT_PLAYERS_LIMIT:
            break
    return payload


def _get_landing_recent_players_snapshot(
    realm: str = DEFAULT_REALM,
) -> LandingRecentPlayersSnapshot | None:
    """Read the durable Tier-2 fallback row for the recent-players surface."""
    return (
        LandingRecentPlayersSnapshot.objects
        .filter(realm=realm)
        .only('payload_json', 'generated_at')
        .first()
    )


def materialize_landing_recent_players_snapshot(realm: str = DEFAULT_REALM) -> dict:
    """Rebuild the recent-players payload from PlayerDailyShipStats and write
    it to BOTH the durable DB snapshot AND the Redis cache.

    Write order is DB-first, Redis-second by design — if the Redis write
    fails after the DB write succeeds, the next read still finds Tier 2
    intact. The reverse ordering would silently degrade subsequent
    eviction-triggered reads to Tier 3 (slow inline rebuild) until the
    next warmer tick.

    Returns metadata + the freshly-built payload so `force_refresh=True`
    callers can avoid an immediate re-read.
    """
    # Normalize datetimes → ISO strings before the JSONField write
    # (mirrors `materialize_landing_player_best_snapshot`). Without this,
    # rows like `efficiency_rank_updated_at` blow up the JSON encoder.
    payload = _normalize_landing_player_snapshot_payload(
        _build_recent_players(realm=realm)
    )
    snapshot, _created = LandingRecentPlayersSnapshot.objects.update_or_create(
        realm=realm,
        defaults={'payload_json': payload},
    )
    cache.set(
        realm_cache_key(realm, LANDING_RECENT_PLAYERS_CACHE_KEY),
        payload,
        LANDING_RECENT_PLAYERS_CACHE_TTL,
    )
    return {
        'realm': realm,
        'count': len(payload),
        'generated_at': snapshot.generated_at.isoformat(),
        'payload': payload,
    }


def get_landing_recent_players_payload(force_refresh: bool = False, realm: str = DEFAULT_REALM) -> list[dict]:
    """3-tier fallback for the landing recent-players surface:

      Tier 1: Redis cache (`cache.get`) — steady-state ~5 ms.
      Tier 2: DB snapshot (`LandingRecentPlayersSnapshot`) — Redis evicted
              but the durable copy is intact (~10 ms). Re-warms Redis on
              the way out.
      Tier 3: Inline rebuild (`_build_recent_players`) — both stores
              empty (cold start, post-deploy first touch). Logs a warning
              because steady-state should never hit this.

    `force_refresh=True` (used by the periodic warmer) bypasses Tiers 1+2
    and runs the materializer, which writes BOTH stores.
    """
    cache_key = realm_cache_key(realm, LANDING_RECENT_PLAYERS_CACHE_KEY)
    if force_refresh:
        return list(materialize_landing_recent_players_snapshot(realm=realm)['payload'])
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    snapshot = _get_landing_recent_players_snapshot(realm=realm)
    if snapshot is not None:
        # Re-warm Redis with the same TTL=None as the materializer so the
        # next reader hits Tier 1.
        cache.set(cache_key, snapshot.payload_json,
                  LANDING_RECENT_PLAYERS_CACHE_TTL)
        # Defensive copy — JSONField returns the live model attribute
        # reference; downstream callers must not mutate it.
        return list(snapshot.payload_json)
    logger.warning(
        "recent_players: cold-start fallback to inline rebuild (realm=%s)",
        realm,
    )
    return list(materialize_landing_recent_players_snapshot(realm=realm)['payload'])


# Surface-scope sets for warm_landing_page_content(scope=...). Invalidation-
# driven republishes pass the family that actually changed so a clan write does
# not rebuild player surfaces (and vice versa). The periodic/startup warmers use
# scope='all'. recent_clans/recent_players are intentionally members of their
# family set; whether they actually rebuild is still gated by include_recent.
LANDING_CLAN_WARM_SURFACES = frozenset({
    'clans_best_overall', 'clans_best_wr', 'recent_clans',
})
LANDING_PLAYER_WARM_SURFACES = frozenset({
    'players_best_overall', 'players_best_ranked', 'players_best_efficiency',
    'players_best_wr', 'players_best_cb', 'players_popular', 'recent_players',
})


def warm_landing_page_content(force_refresh: bool = False, include_recent: bool = True, realm: str = DEFAULT_REALM, scope: str = 'all') -> dict:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # 'players_random' + 'clans' (the random-clan cache) were retired
    # alongside the Random landing pills on 2026-05-07; their warmers are
    # gone here so the periodic landing-page warm doesn't waste cycles
    # rebuilding caches no surface reads.
    surfaces = {
        'players_best_overall': lambda: len(get_landing_players_payload('best', LANDING_PLAYER_LIMIT, force_refresh=force_refresh, realm=realm, sort='overall')),
        'players_best_ranked': lambda: len(get_landing_players_payload('best', LANDING_PLAYER_LIMIT, force_refresh=force_refresh, realm=realm, sort='ranked')),
        'players_best_efficiency': lambda: len(get_landing_players_payload('best', LANDING_PLAYER_LIMIT, force_refresh=force_refresh, realm=realm, sort='efficiency')),
        'players_best_wr': lambda: len(get_landing_players_payload('best', LANDING_PLAYER_LIMIT, force_refresh=force_refresh, realm=realm, sort='wr')),
        'players_best_cb': lambda: len(get_landing_players_payload('best', LANDING_PLAYER_LIMIT, force_refresh=force_refresh, realm=realm, sort='cb')),
        'players_popular': lambda: len(get_landing_players_payload('popular', LANDING_PLAYER_LIMIT, force_refresh=force_refresh, realm=realm)),
        'clans_best_overall': lambda: len(get_landing_best_clans_payload(force_refresh=force_refresh, realm=realm, sort='overall')),
        'clans_best_wr': lambda: len(get_landing_best_clans_payload(force_refresh=force_refresh, realm=realm, sort='wr')),
        'recent_clans': lambda: len(get_landing_recent_clans_payload(force_refresh=force_refresh if include_recent else False, realm=realm)),
        'recent_players': lambda: len(get_landing_recent_players_payload(force_refresh=force_refresh if include_recent else False, realm=realm)),
    }

    # Narrow to the requested family. Unknown scope falls back to 'all' so a
    # bad caller never silently warms nothing.
    if scope == 'clans':
        surfaces = {k: v for k, v in surfaces.items() if k in LANDING_CLAN_WARM_SURFACES}
    elif scope == 'players':
        surfaces = {k: v for k, v in surfaces.items() if k in LANDING_PLAYER_WARM_SURFACES}

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
    if connection.vendor == 'sqlite':
        parallel = False
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

    # Clear only the dirty keys for the family we actually rebuilt. A clan-scoped
    # warm must NOT clear the players dirty key (it didn't rebuild player
    # surfaces) — doing so would strand a stale published player payload with no
    # pending republish until the next periodic warm.
    dirty_keys = []
    if scope in ('all', 'clans'):
        dirty_keys += [
            realm_cache_key(realm, LANDING_CLANS_DIRTY_KEY),
            realm_cache_key(realm, LANDING_RECENT_CLANS_DIRTY_KEY),
        ]
    if scope in ('all', 'players'):
        dirty_keys.append(realm_cache_key(realm, LANDING_PLAYERS_DIRTY_KEY))
    _clear_cache_family_dirty(*dirty_keys)
    return {'status': 'completed', 'warmed': warmed}
