import logging
import random
from functools import partial
from datetime import timedelta
from kombu.exceptions import OperationalError as KombuOperationalError
from django.core.cache import cache
from django.db.models import Sum, F, FloatField, Case, When, Value, IntegerField, Count, Q
from django.db.models.functions import Cast
from django.http import Http404
from rest_framework import generics, permissions, viewsets
from rest_framework import status
from rest_framework.decorators import api_view, throttle_classes
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
from django.utils import timezone
from warships.models import Player, Clan, Ship
from warships.api.players import _fetch_player_id_by_name
from warships.serializers import PlayerSerializer, ClanSerializer, ShipSerializer, ActivityDataSerializer, \
    TierDataSerializer, TypeDataSerializer, RandomsDataSerializer, ClanDataSerializer, ClanMemberSerializer, \
    RankedDataSerializer, ClanBattleSeasonSummarySerializer, PlayerClanBattleSeasonSerializer, PlayerSummarySerializer, PlayerExplorerRowSerializer, \
    WRDistributionBinSerializer, PlayerPopulationDistributionSerializer, PlayerCorrelationDistributionSerializer, PlayerExtendedCorrelationDistributionSerializer, \
    PlayerTierTypeCorrelationSerializer, LandingActivityAttritionSerializer, EntityVisitIngestSerializer, EntityVisitIngestResponseSerializer, TopEntitiesQuerySerializer, TopEntityVisitSerializer
from warships.data import fetch_tier_data, fetch_activity_data, fetch_type_data, fetch_randoms_data, fetch_clan_plot_data, _extract_randoms_rows, \
    fetch_ranked_data, fetch_clan_battle_seasons, has_clan_battle_summary_cache, fetch_player_summary, \
    fetch_player_explorer_rows, fetch_wr_distribution, fetch_player_population_distribution, fetch_player_wr_survival_correlation, \
    fetch_player_tier_type_correlation, fetch_player_ranked_wr_battles_correlation, fetch_player_clan_battle_seasons, fetch_landing_activity_attrition, compute_player_verdict, _explorer_summary_needs_refresh, _get_published_efficiency_rank_payload, refresh_player_explorer_summary, update_battle_data, _calculate_tier_filtered_pvp_record, get_player_clan_battle_summaries, get_player_clan_battle_summary, is_clan_battle_enjoyer, is_pve_player, is_ranked_player, \
    is_sleepy_player, get_highest_ranked_league_name
from warships.landing import get_landing_clans_payload_with_cache_metadata, get_landing_players_payload_with_cache_metadata, get_landing_recent_clans_payload, get_landing_recent_players_payload, invalidate_landing_clan_caches, invalidate_landing_recent_player_cache, normalize_landing_player_limit, normalize_landing_player_mode
from warships.visit_analytics import get_top_entities, record_entity_visit
from warships.agentic.dashboard import get_agentic_trace_dashboard
from .tasks import update_clan_data_task, update_player_data_task, update_clan_members_task
from .tasks import update_clan_battle_summary_task

logging.basicConfig(level=logging.INFO)


def _delay_task_safely(task, **kwargs) -> None:
    try:
        task.delay(**kwargs)
    except KombuOperationalError as error:
        logging.warning(
            'Skipping async task enqueue for %s due to broker error: %s',
            getattr(task, 'name', repr(task)),
            error,
        )


def _record_clan_lookup(clan: Clan) -> None:
    clan.last_lookup = timezone.now()
    clan.save(update_fields=["last_lookup"])
    invalidate_landing_clan_caches()


