from __future__ import annotations

import json
import os

from django.db.models.signals import post_migrate
from django.dispatch import receiver

from warships.models import VALID_REALMS

# Per-realm cron hour offsets for staggering heavy tasks.
REALM_CRAWL_CRON_HOURS = {'eu': 0, 'na': 6, 'asia': 12}


def _configured_clan_battle_warm_ids():
    raw_value = os.getenv("CLAN_BATTLE_WARM_CLAN_IDS", "1000055908")
    return [clan_id.strip() for clan_id in raw_value.split(",") if clan_id.strip()]


# Tasks migrated to DO Functions (no longer registered as Celery Beat schedules):
#   - daily-clan-crawl-{realm}        → Functions Phase 2
#   - clan-crawl-watchdog-{realm}     → Functions Phase 2
#   - player-enrichment-kickstart     → Functions enrichment/enrich-batch
#   - incremental-player-refresh-*    → Functions Phase 2
#   - daily-ranked-incrementals-*     → Functions Phase 2
_RETIRED_SCHEDULE_NAMES = [
    "daily-clan-crawl",
    "daily-clan-crawl-eu",
    "daily-clan-crawl-na",
    "clan-crawl-watchdog",
    "clan-crawl-watchdog-eu",
    "clan-crawl-watchdog-na",
    "player-enrichment-kickstart",
    "daily-player-enrichment",
    "player-enrichment",
    "incremental-player-refresh-am",
    "incremental-player-refresh-am-eu",
    "incremental-player-refresh-am-na",
    "incremental-player-refresh-pm",
    "incremental-player-refresh-pm-eu",
    "incremental-player-refresh-pm-na",
    "daily-ranked-incrementals",
    "daily-ranked-incrementals-eu",
    "daily-ranked-incrementals-na",
]


