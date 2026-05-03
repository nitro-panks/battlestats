import logging
import random
from functools import partial
from datetime import timedelta
from hashlib import sha256
from kombu.exceptions import OperationalError as KombuOperationalError
from django.conf import settings
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
from warships.data import (
    calculate_tier_filtered_pvp_record,
    clan_detail_needs_refresh,
    clan_members_missing_or_incomplete,
    compute_player_verdict,
    explorer_summary_needs_refresh,
    extract_randoms_rows,
    fetch_activity_data,
    fetch_clan_battle_seasons,
    fetch_clan_plot_data,
    fetch_landing_activity_attrition,
    fetch_player_clan_battle_seasons,
    fetch_player_explorer_page,
    fetch_player_explorer_rows,
    fetch_player_population_distribution,
    fetch_player_ranked_wr_battles_correlation,
    fetch_player_summary,
    fetch_player_tier_type_correlation,
    fetch_player_wr_survival_correlation,
    fetch_randoms_data,
    fetch_ranked_data,
    fetch_tier_data,
    fetch_type_data,
    fetch_wr_distribution,
    get_highest_ranked_league_name,
    get_published_efficiency_rank_payload,
    has_clan_battle_summary_cache,
    is_clan_battle_enjoyer,
    is_pve_player,
    is_ranked_player,
    is_sleepy_player,
    player_battle_data_needs_refresh,
    player_detail_needs_refresh,
    refresh_player_explorer_summary,
    update_battle_data,
)
from warships.landing import get_landing_best_clans_payload_with_cache_metadata, get_landing_clans_payload_with_cache_metadata, get_landing_players_payload_with_cache_metadata, get_landing_recent_clans_payload, get_landing_recent_players_payload, get_random_landing_player_queue_payload, invalidate_landing_clan_caches, invalidate_landing_recent_player_cache, normalize_landing_clan_best_sort, normalize_landing_clan_limit, normalize_landing_clan_mode, normalize_landing_player_best_sort, normalize_landing_player_limit, normalize_landing_player_mode
from warships.visit_analytics import get_top_entities, record_entity_visit
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
        """Bump the page-visit timestamp + push to the recently-viewed list.

        ``last_lookup`` is still updated for analytics, hot-entity warming,
        and recently-viewed-warmer consumers — only the landing Recent pill
        moved off it. The pill is now invalidated by the BattleEvent capture
        path instead.

        When ``update_last_lookup`` is True (cache-hit path), bump
        ``last_lookup`` in the database. The cache-miss path already saves
        ``last_lookup`` on the model instance, so it passes False.
        """
        from warships.data import push_recently_viewed_player

        if update_last_lookup:
            Player.objects.filter(player_id=player_id, realm=realm).update(
                last_lookup=timezone.now())
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
            update_player_data(player=obj, force_refresh=True, realm=realm)
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

        if not obj.is_hidden and explorer_summary_needs_refresh(obj):
            refresh_player_explorer_summary(obj)

        player_refresh_stale = player_detail_needs_refresh(obj)

        # When clan is still missing, force a refresh task so we do not get
        # stuck on fresh-but-incomplete player records.
        if not obj.clan:
            _delay_task_safely(
                update_player_data_task,
                player_id=obj.player_id,
                force_refresh=True,
                realm=realm,
            )
        elif needs_kdr_backfill or needs_efficiency_refresh:
            _delay_task_safely(
                update_player_data_task,
                player_id=obj.player_id,
                force_refresh=True,
                realm=realm,
            )
        elif player_refresh_stale:
            _delay_task_safely(
                update_player_data_task,
                player_id=obj.player_id,
                realm=realm,
            )

        if obj.clan:
            clan = obj.clan
            clan_refresh_stale = clan_detail_needs_refresh(clan)
            clan_members_incomplete = clan_members_missing_or_incomplete(clan)

            if clan_refresh_stale:
                logging.info(
                    f'Updating clan data: {obj.name} : {clan.name} {obj.player_id}')
                _delay_task_safely(
                    update_clan_data_task,
                    clan_id=clan.clan_id,
                    realm=realm,
                )

            if clan_refresh_stale or clan_members_incomplete:
                _delay_task_safely(
                    update_clan_members_task,
                    clan_id=clan.clan_id,
                    realm=realm,
                )

        if not obj.is_hidden and (not obj.battles_json or player_battle_data_needs_refresh(obj)):
            from warships.tasks import update_battle_data_task

            _delay_task_safely(
                update_battle_data_task,
                player_id=obj.player_id,
                realm=realm,
            )

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
            _delay_task_safely(
                update_clan_data_task,
                clan_id=obj.clan_id,
                realm=realm,
            )
        if clan_members_missing_or_incomplete(obj):
            _delay_task_safely(
                update_clan_members_task,
                clan_id=obj.clan_id,
                realm=realm,
            )
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
            data = extract_randoms_rows(player.battles_json, limit=None)
        else:
            data = extract_randoms_rows(
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
    if is_ranked_data_refresh_pending(player_id):
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


BATTLE_HISTORY_DEFAULT_DAYS = 7
BATTLE_HISTORY_MAX_DAYS = 30
BATTLE_HISTORY_CACHE_TTL = 5 * 60  # 5 minutes

# Phase 6: period switcher. Each period maps to a backing model + a default
# window count + a cap.
BATTLE_HISTORY_PERIODS = {
    "daily": {"default_windows": 7, "max_windows": 30},
    "weekly": {"default_windows": 12, "max_windows": 52},
    "monthly": {"default_windows": 12, "max_windows": 36},
    "yearly": {"default_windows": 5, "max_windows": 20},
}

# Phase 4 of the ranked rollout (runbook-ranked-battle-history-rollout-2026-05-02.md).
# `random` (default) preserves the pre-ranked contract exactly. `ranked`
# filters PlayerDailyShipStats to ranked rows. `combined` sums both modes
# but suppresses lifetime-delta fields since the lifetime baseline
# (Player.battles_json / Player.pvp_*) is randoms-only.
BATTLE_HISTORY_MODES = {"random", "ranked", "combined"}
BATTLE_HISTORY_DEFAULT_MODE = "random"


def _battle_history_cache_key(realm: str, player_name: str, period: str,
                              windows: int, mode: str) -> str:
    norm = (player_name or "").strip().lower()
    return realm_cache_key(
        realm, f"battle-history:{norm}:{period}:{windows}:{mode}"
    )


def _period_window_start(today, period: str, windows: int):
    """Return the inclusive lower bound for `windows` periods ending today."""
    from datetime import timedelta as _td

    if period == "daily":
        return today - _td(days=windows - 1)
    if period == "weekly":
        # Window starts at the Monday of `windows-1` weeks ago.
        from warships.incremental_battles import _week_start
        current_week_start = _week_start(today)
        return current_week_start - _td(days=7 * (windows - 1))
    if period == "monthly":
        # Walk back N-1 months, snap to first-of-month.
        m = today.month - (windows - 1)
        y = today.year
        while m <= 0:
            m += 12
            y -= 1
        return today.replace(year=y, month=m, day=1)
    if period == "yearly":
        return today.replace(year=today.year - (windows - 1), month=1, day=1)
    raise ValueError(f"Unknown period: {period}")


def _battle_history_period_table(period: str):
    from warships.models import (
        PlayerDailyShipStats,
        PlayerMonthlyShipStats,
        PlayerWeeklyShipStats,
        PlayerYearlyShipStats,
    )
    return {
        "daily": PlayerDailyShipStats,
        "weekly": PlayerWeeklyShipStats,
        "monthly": PlayerMonthlyShipStats,
        "yearly": PlayerYearlyShipStats,
    }[period]


def _build_battle_history_payload(player, period: str, windows: int,
                                  mode: str = BATTLE_HISTORY_DEFAULT_MODE) -> dict:
    """Read the period rollup table for `player` over the last `windows`
    periods and return the totals / by_ship / by_day shape.

    Each by_ship entry is enriched with `lifetime_*` (the player's career
    aggregate from Player.battles_json for that ship) and `delta_*` (how
    much the period dragged the lifetime number — positive means the
    period outperformed prior history, negative means it dragged it
    down). Returns null on those fields when the lifetime aggregate is
    not available.

    The `by_day` field name is preserved for back-compat with the
    frontend even when period != daily; entries' `date` carries the
    period_start (Monday for weekly, first-of-month for monthly, Jan 1
    for yearly).
    """
    today = timezone.now().date()
    since = _period_window_start(today, period, windows)
    table = _battle_history_period_table(period)

    if period == "daily":
        # Daily layer keeps `date`; period rollups use `period_start`.
        date_field = "date"
    else:
        date_field = "period_start"

    # Availability probe: which modes have ANY rollup rows for this player
    # (across all dates, not just the current window). Drives the
    # frontend's mode-pill visibility — we hide pills for modes the
    # player has never had data in.
    from warships.models import PlayerDailyShipStats as _PDSS
    available_modes = list(
        _PDSS.objects.filter(player=player)
        .values_list("mode", flat=True).distinct().order_by("mode")
    )

    qs = table.objects.filter(
        player=player, **{f"{date_field}__gte": since}
    )
    # Phase 4 ranked rollout: only the daily layer carries the `mode`
    # column. Period tables (weekly/monthly/yearly) are randoms-only by
    # the Phase 3 period-rollup guard, so a `ranked` request against a
    # period tier returns empty by construction.
    if period == "daily" and mode in ("random", "ranked"):
        qs = qs.filter(mode=mode)
    elif period != "daily" and mode == "ranked":
        qs = qs.none()
    rows = list(qs.order_by(date_field, "ship_id"))

    # Lookup table for per-ship lifetime aggregates (Player.battles_json
    # is one row per ship the player has touched).
    lifetime_by_ship: dict = {}
    for entry in (player.battles_json or []):
        if not isinstance(entry, dict):
            continue
        ship_id = entry.get("ship_id")
        if ship_id is None:
            continue
        try:
            lifetime_by_ship[int(ship_id)] = {
                "battles": int(entry.get("pvp_battles", 0) or 0),
                "wins": int(entry.get("wins", 0) or 0),
                "losses": int(entry.get("losses", 0) or 0),
            }
        except (TypeError, ValueError):
            continue

    totals = {
        "battles": 0, "wins": 0, "losses": 0,
        "damage": 0, "frags": 0, "xp": 0, "planes_killed": 0,
        "survived_battles": 0,
    }
    by_ship_acc: dict = {}
    by_day_acc: dict = {}

    ship_ids = {row.ship_id for row in rows}
    ship_meta = {
        s.ship_id: s for s in Ship.objects.filter(ship_id__in=ship_ids)
    }

    for row in rows:
        totals["battles"] += row.battles
        totals["wins"] += row.wins
        totals["losses"] += row.losses
        totals["damage"] += row.damage
        totals["frags"] += row.frags
        totals["xp"] += row.xp
        totals["planes_killed"] += row.planes_killed
        totals["survived_battles"] += row.survived_battles

        ship_entry = by_ship_acc.setdefault(row.ship_id, {
            "ship_id": row.ship_id,
            "ship_name": row.ship_name or (ship_meta.get(row.ship_id).name
                                           if ship_meta.get(row.ship_id)
                                           else ""),
            "ship_tier": ship_meta.get(row.ship_id).tier
            if ship_meta.get(row.ship_id) else None,
            "ship_type": ship_meta.get(row.ship_id).ship_type
            if ship_meta.get(row.ship_id) else None,
            "battles": 0, "wins": 0, "losses": 0, "frags": 0,
            "damage": 0, "xp": 0, "planes_killed": 0,
            "survived_battles": 0,
        })
        ship_entry["battles"] += row.battles
        ship_entry["wins"] += row.wins
        ship_entry["losses"] += row.losses
        ship_entry["frags"] += row.frags
        ship_entry["damage"] += row.damage
        ship_entry["xp"] += row.xp
        ship_entry["planes_killed"] += row.planes_killed
        ship_entry["survived_battles"] += row.survived_battles

        bucket_date = row.date if period == "daily" else row.period_start
        day_iso = bucket_date.isoformat()
        day_entry = by_day_acc.setdefault(day_iso, {
            "date": day_iso,
            "battles": 0, "wins": 0, "damage": 0, "frags": 0,
        })
        day_entry["battles"] += row.battles
        day_entry["wins"] += row.wins
        day_entry["damage"] += row.damage
        day_entry["frags"] += row.frags

    win_rate = round(100.0 * totals["wins"] / totals["battles"], 1) \
        if totals["battles"] else 0.0
    avg_damage = int(round(totals["damage"] / totals["battles"])) \
        if totals["battles"] else 0
    survival_rate = round(
        100.0 * totals["survived_battles"] / totals["battles"], 1
    ) if totals["battles"] else 0.0

    by_ship = sorted(by_ship_acc.values(),
                     key=lambda s: s["battles"], reverse=True)
    for s in by_ship:
        s["win_rate"] = round(
            100.0 * s["wins"] / s["battles"], 1) if s["battles"] else 0.0
        s["avg_damage"] = int(round(s["damage"] / s["battles"])) \
            if s["battles"] else 0

        # Phase 4 ranked rollout: lifetime baseline (Player.battles_json)
        # is randoms-only, so the delta math only makes sense for
        # mode=random. For ranked/combined views, leave the lifetime
        # fields null — the frontend already tolerates them.
        lifetime = lifetime_by_ship.get(s["ship_id"]) if mode == "random" else None
        if lifetime and lifetime["battles"] >= s["battles"]:
            # The period rolls into lifetime — battles_json is the
            # latest snapshot, including the period's matches. Subtract
            # to get the "prior" state.
            prior_battles = lifetime["battles"] - s["battles"]
            prior_wins = lifetime["wins"] - s["wins"]
            lifetime_wr_now = round(
                100.0 * lifetime["wins"] / lifetime["battles"], 1)
            prior_wr = round(
                100.0 * prior_wins / prior_battles, 1) if prior_battles > 0 else None
            s["lifetime_battles"] = lifetime["battles"]
            s["lifetime_win_rate"] = lifetime_wr_now
            s["delta_win_rate"] = round(
                lifetime_wr_now - prior_wr, 1) if prior_wr is not None else None
        else:
            # Lifetime row is missing or stale (period > lifetime, which
            # shouldn't happen in practice — guard for sync skew).
            s["lifetime_battles"] = None
            s["lifetime_win_rate"] = None
            s["delta_win_rate"] = None

    by_day = sorted(by_day_acc.values(), key=lambda d: d["date"])

    # Overall lifetime delta — uses Player aggregate columns directly.
    # Same rationale as the per-ship lifetime: pvp_* are randoms-only, so
    # only mode=random gets meaningful delta numbers; ranked/combined
    # leave the lifetime fields null.
    if mode == "random":
        lifetime_battles_overall = int(player.pvp_battles or 0)
        lifetime_wins_overall = int(player.pvp_wins or 0)
    else:
        lifetime_battles_overall = 0
        lifetime_wins_overall = 0
    lifetime_overall_wr = round(
        100.0 * lifetime_wins_overall / lifetime_battles_overall, 1
    ) if lifetime_battles_overall else None
    if (
        lifetime_battles_overall >= totals["battles"]
        and totals["battles"] > 0
    ):
        prior_battles_overall = lifetime_battles_overall - totals["battles"]
        prior_wins_overall = lifetime_wins_overall - totals["wins"]
        prior_overall_wr = round(
            100.0 * prior_wins_overall / prior_battles_overall, 1
        ) if prior_battles_overall > 0 else None
        delta_overall_wr = round(
            lifetime_overall_wr - prior_overall_wr, 1
        ) if prior_overall_wr is not None else None
    else:
        delta_overall_wr = None

    return {
        "period": period,
        "windows": windows,
        "window_days": windows if period == "daily" else None,
        "mode": mode,
        "available_modes": available_modes,
        "as_of": timezone.now().isoformat(),
        "totals": {
            **totals,
            "win_rate": win_rate,
            "avg_damage": avg_damage,
            "survival_rate": survival_rate,
            "lifetime_battles": lifetime_battles_overall or None,
            "lifetime_win_rate": lifetime_overall_wr,
            "delta_win_rate": delta_overall_wr,
        },
        "by_ship": by_ship,
        "by_day": by_day,
    }


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def battle_history(request, player_name: str) -> Response:
    """Per-player longitudinal battle history.

    Phase 4 of the battle-history rollout. Reads PlayerDailyShipStats only.
    Returns 404 when BATTLE_HISTORY_API_ENABLED is not set so the absence
    is indistinguishable from a missing route.
    """
    import os

    if os.getenv("BATTLE_HISTORY_API_ENABLED", "0") != "1":
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

    realm = _get_realm(request)
    period = request.query_params.get("period", "daily")
    if period not in BATTLE_HISTORY_PERIODS:
        period = "daily"
    period_cfg = BATTLE_HISTORY_PERIODS[period]

    mode = request.query_params.get("mode", BATTLE_HISTORY_DEFAULT_MODE)
    if mode not in BATTLE_HISTORY_MODES:
        mode = BATTLE_HISTORY_DEFAULT_MODE

    # Accept the legacy `days` param when period=daily for back-compat;
    # otherwise prefer `windows`.
    raw_windows = request.query_params.get(
        "windows",
        request.query_params.get("days") if period == "daily" else None,
    )
    try:
        windows = int(raw_windows) if raw_windows is not None else period_cfg["default_windows"]
    except (TypeError, ValueError):
        windows = period_cfg["default_windows"]
    windows = max(1, min(period_cfg["max_windows"], windows))

    player = (
        Player.objects
        .alias(name_lower=Lower("name"))
        .filter(name_lower=(player_name or "").strip().lower(), realm=realm)
        .first()
    )
    if player is None:
        return Response({"detail": "Player not found."},
                        status=status.HTTP_404_NOT_FOUND)

    cache_key = _battle_history_cache_key(
        realm, player.name, period, windows, mode,
    )
    cached = cache.get(cache_key)
    if cached is not None:
        response = Response(cached)
    else:
        payload = _build_battle_history_payload(player, period, windows, mode)
        cache.set(cache_key, payload, BATTLE_HISTORY_CACHE_TTL)
        response = Response(payload)

    # On-render ranked-observation refresh signal: when a fresh ranked
    # observation is in flight (dispatched by fetch_player_summary on the
    # current profile render), tell the frontend to poll for fresh data.
    # The dispatch dedup key is set the moment queue_ranked_observation_refresh
    # accepts the enqueue and is cleared by the task on completion or failure.
    from warships.tasks import is_ranked_observation_refresh_pending
    if mode in ("ranked", "combined") and is_ranked_observation_refresh_pending(
        player.player_id, realm=realm,
    ):
        response["X-Ranked-Observation-Pending"] = "true"
    return response


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

        is_population_pending = data.pop('_population_pending', False)
        response = _validated_single_response(
            data, PlayerTierTypeCorrelationSerializer)
        if is_population_pending or (not player.battles_json and not data.get('player_cells')):
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
        _delay_task_safely(
            update_clan_data_task,
            clan_id=clan_id,
            realm=realm,
        )
    if needs_member_refresh:
        _delay_task_safely(
            update_clan_members_task,
            clan_id=clan_id,
            realm=realm,
        )

    # B1: Check response cache before doing expensive member serialization.
    # v3 of the cache key: `days_since_last_battle` is now derived from
    # `last_battle_date` at response-build time rather than read from the
    # stored snapshot column (which goes stale by 1 day/day between
    # refreshes). v2 entries served the stored, drifting value, so they
    # are bypassed.
    CLAN_MEMBERS_CACHE_TTL = 300  # 5 minutes
    cache_key = realm_cache_key(realm, f'clan:members:v3:{clan_id}')
    cached = cache.get(cache_key)
    if cached is not None:
        response = Response(cached)
        response['X-Clan-Members-Cache'] = 'hit'
        return response

    # Simplified ordering: hidden players cluster at the bottom; otherwise
    # most-recently-played first with name as the deterministic tiebreak.
    # Drives the "recent activity" feel of the clan-members list and lets
    # the frontend FLIP animation surface real upward moves when a refresh
    # rotates active players to the top.
    members = clan.player_set.select_related('explorer_summary').exclude(name='').order_by(
        F('is_hidden').asc(),
        F('last_battle_date').desc(nulls_last=True),
        'name',
    )

    members = list(members)
    hydration_state = queue_clan_ranked_hydration(members, realm=realm)
    pending_player_ids = hydration_state['pending_player_ids']
    efficiency_hydration_state = queue_clan_efficiency_hydration(
        members, realm=realm)
    pending_efficiency_player_ids = efficiency_hydration_state['pending_player_ids']

    leader_name = (clan.leader_name or '').strip().lower()
    today = timezone.now().date()

    def _days_since_last_battle(member) -> int | None:
        # Derive from `last_battle_date` rather than the stored
        # `days_since_last_battle` field — the stored value is a snapshot
        # taken at refresh time and goes stale by 1/day until the next
        # refresh. The order column (`last_battle_date`) is the source of
        # truth, and this keeps the displayed "X days idle" label
        # consistent with the row order.
        if not member.last_battle_date:
            return None
        return max(0, (today - member.last_battle_date).days)

    member_rows = []
    for member in members:
        days_since = _days_since_last_battle(member)
        member_rows.append({
            'name': member.name,
            'is_hidden': member.is_hidden,
            'is_streamer': member.is_streamer,
            'pvp_ratio': member.pvp_ratio,
            'days_since_last_battle': days_since,
            'is_leader': (
                (clan.leader_id is not None and member.player_id == clan.leader_id)
                or (leader_name and member.name.strip().lower() == leader_name)
            ),
            'is_pve_player': is_pve_player(member.total_battles, member.pvp_battles),
            'is_sleepy_player': is_sleepy_player(days_since),
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
            **get_published_efficiency_rank_payload(member),
        })

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
        pending_key = realm_cache_key(
            realm, f'clan:tiers:v3:{clan_id}:pending')
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
def clan_member_tiers(request, clan_id: str) -> Response:
    from warships.data import compute_clan_member_avg_tiers
    realm = _get_realm(request)
    data = compute_clan_member_avg_tiers(clan_id=clan_id, realm=realm)
    return Response(data)


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

    try:
        best_sort = normalize_landing_clan_best_sort(
            request.query_params.get('sort'))
    except ValueError:
        return Response({'detail': 'sort must be one of: overall, wr'}, status=status.HTTP_400_BAD_REQUEST)

    realm = _get_realm(request)
    limit = normalize_landing_clan_limit(request.query_params.get('limit'))
    if mode == 'random':
        payload, cache_metadata = get_landing_clans_payload_with_cache_metadata(
            realm=realm)
    else:
        payload, cache_metadata = get_landing_best_clans_payload_with_cache_metadata(
            realm=realm,
            sort=best_sort,
        )

    payload = payload[:limit]

    response = Response(payload)
    response['X-Landing-Clans-Cache-Mode'] = mode
    if mode == 'best':
        response['X-Landing-Clans-Cache-Sort'] = best_sort
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
        return Response({'detail': 'mode must be one of: random, best, sigma, popular'}, status=status.HTTP_400_BAD_REQUEST)
    best_sort = 'overall'
    payload_kwargs = {}
    if mode == 'best':
        try:
            best_sort = normalize_landing_player_best_sort(
                request.query_params.get('sort'))
        except ValueError as error:
            return Response({'detail': str(error)}, status=status.HTTP_400_BAD_REQUEST)
        payload_kwargs['sort'] = best_sort
    realm = _get_realm(request)
    limit = normalize_landing_player_limit(request.query_params.get('limit'))
    payload, cache_metadata = get_landing_players_payload_with_cache_metadata(
        mode=mode,
        limit=limit,
        realm=realm,
        **payload_kwargs,
    )
    response = Response(payload)
    response['X-Landing-Players-Cache-Mode'] = mode
    if mode == 'best':
        response['X-Landing-Players-Cache-Sort'] = best_sort
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
    """Players ordered by most-recently-detected random battle.

    Source of truth: Player.last_random_battle_at, populated by the
    BattleEvent capture hook. Empty list when capture hasn't fired yet
    or when no players in the realm have triggered events.
    """
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
    query = (request.query_params.get('q') or '').strip().replace('\x00', '')
    if len(query) < 3:
        return Response([])

    realm = _get_realm(request)
    cache_key = f'{realm}:suggest:{query.lower()}'
    cached = cache.get(cache_key)
    if cached is not None:
        return Response(cached)

    from django.db import connection
    if connection.vendor == 'postgresql':
        # Raw SQL with ILIKE so the pg_trgm GIN index (player_name_trgm_idx) is used.
        # Django's icontains generates UPPER(col) LIKE UPPER(pat) which bypasses trigram indexes.
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
            suggestions = [dict(zip(columns, row))
                           for row in cursor.fetchall()]
    else:
        prefix_lower = query.lower()
        suggestions = list(
            Player.objects.exclude(name='').filter(
                realm=realm,
                name__icontains=query,
            ).annotate(
                prefix_match=Case(
                    When(name__istartswith=query, then=Value(0)),
                    default=Value(1),
                    output_field=IntegerField(),
                ),
            ).order_by(
                'prefix_match',
                F('last_battle_date').desc(nulls_last=True),
                'name',
            ).values('name', 'pvp_ratio', 'is_hidden')[:8]
        )

    cache.set(cache_key, suggestions, timeout=600)
    return Response(suggestions)


@api_view(["GET"])
@throttle_classes(PUBLIC_API_THROTTLES)
def clan_name_suggestions(request) -> Response:
    query = (request.query_params.get('q') or '').strip().replace('\x00', '')
    if len(query) < 2:
        return Response([])

    realm = _get_realm(request)
    cache_key = f'{realm}:clan-suggest:{query.lower()}'
    cached = cache.get(cache_key)
    if cached is not None:
        return Response(cached)

    from django.db import connection
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT clan_id, tag, name, members_count
                FROM warships_clan
                WHERE realm = %s
                  AND (name ILIKE %s OR tag ILIKE %s)
                ORDER BY
                    CASE WHEN name ILIKE %s OR tag ILIKE %s THEN 0 ELSE 1 END,
                    members_count DESC NULLS LAST,
                    name
                LIMIT 8
                """,
                [realm, f'%{query}%', f'%{query}%', f'{query}%', f'{query}%'],
            )
            columns = [col[0] for col in cursor.description]
            suggestions = [dict(zip(columns, row)) for row in cursor.fetchall()]
    else:
        suggestions = list(
            Clan.objects.filter(
                realm=realm,
            ).filter(
                Q(name__icontains=query) | Q(tag__icontains=query),
            ).annotate(
                prefix_match=Case(
                    When(Q(name__istartswith=query) | Q(tag__istartswith=query), then=Value(0)),
                    default=Value(1),
                    output_field=IntegerField(),
                ),
            ).order_by(
                'prefix_match',
                F('members_count').desc(nulls_last=True),
                'name',
            ).values('clan_id', 'tag', 'name', 'members_count')[:8]
        )

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


@api_view(["POST"])
@throttle_classes(PUBLIC_API_THROTTLES)
def streamer_submission_view(request) -> Response:
    from .serializers import StreamerSubmissionSerializer
    serializer = StreamerSubmissionSerializer(
        data=request.data, context={'request': request})
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response({'status': 'queued'}, status=status.HTTP_201_CREATED)


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
