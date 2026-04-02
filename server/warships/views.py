import logging
import random
from functools import partial
from datetime import timedelta
from hashlib import sha256
from kombu.exceptions import OperationalError as KombuOperationalError
from django.core.cache import cache
from django.db.models import Sum, F, FloatField, Case, When, Value, IntegerField, Count, Q
from django.db.models.functions import Cast, Lower
from django.http import Http404
from rest_framework import generics, permissions, viewsets
from rest_framework import status
from rest_framework.decorators import api_view, throttle_classes
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
from django.utils import timezone
from warships.models import DEFAULT_REALM, VALID_REALMS, Player, Clan, Ship, EntityVisitDaily, realm_cache_key
from warships.api.players import _fetch_player_id_by_name
from warships.serializers import PlayerSerializer, ClanSerializer, ShipSerializer, ActivityDataSerializer, \
    TierDataSerializer, TypeDataSerializer, RandomsDataSerializer, ClanDataSerializer, ClanMemberSerializer, \
    RankedDataSerializer, ClanBattleSeasonSummarySerializer, PlayerClanBattleSeasonSerializer, PlayerSummarySerializer, PlayerExplorerRowSerializer, \
    WRDistributionBinSerializer, PlayerPopulationDistributionSerializer, CompactPlayerCorrelationDistributionSerializer, PlayerCorrelationDistributionSerializer, PlayerExtendedCorrelationDistributionSerializer, RankedPlayerCorrelationDistributionSerializer, \
    PlayerTierTypeCorrelationSerializer, LandingActivityAttritionSerializer, EntityVisitIngestSerializer, EntityVisitIngestResponseSerializer, TopEntitiesQuerySerializer, TopEntityVisitSerializer
from warships.data import clan_detail_needs_refresh, clan_members_missing_or_incomplete, fetch_tier_data, fetch_activity_data, fetch_type_data, fetch_randoms_data, fetch_clan_plot_data, _extract_randoms_rows, \
    fetch_ranked_data, fetch_clan_battle_seasons, has_clan_battle_summary_cache, fetch_player_summary, \
    fetch_player_explorer_page, fetch_player_explorer_rows, fetch_wr_distribution, fetch_player_population_distribution, fetch_player_wr_survival_correlation, player_battle_data_needs_refresh, player_detail_needs_refresh, \
    fetch_player_tier_type_correlation, fetch_player_ranked_wr_battles_correlation, fetch_player_clan_battle_seasons, fetch_landing_activity_attrition, compute_player_verdict, _explorer_summary_needs_refresh, _get_published_efficiency_rank_payload, refresh_player_explorer_summary, update_battle_data, _calculate_tier_filtered_pvp_record, is_clan_battle_enjoyer, is_pve_player, is_ranked_player, \
    is_sleepy_player, get_highest_ranked_league_name
from warships.landing import get_landing_best_clans_payload_with_cache_metadata, get_landing_clans_payload_with_cache_metadata, get_landing_players_payload_with_cache_metadata, get_landing_recent_clans_payload, get_landing_recent_players_payload, get_random_landing_player_queue_payload, invalidate_landing_clan_caches, invalidate_landing_recent_player_cache, normalize_landing_clan_limit, normalize_landing_clan_mode, normalize_landing_player_limit, normalize_landing_player_mode
from warships.visit_analytics import get_top_entities, record_entity_visit
from warships.agentic.dashboard import get_agentic_trace_dashboard
from .tasks import is_clan_battle_summary_refresh_pending, is_ranked_data_refresh_pending, queue_landing_best_entity_warm, update_clan_data_task, update_player_data_task, update_clan_members_task

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


LAZY_REFRESH_DEDUP_TIMEOUT = 60  # seconds


def _get_realm(request) -> str:
    realm = (getattr(request, 'query_params', None)
             or request.GET).get('realm', DEFAULT_REALM)
    realm = (realm or DEFAULT_REALM).lower().strip()
    return realm if realm in VALID_REALMS else DEFAULT_REALM


def _delay_task_safely(task, **kwargs) -> None:
    task_name = getattr(task, 'name', repr(task))
    kw_hash = sha256(str(sorted(kwargs.items())).encode()).hexdigest()[:12]
    dedup_key = f'views_dedup:{task_name.rsplit(".", 1)[-1]}:{kw_hash}'
    if not cache.add(dedup_key, 1, timeout=LAZY_REFRESH_DEDUP_TIMEOUT):
        return
    try:
        task.delay(**kwargs)
    except KombuOperationalError as error:
        cache.delete(dedup_key)
        logging.warning(
            'Skipping async task enqueue for %s due to broker error: %s',
            task_name,
            error,
        )


def _record_clan_lookup(clan: Clan, realm: str = DEFAULT_REALM) -> None:
    clan.last_lookup = timezone.now()
    clan.save(update_fields=["last_lookup"])
    invalidate_landing_clan_caches(realm=realm)


