import logging
import random
from datetime import timedelta
from django.core.cache import cache
from django.db.models import Sum, F, FloatField, Case, When, Value, IntegerField
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
    RankedDataSerializer, ClanBattleSeasonSummarySerializer, PlayerSummarySerializer, PlayerExplorerRowSerializer, \
    WRDistributionBinSerializer, PlayerPopulationDistributionSerializer, PlayerCorrelationDistributionSerializer
from warships.data import fetch_tier_data, fetch_activity_data, fetch_type_data, fetch_randoms_data, fetch_clan_plot_data, _extract_randoms_rows, \
    fetch_ranked_data, fetch_clan_battle_seasons, has_clan_battle_summary_cache, fetch_player_summary, \
    fetch_player_explorer_rows, fetch_wr_distribution, fetch_player_population_distribution, fetch_player_wr_survival_correlation
from .tasks import update_clan_data_task, update_player_data_task, update_clan_members_task
from .tasks import update_clan_battle_summary_task

logging.basicConfig(level=logging.INFO)


PUBLIC_API_THROTTLES = [AnonRateThrottle, UserRateThrottle]
LANDING_CLAN_FEATURED_COUNT = 40
LANDING_CLAN_MIN_TOTAL_BATTLES = 100000


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


class PlayerViewSet(viewsets.ModelViewSet):
    queryset = Player.objects.select_related('clan').all()
    serializer_class = PlayerSerializer
    permission_classes = [permissions.AllowAny]

    def get_object(self):
        lookup_field_value = self.kwargs[self.lookup_field]
        try:
            obj = self.queryset.get(name__iexact=lookup_field_value)
            if not obj.clan:
                from warships.data import update_player_data
                update_player_data(player=obj, force_refresh=True)
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

        self.check_object_permissions(self.request, obj)

        now = timezone.now()

        # Record the last time this player profile was viewed via the API.
        obj.last_lookup = now
        obj.save(update_fields=["last_lookup"])
        cache.delete('landing:recent_players')

        player_refresh_stale = not obj.last_fetch or (
            now - obj.last_fetch) > timedelta(minutes=15)

        # When clan is still missing, force a refresh task so we do not get
        # stuck on fresh-but-incomplete player records.
        if not obj.clan:
            update_player_data_task.delay(
                player_id=obj.player_id,
                force_refresh=True,
            )
        elif player_refresh_stale:
            update_player_data_task.delay(player_id=obj.player_id)

        if obj.clan:
            clan = obj.clan
            clan_refresh_stale = not clan.last_fetch or (
                now - clan.last_fetch) > timedelta(hours=12)
            clan_members_incomplete = not clan.members_count or clan.player_set.count() < clan.members_count

            if clan_refresh_stale:
                logging.info(
                    f'Updating clan data: {obj.name} : {clan.name} {obj.player_id}')
                update_clan_data_task.delay(clan_id=clan.clan_id)

            if clan_refresh_stale or clan_members_incomplete:
                update_clan_members_task.delay(clan_id=clan.clan_id)
        return obj


class PlayerDetail(generics.RetrieveUpdateDestroyAPIView):
    queryset = Player.objects.select_related('clan').all()
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
        now = timezone.now()
        obj.last_lookup = now
        obj.save(update_fields=["last_lookup"])
        cache.delete('landing:clans')
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
def player_correlation_distribution(request, metric: str) -> Response:
    if metric != 'win_rate_survival':
        return Response({'detail': 'Unsupported player correlation metric.'}, status=status.HTTP_404_NOT_FOUND)

    data = fetch_player_wr_survival_correlation()
    return _validated_single_response(data, PlayerCorrelationDistributionSerializer)


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

    from warships.data import update_clan_data, update_clan_members
    if not clan.members_count:
        update_clan_data(clan_id=clan_id)
        clan.refresh_from_db()

    members = clan.player_set.exclude(name='').order_by('-last_battle_date')
    if not members.exists() or (clan.members_count and members.count() < clan.members_count):
        update_clan_members(clan_id=clan_id)
        members = clan.player_set.exclude(
            name='').order_by('-last_battle_date')

    serializer = ClanMemberSerializer(members, many=True)
    return Response(serializer.data)


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

    data = fetch_clan_plot_data(clan_id=clan_id, filter_type=filter_type)
    return _validated_list_response(data, ClanDataSerializer)


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def clan_battle_seasons(request, clan_id: str) -> Response:
    had_cached_summary = has_clan_battle_summary_cache(clan_id)
    data = fetch_clan_battle_seasons(clan_id)
    response = _validated_list_response(
        data, ClanBattleSeasonSummarySerializer)
    if not had_cached_summary and not data:
        response["X-Clan-Battles-Pending"] = "true"

    return response


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def landing_clans(request) -> Response:
    def _fetch_landing_clans():
        qs = Clan.objects.exclude(name__isnull=True).exclude(name='').annotate(
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
        ).order_by(F('last_lookup').desc(nulls_last=True))
        return _prioritize_landing_clans(list(qs))

    data = cache.get_or_set('landing:clans', _fetch_landing_clans, 60)
    return Response(data)


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def landing_players(request) -> Response:
    def _fetch_landing_players():
        return list(
            Player.objects.exclude(name='').filter(
                is_hidden=False,
            ).exclude(
                last_battle_date__isnull=True
            ).values('name', 'pvp_ratio', 'is_hidden').order_by('-last_battle_date')
        )

    data = cache.get_or_set('landing:players', _fetch_landing_players, 60)
    return Response(data)


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def landing_recent_players(request) -> Response:
    def _fetch_recent_players():
        return list(
            Player.objects.exclude(name='').exclude(
                last_lookup__isnull=True
            ).values('name', 'pvp_ratio').order_by('-last_lookup')[:40]
        )

    data = cache.get_or_set('landing:recent_players',
                            _fetch_recent_players, 60)
    return Response(data)


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
