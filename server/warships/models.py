from django.conf import settings
from django.db import models
from django.db.models.functions import Lower


REALM_CHOICES = [('na', 'NA'), ('eu', 'EU'), ('asia', 'ASIA')]
VALID_REALMS = {code for code, _label in REALM_CHOICES}
DEFAULT_REALM = 'na'


def realm_cache_key(realm: str, key: str) -> str:
    return f'{realm}:{key}'


class Player(models.Model):
    name = models.CharField(max_length=200)
    player_id = models.BigIntegerField(null=False, blank=False, db_index=True)
    realm = models.CharField(
        max_length=4, choices=REALM_CHOICES, default=DEFAULT_REALM, db_index=True)
    is_hidden = models.BooleanField(default=False)
    is_streamer = models.BooleanField(default=False)
    twitch_handle = models.CharField(max_length=64, blank=True, default='')
    twitch_url = models.URLField(max_length=500, blank=True, default='')
    total_battles = models.IntegerField(default=0)
    pvp_battles = models.IntegerField(default=0)
    pvp_wins = models.IntegerField(default=0)
    pvp_losses = models.IntegerField(default=0)
    pvp_frags = models.IntegerField(default=0)
    pvp_survived_battles = models.IntegerField(default=0)
    pvp_deaths = models.IntegerField(default=0)
    pvp_ratio = models.FloatField(null=True, blank=True)
    actual_kdr = models.FloatField(null=True, blank=True)
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

    achievements_json = models.JSONField(null=True, blank=True)
    achievements_updated_at = models.DateTimeField(null=True, blank=True)

    verdict = models.CharField(max_length=20, null=True, blank=True)

    ENRICHMENT_PENDING = 'pending'
    ENRICHMENT_ENRICHED = 'enriched'
    ENRICHMENT_EMPTY = 'empty'
    ENRICHMENT_SKIPPED_HIDDEN = 'skipped_hidden'
    ENRICHMENT_SKIPPED_LOW_BATTLES = 'skipped_low_battles'
    ENRICHMENT_SKIPPED_INACTIVE = 'skipped_inactive'
    ENRICHMENT_STATUS_CHOICES = [
        (ENRICHMENT_PENDING, 'Pending'),
        (ENRICHMENT_ENRICHED, 'Enriched'),
        (ENRICHMENT_EMPTY, 'Empty'),
        (ENRICHMENT_SKIPPED_HIDDEN, 'Skipped — hidden'),
        (ENRICHMENT_SKIPPED_LOW_BATTLES, 'Skipped — low battles'),
        (ENRICHMENT_SKIPPED_INACTIVE, 'Skipped — inactive'),
    ]
    enrichment_status = models.CharField(
        max_length=24,
        choices=ENRICHMENT_STATUS_CHOICES,
        default=ENRICHMENT_PENDING,
        db_index=True,
    )

    def __str__(self):
        clan_name = self.clan.name if self.clan else "No Clan"
        return f"{self.name} ({self.player_id}) {clan_name}"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['player_id', 'realm'], name='unique_player_per_realm'),
        ]
        indexes = [
            models.Index(fields=['last_lookup'],
                         name='player_last_lookup_idx'),
            models.Index(fields=['pvp_battles', 'pvp_ratio'],
                         name='player_battles_ratio_idx'),
            models.Index(
                fields=['pvp_battles', 'pvp_survival_rate'], name='player_battles_surv_idx'),
            models.Index(Lower('name'), name='player_name_lower_idx'),
            models.Index(fields=['realm', 'pvp_battles', 'pvp_ratio'],
                         name='player_realm_battles_ratio_idx'),
            models.Index(fields=['realm', 'pvp_battles', 'pvp_survival_rate'],
                         name='player_realm_battles_surv_idx'),
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
    clan_id = models.BigIntegerField(db_index=True)
    realm = models.CharField(
        max_length=4, choices=REALM_CHOICES, default=DEFAULT_REALM, db_index=True)
    description = models.TextField(null=True, blank=True)
    leader_id = models.BigIntegerField(null=True, blank=True)
    leader_name = models.CharField(max_length=200, null=True, blank=True)
    members_count = models.IntegerField(default=0)
    name = models.CharField(max_length=200, null=True, blank=True)
    tag = models.CharField(max_length=200, null=True, blank=True)
    last_fetch = models.DateTimeField(null=True, blank=True)
    last_lookup = models.DateTimeField(null=True, blank=True)
    cached_total_wins = models.BigIntegerField(null=True, blank=True)
    cached_total_battles = models.BigIntegerField(null=True, blank=True)
    cached_active_member_count = models.IntegerField(null=True, blank=True)
    cached_clan_wr = models.FloatField(null=True, blank=True)

    def __str__(self):
        return str(self.clan_id) + '-' + self.name

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['clan_id', 'realm'], name='unique_clan_per_realm'),
        ]
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
    eligible_ship_count = models.IntegerField(null=True, blank=True)
    efficiency_badge_rows_total = models.IntegerField(null=True, blank=True)
    badge_rows_unmapped = models.IntegerField(null=True, blank=True)
    expert_count = models.IntegerField(null=True, blank=True)
    grade_i_count = models.IntegerField(null=True, blank=True)
    grade_ii_count = models.IntegerField(null=True, blank=True)
    grade_iii_count = models.IntegerField(null=True, blank=True)
    raw_badge_points = models.IntegerField(null=True, blank=True)
    normalized_badge_strength = models.FloatField(null=True, blank=True)
    shrunken_efficiency_strength = models.FloatField(null=True, blank=True)
    efficiency_rank_percentile = models.FloatField(null=True, blank=True)
    efficiency_rank_tier = models.CharField(
        max_length=4, null=True, blank=True)
    has_efficiency_rank_icon = models.BooleanField(default=False)
    efficiency_rank_population_size = models.IntegerField(
        null=True, blank=True)
    efficiency_rank_updated_at = models.DateTimeField(null=True, blank=True)
    ranked_seasons_participated = models.IntegerField(null=True, blank=True)
    latest_ranked_battles = models.IntegerField(null=True, blank=True)
    highest_ranked_league_recent = models.CharField(
        max_length=32, null=True, blank=True)
    clan_battle_seasons_participated = models.IntegerField(
        null=True, blank=True)
    clan_battle_total_battles = models.IntegerField(null=True, blank=True)
    clan_battle_overall_win_rate = models.FloatField(null=True, blank=True)
    clan_battle_summary_updated_at = models.DateTimeField(
        null=True, blank=True)
    refreshed_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['efficiency_rank_percentile'],
                         name='explorer_eff_rank_idx'),
        ]

    def __str__(self):
        return f"Explorer summary for {self.player_id}"


