import re
import time

from rest_framework import serializers
from .models import Player, Clan, Ship, StreamerSubmission
from .data import _calculate_player_kill_ratio, _coerce_battle_rows, _get_published_efficiency_rank_payload, build_player_summary, get_highest_ranked_league_name, get_published_clan_battle_summary_payload, is_clan_battle_enjoyer, is_pve_player


TWITCH_URL_RE = re.compile(
    r"^https://(www\.)?twitch\.tv/[A-Za-z0-9_]{3,25}/?$")
TWITCH_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{3,25}$")
IGN_RE = re.compile(r"^[A-Za-z0-9_\-]{3,32}$")


class StreamerSubmissionSerializer(serializers.ModelSerializer):
    website = serializers.CharField(
        required=False, allow_blank=True, write_only=True)
    form_loaded_at = serializers.IntegerField(required=False, write_only=True)

    class Meta:
        model = StreamerSubmission
        fields = ['ign', 'realm', 'twitch_handle',
                  'twitch_url', 'website', 'form_loaded_at']

    def validate_website(self, value):
        if value:
            raise serializers.ValidationError('spam')
        return value

    def validate_form_loaded_at(self, value):
        if value and (time.time() * 1000 - value) < 2000:
            raise serializers.ValidationError('too_fast')
        return value

    def validate_ign(self, value):
        value = value.strip()
        if not IGN_RE.match(value):
            raise serializers.ValidationError('invalid IGN format')
        return value

    def validate_twitch_handle(self, value):
        v = value.strip().lstrip('@')
        if not TWITCH_HANDLE_RE.match(v):
            raise serializers.ValidationError('invalid Twitch handle')
        return v

    def validate_twitch_url(self, value):
        value = value.strip()
        if not TWITCH_URL_RE.match(value):
            raise serializers.ValidationError(
                'must be a https://twitch.tv/<handle> URL')
        return value

    def validate_realm(self, value):
        if not value:
            return ''
        v = value.strip().lower()
        if v not in {'na', 'eu', 'asia'}:
            raise serializers.ValidationError('invalid realm')
        return v

    def validate(self, attrs):
        url_handle = attrs['twitch_url'].rstrip('/').rsplit('/', 1)[-1]
        if url_handle.lower() != attrs['twitch_handle'].lower():
            raise serializers.ValidationError(
                'handle and URL handle must match')
        return attrs

    def create(self, validated):
        validated.pop('website', None)
        validated.pop('form_loaded_at', None)
        request = self.context.get('request')
        if request is not None:
            validated['submitter_ip'] = request.META.get('REMOTE_ADDR')
            validated['submitter_ua'] = (
                request.META.get('HTTP_USER_AGENT') or '')[:300]
        return super().create(validated)