PUBLIC_API_THROTTLES = [AnonRateThrottle, UserRateThrottle]
LANDING_CLAN_FEATURED_COUNT = 30
LANDING_CLAN_MIN_TOTAL_BATTLES = 100000
LANDING_RECENT_PLAYER_SCORE_WINDOW = 120
LANDING_PLAYER_LIMIT = 25
LANDING_PLAYER_RANDOM_MIN_PVP_BATTLES = 500
LANDING_PLAYER_BEST_MIN_PVP_BATTLES = 2500
LANDING_PLAYER_BEST_CANDIDATE_LIMIT = 400
PLAYER_EXPLORER_RESPONSE_CACHE_TTL = 60
MISSING_PLAYER_LOOKUP_CACHE_TTL = 600


def _missing_player_lookup_cache_key(player_name: str) -> str:
    normalized_name = (player_name or '').strip().casefold()
    return f'player:lookup:missing:v1:{normalized_name}'


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


def _player_score_ordering(secondary_field: str):
    return (
        F('explorer_summary__player_score').desc(nulls_last=True),
        F(secondary_field).desc(nulls_last=True),
        'name',
    )


class PlayerViewSet(viewsets.ModelViewSet):
    queryset = Player.objects.select_related('clan', 'explorer_summary').all()
    serializer_class = PlayerSerializer
    permission_classes = [permissions.AllowAny]

    def retrieve(self, request, *args, **kwargs):
        from warships.data import get_cached_player_detail

        realm = _get_realm(request)

        # Try bulk-loaded cache before hitting DB + serializer
        lookup_value = (self.kwargs.get(self.lookup_field) or '').strip()
        if lookup_value:
            player_id = Player.objects.alias(name_lower=Lower("name")).filter(
                name_lower=lookup_value.casefold(),
                realm=realm,
            ).values_list('player_id', flat=True).first()
            if player_id:
                cached = get_cached_player_detail(player_id, realm=realm)
                if cached is not None:
                    self._record_player_view(player_id, realm=realm)
                    if (
                        cached.get('actual_kdr') is None
                        and not cached.get('is_hidden')
                        and (cached.get('pvp_battles') or 0) > 0
                    ):
                        _delay_task_safely(
                            update_player_data_task,
                            player_id=player_id,
                            force_refresh=True,
                        )
                    response = Response(cached)
                    response['X-Player-Cache'] = 'hit'
                    return response

        response = super().retrieve(request, *args, **kwargs)
        response['X-Player-Cache'] = 'miss'
        return response

    def _record_player_view(self, player_id: int, update_last_lookup: bool = True, realm: str = DEFAULT_REALM) -> None:
        """Mark recent-players cache dirty, push to recently-viewed list.

        When ``update_last_lookup`` is True (cache-hit path), also bump the
        ``last_lookup`` timestamp in the database.  The cache-miss path already
        saves ``last_lookup`` on the model instance, so it passes False.
        """
        from warships.data import push_recently_viewed_player

        if update_last_lookup:
            Player.objects.filter(player_id=player_id, realm=realm).update(
                last_lookup=timezone.now())
        invalidate_landing_recent_player_cache(realm=realm)
        push_recently_viewed_player(player_id, realm=realm)

    def get_object(self):
        realm = _get_realm(self.request)
        lookup_field_value = self.kwargs[self.lookup_field]
        normalized_lookup_value = (lookup_field_value or '').strip()
        missing_lookup_cache_key = _missing_player_lookup_cache_key(
            normalized_lookup_value)
        try:
            obj = self.queryset.alias(name_lower=Lower("name")).get(
                name_lower=normalized_lookup_value.casefold(),
                realm=realm,
            )
            cache.delete(missing_lookup_cache_key)
        except Player.DoesNotExist:
            if cache.get(missing_lookup_cache_key):
                raise Http404("Player matching query does not exist.")

            player_id = _fetch_player_id_by_name(
                normalized_lookup_value, realm=realm)
            if not player_id:
                cache.set(missing_lookup_cache_key, True,
                          MISSING_PLAYER_LOOKUP_CACHE_TTL)
                raise Http404("Player matching query does not exist.")

            cache.delete(missing_lookup_cache_key)

            from warships.blocklist import is_account_blocked
            if is_account_blocked(int(player_id)):
                raise Http404("Player matching query does not exist.")

            obj, _ = Player.objects.get_or_create(
                player_id=int(player_id),
                realm=realm,
                defaults={"name": normalized_lookup_value}
            )

            from warships.data import update_player_data
            update_player_data(player=obj, force_refresh=True)
            obj.refresh_from_db()

        needs_efficiency_refresh = (
            not obj.is_hidden and
            obj.efficiency_json is None and
            obj.actual_kdr is not None and
            (obj.pvp_battles or 0) > 0
        )
        # Detect players ingested by the clan crawl before it populated
        # pvp_frags / pvp_survived_battles / actual_kdr.
        needs_kdr_backfill = (
            not obj.is_hidden and
            obj.actual_kdr is None and
            (obj.pvp_battles or 0) > 0
        )

        self.check_object_permissions(self.request, obj)

        now = timezone.now()

        # Record the last time this player profile was viewed via the API.
        obj.last_lookup = now
        update_fields = ["last_lookup"]

        if obj.verdict is None and not obj.is_hidden:
            inferred_verdict = compute_player_verdict(
                obj.pvp_battles or 0,
                obj.pvp_ratio,
                obj.pvp_survival_rate,
            )
            if inferred_verdict is not None:
                obj.verdict = inferred_verdict
                update_fields.append("verdict")

        obj.save(update_fields=update_fields)
        self._record_player_view(
            obj.player_id, update_last_lookup=False, realm=realm)

        if not obj.is_hidden and _explorer_summary_needs_refresh(obj):
            refresh_player_explorer_summary(obj)

        player_refresh_stale = player_detail_needs_refresh(obj)

        # When clan is still missing, force a refresh task so we do not get
        # stuck on fresh-but-incomplete player records.
        if not obj.clan:
            _delay_task_safely(
                update_player_data_task,
                player_id=obj.player_id,
                force_refresh=True,
            )
        elif needs_kdr_backfill or needs_efficiency_refresh:
            _delay_task_safely(
                update_player_data_task,
                player_id=obj.player_id,
                force_refresh=True,
            )
        elif player_refresh_stale:
            _delay_task_safely(update_player_data_task,
                               player_id=obj.player_id)

        if obj.clan:
            clan = obj.clan
            clan_refresh_stale = clan_detail_needs_refresh(clan)
            clan_members_incomplete = clan_members_missing_or_incomplete(clan)

            if clan_refresh_stale:
                logging.info(
                    f'Updating clan data: {obj.name} : {clan.name} {obj.player_id}')
                _delay_task_safely(update_clan_data_task, clan_id=clan.clan_id)

            if clan_refresh_stale or clan_members_incomplete:
                _delay_task_safely(update_clan_members_task,
                                   clan_id=clan.clan_id)

        if not obj.is_hidden and (obj.battles_json is None or player_battle_data_needs_refresh(obj)):
            from warships.tasks import update_battle_data_task

            _delay_task_safely(update_battle_data_task,
                               player_id=obj.player_id)

        from warships.data import maybe_refresh_clan_battle_data
        maybe_refresh_clan_battle_data(obj, realm=realm)

        return obj