class LandingPlayerBestSnapshot(models.Model):
    realm = models.CharField(
        max_length=4,
        choices=REALM_CHOICES,
        default=DEFAULT_REALM,
        db_index=True,
    )
    sort = models.CharField(max_length=16, db_index=True)
    payload_json = models.JSONField(default=list, blank=True)
    generated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['realm', 'sort'],
                name='unique_landing_player_best_snapshot_per_realm_sort',
            ),
        ]

    def __str__(self):
        return f"Landing best snapshot {self.realm}:{self.sort}"


class EntityVisitEvent(models.Model):
    ENTITY_TYPE_PLAYER = 'player'
    ENTITY_TYPE_CLAN = 'clan'
    ENTITY_TYPE_CHOICES = [
        (ENTITY_TYPE_PLAYER, 'Player'),
        (ENTITY_TYPE_CLAN, 'Clan'),
    ]

    SOURCE_WEB_FIRST_PARTY = 'web_first_party'
    SOURCE_GA4 = 'ga4'
    SOURCE_CHOICES = [
        (SOURCE_WEB_FIRST_PARTY, 'Web First Party'),
        (SOURCE_GA4, 'Google Analytics 4'),
    ]

    event_uuid = models.UUIDField(unique=True)
    occurred_at = models.DateTimeField()
    event_date = models.DateField(db_index=True)
    entity_type = models.CharField(max_length=16, choices=ENTITY_TYPE_CHOICES)
    entity_id = models.BigIntegerField()
    realm = models.CharField(
        max_length=4, choices=REALM_CHOICES, default=DEFAULT_REALM, db_index=True)
    entity_name_snapshot = models.CharField(max_length=200)
    entity_slug_snapshot = models.CharField(
        max_length=255, blank=True, default='')
    route_path = models.CharField(max_length=255)
    referrer_path = models.CharField(max_length=255, blank=True, default='')
    source = models.CharField(
        max_length=32, choices=SOURCE_CHOICES, default=SOURCE_WEB_FIRST_PARTY)
    visitor_key_hash = models.CharField(max_length=64)
    session_key_hash = models.CharField(max_length=64)
    dedupe_bucket_started_at = models.DateTimeField(null=True, blank=True)
    counted_in_deduped_views = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['entity_type', 'entity_id',
                         'event_date'], name='visit_event_entity_day_idx'),
            models.Index(fields=['entity_type', 'entity_id',
                         'occurred_at'], name='visit_event_entity_time_idx'),
            models.Index(fields=['entity_type', 'entity_id', 'visitor_key_hash',
                         'occurred_at'], name='visit_event_dedupe_idx'),
        ]

    def __str__(self):
        return f"{self.entity_type}:{self.entity_id} @ {self.occurred_at.isoformat()}"