class PlayerSerializer(serializers.ModelSerializer):
    clan_name = serializers.SerializerMethodField()
    clan_id = serializers.SerializerMethodField()
    clan_tag = serializers.SerializerMethodField()
    is_clan_leader = serializers.SerializerMethodField()
    highest_ranked_league = serializers.SerializerMethodField()
    kill_ratio = serializers.SerializerMethodField()
    player_score = serializers.SerializerMethodField()
    efficiency_rank_percentile = serializers.SerializerMethodField()
    efficiency_rank_tier = serializers.SerializerMethodField()
    has_efficiency_rank_icon = serializers.SerializerMethodField()
    efficiency_rank_population_size = serializers.SerializerMethodField()
    efficiency_rank_updated_at = serializers.SerializerMethodField()
    clan_battle_header_eligible = serializers.SerializerMethodField()
    clan_battle_header_total_battles = serializers.SerializerMethodField()
    clan_battle_header_seasons_played = serializers.SerializerMethodField()
    clan_battle_header_overall_win_rate = serializers.SerializerMethodField()
    clan_battle_header_updated_at = serializers.SerializerMethodField()
    is_pve_player = serializers.SerializerMethodField()

    class Meta:
        model = Player
        fields = '__all__'
        extra_kwargs = {
            'pvp_frags': {'write_only': True},
            'pvp_survived_battles': {'write_only': True},
            'pvp_deaths': {'write_only': True},
        }

    def get_clan_name(self, obj):
        return obj.clan.name if obj.clan else None

    def get_clan_id(self, obj):
        return obj.clan.clan_id if obj.clan else None

    def get_clan_tag(self, obj):
        return obj.clan.tag if obj.clan else None

    def get_is_clan_leader(self, obj):
        return bool(obj.clan and obj.clan.leader_id is not None and obj.player_id == obj.clan.leader_id)

    def get_highest_ranked_league(self, obj):
        return get_highest_ranked_league_name(obj.ranked_json)

    def get_kill_ratio(self, obj):
        explorer_summary = getattr(obj, 'explorer_summary', None)
        if explorer_summary is not None and explorer_summary.kill_ratio is not None:
            return explorer_summary.kill_ratio

        return _calculate_player_kill_ratio(_coerce_battle_rows(obj.battles_json))

    def get_player_score(self, obj):
        explorer_summary = getattr(obj, 'explorer_summary', None)
        if explorer_summary is not None and explorer_summary.player_score is not None:
            return explorer_summary.player_score

        return build_player_summary(obj, use_cached_summary=False).get('player_score')

    def _get_efficiency_rank_payload(self, obj):
        payload_cache = getattr(self, '_efficiency_rank_payload_cache', None)
        if payload_cache is None:
            payload_cache = {}
            self._efficiency_rank_payload_cache = payload_cache

        cache_key = getattr(obj, 'pk', id(obj))
        if cache_key not in payload_cache:
            payload_cache[cache_key] = _get_published_efficiency_rank_payload(
                obj)

        return payload_cache[cache_key]

    def get_efficiency_rank_percentile(self, obj):
        return self._get_efficiency_rank_payload(obj)['efficiency_rank_percentile']

    def get_efficiency_rank_tier(self, obj):
        return self._get_efficiency_rank_payload(obj)['efficiency_rank_tier']

    def get_has_efficiency_rank_icon(self, obj):
        return self._get_efficiency_rank_payload(obj)['has_efficiency_rank_icon']

    def get_efficiency_rank_population_size(self, obj):
        return self._get_efficiency_rank_payload(obj)['efficiency_rank_population_size']

    def get_efficiency_rank_updated_at(self, obj):
        return self._get_efficiency_rank_payload(obj)['efficiency_rank_updated_at']

    def _get_clan_battle_header_payload(self, obj):
        payload_cache = getattr(
            self, '_clan_battle_header_payload_cache', None)
        if payload_cache is None:
            payload_cache = {}
            self._clan_battle_header_payload_cache = payload_cache

        cache_key = getattr(obj, 'pk', id(obj))
        if cache_key not in payload_cache:
            if obj.is_hidden:
                payload_cache[cache_key] = {
                    'clan_battle_header_eligible': False,
                    'clan_battle_header_total_battles': 0,
                    'clan_battle_header_seasons_played': 0,
                    'clan_battle_header_overall_win_rate': None,
                    'clan_battle_header_updated_at': None,
                }
            else:
                summary = get_published_clan_battle_summary_payload(
                    obj,
                )
                payload_cache[cache_key] = {
                    'clan_battle_header_eligible': is_clan_battle_enjoyer(
                        summary.get('total_battles'),
                        summary.get('seasons_participated'),
                    ),
                    'clan_battle_header_total_battles': int(summary.get('total_battles') or 0),
                    'clan_battle_header_seasons_played': int(summary.get('seasons_participated') or 0),
                    'clan_battle_header_overall_win_rate': summary.get('win_rate'),
                    'clan_battle_header_updated_at': summary.get('updated_at'),
                }

        return payload_cache[cache_key]

    def get_clan_battle_header_eligible(self, obj):
        return self._get_clan_battle_header_payload(obj)['clan_battle_header_eligible']

    def get_clan_battle_header_total_battles(self, obj):
        return self._get_clan_battle_header_payload(obj)['clan_battle_header_total_battles']

    def get_clan_battle_header_seasons_played(self, obj):
        return self._get_clan_battle_header_payload(obj)['clan_battle_header_seasons_played']

    def get_clan_battle_header_overall_win_rate(self, obj):
        return self._get_clan_battle_header_payload(obj)['clan_battle_header_overall_win_rate']

    def get_clan_battle_header_updated_at(self, obj):
        return self._get_clan_battle_header_payload(obj)['clan_battle_header_updated_at']

    def get_is_pve_player(self, obj):
        if obj.is_hidden:
            return False

        return is_pve_player(obj.total_battles, obj.pvp_battles)


