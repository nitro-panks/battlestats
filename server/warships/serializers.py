from rest_framework import serializers
from .models import Player, Clan, Ship


class PlayerSerializer(serializers.ModelSerializer):
    clan_name = serializers.SerializerMethodField()
    clan_id = serializers.SerializerMethodField()
    clan_tag = serializers.SerializerMethodField()

    class Meta:
        model = Player
        fields = '__all__'

    def get_clan_name(self, obj):
        return obj.clan.name if obj.clan else None

    def get_clan_id(self, obj):
        return obj.clan.clan_id if obj.clan else None

    def get_clan_tag(self, obj):
        return obj.clan.tag if obj.clan else None


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


class ClanMemberSerializer(serializers.Serializer):
    name = serializers.CharField()
    is_hidden = serializers.BooleanField()
    pvp_ratio = serializers.FloatField(allow_null=True)


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


class PlayerExplorerRowSerializer(serializers.Serializer):
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
