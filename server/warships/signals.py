from __future__ import annotations

import json
import os

from django.db.models.signals import post_migrate
from django.dispatch import receiver

from warships.models import VALID_REALMS

# Per-realm cron hour offsets for staggering heavy tasks.
# Prevents concurrent full crawls from competing for worker memory.
REALM_CRAWL_CRON_HOURS = {'eu': 0, 'na': 6, 'asia': 12}
REALM_REFRESH_AM_OFFSETS = {'eu': 0, 'na': 2, 'asia': 4}
REALM_REFRESH_PM_OFFSETS = {'eu': 0, 'na': 2, 'asia': 4}
REALM_RANKED_OFFSETS = {'eu': 0, 'na': 1, 'asia': 2}


def _env_flag(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _configured_clan_battle_warm_ids():
    raw_value = os.getenv("CLAN_BATTLE_WARM_CLAN_IDS", "1000055908")
    return [clan_id.strip() for clan_id in raw_value.split(",") if clan_id.strip()]


@receiver(post_migrate)
def ensure_daily_clan_crawl_schedule(sender, **kwargs):
    if getattr(sender, "name", None) != "warships":
        return

    from django_celery_beat.models import CrontabSchedule, IntervalSchedule, PeriodicTask

    crawler_schedules_enabled = _env_flag("ENABLE_CRAWLER_SCHEDULES", False)

    base_hour = int(os.getenv("CLAN_CRAWL_SCHEDULE_HOUR", "3"))
    minute = os.getenv("CLAN_CRAWL_SCHEDULE_MINUTE", "0")

    for realm in sorted(VALID_REALMS):
        realm_hour = (base_hour + REALM_CRAWL_CRON_HOURS.get(realm, 0)) % 24
        schedule, _ = CrontabSchedule.objects.get_or_create(
            minute=minute,
            hour=str(realm_hour),
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone="UTC",
        )
        PeriodicTask.objects.update_or_create(
            name=f"daily-clan-crawl-{realm}",
            defaults={
                "task": "warships.tasks.crawl_all_clans_task",
                "crontab": schedule,
                "enabled": crawler_schedules_enabled,
                "args": json.dumps([]),
                "kwargs": json.dumps({"resume": False, "realm": realm}),
                "description": f"Daily full crawl of clans and players from the Wargaming API ({realm.upper()}).",
            },
        )

    # Clean up legacy non-realm schedule
    PeriodicTask.objects.filter(name="daily-clan-crawl").delete()

    # -- Incremental Player Refresh (AM + PM) --
    base_am_hour = int(os.getenv("PLAYER_REFRESH_SCHEDULE_HOUR_AM", "5"))
    base_pm_hour = int(os.getenv("PLAYER_REFRESH_SCHEDULE_HOUR_PM", "15"))

    for realm in sorted(VALID_REALMS):
        am_hour = (base_am_hour + REALM_REFRESH_AM_OFFSETS.get(realm, 0)) % 24
        player_am_schedule, _ = CrontabSchedule.objects.get_or_create(
            minute="0",
            hour=str(am_hour),
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone="UTC",
        )
        PeriodicTask.objects.update_or_create(
            name=f"incremental-player-refresh-am-{realm}",
            defaults={
                "task": "warships.tasks.incremental_player_refresh_task",
                "crontab": player_am_schedule,
                "enabled": crawler_schedules_enabled,
                "args": json.dumps([]),
                "kwargs": json.dumps({"realm": realm}),
                "description": f"Morning incremental refresh of active player data ({realm.upper()}).",
            },
        )

    PeriodicTask.objects.filter(name="incremental-player-refresh-am").delete()

    for realm in sorted(VALID_REALMS):
        pm_hour = (base_pm_hour + REALM_REFRESH_PM_OFFSETS.get(realm, 0)) % 24
        player_pm_schedule, _ = CrontabSchedule.objects.get_or_create(
            minute="0",
            hour=str(pm_hour),
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone="UTC",
        )
        PeriodicTask.objects.update_or_create(
            name=f"incremental-player-refresh-pm-{realm}",
            defaults={
                "task": "warships.tasks.incremental_player_refresh_task",
                "crontab": player_pm_schedule,
                "enabled": crawler_schedules_enabled,
                "args": json.dumps([]),
                "kwargs": json.dumps({"realm": realm}),
                "description": f"Afternoon incremental refresh of active player data ({realm.upper()}).",
            },
        )

    PeriodicTask.objects.filter(name="incremental-player-refresh-pm").delete()

    base_ranked_hour = int(os.getenv("RANKED_INCREMENTAL_SCHEDULE_HOUR", "10"))
    ranked_minute = os.getenv("RANKED_INCREMENTAL_SCHEDULE_MINUTE", "30")

    for realm in sorted(VALID_REALMS):
        ranked_hour = (base_ranked_hour + REALM_RANKED_OFFSETS.get(realm, 0)) % 24
        ranked_schedule, _ = CrontabSchedule.objects.get_or_create(
            minute=ranked_minute,
            hour=str(ranked_hour),
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone="UTC",
        )
        PeriodicTask.objects.update_or_create(
            name=f"daily-ranked-incrementals-{realm}",
            defaults={
                "task": "warships.tasks.incremental_ranked_data_task",
                "crontab": ranked_schedule,
                "enabled": crawler_schedules_enabled,
                "args": json.dumps([]),
                "kwargs": json.dumps({"realm": realm}),
                "description": f"Daily incremental refresh of ranked history ({realm.upper()}).",
            },
        )

    PeriodicTask.objects.filter(name="daily-ranked-incrementals").delete()

    watchdog_minutes = int(os.getenv("CLAN_CRAWL_WATCHDOG_MINUTES", "5"))
    watchdog_schedule, _ = IntervalSchedule.objects.get_or_create(
        every=watchdog_minutes,
        period=IntervalSchedule.MINUTES,
    )

    for realm in sorted(VALID_REALMS):
        PeriodicTask.objects.update_or_create(
            name=f"clan-crawl-watchdog-{realm}",
            defaults={
                "task": "warships.tasks.ensure_crawl_all_clans_running_task",
                "interval": watchdog_schedule,
                "enabled": crawler_schedules_enabled,
                "args": json.dumps([]),
                "kwargs": json.dumps({"realm": realm}),
                "description": f"Checks for stale clan-crawl lock and resumes interrupted crawls ({realm.upper()}).",
            },
        )

    PeriodicTask.objects.filter(name="clan-crawl-watchdog").delete()

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

    landing_warm_minutes = int(os.getenv("LANDING_PAGE_WARM_MINUTES", "55"))
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
        realm_hour = (clan_tier_dist_warm_hour + REALM_CRAWL_CRON_HOURS.get(realm, 0)) % 24
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