class ClanSerializer(serializers.ModelSerializer):

    class Meta:
        model = Clan
        fields = '__all__'


class ShipSerializer(serializers.ModelSerializer):

    class Meta:
        model = Ship
        fields = '__all__'


class ActivityDataSerializer(serializers.Serializer):
    date = serializers.DateField()
    battles = serializers.IntegerField()
    wins = serializers.IntegerField()


class EntityVisitIngestSerializer(serializers.Serializer):
    event_uuid = serializers.UUIDField()
    occurred_at = serializers.DateTimeField()
    entity_type = serializers.ChoiceField(choices=['player', 'clan'])
    entity_id = serializers.IntegerField(min_value=1)
    entity_slug = serializers.CharField(
        max_length=255, allow_blank=True, required=False)
    entity_name = serializers.CharField(max_length=200)
    route_path = serializers.CharField(max_length=255)
    referrer_path = serializers.CharField(
        max_length=255, allow_blank=True, required=False)
    source = serializers.ChoiceField(
        choices=['web_first_party', 'ga4'], required=False)
    visitor_key = serializers.CharField(max_length=128)
    session_key = serializers.CharField(max_length=128)


class EntityVisitIngestResponseSerializer(serializers.Serializer):
    accepted = serializers.BooleanField()
    counted_in_deduped_views = serializers.BooleanField()
    reason = serializers.CharField()


class TopEntitiesQuerySerializer(serializers.Serializer):
    entity_type = serializers.ChoiceField(choices=['player', 'clan'])
    period = serializers.ChoiceField(choices=['1d', '7d', '30d'], default='7d')
    metric = serializers.ChoiceField(
        choices=['views_raw', 'views_deduped',
                 'unique_visitors', 'unique_sessions'],
        default='views_deduped',
    )
    limit = serializers.IntegerField(min_value=1, max_value=100, default=25)


class TopEntityVisitSerializer(serializers.Serializer):
    entity_type = serializers.CharField()
    entity_id = serializers.IntegerField()
    entity_name = serializers.CharField(allow_blank=True)
    views_raw = serializers.IntegerField()
    views_deduped = serializers.IntegerField()
    unique_visitors = serializers.IntegerField()
    unique_sessions = serializers.IntegerField()
    last_view_at = serializers.DateTimeField(allow_null=True)


class TierDataSerializer(serializers.Serializer):
    ship_tier = serializers.IntegerField()
    pvp_battles = serializers.IntegerField()
    wins = serializers.IntegerField()
    win_ratio = serializers.FloatField()


class TypeDataSerializer(serializers.Serializer):
    ship_type = serializers.CharField()
    pvp_battles = serializers.IntegerField()
    wins = serializers.IntegerField()
    win_ratio = serializers.FloatField()


class RandomsDataSerializer(serializers.Serializer):
    pvp_battles = serializers.IntegerField()
    ship_name = serializers.CharField()
    ship_chart_name = serializers.CharField()
    ship_type = serializers.CharField()
    ship_tier = serializers.IntegerField()
    win_ratio = serializers.FloatField()
    wins = serializers.IntegerField()


class RankedSprintSerializer(serializers.Serializer):
    sprint_number = serializers.IntegerField()
    league = serializers.IntegerField()
    league_name = serializers.CharField()
    rank = serializers.IntegerField()
    best_rank = serializers.IntegerField()
    battles = serializers.IntegerField()
    wins = serializers.IntegerField()


class RankedDataSerializer(serializers.Serializer):
    season_id = serializers.IntegerField()
    season_name = serializers.CharField()
    season_label = serializers.CharField()
    start_date = serializers.CharField(allow_null=True)
    end_date = serializers.CharField(allow_null=True)
    highest_league = serializers.IntegerField()
    highest_league_name = serializers.CharField()
    total_battles = serializers.IntegerField()
    total_wins = serializers.IntegerField()
    win_rate = serializers.FloatField()
    top_ship_name = serializers.CharField(allow_null=True, required=False)
    best_sprint = RankedSprintSerializer(allow_null=True)
    sprints = RankedSprintSerializer(many=True)


class ClanDataSerializer(serializers.Serializer):
    player_name = serializers.CharField()
    pvp_battles = serializers.IntegerField()
    pvp_ratio = serializers.FloatField()


