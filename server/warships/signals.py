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


def _env_flag(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


# Retired schedule names — removed from Celery Beat on next post_migrate.
# Kept as a deletion list so stale PeriodicTask rows are cleaned up across deploys.
#
# Historical arc:
#   2026-04-04 (c8f542d) — DO Functions migration retired clan crawl,
#       enrichment, incremental player refresh, and incremental ranked
#       refresh schedules in favor of serverless cron triggers.
#   2026-04-08           — Migration reverted. DO Functions egress from a
#       rotating IP pool that cannot be whitelisted by the Wargaming
#       application_id; every serverless call returned 407 INVALID_IP_ADDRESS.
#       Only the enrichment schedule was restored at that time (via
#       `player-enrichment-kickstart` below).
#   2026-04-11           — Clan crawl, incremental player refresh, and
#       incremental ranked refresh schedules restored here as well,
#       completing the revert. The legacy am/pm player refresh names and
#       `daily-ranked-incrementals-*` names stay retired because the new
#       schedules use different (interval-based) names.
_RETIRED_SCHEDULE_NAMES = [
    "daily-clan-crawl",
    "clan-crawl-watchdog",
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

    # -- Player Enrichment Kickstart --
    # The enrichment task self-chains between batches via apply_async(countdown=10s),
    # so this periodic task only exists to re-seed the chain if it ever stops (worker
    # restart, cleared lock, purged queue). The task itself checks its Redis lock and
    # returns immediately with {'status': 'skipped', 'reason': 'already-running'} when
    # a batch is in progress, so a frequent interval is safe and cheap.
    enrich_kickstart_minutes = int(
        os.getenv("ENRICH_KICKSTART_MINUTES", "15"))
    enrich_kickstart_schedule, _ = IntervalSchedule.objects.get_or_create(
        every=enrich_kickstart_minutes,
        period=IntervalSchedule.MINUTES,
    )
    PeriodicTask.objects.update_or_create(
        name="player-enrichment-kickstart",
        defaults={
            "task": "warships.tasks.enrich_player_data_task",
            "interval": enrich_kickstart_schedule,
            "enabled": True,
            "args": json.dumps([]),
            "kwargs": json.dumps({}),
            "description": "Periodically re-seeds the self-chaining player enrichment crawler. No-op if a batch is already running.",
        },
    )

    # -- Clan Crawl + Incremental Refresh Families --
    # Gated by ENABLE_CRAWLER_SCHEDULES. These four families were retired on
    # 2026-04-04 for the DO Functions migration and restored on 2026-04-11
    # after the revert — see the historical arc comment on _RETIRED_SCHEDULE_NAMES
    # above and `agents/runbooks/runbook-periodic-task-topology-2026-04-11.md`.
    crawler_schedules_enabled = _env_flag("ENABLE_CRAWLER_SCHEDULES", False)

    # -- Daily Clan Crawl (per realm, staggered via REALM_CRAWL_CRON_HOURS) --
    clan_crawl_base_hour = int(os.getenv("CLAN_CRAWL_SCHEDULE_HOUR", "3"))
    clan_crawl_minute = os.getenv("CLAN_CRAWL_SCHEDULE_MINUTE", "0")
    for realm in sorted(VALID_REALMS):
        realm_hour = (clan_crawl_base_hour +
                      REALM_CRAWL_CRON_HOURS.get(realm, 0)) % 24
        clan_crawl_schedule, _ = CrontabSchedule.objects.get_or_create(
            minute=clan_crawl_minute,
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
                "crontab": clan_crawl_schedule,
                "enabled": crawler_schedules_enabled,
                "args": json.dumps([]),
                "kwargs": json.dumps({"resume": False, "realm": realm}),
                "description": f"Daily full crawl of clans and players from the Wargaming API ({realm.upper()}).",
            },
        )

    # -- Clan Crawl Watchdog (per realm) --
    clan_crawl_watchdog_minutes = int(
        os.getenv("CLAN_CRAWL_WATCHDOG_MINUTES", "5"))
    clan_crawl_watchdog_schedule, _ = IntervalSchedule.objects.get_or_create(
        every=clan_crawl_watchdog_minutes,
        period=IntervalSchedule.MINUTES,
    )
    for realm in sorted(VALID_REALMS):
        PeriodicTask.objects.update_or_create(
            name=f"clan-crawl-watchdog-{realm}",
            defaults={
                "task": "warships.tasks.ensure_crawl_all_clans_running_task",
                "interval": clan_crawl_watchdog_schedule,
                "enabled": crawler_schedules_enabled,
                "args": json.dumps([]),
                "kwargs": json.dumps({"realm": realm}),
                "description": f"Clears stale clan-crawl locks and re-dispatches if a crawl died mid-flight ({realm.upper()}).",
            },
        )

    # -- Incremental Player Refresh (per realm) --
    # Default 180 min: each cycle walks ~1200 players × 6 WG API calls + DB
    # writes and takes 35-78 min/realm. With -c 2 worker slots the safe minimum
    # interval is cycle_time × num_realms / num_slots ≈ 117 min.
    player_refresh_minutes = int(
        os.getenv("PLAYER_REFRESH_INTERVAL_MINUTES", "180"))
    player_refresh_schedule, _ = IntervalSchedule.objects.get_or_create(
        every=player_refresh_minutes,
        period=IntervalSchedule.MINUTES,
    )
    for realm in sorted(VALID_REALMS):
        PeriodicTask.objects.update_or_create(
            name=f"incremental-player-refresh-{realm}",
            defaults={
                "task": "warships.tasks.incremental_player_refresh_task",
                "interval": player_refresh_schedule,
                "enabled": crawler_schedules_enabled,
                "args": json.dumps([]),
                "kwargs": json.dumps({"realm": realm}),
                "description": f"Graduated hot/active/warm incremental player refresh ({realm.upper()}). Defers while a clan crawl holds its realm lock.",
            },
        )

    # -- Incremental Ranked Refresh (per realm) --
    ranked_refresh_minutes = int(
        os.getenv("RANKED_REFRESH_INTERVAL_MINUTES", "120"))
    ranked_refresh_schedule, _ = IntervalSchedule.objects.get_or_create(
        every=ranked_refresh_minutes,
        period=IntervalSchedule.MINUTES,
    )
    for realm in sorted(VALID_REALMS):
        PeriodicTask.objects.update_or_create(
            name=f"incremental-ranked-refresh-{realm}",
            defaults={
                "task": "warships.tasks.incremental_ranked_data_task",
                "interval": ranked_refresh_schedule,
                "enabled": crawler_schedules_enabled,
                "args": json.dumps([]),
                "kwargs": json.dumps({"realm": realm}),
                "description": f"Incremental ranked data refresh ({realm.upper()}). Defers while a clan crawl holds its realm lock.",
            },
        )

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

    # -- Incremental Battle Capture PoC dispatcher --
    # Runbook: agents/runbooks/runbook-incremental-battle-poc-2026-04-27.md
    # The dispatcher reads BATTLE_TRACKING_PLAYER_NAMES at runtime; if unset it
    # short-circuits with no work. Production droplet leaves it unset.
    poll_interval_seconds = int(
        os.getenv("BATTLE_TRACKING_POLL_SECONDS", "60"))
    poll_schedule, _ = IntervalSchedule.objects.get_or_create(
        every=poll_interval_seconds,
        period=IntervalSchedule.SECONDS,
    )
    PeriodicTask.objects.update_or_create(
        name="poll-tracked-player-battles",
        defaults={
            "task": "warships.tasks.dispatch_tracked_player_polls_task",
            "interval": poll_schedule,
            "enabled": True,
            "args": json.dumps([]),
            "kwargs": json.dumps({}),
            "description": "Incremental battle capture PoC dispatcher. No-op unless BATTLE_TRACKING_PLAYER_NAMES is set.",
        },
    )

    # -- Battle History Rollup nightly sweeper --
    # Runbook: agents/runbooks/runbook-battle-history-rollout-2026-04-28.md
    # Phase 3. Rebuilds PlayerDailyShipStats for the previous UTC day from
    # BattleEvent rows. No-op unless BATTLE_HISTORY_ROLLUP_ENABLED=1.
    rollup_hour = os.getenv("BATTLE_HISTORY_ROLLUP_HOUR", "4")
    rollup_minute = os.getenv("BATTLE_HISTORY_ROLLUP_MINUTE", "30")
    battle_history_rollup_schedule, _ = CrontabSchedule.objects.get_or_create(
        minute=str(rollup_minute),
        hour=str(rollup_hour),
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
        timezone="UTC",
    )
    PeriodicTask.objects.update_or_create(
        name="battle-history-daily-rollup",
        defaults={
            "task": "warships.tasks.roll_up_player_daily_ship_stats_task",
            "crontab": battle_history_rollup_schedule,
            "enabled": True,
            "args": json.dumps([]),
            "kwargs": json.dumps({}),
            "description": "Nightly rebuild of PlayerDailyShipStats from BattleEvent. No-op unless BATTLE_HISTORY_ROLLUP_ENABLED=1.",
        },
    )
