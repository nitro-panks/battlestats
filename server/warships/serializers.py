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