class EntityVisitDaily(models.Model):
    date = models.DateField()
    entity_type = models.CharField(
        max_length=16, choices=EntityVisitEvent.ENTITY_TYPE_CHOICES)
    entity_id = models.BigIntegerField()
    realm = models.CharField(
        max_length=4, choices=REALM_CHOICES, default=DEFAULT_REALM, db_index=True)
    entity_name_snapshot = models.CharField(max_length=200)
    views_raw = models.IntegerField(default=0)
    views_deduped = models.IntegerField(default=0)
    unique_visitors = models.IntegerField(default=0)
    unique_sessions = models.IntegerField(default=0)
    last_view_at = models.DateTimeField(null=True, blank=True)
    source_first_party_views = models.IntegerField(default=0)
    source_ga4_views = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['date', 'entity_type', 'entity_id', 'realm'], name='unique_entity_visit_daily_realm'),
        ]
        indexes = [
            models.Index(fields=['entity_type', 'date'],
                         name='visit_daily_type_date_idx'),
            models.Index(fields=['entity_type', 'entity_id',
                         'date'], name='visit_daily_entity_date_idx'),
        ]

    def __str__(self):
        return f"{self.date} {self.entity_type}:{self.entity_id}"


class PlayerAchievementStat(models.Model):
    player = models.ForeignKey(
        Player,
        on_delete=models.CASCADE,
        related_name='achievement_stats',
    )
    achievement_code = models.CharField(max_length=64)
    achievement_slug = models.CharField(max_length=64)
    achievement_label = models.CharField(max_length=128)
    category = models.CharField(max_length=32)
    count = models.IntegerField()
    source_kind = models.CharField(max_length=16, default='battle')
    refreshed_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['player', 'achievement_code', 'source_kind'],
                name='unique_player_achievement_source',
            ),
        ]
        indexes = []

    def __str__(self):
        return f"{self.player.name} - {self.achievement_label}"


class DeletedAccount(models.Model):
    """Permanently blocklisted Wargaming account IDs (GDPR / account deletion)."""
    account_id = models.BigIntegerField(unique=True)
    deleted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = []

    def __str__(self):
        return f"DeletedAccount({self.account_id})"


class StreamerSubmission(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_REJECTED, 'Rejected'),
    ]

    ign = models.CharField(max_length=64)
    realm = models.CharField(max_length=8, blank=True, default='')
    twitch_handle = models.CharField(max_length=64)
    twitch_url = models.URLField(max_length=500)
    submitter_ip = models.GenericIPAddressField(null=True, blank=True)
    submitter_ua = models.CharField(max_length=300, blank=True, default='')
    status = models.CharField(
        max_length=12, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='streamer_submissions_reviewed',
    )

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'created_at'],
                         name='streamer_sub_status_idx'),
        ]

    def __str__(self):
        return f"StreamerSubmission({self.ign} -> {self.twitch_handle}, {self.status})"


class MvPlayerDistributionStats(models.Model):
    """Unmanaged model backed by the mv_player_distribution_stats materialized view."""
    realm = models.CharField(max_length=4)
    pvp_ratio = models.FloatField(null=True)
    pvp_survival_rate = models.FloatField(null=True)
    pvp_battles = models.IntegerField()
    is_hidden = models.BooleanField()

    class Meta:
        managed = False
        db_table = 'mv_player_distribution_stats'
