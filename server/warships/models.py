from django.db import models
from django.db.models.functions import Lower


class Player(models.Model):
    name = models.CharField(max_length=200)
    player_id = models.IntegerField(null=False, blank=False, db_index=True)
    is_hidden = models.BooleanField(default=False)
    total_battles = models.IntegerField(default=0)
    pvp_battles = models.IntegerField(default=0)
    pvp_wins = models.IntegerField(default=0)
    pvp_losses = models.IntegerField(default=0)
    pvp_ratio = models.FloatField(null=True, blank=True)
    pvp_survival_rate = models.FloatField(null=True, blank=True)
    wins_survival_rate = models.FloatField(null=True, blank=True)
    creation_date = models.DateTimeField(null=True, blank=True)
    days_since_last_battle = models.IntegerField(default=0)
    last_battle_date = models.DateField(null=True, blank=True)
    clan = models.ForeignKey(
        'Clan', on_delete=models.CASCADE, null=True, blank=True)
    last_lookup = models.DateTimeField(null=True, blank=True)
    last_fetch = models.DateTimeField(null=True, blank=True)

    # TODO: consider refactoring these fields into a separate model
    battles_json = models.JSONField(null=True, blank=True)
    battles_updated_at = models.DateTimeField(null=True, blank=True)

    tiers_json = models.JSONField(null=True, blank=True)
    tiers_updated_at = models.DateTimeField(null=True, blank=True)

    activity_json = models.JSONField(null=True, blank=True)
    activity_updated_at = models.DateTimeField(null=True, blank=True)

    type_json = models.JSONField(null=True, blank=True)
    type_updated_at = models.DateTimeField(null=True, blank=True)

    randoms_json = models.JSONField(null=True, blank=True)
    randoms_updated_at = models.DateTimeField(null=True, blank=True)

    ranked_json = models.JSONField(null=True, blank=True)
    ranked_updated_at = models.DateTimeField(null=True, blank=True)

    efficiency_json = models.JSONField(null=True, blank=True)
    efficiency_updated_at = models.DateTimeField(null=True, blank=True)

    verdict = models.CharField(max_length=20, null=True, blank=True)

    def __str__(self):
        clan_name = self.clan.name if self.clan else "No Clan"
        return f"{self.name} ({self.player_id}) {clan_name}"

    class Meta:
        indexes = [
            models.Index(fields=['is_hidden', 'last_battle_date'],
                         name='player_hidden_battle_idx'),
            models.Index(fields=['last_lookup'],
                         name='player_last_lookup_idx'),
            models.Index(fields=['clan', 'last_battle_date'],
                         name='player_clan_battle_idx'),
            models.Index(fields=['pvp_battles', 'pvp_ratio'],
                         name='player_battles_ratio_idx'),
            models.Index(
                fields=['pvp_battles', 'pvp_survival_rate'], name='player_battles_surv_idx'),
            models.Index(Lower('name'), name='player_name_lower_idx'),
        ]


class Ship(models.Model):
    name = models.CharField(max_length=200, null=False, blank=False)
    chart_name = models.CharField(max_length=200, blank=True, default='')
    nation = models.CharField(max_length=200)
    ship_id = models.BigIntegerField(unique=True)
    ship_type = models.CharField(max_length=200)
    tier = models.IntegerField(null=True, blank=True)
    is_premium = models.BooleanField(default=False)

    def __str__(self):
        return str(self.ship_id) + " - " + self.name


class Clan(models.Model):
    clan_id = models.IntegerField(unique=True)
    description = models.TextField(null=True, blank=True)
    leader_id = models.IntegerField(null=True, blank=True)
    leader_name = models.CharField(max_length=200, null=True, blank=True)
    members_count = models.IntegerField(default=0)
    name = models.CharField(max_length=200, null=True, blank=True)
    tag = models.CharField(max_length=200, null=True, blank=True)
    last_fetch = models.DateTimeField(null=True, blank=True)
    last_lookup = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return str(self.clan_id) + '-' + self.name

    class Meta:
        indexes = [
            models.Index(fields=['last_lookup'], name='clan_last_lookup_idx'),
        ]


class Snapshot(models.Model):
    player = models.ForeignKey(Player, on_delete=models.CASCADE)
    date = models.DateField()
    battles = models.IntegerField(default=0)
    wins = models.IntegerField(default=0)
    survived_battles = models.IntegerField(default=0)
    battle_type = models.CharField(max_length=200, null=True, blank=True)
    last_fetch = models.DateTimeField(null=True, blank=True)
    interval_battles = models.IntegerField(null=True, blank=True)
    interval_wins = models.IntegerField(null=True, blank=True)

    def __str__(self):
        return self.player.name + " - " + str(self.date) + " - " + str(self.battles)

    # player and date should be unique together
    class Meta:
        unique_together = ('player', 'date')


class PlayerExplorerSummary(models.Model):
    player = models.OneToOneField(
        Player,
        on_delete=models.CASCADE,
        related_name='explorer_summary',
    )
    battles_last_29_days = models.IntegerField(null=True, blank=True)
    wins_last_29_days = models.IntegerField(null=True, blank=True)
    active_days_last_29_days = models.IntegerField(null=True, blank=True)
    recent_win_rate = models.FloatField(null=True, blank=True)
    activity_trend_direction = models.CharField(
        max_length=16, null=True, blank=True)
    kill_ratio = models.FloatField(null=True, blank=True)
    player_score = models.FloatField(null=True, blank=True)
    ships_played_total = models.IntegerField(null=True, blank=True)
    ship_type_spread = models.IntegerField(null=True, blank=True)
    tier_spread = models.IntegerField(null=True, blank=True)
    ranked_seasons_participated = models.IntegerField(null=True, blank=True)
    latest_ranked_battles = models.IntegerField(null=True, blank=True)
    highest_ranked_league_recent = models.CharField(
        max_length=32, null=True, blank=True)
    refreshed_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['player_score'],
                         name='explorer_score_idx'),
            models.Index(fields=['battles_last_29_days'],
                         name='explorer_battles29_idx'),
            models.Index(fields=['active_days_last_29_days'],
                         name='explorer_active29_idx'),
            models.Index(fields=['ships_played_total'],
                         name='explorer_ships_idx'),
            models.Index(fields=['ranked_seasons_participated'],
                         name='explorer_ranked_idx'),
        ]

    def __str__(self):
        return f"Explorer summary for {self.player_id}"