def _classify_clan_member_activity(days_since_last_battle):
    if days_since_last_battle is None:
        return 'unknown'
    if days_since_last_battle <= 7:
        return 'active_7d'
    if days_since_last_battle <= 30:
        return 'active_30d'
    if days_since_last_battle <= 90:
        return 'cooling_90d'
    if days_since_last_battle <= 180:
        return 'dormant_180d'
    return 'inactive_180d_plus'


class ClanMemberSerializer(serializers.Serializer):
    name = serializers.CharField()
    is_hidden = serializers.BooleanField()
    is_streamer = serializers.BooleanField()
    pvp_ratio = serializers.FloatField(allow_null=True)
    days_since_last_battle = serializers.IntegerField(allow_null=True)
    is_leader = serializers.BooleanField()
    is_pve_player = serializers.BooleanField()
    is_sleepy_player = serializers.BooleanField()
    is_ranked_player = serializers.BooleanField()
    is_clan_battle_player = serializers.BooleanField()
    clan_battle_win_rate = serializers.FloatField(allow_null=True)
    efficiency_hydration_pending = serializers.BooleanField()
    highest_ranked_league = serializers.CharField(allow_null=True)
    ranked_hydration_pending = serializers.BooleanField()
    ranked_updated_at = serializers.DateTimeField(allow_null=True)
    efficiency_rank_percentile = serializers.FloatField(allow_null=True)
    efficiency_rank_tier = serializers.CharField(allow_null=True)
    has_efficiency_rank_icon = serializers.BooleanField()
    efficiency_rank_population_size = serializers.IntegerField(allow_null=True)
    efficiency_rank_updated_at = serializers.DateTimeField(allow_null=True)
    activity_bucket = serializers.SerializerMethodField()

    def get_activity_bucket(self, obj):
        if isinstance(obj, dict):
            return _classify_clan_member_activity(obj.get('days_since_last_battle'))

        return _classify_clan_member_activity(obj.days_since_last_battle)


class ClanBattleSeasonSummarySerializer(serializers.Serializer):
    season_id = serializers.IntegerField()
    season_name = serializers.CharField()
    season_label = serializers.CharField()
    start_date = serializers.CharField(allow_null=True)
    end_date = serializers.CharField(allow_null=True)
    ship_tier_min = serializers.IntegerField(allow_null=True)
    ship_tier_max = serializers.IntegerField(allow_null=True)
    participants = serializers.IntegerField()
    roster_battles = serializers.IntegerField()
    roster_wins = serializers.IntegerField()
    roster_losses = serializers.IntegerField()
    roster_win_rate = serializers.FloatField()
    clan_battles = serializers.IntegerField(default=0)
    clan_wins = serializers.IntegerField(default=0)


class PlayerClanBattleSeasonSerializer(serializers.Serializer):
    season_id = serializers.IntegerField()
    season_name = serializers.CharField()
    season_label = serializers.CharField()
    start_date = serializers.CharField(allow_null=True)
    end_date = serializers.CharField(allow_null=True)
    ship_tier_min = serializers.IntegerField(allow_null=True)
    ship_tier_max = serializers.IntegerField(allow_null=True)
    battles = serializers.IntegerField()
    wins = serializers.IntegerField()
    losses = serializers.IntegerField()
    win_rate = serializers.FloatField()


class PlayerSummarySerializer(serializers.Serializer):
    kill_ratio = serializers.FloatField(allow_null=True)
    player_score = serializers.FloatField(allow_null=True)
    player_id = serializers.IntegerField()
    name = serializers.CharField()
    is_hidden = serializers.BooleanField()
    days_since_last_battle = serializers.IntegerField(allow_null=True)
    last_battle_date = serializers.CharField(allow_null=True)
    account_age_days = serializers.IntegerField(allow_null=True)
    pvp_ratio = serializers.FloatField(allow_null=True)
    pvp_battles = serializers.IntegerField(allow_null=True)
    pvp_survival_rate = serializers.FloatField(allow_null=True)
    battles_last_29_days = serializers.IntegerField(allow_null=True)
    wins_last_29_days = serializers.IntegerField(allow_null=True)
    active_days_last_29_days = serializers.IntegerField(allow_null=True)
    recent_win_rate = serializers.FloatField(allow_null=True)
    activity_trend_direction = serializers.CharField(allow_null=True)
    ships_played_total = serializers.IntegerField(allow_null=True)
    ship_type_spread = serializers.IntegerField(allow_null=True)
    tier_spread = serializers.IntegerField(allow_null=True)
    ranked_seasons_participated = serializers.IntegerField(allow_null=True)
    latest_ranked_battles = serializers.IntegerField(allow_null=True)
    highest_ranked_league_recent = serializers.CharField(allow_null=True)