PUBLIC_API_THROTTLES = [AnonRateThrottle, UserRateThrottle]
LANDING_CLAN_FEATURED_COUNT = 40
LANDING_CLAN_MIN_TOTAL_BATTLES = 100000
LANDING_RECENT_PLAYER_SCORE_WINDOW = 120
LANDING_PLAYER_LIMIT = 40
LANDING_PLAYER_RANDOM_MIN_PVP_BATTLES = 500
LANDING_PLAYER_BEST_MIN_PVP_BATTLES = 2500
LANDING_PLAYER_BEST_CANDIDATE_LIMIT = 400


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

    def get_object(self):
        lookup_field_value = self.kwargs[self.lookup_field]
        try:
            obj = self.queryset.get(name__iexact=lookup_field_value)
            if not obj.clan:
                from warships.data import update_player_data
                update_player_data(player=obj, force_refresh=True)
                obj.refresh_from_db()
        except Player.DoesNotExist:
            player_id = _fetch_player_id_by_name(lookup_field_value)
            if not player_id:
                raise Http404("Player matching query does not exist.")

            obj, _ = Player.objects.get_or_create(
                player_id=int(player_id),
                defaults={"name": lookup_field_value.strip()}
            )

            from warships.data import update_player_data
            update_player_data(player=obj, force_refresh=True)
            obj.refresh_from_db()

        if obj.clan and (not obj.clan.name or not obj.clan.last_fetch):
            from warships.data import update_clan_data
            update_clan_data(obj.clan.clan_id)
            obj.refresh_from_db()

        if not obj.is_hidden and not obj.battles_json and (obj.pvp_battles or 0) > 0:
            update_battle_data(obj.player_id)
            obj.refresh_from_db()

        if not obj.is_hidden and (obj.efficiency_json is None or obj.actual_kdr is None) and (obj.pvp_battles or 0) > 0:
            from warships.data import update_player_data
            update_player_data(player=obj, force_refresh=True)
            obj.refresh_from_db()

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
        invalidate_landing_recent_player_cache()

        if not obj.is_hidden and _explorer_summary_needs_refresh(obj):
            refresh_player_explorer_summary(obj)

        player_refresh_stale = not obj.last_fetch or (
            now - obj.last_fetch) > timedelta(minutes=15)

        # When clan is still missing, force a refresh task so we do not get
        # stuck on fresh-but-incomplete player records.
        if not obj.clan:
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
            clan_refresh_stale = not clan.last_fetch or (
                now - clan.last_fetch) > timedelta(hours=12)
            clan_members_incomplete = not clan.members_count or clan.player_set.count() < clan.members_count

            if clan_refresh_stale:
                logging.info(
                    f'Updating clan data: {obj.name} : {clan.name} {obj.player_id}')
                _delay_task_safely(update_clan_data_task, clan_id=clan.clan_id)

            if clan_refresh_stale or clan_members_incomplete:
                _delay_task_safely(update_clan_members_task,
                                   clan_id=clan.clan_id)
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

    def get_object(self):
        obj = super().get_object()
        _record_clan_lookup(obj)
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


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def tier_data(request, player_id: str) -> Response:
    data = fetch_tier_data(player_id)
    return _validated_list_response(data, TierDataSerializer)


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def activity_data(request, player_id: str) -> Response:
    data = fetch_activity_data(player_id)
    return _validated_list_response(data, ActivityDataSerializer)


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def type_data(request, player_id: str) -> Response:
    data = fetch_type_data(player_id)
    return _validated_list_response(data, TypeDataSerializer)


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def randoms_data(request, player_id: str) -> Response:
    fetch_all = request.query_params.get('all', '').lower() in ('true', '1')

    if fetch_all:
        # Return all ships from battles_json (sorted by pvp_battles desc)
        # while still triggering any staleness refresh via fetch_randoms_data
        fetch_randoms_data(player_id)
        player = Player.objects.filter(player_id=player_id).first()
        if not player or not player.battles_json:
            return Response([])
        data = _extract_randoms_rows(player.battles_json, limit=None)
    else:
        data = fetch_randoms_data(player_id)

    response = _validated_list_response(data, RandomsDataSerializer)

    player = Player.objects.filter(player_id=player_id).first()
    if player and player.randoms_updated_at:
        response["X-Randoms-Updated-At"] = player.randoms_updated_at.isoformat()
    if player and player.battles_updated_at:
        response["X-Battles-Updated-At"] = player.battles_updated_at.isoformat()

    return response


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def ranked_data(request, player_id: str) -> Response:
    data = fetch_ranked_data(player_id)
    response = _validated_list_response(data, RankedDataSerializer)

    player = Player.objects.filter(player_id=player_id).first()
    if player and player.ranked_updated_at:
        response["X-Ranked-Updated-At"] = player.ranked_updated_at.isoformat()

    return response


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def player_summary(request, player_id: str) -> Response:
    try:
        data = fetch_player_summary(player_id)
    except Player.DoesNotExist:
        return Response({'detail': 'Player not found.'}, status=status.HTTP_404_NOT_FOUND)

    return _validated_single_response(data, PlayerSummarySerializer)


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def wr_distribution(request) -> Response:
    data = fetch_wr_distribution()
    return _validated_list_response(data, WRDistributionBinSerializer)


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def player_distribution(request, metric: str) -> Response:
    try:
        data = fetch_player_population_distribution(metric)
    except ValueError:
        return Response({'detail': 'Unsupported player distribution metric.'}, status=status.HTTP_404_NOT_FOUND)

    return _validated_single_response(data, PlayerPopulationDistributionSerializer)


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def player_correlation_distribution(request, metric: str, player_id: str | None = None) -> Response:
    if metric == 'win_rate_survival' and player_id is None:
        data = fetch_player_wr_survival_correlation()
        return _validated_single_response(data, PlayerCorrelationDistributionSerializer)

    if metric == 'ranked_wr_battles' and player_id is not None:
        try:
            data = fetch_player_ranked_wr_battles_correlation(player_id)
        except Player.DoesNotExist:
            return Response({'detail': 'Player not found.'}, status=status.HTTP_404_NOT_FOUND)

        return _validated_single_response(data, PlayerExtendedCorrelationDistributionSerializer)

    if metric == 'tier_type' and player_id is not None:
        try:
            data = fetch_player_tier_type_correlation(player_id)
        except Player.DoesNotExist:
            return Response({'detail': 'Player not found.'}, status=status.HTTP_404_NOT_FOUND)

        return _validated_single_response(data, PlayerTierTypeCorrelationSerializer)

    return Response({'detail': 'Unsupported player correlation metric.'}, status=status.HTTP_404_NOT_FOUND)


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def players_explorer(request) -> Response:
    query = (request.query_params.get('q') or '').strip()
    hidden = (request.query_params.get('hidden') or 'all').strip().lower()
    activity_bucket = (request.query_params.get(
        'activity_bucket') or 'all').strip().lower()
    ranked = (request.query_params.get('ranked') or 'all').strip().lower()
    sort = (request.query_params.get('sort')
            or 'days_since_last_battle').strip()
    direction = (request.query_params.get(
        'direction') or 'asc').strip().lower()

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

    rows = fetch_player_explorer_rows(
        query=query,
        hidden=hidden,
        activity_bucket=activity_bucket,
        ranked=ranked,
        min_pvp_battles=min_pvp_battles,
    )

    reverse = direction == 'desc'
    if sort == 'name':
        rows.sort(key=lambda row: (row.get('name')
                  or '').lower(), reverse=reverse)
    else:
        rows.sort(
            key=lambda row: (row.get(sort) is None, row.get(
                sort) if row.get(sort) is not None else 0),
            reverse=reverse,
        )

    total_count = len(rows)
    start = (page - 1) * page_size
    end = start + page_size
    page_rows = rows[start:end]

    serializer = PlayerExplorerRowSerializer(data=page_rows, many=True)
    serializer.is_valid(raise_exception=True)
    return Response({
        'count': total_count,
        'page': page,
        'page_size': page_size,
        'results': serializer.data,
    })


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def clan_members(request, clan_id: str) -> Response:
    if not clan_id or clan_id in {"null", "None", "undefined"}:
        return Response([])

    try:
        clan = Clan.objects.get(clan_id=clan_id)
    except Clan.DoesNotExist:
        return Response([])

    _record_clan_lookup(clan)

    from warships.data import update_clan_data, update_clan_members, queue_clan_battle_hydration, queue_clan_efficiency_hydration, queue_clan_ranked_hydration
    if not clan.members_count or (clan.leader_id is None and not clan.leader_name):
        update_clan_data(clan_id=clan_id)
        clan.refresh_from_db()

    members = clan.player_set.exclude(name='').order_by(
        *_player_score_ordering('last_battle_date'))
    if not members.exists() or (clan.members_count and members.count() < clan.members_count):
        update_clan_members(clan_id=clan_id)
        members = clan.player_set.exclude(name='').order_by(
            *_player_score_ordering('last_battle_date'))

    members = list(members)
    hydration_state = queue_clan_ranked_hydration(members)
    pending_player_ids = hydration_state['pending_player_ids']
    clan_battle_hydration_state = queue_clan_battle_hydration(members)
    pending_clan_battle_player_ids = clan_battle_hydration_state['pending_player_ids']
    efficiency_hydration_state = queue_clan_efficiency_hydration(members)
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
                clan_battle_summary['total_battles'], clan_battle_summary['seasons_participated']),
            'clan_battle_win_rate': clan_battle_summary['win_rate'],
            'clan_battle_hydration_pending': member.player_id in pending_clan_battle_player_ids,
            'efficiency_hydration_pending': member.player_id in pending_efficiency_player_ids,
            'highest_ranked_league': get_highest_ranked_league_name(member.ranked_json),
            'ranked_hydration_pending': member.player_id in pending_player_ids,
            'ranked_updated_at': member.ranked_updated_at,
            **_get_published_efficiency_rank_payload(member),
        }
        for member in members
        for clan_battle_summary in [get_player_clan_battle_summary(member.player_id, allow_fetch=False)]
    ]

    serializer = ClanMemberSerializer(member_rows, many=True)
    response = Response(serializer.data)
    response['X-Ranked-Hydration-Queued'] = str(
        len(hydration_state['queued_player_ids']))
    response['X-Ranked-Hydration-Deferred'] = str(
        len(hydration_state['deferred_player_ids']))
    response['X-Ranked-Hydration-Pending'] = str(
        len(hydration_state['pending_player_ids']))
    response['X-Ranked-Hydration-Max-In-Flight'] = str(
        hydration_state['max_in_flight'])
    response['X-Clan-Battle-Hydration-Queued'] = str(
        len(clan_battle_hydration_state['queued_player_ids']))
    response['X-Clan-Battle-Hydration-Deferred'] = str(
        len(clan_battle_hydration_state['deferred_player_ids']))
    response['X-Clan-Battle-Hydration-Pending'] = str(
        len(clan_battle_hydration_state['pending_player_ids']))
    response['X-Clan-Battle-Hydration-Max-In-Flight'] = str(
        clan_battle_hydration_state['max_in_flight'])
    response['X-Efficiency-Hydration-Queued'] = str(
        len(efficiency_hydration_state['queued_player_ids']))
    response['X-Efficiency-Hydration-Deferred'] = str(
        len(efficiency_hydration_state['deferred_player_ids']))
    response['X-Efficiency-Hydration-Pending'] = str(
        len(efficiency_hydration_state['pending_player_ids']))
    response['X-Efficiency-Hydration-Max-In-Flight'] = str(
        efficiency_hydration_state['max_in_flight'])
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

    clan = Clan.objects.filter(clan_id=clan_id).first()
    if clan is not None:
        _record_clan_lookup(clan)

    data = fetch_clan_plot_data(clan_id=clan_id, filter_type=filter_type)
    return _validated_list_response(data, ClanDataSerializer)


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def clan_battle_seasons(request, clan_id: str) -> Response:
    clan = Clan.objects.filter(clan_id=clan_id).first()
    if clan is not None:
        _record_clan_lookup(clan)

    had_cached_summary = has_clan_battle_summary_cache(clan_id)
    data = fetch_clan_battle_seasons(clan_id)
    response = _validated_list_response(
        data, ClanBattleSeasonSummarySerializer)
    if not had_cached_summary and not data:
        response["X-Clan-Battles-Pending"] = "true"

    return response


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def player_clan_battle_seasons(request, player_id: str) -> Response:
    data = fetch_player_clan_battle_seasons(player_id)
    return _validated_list_response(data, PlayerClanBattleSeasonSerializer)


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def landing_activity_attrition(request) -> Response:
    data = fetch_landing_activity_attrition()
    return _validated_single_response(data, LandingActivityAttritionSerializer)


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def landing_clans(request) -> Response:
    payload, cache_metadata = get_landing_clans_payload_with_cache_metadata()
    response = Response(payload)
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
    return Response(get_landing_recent_clans_payload())


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def landing_players(request) -> Response:
    try:
        mode = normalize_landing_player_mode(request.query_params.get('mode'))
    except ValueError:
        return Response({'detail': 'mode must be one of: random, best'}, status=status.HTTP_400_BAD_REQUEST)
    limit = normalize_landing_player_limit(request.query_params.get('limit'))
    payload, cache_metadata = get_landing_players_payload_with_cache_metadata(
        mode=mode,
        limit=limit,
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
    return Response(get_landing_recent_players_payload())


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def player_name_suggestions(request) -> Response:
    query = (request.query_params.get('q') or '').strip()
    if len(query) < 2:
        return Response([])

    suggestions = list(
        Player.objects.exclude(name='').filter(name__icontains=query).annotate(
            prefix_rank=Case(
                When(name__istartswith=query, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            ),
        ).values('name', 'pvp_ratio', 'is_hidden').order_by(
            'prefix_rank',
            F('last_battle_date').desc(nulls_last=True),
            'name',
        )[:8]
    )
    return Response(suggestions)


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def db_stats(request) -> Response:
    def _fetch_db_stats():
        return {
            'players': Player.objects.count(),
            'clans': Clan.objects.count(),
        }
    data = cache.get_or_set('db:stats', _fetch_db_stats, 300)
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
