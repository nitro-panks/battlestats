import logging
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
    RankedDataSerializer, ClanBattleSeasonSummarySerializer
from warships.data import fetch_tier_data, fetch_activity_data, fetch_type_data, fetch_randoms_data, fetch_clan_plot_data, \
    fetch_ranked_data, fetch_clan_battle_seasons
from .tasks import update_clan_data_task, update_player_data_task, update_clan_members_task

logging.basicConfig(level=logging.INFO)


PUBLIC_API_THROTTLES = [AnonRateThrottle, UserRateThrottle]


class PlayerViewSet(viewsets.ModelViewSet):
    queryset = Player.objects.all()
    serializer_class = PlayerSerializer
    permission_classes = [permissions.AllowAny]

    def get_object(self):
        lookup_field_value = self.kwargs[self.lookup_field]
        try:
            obj = Player.objects.get(name__iexact=lookup_field_value)
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
    queryset = Player.objects.all()
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

        import pandas as pd
        df = pd.DataFrame(player.battles_json)
        df = df.filter(['pvp_battles', 'ship_name', 'ship_type',
                        'ship_tier', 'win_ratio', 'wins'])
        try:
            df = df.sort_values(by='pvp_battles', ascending=False)
        except KeyError:
            pass
        data = df.to_dict(orient='records')
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
    data = fetch_clan_battle_seasons(clan_id)
    return _validated_list_response(data, ClanBattleSeasonSummarySerializer)


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
        return list(qs)

    data = cache.get_or_set('landing:clans', _fetch_landing_clans, 60)
    return Response(data)


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def landing_players(request) -> Response:
    def _fetch_landing_players():
        return list(
            Player.objects.exclude(name='').exclude(
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