class PlayerDetail(generics.RetrieveUpdateDestroyAPIView):
    queryset = Player.objects.select_related('clan', 'explorer_summary').all()
    serializer_class = PlayerSerializer
    lookup_field = 'name'
    permission_classes = [permissions.AllowAny]


class ClanViewSet(viewsets.ModelViewSet):
    queryset = Clan.objects.all()
    serializer_class = ClanSerializer
    lookup_field = 'clan_id'
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        realm = _get_realm(self.request)
        return Clan.objects.filter(realm=realm)

    def get_object(self):
        realm = _get_realm(self.request)
        obj = super().get_object()
        _record_clan_lookup(obj, realm=realm)
        if clan_detail_needs_refresh(obj):
            _delay_task_safely(update_clan_data_task, clan_id=obj.clan_id)
        if clan_members_missing_or_incomplete(obj):
            _delay_task_safely(update_clan_members_task, clan_id=obj.clan_id)
        return obj


class ClanDetail(generics.RetrieveUpdateDestroyAPIView):
    queryset = Clan.objects.all()
    serializer_class = ClanSerializer
    permission_classes = [permissions.AllowAny]


class ShipViewSet(viewsets.ModelViewSet):
    queryset = Ship.objects.all()
    serializer_class = ShipSerializer
    permission_classes = [permissions.AllowAny]


class ShipDetail(generics.RetrieveUpdateDestroyAPIView):
    queryset = Ship.objects.all()
    serializer_class = ShipSerializer
    permission_classes = [permissions.AllowAny]


def _validated_list_response(data, serializer_class):
    serializer = serializer_class(data=data, many=True)
    serializer.is_valid(raise_exception=True)
    return Response(serializer.data)


def _validated_single_response(data, serializer_class):
    serializer = serializer_class(data=data)
    serializer.is_valid(raise_exception=True)
    return Response(serializer.data)