class WRDistributionBinSerializer(serializers.Serializer):
    wr_min = serializers.FloatField()
    wr_max = serializers.FloatField()
    count = serializers.IntegerField()


class PlayerPopulationDistributionBinSerializer(serializers.Serializer):
    bin_min = serializers.FloatField()
    bin_max = serializers.FloatField()
    count = serializers.IntegerField()


class PlayerPopulationDistributionSerializer(serializers.Serializer):
    metric = serializers.CharField()
    label = serializers.CharField()
    x_label = serializers.CharField()
    scale = serializers.ChoiceField(choices=['linear', 'log'])
    value_format = serializers.ChoiceField(choices=['percent', 'integer', 'decimal'])
    tracked_population = serializers.IntegerField()
    bins = PlayerPopulationDistributionBinSerializer(many=True)


class PlayerCorrelationDomainSerializer(serializers.Serializer):
    min = serializers.FloatField()
    max = serializers.FloatField()
    bin_width = serializers.FloatField(allow_null=True, required=False)


class PlayerCorrelationTileSerializer(serializers.Serializer):
    x_min = serializers.FloatField()
    x_max = serializers.FloatField()
    y_min = serializers.FloatField()
    y_max = serializers.FloatField()
    count = serializers.IntegerField()


class PlayerCorrelationTrendPointSerializer(serializers.Serializer):
    x = serializers.FloatField()
    y = serializers.FloatField()
    count = serializers.IntegerField()


class CompactPlayerCorrelationTileSerializer(serializers.Serializer):
    x_index = serializers.IntegerField(min_value=0)
    y_index = serializers.IntegerField(min_value=0)
    count = serializers.IntegerField(min_value=0)


class CompactPlayerCorrelationTrendPointSerializer(serializers.Serializer):
    x_index = serializers.IntegerField(min_value=0)
    y = serializers.FloatField()
    count = serializers.IntegerField(min_value=0)


class CompactPlayerCorrelationDistributionSerializer(serializers.Serializer):
    metric = serializers.CharField()
    label = serializers.CharField()
    x_label = serializers.CharField()
    y_label = serializers.CharField()
    tracked_population = serializers.IntegerField()
    correlation = serializers.FloatField(allow_null=True)
    x_domain = PlayerCorrelationDomainSerializer()
    y_domain = PlayerCorrelationDomainSerializer()
    tiles = CompactPlayerCorrelationTileSerializer(many=True)
    trend = CompactPlayerCorrelationTrendPointSerializer(many=True)


class PlayerCorrelationDistributionSerializer(serializers.Serializer):
    metric = serializers.CharField()
    label = serializers.CharField()
    x_label = serializers.CharField()
    y_label = serializers.CharField()
    tracked_population = serializers.IntegerField()
    correlation = serializers.FloatField(allow_null=True)
    x_domain = PlayerCorrelationDomainSerializer()
    y_domain = PlayerCorrelationDomainSerializer()
    tiles = PlayerCorrelationTileSerializer(many=True)
    trend = PlayerCorrelationTrendPointSerializer(many=True)


class PlayerCorrelationPointSerializer(serializers.Serializer):
    x = serializers.FloatField()
    y = serializers.FloatField()
    label = serializers.CharField(required=False)


class PlayerExtendedCorrelationDistributionSerializer(PlayerCorrelationDistributionSerializer):
    x_scale = serializers.ChoiceField(choices=['linear', 'log'])
    y_scale = serializers.ChoiceField(choices=['linear', 'log'])
    x_ticks = serializers.ListField(
        child=serializers.FloatField(), required=False)
    player_point = PlayerCorrelationPointSerializer(
        allow_null=True, required=False)


class RankedPlayerCorrelationTileSerializer(serializers.Serializer):
    x_index = serializers.IntegerField(min_value=0)
    y_index = serializers.IntegerField(min_value=0)
    count = serializers.IntegerField(min_value=0)


class RankedPlayerCorrelationTrendPointSerializer(serializers.Serializer):
    x_index = serializers.IntegerField(min_value=0)
    y = serializers.FloatField()
    count = serializers.IntegerField(min_value=0)


