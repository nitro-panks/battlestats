from rest_framework import serializers
from .models import Player, Clan, Ship
from .data import _calculate_player_kill_ratio, _coerce_battle_rows, build_player_summary, get_highest_ranked_league_name


class PlayerSerializer(serializers.ModelSerializer):
    clan_name = serializers.SerializerMethodField()
    clan_id = serializers.SerializerMethodField()
    clan_tag = serializers.SerializerMethodField()
    is_clan_leader = serializers.SerializerMethodField()
    highest_ranked_league = serializers.SerializerMethodField()
    kill_ratio = serializers.SerializerMethodField()
    player_score = serializers.SerializerMethodField()

    class Meta:
        model = Player
        fields = '__all__'

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
    pvp_ratio = serializers.FloatField(allow_null=True)
    days_since_last_battle = serializers.IntegerField(allow_null=True)
    is_leader = serializers.BooleanField()
    is_pve_player = serializers.BooleanField()
    is_ranked_player = serializers.BooleanField()
    highest_ranked_league = serializers.CharField(allow_null=True)
    ranked_hydration_pending = serializers.BooleanField()
    ranked_updated_at = serializers.DateTimeField(allow_null=True)
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
    value_format = serializers.ChoiceField(choices=['percent', 'integer'])
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


class PlayerTierTypeTileSerializer(serializers.Serializer):
    ship_type = serializers.CharField()
    ship_tier = serializers.IntegerField()
    count = serializers.IntegerField()


class PlayerTierTypeTrendSerializer(serializers.Serializer):
    ship_type = serializers.CharField()
    avg_tier = serializers.FloatField()
    count = serializers.IntegerField()


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
