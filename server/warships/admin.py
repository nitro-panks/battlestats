from django.contrib import admin
from django.utils import timezone
from .models import Player, Ship, Clan, Snapshot, EntityVisitDaily, EntityVisitEvent, StreamerSubmission


@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):
    search_fields = ('name', 'player_id')
    list_display = ('name', 'player_id', 'is_hidden', 'is_streamer',
                    'last_lookup', 'last_fetch')
    list_filter = ('is_hidden', 'is_streamer')


@admin.register(Ship)
class ShipAdmin(admin.ModelAdmin):
    search_fields = ('name', 'ship_id')
    list_display = ('name', 'ship_id', 'ship_type', 'tier', 'is_premium')
    list_filter = ('ship_type', 'tier', 'is_premium')


@admin.register(Snapshot)
class SnapshotAdmin(admin.ModelAdmin):
    search_fields = ('player__name',)
    list_display = ('player', 'date', 'battles', 'wins', 'battle_type')
    list_filter = ('battle_type', 'date')


@admin.register(Clan)
class ClanAdmin(admin.ModelAdmin):
    search_fields = ('name', 'tag', 'clan_id')
    list_display = ('name', 'tag', 'clan_id', 'members_count',
                    'last_lookup', 'last_fetch')
    list_filter = ('members_count',)


@admin.register(StreamerSubmission)
class StreamerSubmissionAdmin(admin.ModelAdmin):
    list_display = ('ign', 'twitch_handle', 'realm', 'status',
                    'created_at', 'submitter_ip')
    list_filter = ('status', 'realm', 'created_at')
    search_fields = ('ign', 'twitch_handle', 'twitch_url', 'submitter_ip')
    readonly_fields = ('submitter_ip', 'submitter_ua', 'created_at')
    actions = ('approve_selected', 'reject_selected')

    def approve_selected(self, request, queryset):
        from .data import invalidate_player_detail_cache
        promoted = 0
        for sub in queryset:
            qs = Player.objects.filter(name__iexact=sub.ign)
            if sub.realm:
                qs = qs.filter(realm=sub.realm)
            player = qs.first()
            if player is not None:
                player.is_streamer = True
                player.twitch_handle = sub.twitch_handle
                player.twitch_url = sub.twitch_url
                player.save(update_fields=[
                    'is_streamer', 'twitch_handle', 'twitch_url'])
                invalidate_player_detail_cache(
                    player.player_id, realm=player.realm)
                promoted += 1
        updated = queryset.update(
            status=StreamerSubmission.STATUS_APPROVED,
            reviewed_at=timezone.now(),
            reviewed_by=request.user,
        )
        self.message_user(
            request,
            f"{updated} submission(s) approved; {promoted} player(s) promoted.",
        )
    approve_selected.short_description = "Approve selected submissions"

    def reject_selected(self, request, queryset):
        updated = queryset.update(
            status=StreamerSubmission.STATUS_REJECTED,
            reviewed_at=timezone.now(),
            reviewed_by=request.user,
        )
        self.message_user(request, f"{updated} submission(s) rejected.")
    reject_selected.short_description = "Reject selected submissions"


@admin.register(EntityVisitEvent)
class EntityVisitEventAdmin(admin.ModelAdmin):
    search_fields = ('entity_name_snapshot', 'entity_id',
                     'route_path', 'event_uuid')
    list_display = (
        'event_date',
        'entity_type',
        'entity_id',
        'entity_name_snapshot',
        'counted_in_deduped_views',
        'source',
        'occurred_at',
    )
    list_filter = ('entity_type', 'source',
                   'counted_in_deduped_views', 'event_date')
    readonly_fields = (
        'event_uuid',
        'occurred_at',
        'event_date',
        'entity_type',
        'entity_id',
        'entity_name_snapshot',
        'entity_slug_snapshot',
        'route_path',
        'referrer_path',
        'source',
        'visitor_key_hash',
        'session_key_hash',
        'dedupe_bucket_started_at',
        'counted_in_deduped_views',
        'created_at',
    )
    ordering = ('-occurred_at',)


@admin.register(EntityVisitDaily)
class EntityVisitDailyAdmin(admin.ModelAdmin):
    search_fields = ('entity_name_snapshot', 'entity_id')
    list_display = (
        'date',
        'entity_type',
        'entity_id',
        'entity_name_snapshot',
        'views_raw',
        'views_deduped',
        'unique_visitors',
        'last_view_at',
    )
    list_filter = ('entity_type', 'date')
    readonly_fields = (
        'date',
        'entity_type',
        'entity_id',
        'entity_name_snapshot',
        'views_raw',
        'views_deduped',
        'unique_visitors',
        'unique_sessions',
        'last_view_at',
        'source_first_party_views',
        'source_ga4_views',
        'updated_at',
    )
    ordering = ('-date', '-views_deduped', '-last_view_at')