class RankedPlayerCorrelationDistributionSerializer(serializers.Serializer):
    metric = serializers.CharField()
    label = serializers.CharField()
    x_label = serializers.CharField()
    y_label = serializers.CharField()
    tracked_population = serializers.IntegerField()
    correlation = serializers.FloatField(allow_null=True)
    x_scale = serializers.ChoiceField(choices=['linear', 'log'])
    y_scale = serializers.ChoiceField(choices=['linear', 'log'])
    x_ticks = serializers.ListField(
        child=serializers.FloatField(), required=False)
    x_edges = serializers.ListField(child=serializers.FloatField())
    y_domain = PlayerCorrelationDomainSerializer()
    tiles = RankedPlayerCorrelationTileSerializer(many=True)
    trend = RankedPlayerCorrelationTrendPointSerializer(many=True)
    player_point = PlayerCorrelationPointSerializer(
        allow_null=True, required=False)


class PlayerTierTypeTileSerializer(serializers.Serializer):
    x_index = serializers.IntegerField(min_value=0)
    y_index = serializers.IntegerField(min_value=0)
    count = serializers.IntegerField(min_value=0)


class PlayerTierTypeTrendSerializer(serializers.Serializer):
    x_index = serializers.IntegerField(min_value=0)
    avg_tier = serializers.FloatField()
    count = serializers.IntegerField(min_value=0)


class PlayerTierTypeCellSerializer(serializers.Serializer):
    ship_type = serializers.CharField()
    ship_tier = serializers.IntegerField()
    pvp_battles = serializers.IntegerField()
    wins = serializers.IntegerField()
    win_ratio = serializers.FloatField()


class PlayerTierTypeCorrelationSerializer(serializers.Serializer):
    metric = serializers.CharField()
    label = serializers.CharField()
    x_label = serializers.CharField()
    y_label = serializers.CharField()
    tracked_population = serializers.IntegerField()
    x_labels = serializers.ListField(child=serializers.CharField())
    y_values = serializers.ListField(
        child=serializers.IntegerField(min_value=1))
    tiles = PlayerTierTypeTileSerializer(many=True)
    trend = PlayerTierTypeTrendSerializer(many=True)
    player_cells = PlayerTierTypeCellSerializer(many=True)


class LandingActivityAttritionMonthSerializer(serializers.Serializer):
    month = serializers.DateField()
    total_players = serializers.IntegerField()
    active_players = serializers.IntegerField()
    cooling_players = serializers.IntegerField()
    dormant_players = serializers.IntegerField()
    active_share = serializers.FloatField()


class LandingActivityAttritionSummarySerializer(serializers.Serializer):
    latest_month = serializers.DateField()
    population_signal = serializers.CharField()
    signal_delta_pct = serializers.FloatField(allow_null=True)
    recent_active_avg = serializers.FloatField()
    prior_active_avg = serializers.FloatField()
    recent_new_avg = serializers.FloatField()
    prior_new_avg = serializers.FloatField()
    months_compared = serializers.IntegerField()


class LandingActivityAttritionSerializer(serializers.Serializer):
    metric = serializers.CharField()
    label = serializers.CharField()
    x_label = serializers.CharField()
    y_label = serializers.CharField()
    tracked_population = serializers.IntegerField()
    months = LandingActivityAttritionMonthSerializer(many=True)
    summary = LandingActivityAttritionSummarySerializer()


class PlayerExplorerRowSerializer(serializers.Serializer):
    kill_ratio = serializers.FloatField(allow_null=True)
    player_score = serializers.FloatField(allow_null=True)
    pvp_survival_rate = serializers.FloatField(allow_null=True)
    name = serializers.CharField()
    player_id = serializers.IntegerField()
    is_hidden = serializers.BooleanField()
    days_since_last_battle = serializers.IntegerField(allow_null=True)
    pvp_ratio = serializers.FloatField(allow_null=True)
    pvp_battles = serializers.IntegerField(allow_null=True)
    account_age_days = serializers.IntegerField(allow_null=True)
    battles_last_29_days = serializers.IntegerField(allow_null=True)
    active_days_last_29_days = serializers.IntegerField(allow_null=True)
    ships_played_total = serializers.IntegerField(allow_null=True)
    ranked_seasons_participated = serializers.IntegerField(allow_null=True)