def _player_explorer_response_cache_key(params: dict[str, object]) -> str:
    parts = [
        f"{key}={params[key]}"
        for key in sorted(params)
    ]
    digest = sha256('&'.join(parts).encode('utf-8')).hexdigest()
    return f'players:explorer:response:v1:{digest}'


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def tier_data(request, player_id: str) -> Response:
    realm = _get_realm(request)
    data = fetch_tier_data(player_id, realm=realm)
    return _validated_list_response(data, TierDataSerializer)


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def activity_data(request, player_id: str) -> Response:
    realm = _get_realm(request)
    data = fetch_activity_data(player_id, realm=realm)
    return _validated_list_response(data, ActivityDataSerializer)


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def type_data(request, player_id: str) -> Response:
    realm = _get_realm(request)
    data = fetch_type_data(player_id, realm=realm)
    return _validated_list_response(data, TypeDataSerializer)


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def randoms_data(request, player_id: str) -> Response:
    realm = _get_realm(request)
    fetch_all = request.query_params.get('all', '').lower() in ('true', '1')

    if fetch_all:
        # Prefer the full source cache, but fall back to derived randoms rows so
        # the player page does not blank out while source data is repopulating.
        cached_randoms_rows = fetch_randoms_data(player_id, realm=realm)
        player = Player.objects.filter(
            player_id=player_id, realm=realm).first()
        if not player:
            data = []
        elif player.battles_json:
            data = _extract_randoms_rows(player.battles_json, limit=None)
        else:
            data = _extract_randoms_rows(
                player.randoms_json, limit=None) or cached_randoms_rows
    else:
        data = fetch_randoms_data(player_id, realm=realm)

    response = _validated_list_response(data, RandomsDataSerializer)

    player = Player.objects.filter(player_id=player_id, realm=realm).first()
    if player and player.randoms_updated_at:
        response["X-Randoms-Updated-At"] = player.randoms_updated_at.isoformat()
    if player and player.battles_updated_at:
        response["X-Battles-Updated-At"] = player.battles_updated_at.isoformat()

    return response


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def ranked_data(request, player_id: str) -> Response:
    realm = _get_realm(request)
    data = fetch_ranked_data(player_id, realm=realm)
    response = _validated_list_response(data, RankedDataSerializer)

    player = Player.objects.filter(player_id=player_id, realm=realm).first()
    if not data and is_ranked_data_refresh_pending(player_id):
        response["X-Ranked-Pending"] = "true"
    if player and player.ranked_updated_at:
        response["X-Ranked-Updated-At"] = player.ranked_updated_at.isoformat()

    return response


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def player_summary(request, player_id: str) -> Response:
    realm = _get_realm(request)
    try:
        data = fetch_player_summary(player_id, realm=realm)
    except Player.DoesNotExist:
        return Response({'detail': 'Player not found.'}, status=status.HTTP_404_NOT_FOUND)

    return _validated_single_response(data, PlayerSummarySerializer)


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def wr_distribution(request) -> Response:
    realm = _get_realm(request)
    data = fetch_wr_distribution(realm=realm)
    return _validated_list_response(data, WRDistributionBinSerializer)


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def player_distribution(request, metric: str) -> Response:
    realm = _get_realm(request)
    try:
        data = fetch_player_population_distribution(metric, realm=realm)
    except ValueError:
        return Response({'detail': 'Unsupported player distribution metric.'}, status=status.HTTP_404_NOT_FOUND)

    return _validated_single_response(data, PlayerPopulationDistributionSerializer)


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def player_correlation_distribution(request, metric: str, player_id: str | None = None) -> Response:
    realm = _get_realm(request)
    if metric == 'win_rate_survival' and player_id is None:
        data = fetch_player_wr_survival_correlation(realm=realm)
        return _validated_single_response(data, CompactPlayerCorrelationDistributionSerializer)

    if metric == 'ranked_wr_battles' and player_id is not None:
        try:
            data = fetch_player_ranked_wr_battles_correlation(
                player_id, realm=realm)
        except Player.DoesNotExist:
            return Response({'detail': 'Player not found.'}, status=status.HTTP_404_NOT_FOUND)
        except Exception:
            logger.exception(
                "ranked_wr_battles correlation failed for player_id=%s", player_id)
            data = {
                'metric': 'ranked_wr_battles',
                'label': 'Ranked Games vs Win Rate',
                'x_label': 'Total Ranked Games',
                'y_label': 'Ranked Win Rate',
                'x_scale': 'log',
                'y_scale': 'linear',
                'x_ticks': [50.0, 100.0],
                'x_edges': [50.0, 100.0],
                'tracked_population': 0,
                'correlation': None,
                'y_domain': {'min': 35.0, 'max': 75.0, 'bin_width': 0.75},
                'tiles': [],
                'trend': [],
                'player_point': None,
                '_pending': True,
            }

        is_pending = data.pop('_pending', False)
        response = _validated_single_response(
            data, RankedPlayerCorrelationDistributionSerializer)
        if is_pending:
            response['X-Ranked-WR-Battles-Pending'] = 'true'
        return response

    if metric == 'tier_type' and player_id is not None:
        try:
            player = Player.objects.only(
                'player_id', 'battles_json').get(player_id=player_id, realm=realm)
            data = fetch_player_tier_type_correlation(
                player_id, player=player, realm=realm)
        except Player.DoesNotExist:
            return Response({'detail': 'Player not found.'}, status=status.HTTP_404_NOT_FOUND)

        response = _validated_single_response(
            data, PlayerTierTypeCorrelationSerializer)
        if not player.battles_json and not data.get('player_cells'):
            response['X-Tier-Type-Pending'] = 'true'
        return response

    return Response({'detail': 'Unsupported player correlation metric.'}, status=status.HTTP_404_NOT_FOUND)


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def players_explorer(request) -> Response:
    realm = _get_realm(request)
    query = (request.query_params.get('q') or '').strip()
    hidden = (request.query_params.get('hidden') or 'all').strip().lower()
    activity_bucket = (request.query_params.get(
        'activity_bucket') or 'all').strip().lower()
    ranked = (request.query_params.get('ranked') or 'all').strip().lower()
    sort = (request.query_params.get('sort')
            or 'player_score').strip()
    direction = (request.query_params.get(
        'direction') or 'desc').strip().lower()

    try:
        min_pvp_battles = max(
            int(request.query_params.get('min_pvp_battles') or 0), 0)
    except ValueError:
        return Response({'detail': 'min_pvp_battles must be an integer.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        page = max(int(request.query_params.get('page') or 1), 1)
        page_size = min(
            max(int(request.query_params.get('page_size') or 25), 1), 100)
    except ValueError:
        return Response({'detail': 'page and page_size must be integers.'}, status=status.HTTP_400_BAD_REQUEST)

    allowed_hidden = {'all', 'visible', 'hidden'}
    allowed_activity_buckets = {'all', '7d', '30d', '90d', 'dormant90plus'}
    allowed_ranked = {'all', 'yes', 'no'}
    allowed_sorts = {
        'name',
        'days_since_last_battle',
        'pvp_ratio',
        'pvp_battles',
        'pvp_survival_rate',
        'kill_ratio',
        'player_score',
        'account_age_days',
        'battles_last_29_days',
        'active_days_last_29_days',
        'ships_played_total',
        'ranked_seasons_participated',
    }

    if hidden not in allowed_hidden:
        return Response({'detail': 'hidden must be one of: all, visible, hidden'}, status=status.HTTP_400_BAD_REQUEST)
    if activity_bucket not in allowed_activity_buckets:
        return Response({'detail': 'activity_bucket must be one of: all, 7d, 30d, 90d, dormant90plus'}, status=status.HTTP_400_BAD_REQUEST)
    if ranked not in allowed_ranked:
        return Response({'detail': 'ranked must be one of: all, yes, no'}, status=status.HTTP_400_BAD_REQUEST)
    if sort not in allowed_sorts:
        return Response({'detail': 'sort must be a supported field.'}, status=status.HTTP_400_BAD_REQUEST)
    if direction not in {'asc', 'desc'}:
        return Response({'detail': 'direction must be asc or desc.'}, status=status.HTTP_400_BAD_REQUEST)

    cache_key = _player_explorer_response_cache_key({
        'activity_bucket': activity_bucket,
        'direction': direction,
        'hidden': hidden,
        'min_pvp_battles': min_pvp_battles,
        'page': page,
        'page_size': page_size,
        'q': query,
        'ranked': ranked,
        'realm': realm,
        'sort': sort,
    })
    cached_payload = cache.get(cache_key)
    if cached_payload is not None:
        response = Response(cached_payload)
        response['X-Players-Explorer-Cache'] = 'hit'
        response['X-Players-Explorer-Cache-TTL-Seconds'] = str(
            PLAYER_EXPLORER_RESPONSE_CACHE_TTL)
        return response

    total_count, page_rows = fetch_player_explorer_page(
        query=query,
        hidden=hidden,
        activity_bucket=activity_bucket,
        ranked=ranked,
        min_pvp_battles=min_pvp_battles,
        sort=sort,
        direction=direction,
        page=page,
        page_size=page_size,
        realm=realm,
    )

    serializer = PlayerExplorerRowSerializer(data=page_rows, many=True)
    serializer.is_valid(raise_exception=True)
    payload = {
        'count': total_count,
        'page': page,
        'page_size': page_size,
        'results': serializer.data,
    }
    cache.set(cache_key, payload, PLAYER_EXPLORER_RESPONSE_CACHE_TTL)
    response = Response(payload)
    response['X-Players-Explorer-Cache'] = 'miss'
    response['X-Players-Explorer-Cache-TTL-Seconds'] = str(
        PLAYER_EXPLORER_RESPONSE_CACHE_TTL)
    return response


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def clan_members(request, clan_id: str) -> Response:
    if not clan_id or clan_id in {"null", "None", "undefined"}:
        return Response([])

    realm = _get_realm(request)
    try:
        clan = Clan.objects.get(clan_id=clan_id, realm=realm)
    except Clan.DoesNotExist:
        return Response([])

    _record_clan_lookup(clan, realm=realm)

    from warships.data import queue_clan_efficiency_hydration, queue_clan_ranked_hydration, clan_battle_summary_is_stale, maybe_refresh_clan_battle_data, CLAN_BATTLE_PLAYER_HYDRATION_MAX_IN_FLIGHT
    local_member_count = clan.player_set.exclude(name='').count()
    needs_clan_refresh = (
        not clan.members_count
        or (clan.leader_id is None and not clan.leader_name)
        or clan_detail_needs_refresh(clan)
    )
    needs_member_refresh = local_member_count == 0 or (
        clan.members_count and local_member_count < clan.members_count
    )

    if needs_clan_refresh:
        _delay_task_safely(update_clan_data_task, clan_id=clan_id)
    if needs_member_refresh:
        _delay_task_safely(update_clan_members_task, clan_id=clan_id)

    # B1: Check response cache before doing expensive member serialization
    CLAN_MEMBERS_CACHE_TTL = 300  # 5 minutes
    cache_key = realm_cache_key(realm, f'clan:members:{clan_id}')
    cached = cache.get(cache_key)
    if cached is not None:
        response = Response(cached)
        response['X-Clan-Members-Cache'] = 'hit'
        return response

    members = clan.player_set.select_related('explorer_summary').exclude(name='').order_by(
        *_player_score_ordering('last_battle_date'))

    members = list(members)
    hydration_state = queue_clan_ranked_hydration(members, realm=realm)
    pending_player_ids = hydration_state['pending_player_ids']
    efficiency_hydration_state = queue_clan_efficiency_hydration(
        members, realm=realm)
    pending_efficiency_player_ids = efficiency_hydration_state['pending_player_ids']

    leader_name = (clan.leader_name or '').strip().lower()
    member_rows = [
        {
            'name': member.name,
            'is_hidden': member.is_hidden,
            'pvp_ratio': member.pvp_ratio,
            'days_since_last_battle': member.days_since_last_battle,
            'is_leader': (
                (clan.leader_id is not None and member.player_id == clan.leader_id)
                or (leader_name and member.name.strip().lower() == leader_name)
            ),
            'is_pve_player': is_pve_player(member.total_battles, member.pvp_battles),
            'is_sleepy_player': is_sleepy_player(member.days_since_last_battle),
            'is_ranked_player': is_ranked_player(member.ranked_json),
            'is_clan_battle_player': is_clan_battle_enjoyer(
                getattr(getattr(member, 'explorer_summary', None),
                        'clan_battle_total_battles', None),
                getattr(getattr(member, 'explorer_summary', None),
                        'clan_battle_seasons_participated', None),
            ),
            'clan_battle_win_rate': getattr(getattr(member, 'explorer_summary', None), 'clan_battle_overall_win_rate', None),
            'efficiency_hydration_pending': member.player_id in pending_efficiency_player_ids,
            'highest_ranked_league': get_highest_ranked_league_name(member.ranked_json),
            'ranked_hydration_pending': member.player_id in pending_player_ids,
            'ranked_updated_at': member.ranked_updated_at,
            **_get_published_efficiency_rank_payload(member),
        }
        for member in members
    ]

    serializer = ClanMemberSerializer(member_rows, many=True)
    serialized_data = serializer.data

    # B1: Only cache when hydration is complete — pending responses must not
    # be cached or the client poll loop will see stale "pending" flags forever.
    has_pending = pending_player_ids or pending_efficiency_player_ids
    if not has_pending:
        cache.set(cache_key, serialized_data, CLAN_MEMBERS_CACHE_TTL)

    response = Response(serialized_data)
    response['X-Clan-Members-Cache'] = 'miss' if not has_pending else 'skip-pending'
    response['X-Ranked-Hydration-Queued'] = str(
        len(hydration_state['queued_player_ids']))
    response['X-Ranked-Hydration-Deferred'] = str(
        len(hydration_state['deferred_player_ids']))
    response['X-Ranked-Hydration-Pending'] = str(
        len(hydration_state['pending_player_ids']))
    response['X-Ranked-Hydration-Max-In-Flight'] = str(
        hydration_state['max_in_flight'])
    response['X-Efficiency-Hydration-Queued'] = str(
        len(efficiency_hydration_state['queued_player_ids']))
    response['X-Efficiency-Hydration-Deferred'] = str(
        len(efficiency_hydration_state['deferred_player_ids']))
    response['X-Efficiency-Hydration-Pending'] = str(
        len(efficiency_hydration_state['pending_player_ids']))
    response['X-Efficiency-Hydration-Max-In-Flight'] = str(
        efficiency_hydration_state['max_in_flight'])
    stale_members = [m for m in members if clan_battle_summary_is_stale(m)]
    for member in stale_members[:CLAN_BATTLE_PLAYER_HYDRATION_MAX_IN_FLIGHT]:
        maybe_refresh_clan_battle_data(member, realm=realm)
    return response


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def clan_data(request, clan_filter: str) -> Response:
    if ':' in clan_filter:
        clan_id, filter_type = clan_filter.split(':', 1)
    else:
        clan_id, filter_type = clan_filter, 'active'

    if filter_type not in {'active', 'all'}:
        return Response(
            {'detail': "filter_type must be one of: 'active', 'all'"},
            status=status.HTTP_400_BAD_REQUEST
        )

    realm = _get_realm(request)
    clan = Clan.objects.filter(clan_id=clan_id, realm=realm).first()
    if clan is not None:
        _record_clan_lookup(clan, realm=realm)

    data = fetch_clan_plot_data(
        clan_id=clan_id, filter_type=filter_type, realm=realm)
    response = _validated_list_response(data, ClanDataSerializer)

    if clan is not None and not data:
        cache_key = f'clan:plot:v1:{clan_id}:{filter_type}'
        member_count = clan.player_set.exclude(name='').count()
        has_cached_plot = cache.get(cache_key) is not None

        if (
            not has_cached_plot
            or clan_detail_needs_refresh(clan)
            or clan_members_missing_or_incomplete(clan, member_count=member_count)
        ):
            response['X-Clan-Plot-Pending'] = 'true'

    return response


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def clan_tier_distribution(request, clan_id: str) -> Response:
    realm = _get_realm(request)
    clan = Clan.objects.filter(clan_id=clan_id, realm=realm).first()
    if clan is not None:
        _record_clan_lookup(clan, realm=realm)

    cache_key = realm_cache_key(realm, f'clan:tiers:v3:{clan_id}')
    cached = cache.get(cache_key)

    if cached is not None:
        response = Response(cached)
        pending_key = realm_cache_key(realm, f'clan:tiers:v3:{clan_id}:pending')
        if cache.get(pending_key):
            response['X-Clan-Tiers-Pending'] = 'true'
        return response

    from warships.tasks import update_clan_tier_distribution_task
    _delay_task_safely(update_clan_tier_distribution_task,
                       clan_id=clan_id, realm=realm)

    response = Response([])
    response['X-Clan-Tiers-Pending'] = 'true'
    return response


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def clan_battle_seasons(request, clan_id: str) -> Response:
    realm = _get_realm(request)
    clan = Clan.objects.filter(clan_id=clan_id, realm=realm).first()
    if clan is not None:
        _record_clan_lookup(clan, realm=realm)

    had_cached_summary = has_clan_battle_summary_cache(clan_id)
    data = fetch_clan_battle_seasons(clan_id, realm=realm)
    response = _validated_list_response(
        data, ClanBattleSeasonSummarySerializer)
    if not data and (
        not had_cached_summary or is_clan_battle_summary_refresh_pending(
            clan_id)
    ):
        response["X-Clan-Battles-Pending"] = "true"

    return response


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def player_clan_battle_seasons(request, player_id: str) -> Response:
    realm = _get_realm(request)
    player = Player.objects.select_related(
        'clan').filter(player_id=player_id, realm=realm).first()

    try:
        data = fetch_player_clan_battle_seasons(player_id)
    except Exception:
        logger.exception(
            'Player clan battle seasons endpoint failed for player_id=%s player_name=%s clan_id=%s clan_name=%s',
            player_id,
            getattr(player, 'name', None),
            getattr(getattr(player, 'clan', None), 'clan_id', None),
            getattr(getattr(player, 'clan', None), 'name', None),
        )
        raise

    return _validated_list_response(data, PlayerClanBattleSeasonSerializer)


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def landing_activity_attrition(request) -> Response:
    realm = _get_realm(request)
    data = fetch_landing_activity_attrition(realm=realm)
    return _validated_single_response(data, LandingActivityAttritionSerializer)


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def landing_clans(request) -> Response:
    try:
        mode = normalize_landing_clan_mode(request.query_params.get('mode'))
    except ValueError:
        return Response({'detail': 'mode must be one of: random, best'}, status=status.HTTP_400_BAD_REQUEST)

    realm = _get_realm(request)
    limit = normalize_landing_clan_limit(request.query_params.get('limit'))
    if mode == 'random':
        payload, cache_metadata = get_landing_clans_payload_with_cache_metadata(
            realm=realm)
    else:
        payload, cache_metadata = get_landing_best_clans_payload_with_cache_metadata(
            realm=realm)

    payload = payload[:limit]

    response = Response(payload)
    response['X-Landing-Clans-Cache-Mode'] = mode
    response['X-Landing-Clans-Cache-TTL-Seconds'] = str(
        cache_metadata['ttl_seconds'])
    response['X-Landing-Clans-Cache-Cached-At'] = str(
        cache_metadata['cached_at'])
    response['X-Landing-Clans-Cache-Expires-At'] = str(
        cache_metadata['expires_at'])
    return response


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def landing_recent_clans(request) -> Response:
    realm = _get_realm(request)
    return Response(get_landing_recent_clans_payload(realm=realm))


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def landing_players(request) -> Response:
    try:
        mode = normalize_landing_player_mode(request.query_params.get('mode'))
    except ValueError:
        return Response({'detail': 'mode must be one of: random, best'}, status=status.HTTP_400_BAD_REQUEST)
    realm = _get_realm(request)
    limit = normalize_landing_player_limit(request.query_params.get('limit'))
    payload, cache_metadata = get_landing_players_payload_with_cache_metadata(
        mode=mode,
        limit=limit,
        realm=realm,
    )
    response = Response(payload)
    response['X-Landing-Players-Cache-Mode'] = mode
    response['X-Landing-Players-Cache-TTL-Seconds'] = str(
        cache_metadata['ttl_seconds'])
    response['X-Landing-Players-Cache-Cached-At'] = str(
        cache_metadata['cached_at'])
    response['X-Landing-Players-Cache-Expires-At'] = str(
        cache_metadata['expires_at'])
    return response


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def landing_recent_players(request) -> Response:
    realm = _get_realm(request)
    return Response(get_landing_recent_players_payload(realm=realm))


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def landing_best_warmup(request) -> Response:
    realm = _get_realm(request)
    result = queue_landing_best_entity_warm(
        player_limit=LANDING_PLAYER_LIMIT,
        clan_limit=LANDING_PLAYER_LIMIT,
        realm=realm,
    )
    status_code = status.HTTP_202_ACCEPTED if result.get(
        'status') == 'queued' else status.HTTP_200_OK
    return Response(result, status=status_code)


@api_view(["GET"])
def sitemap_entities(request) -> Response:
    """Return recently-visited players and clans for sitemap generation."""
    cutoff = (timezone.now() - timedelta(days=30)).date()

    player_visits = (
        EntityVisitDaily.objects
        .filter(entity_type='player', date__gte=cutoff)
        .values('entity_id', 'entity_name_snapshot')
        .annotate(total_views=Sum('views_deduped'))
        .filter(total_views__gte=2)
        .order_by('-total_views')[:200]
    )

    clan_visits = (
        EntityVisitDaily.objects
        .filter(entity_type='clan', date__gte=cutoff)
        .values('entity_id', 'entity_name_snapshot')
        .annotate(total_views=Sum('views_deduped'))
        .filter(total_views__gte=2)
        .order_by('-total_views')[:100]
    )

    return Response({
        'players': [
            {'name': v['entity_name_snapshot'], 'entity_id': v['entity_id']}
            for v in player_visits
        ],
        'clans': [
            {'name': v['entity_name_snapshot'], 'clan_id': v['entity_id']}
            for v in clan_visits
        ],
    })


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def player_name_suggestions(request) -> Response:
    query = (request.query_params.get('q') or '').strip()
    if len(query) < 3:
        return Response([])

    realm = _get_realm(request)
    cache_key = f'{realm}:suggest:{query.lower()}'
    cached = cache.get(cache_key)
    if cached is not None:
        return Response(cached)

    # Raw SQL with ILIKE so the pg_trgm GIN index (player_name_trgm_idx) is used.
    # Django's icontains generates UPPER(col) LIKE UPPER(pat) which bypasses trigram indexes.
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT name, pvp_ratio, is_hidden
            FROM warships_player
            WHERE name != '' AND realm = %s AND name ILIKE %s
            ORDER BY
                CASE WHEN name ILIKE %s THEN 0 ELSE 1 END,
                last_battle_date DESC NULLS LAST,
                name
            LIMIT 8
            """,
            [realm, f'%{query}%', f'{query}%'],
        )
        columns = [col[0] for col in cursor.description]
        suggestions = [dict(zip(columns, row)) for row in cursor.fetchall()]

    cache.set(cache_key, suggestions, timeout=600)
    return Response(suggestions)


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def db_stats(request) -> Response:
    realm = _get_realm(request)

    def _fetch_db_stats():
        return {
            'players': Player.objects.filter(realm=realm).count(),
            'clans': Clan.objects.filter(realm=realm).count(),
        }
    data = cache.get_or_set(f'{realm}:db:stats', _fetch_db_stats, 300)
    return Response(data)


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def agentic_trace_dashboard(request) -> Response:
    data = cache.get_or_set(
        'agentic:trace_dashboard:v2',
        partial(get_agentic_trace_dashboard, limit=12),
        15,
    )
    return Response(data)


@api_view(["POST"])
@throttle_classes(PUBLIC_API_THROTTLES)
def analytics_entity_view(request) -> Response:
    serializer = EntityVisitIngestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    result = record_entity_visit(
        serializer.validated_data,
        user_agent=request.META.get('HTTP_USER_AGENT', ''),
    )

    response_serializer = EntityVisitIngestResponseSerializer(data=result)
    response_serializer.is_valid(raise_exception=True)
    status_code = status.HTTP_201_CREATED if result['accepted'] else status.HTTP_200_OK
    return Response(response_serializer.data, status=status_code)


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def analytics_top_entities(request) -> Response:
    serializer = TopEntitiesQuerySerializer(data=request.query_params)
    serializer.is_valid(raise_exception=True)

    rows = get_top_entities(**serializer.validated_data)
    response_serializer = TopEntityVisitSerializer(data=rows, many=True)
    response_serializer.is_valid(raise_exception=True)
    return Response(response_serializer.data)