@receiver(post_migrate)
def register_periodic_schedules(sender, **kwargs):
    if getattr(sender, "name", None) != "warships":
        return

    from django_celery_beat.models import CrontabSchedule, IntervalSchedule, PeriodicTask

    # Remove retired schedules (migrated to DO Functions)
    PeriodicTask.objects.filter(name__in=_RETIRED_SCHEDULE_NAMES).delete()

    warm_clan_ids = _configured_clan_battle_warm_ids()
    if warm_clan_ids:
        warm_minutes = int(os.getenv("CLAN_BATTLE_WARM_MINUTES", "30"))
        warm_schedule, _ = IntervalSchedule.objects.get_or_create(
            every=warm_minutes,
            period=IntervalSchedule.MINUTES,
        )

        PeriodicTask.objects.update_or_create(
            name="clan-battle-summary-warmer",
            defaults={
                "task": "warships.tasks.warm_clan_battle_summaries_task",
                "interval": warm_schedule,
                "enabled": True,
                "args": json.dumps([]),
                "kwargs": json.dumps({"clan_ids": warm_clan_ids}),
                "description": "Keeps configured clan-battle summary fixtures warm in cache for smoke tests and UI validation.",
            },
        )
    else:
        PeriodicTask.objects.filter(
            name="clan-battle-summary-warmer").update(enabled=False)

    landing_warm_minutes = int(os.getenv("LANDING_PAGE_WARM_MINUTES", "120"))
    landing_warm_schedule, _ = IntervalSchedule.objects.get_or_create(
        every=landing_warm_minutes,
        period=IntervalSchedule.MINUTES,
    )

    for realm in sorted(VALID_REALMS):
        PeriodicTask.objects.update_or_create(
            name=f"landing-page-warmer-{realm}",
            defaults={
                "task": "warships.tasks.warm_landing_page_content_task",
                "interval": landing_warm_schedule,
                "enabled": True,
                "args": json.dumps([]),
                "kwargs": json.dumps({"include_recent": True, "realm": realm}),
                "description": f"Refreshes landing page caches ({realm.upper()}).",
            },
        )

    PeriodicTask.objects.filter(name="landing-page-warmer").delete()

    landing_best_snapshot_hour = int(
        os.getenv("LANDING_BEST_PLAYER_SNAPSHOT_HOUR", "1"))
    for realm in sorted(VALID_REALMS):
        realm_hour = (landing_best_snapshot_hour +
                      REALM_CRAWL_CRON_HOURS.get(realm, 0)) % 24
        landing_best_snapshot_schedule, _ = CrontabSchedule.objects.get_or_create(
            minute="15",
            hour=str(realm_hour),
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone="UTC",
        )
        PeriodicTask.objects.update_or_create(
            name=f"landing-best-player-snapshot-materializer-{realm}",
            defaults={
                "task": "warships.tasks.materialize_landing_player_best_snapshots_task",
                "crontab": landing_best_snapshot_schedule,
                "enabled": True,
                "args": json.dumps([]),
                "kwargs": json.dumps({"realm": realm}),
                "description": f"Materializes daily landing Best-player sub-sort snapshots ({realm.upper()}).",
            },
        )

    # -- Player Distribution Warmer (split from landing warmer) --
    dist_warm_minutes = int(os.getenv("DISTRIBUTION_WARM_MINUTES", "360"))
    dist_warm_schedule, _ = IntervalSchedule.objects.get_or_create(
        every=dist_warm_minutes,
        period=IntervalSchedule.MINUTES,
    )

    for realm in sorted(VALID_REALMS):
        PeriodicTask.objects.update_or_create(
            name=f"player-distribution-warmer-{realm}",
            defaults={
                "task": "warships.tasks.warm_player_distributions_task",
                "interval": dist_warm_schedule,
                "enabled": True,
                "args": json.dumps([]),
                "kwargs": json.dumps({"realm": realm}),
                "description": f"Refreshes player distribution caches — MV refresh + WR/battles/survival bins ({realm.upper()}).",
            },
        )

    # -- Player Correlation Warmer (split from landing warmer) --
    corr_warm_minutes = int(os.getenv("CORRELATION_WARM_MINUTES", "360"))
    corr_warm_schedule, _ = IntervalSchedule.objects.get_or_create(
        every=corr_warm_minutes,
        period=IntervalSchedule.MINUTES,
    )

    for realm in sorted(VALID_REALMS):
        PeriodicTask.objects.update_or_create(
            name=f"player-correlation-warmer-{realm}",
            defaults={
                "task": "warships.tasks.warm_player_correlations_task",
                "interval": corr_warm_schedule,
                "enabled": True,
                "args": json.dumps([]),
                "kwargs": json.dumps({"realm": realm}),
                "description": f"Refreshes player population correlations — tier-type, WR-survival, ranked WR-battles ({realm.upper()}).",
            },
        )

    hot_entity_warm_minutes = int(
        os.getenv("HOT_ENTITY_CACHE_WARM_MINUTES", "30"))
    hot_entity_warm_schedule, _ = IntervalSchedule.objects.get_or_create(
        every=hot_entity_warm_minutes,
        period=IntervalSchedule.MINUTES,
    )

    for realm in sorted(VALID_REALMS):
        PeriodicTask.objects.update_or_create(
            name=f"hot-entity-cache-warmer-{realm}",
            defaults={
                "task": "warships.tasks.warm_hot_entity_caches_task",
                "interval": hot_entity_warm_schedule,
                "enabled": True,
                "args": json.dumps([]),
                "kwargs": json.dumps({"realm": realm}),
                "description": f"Keeps hottest player and clan detail caches warm ({realm.upper()}).",
            },
        )

    PeriodicTask.objects.filter(name="hot-entity-cache-warmer").delete()

    bulk_cache_load_hours = int(os.getenv("BULK_CACHE_LOAD_HOURS", "12"))
    bulk_cache_load_schedule, _ = IntervalSchedule.objects.get_or_create(
        every=bulk_cache_load_hours * 60,
        period=IntervalSchedule.MINUTES,
    )

    for realm in sorted(VALID_REALMS):
        PeriodicTask.objects.update_or_create(
            name=f"bulk-entity-cache-loader-{realm}",
            defaults={
                "task": "warships.tasks.bulk_load_entity_caches_task",
                "interval": bulk_cache_load_schedule,
                "enabled": True,
                "args": json.dumps([]),
                "kwargs": json.dumps({"realm": realm}),
                "description": f"Bulk-loads top player and clan detail payloads into Redis ({realm.upper()}).",
            },
        )

    PeriodicTask.objects.filter(name="bulk-entity-cache-loader").delete()

    recently_viewed_warm_minutes = int(
        os.getenv("RECENTLY_VIEWED_WARM_MINUTES", "10"))
    recently_viewed_warm_schedule, _ = IntervalSchedule.objects.get_or_create(
        every=recently_viewed_warm_minutes,
        period=IntervalSchedule.MINUTES,
    )

    for realm in sorted(VALID_REALMS):
        PeriodicTask.objects.update_or_create(
            name=f"recently-viewed-player-warmer-{realm}",
            defaults={
                "task": "warships.tasks.warm_recently_viewed_players_task",
                "interval": recently_viewed_warm_schedule,
                "enabled": True,
                "args": json.dumps([]),
                "kwargs": json.dumps({"realm": realm}),
                "description": f"Re-caches recently-viewed players whose detail cache entry is missing ({realm.upper()}).",
            },
        )

    PeriodicTask.objects.filter(name="recently-viewed-player-warmer").delete()

    # -- Daily Clan Tier Distribution Warmer --
    # Recalculates tier distribution cache for every clan with members.
    # Staggered by realm to avoid concurrent DB pressure.
    clan_tier_dist_warm_hour = int(os.getenv("CLAN_TIER_DIST_WARM_HOUR", "2"))
    for realm in sorted(VALID_REALMS):
        realm_hour = (clan_tier_dist_warm_hour +
                      REALM_CRAWL_CRON_HOURS.get(realm, 0)) % 24
        clan_tier_dist_schedule, _ = CrontabSchedule.objects.get_or_create(
            minute="30",
            hour=str(realm_hour),
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone="UTC",
        )
        PeriodicTask.objects.update_or_create(
            name=f"daily-clan-tier-dist-warmer-{realm}",
            defaults={
                "task": "warships.tasks.warm_all_clan_tier_distributions_task",
                "crontab": clan_tier_dist_schedule,
                "enabled": True,
                "args": json.dumps([]),
                "kwargs": json.dumps({"realm": realm}),
                "description": f"Daily recalculation of clan tier distribution cache for all clans ({realm.upper()}).",
            },
        )
