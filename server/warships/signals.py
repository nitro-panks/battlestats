from __future__ import annotations

import json
import os

from django.db.models.signals import post_migrate
from django.dispatch import receiver

from warships.models import VALID_REALMS

# Per-realm cron hour offsets for staggering heavy daily tasks.
REALM_CRAWL_CRON_HOURS = {'eu': 0, 'na': 6, 'asia': 12}

# Per-realm minute-offset *index* used to stripe interval-style schedules
# across a common cycle so at most one realm is mid-cycle at any moment
# on the background worker. The actual stride within a cycle is
# `cycle_minutes // len(REALM_INTERVAL_OFFSETS)` (e.g. 60min for a 180min
# cycle, 40min for 120min, 10min for 30min). See
# `_realm_crontab_for_cycle` below.
REALM_INTERVAL_OFFSETS = {'na': 0, 'eu': 1, 'asia': 2}


def _realm_crontab_for_cycle(
    realm: str, cycle_minutes: int, base_minute: int = 0
) -> tuple[str, str]:
    """Return ``(minute_str, hour_str)`` for a striped per-realm crontab.

    Each realm's task fires every ``cycle_minutes`` starting at
    ``base_minute + offset_index * stride`` minutes-of-day, where
    ``stride = cycle_minutes // num_realms``. The returned strings are
    already in crontab list form (e.g. ``"0,30"`` or ``"0,3,6,9,12,15,18,21"``)
    or the wildcard ``"*"`` when every minute / hour is covered.

    Works for cycles that divide 60 (10, 30) or are multiples of an hour
    aligned with 1440 (60, 120, 180, 360, 720). Other values are rounded
    down at the stride and may produce a slightly off-pattern stripe.

    Emits exactly ``1440 // cycle_minutes`` evenly-spaced fires per day,
    wrapping past midnight modulo 1440. This matters for the largest
    ``base_minute + offset`` start (e.g. the ASIA-offset observation floor
    at base_minute=75, cycle 180): a naive ``while t < 1440`` truncation
    would drop its final fire and leave a 2×-cycle hole overnight.
    """
    realm_count = max(len(REALM_INTERVAL_OFFSETS), 1)
    stride = max(cycle_minutes // realm_count, 1)
    offset_idx = REALM_INTERVAL_OFFSETS.get(realm, 0)
    start_minute_of_day = (base_minute + offset_idx * stride) % 1440

    num_fires = max(1440 // cycle_minutes, 1)
    fire_times = [
        (start_minute_of_day + i * cycle_minutes) % 1440
        for i in range(num_fires)
    ]

    minutes = sorted({t % 60 for t in fire_times})
    hours = sorted({t // 60 for t in fire_times})

    minute_str = '*' if len(minutes) == 60 else ','.join(str(m) for m in minutes)
    hour_str = '*' if len(hours) == 24 else ','.join(str(h) for h in hours)
    return minute_str, hour_str


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
    # Random landing-player + landing-clan queue refill schedules
    # retired 2026-05-07 alongside the Random pill removal. The
    # corresponding tasks (refill_landing_random_*_queue_task) were
    # also deleted; any lingering PeriodicTask rows must be purged so
    # celery-beat doesn't try to dispatch a non-existent task name.
    "landing-random-player-queue-refill",
    "landing-random-player-queue-refill-na",
    "landing-random-player-queue-refill-eu",
    "landing-random-player-queue-refill-asia",
    "landing-random-clan-queue-refill",
    "landing-random-clan-queue-refill-na",
    "landing-random-clan-queue-refill-eu",
    "landing-random-clan-queue-refill-asia",
    # Daily observation floor (2026-05-02) was promoted to a 6-hourly
    # rolling floor on 2026-05-09 and renamed to drop the `daily-` prefix.
    # The 4 daily-* rows must be deleted so beat doesn't keep firing the
    # old daily schedule alongside the new 6-hourly one.
    "daily-observation-floor-na",
    "daily-observation-floor-eu",
    "daily-observation-floor-asia",
    # Landing "Recent" players + clans surfaces retired 2026-06-10. The
    # backing tasks (warm_landing_recent_players_task /
    # warm_landing_recent_clans_task) were deleted, so any lingering
    # PeriodicTask rows must be purged or celery-beat dispatches a
    # non-existent task name.
    "recent-players-warmer-na",
    "recent-players-warmer-eu",
    "recent-players-warmer-asia",
    "recent-clans-warmer-na",
    "recent-clans-warmer-eu",
    "recent-clans-warmer-asia",
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
    for realm in sorted(VALID_REALMS):
        minute_str, hour_str = _realm_crontab_for_cycle(
            realm, landing_warm_minutes, base_minute=55)
        landing_warm_schedule, _ = CrontabSchedule.objects.get_or_create(
            minute=minute_str,
            hour=hour_str,
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone="UTC",
        )
        PeriodicTask.objects.update_or_create(
            name=f"landing-page-warmer-{realm}",
            defaults={
                "task": "warships.tasks.warm_landing_page_content_task",
                "crontab": landing_warm_schedule,
                "interval": None,
                "enabled": True,
                "args": json.dumps([]),
                "kwargs": json.dumps({"realm": realm}),
                "description": f"Refreshes landing page caches ({realm.upper()}).",
            },
        )

    PeriodicTask.objects.filter(name="landing-page-warmer").delete()

    # -- Realm top-ships treemap warmer --
    # The treemap is a static per-season count over the most recently completed
    # fixed 2-week ship season (cached under a season-tagged key). A daily warm
    # is still cheap and keeps the cache fresh across a season boundary, when the
    # completed-season key advances. Fires at hour 0, striped per realm by a few
    # minutes (NA :05, EU :10, ASIA :15 by default) so the three realms don't
    # recompute concurrently on the background worker.
    top_ships_warm_minute = int(os.getenv("TOP_SHIPS_WARM_MINUTE", "5"))
    for realm in sorted(VALID_REALMS):
        realm_minute = (top_ships_warm_minute +
                        REALM_INTERVAL_OFFSETS.get(realm, 0) * 5) % 60
        top_ships_warm_schedule, _ = CrontabSchedule.objects.get_or_create(
            minute=str(realm_minute),
            hour="0",
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone="UTC",
        )
        PeriodicTask.objects.update_or_create(
            name=f"top-ships-warmer-{realm}",
            defaults={
                "task": "warships.tasks.warm_realm_top_ships_task",
                "crontab": top_ships_warm_schedule,
                "interval": None,
                "enabled": True,
                "args": json.dumps([]),
                "kwargs": json.dumps({"realm": realm}),
                "description": f"Daily warm of top-ships treemap caches (random+ranked) + tier/type ship-list buckets, last completed ship season ({realm.upper()}).",
            },
        )

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

    # -- Rolling nightly T10 Top-Ship-Player snapshot (per realm, striped) --
    # Beat ticks nightly; each run recomputes the trailing-window board so the
    # profile badges + /ship standings evolve daily (gated by
    # SHIP_BADGE_SNAPSHOT_ENABLED). Per-realm hour striping keeps the three ~12s
    # aggregations off each other. See
    # agents/runbooks/runbook-ship-badges-rolling-2026-06-14.md.
    ship_badge_hour = int(os.getenv("SHIP_BADGE_SNAPSHOT_HOUR", "2"))
    for realm in sorted(VALID_REALMS):
        realm_hour = (ship_badge_hour +
                      REALM_CRAWL_CRON_HOURS.get(realm, 0)) % 24
        ship_badge_schedule, _ = CrontabSchedule.objects.get_or_create(
            minute="30",
            hour=str(realm_hour),
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone="UTC",
        )
        PeriodicTask.objects.update_or_create(
            name=f"ship-top-player-snapshot-{realm}",
            defaults={
                "task": "warships.tasks.snapshot_ship_top_players_task",
                "crontab": ship_badge_schedule,
                "interval": None,
                "enabled": True,
                "args": json.dumps([]),
                "kwargs": json.dumps({"realm": realm}),
                "description": f"T10 top-player rolling snapshot ({realm.upper()}) — nightly recompute of the trailing {os.getenv('SHIP_LEADERBOARD_WINDOW_DAYS', '14')}-day board.",
            },
        )

    # -- Player Distribution Warmer (split from landing warmer) --
    dist_warm_minutes = int(os.getenv("DISTRIBUTION_WARM_MINUTES", "360"))
    for realm in sorted(VALID_REALMS):
        minute_str, hour_str = _realm_crontab_for_cycle(
            realm, dist_warm_minutes, base_minute=50)
        dist_warm_schedule, _ = CrontabSchedule.objects.get_or_create(
            minute=minute_str,
            hour=hour_str,
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone="UTC",
        )
        PeriodicTask.objects.update_or_create(
            name=f"player-distribution-warmer-{realm}",
            defaults={
                "task": "warships.tasks.warm_player_distributions_task",
                "crontab": dist_warm_schedule,
                "interval": None,
                "enabled": True,
                "args": json.dumps([]),
                "kwargs": json.dumps({"realm": realm}),
                "description": f"Refreshes player distribution caches — MV refresh + WR/battles/survival bins ({realm.upper()}).",
            },
        )

    # -- Player Correlation Warmer (split from landing warmer) --
    corr_warm_minutes = int(os.getenv("CORRELATION_WARM_MINUTES", "360"))
    for realm in sorted(VALID_REALMS):
        minute_str, hour_str = _realm_crontab_for_cycle(
            realm, corr_warm_minutes, base_minute=45)
        corr_warm_schedule, _ = CrontabSchedule.objects.get_or_create(
            minute=minute_str,
            hour=hour_str,
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone="UTC",
        )
        PeriodicTask.objects.update_or_create(
            name=f"player-correlation-warmer-{realm}",
            defaults={
                "task": "warships.tasks.warm_player_correlations_task",
                "crontab": corr_warm_schedule,
                "interval": None,
                "enabled": True,
                "args": json.dumps([]),
                "kwargs": json.dumps({"realm": realm}),
                "description": f"Refreshes player population correlations — tier-type, WR-survival, ranked WR-battles ({realm.upper()}).",
            },
        )

    hot_entity_warm_minutes = int(
        os.getenv("HOT_ENTITY_CACHE_WARM_MINUTES", "30"))
    for realm in sorted(VALID_REALMS):
        minute_str, hour_str = _realm_crontab_for_cycle(
            realm, hot_entity_warm_minutes, base_minute=7)
        hot_entity_warm_schedule, _ = CrontabSchedule.objects.get_or_create(
            minute=minute_str,
            hour=hour_str,
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone="UTC",
        )
        PeriodicTask.objects.update_or_create(
            name=f"hot-entity-cache-warmer-{realm}",
            defaults={
                "task": "warships.tasks.warm_hot_entity_caches_task",
                "crontab": hot_entity_warm_schedule,
                "interval": None,
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
    for realm in sorted(VALID_REALMS):
        minute_str, hour_str = _realm_crontab_for_cycle(
            realm, recently_viewed_warm_minutes, base_minute=2)
        recently_viewed_warm_schedule, _ = CrontabSchedule.objects.get_or_create(
            minute=minute_str,
            hour=hour_str,
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone="UTC",
        )
        PeriodicTask.objects.update_or_create(
            name=f"recently-viewed-player-warmer-{realm}",
            defaults={
                "task": "warships.tasks.warm_recently_viewed_players_task",
                "crontab": recently_viewed_warm_schedule,
                "interval": None,
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

    # -- Enrichment Pool Maintenance (daily, DB-only, crawl-safe) --
    # Re-surfaces `empty` false-negatives (private-at-fetch accounts now public)
    # into `pending` with a per-row cooldown convergence guard, so they don't stay
    # parked invisibly to the crawler. Index-backed (enrichment_status) + no WG
    # calls, so it's cheap and coexists with multi-day crawls (no deferral). Kill
    # switch: ENRICHMENT_POOL_MAINTENANCE_ENABLED (default on). The heavier
    # full-catalog reclassify (skipped_* drift) is deliberately NOT scheduled —
    # prod sizing showed ~36 min/run on the 1-vCPU PG; it stays a supervised manual
    # op pending an incremental redesign. See runbook-enrichment-pool-maintenance.
    pool_maint_enabled = _env_flag("ENRICHMENT_POOL_MAINTENANCE_ENABLED", True)
    pool_maint_schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="17",
        hour="8",
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
        timezone="UTC",
    )
    PeriodicTask.objects.update_or_create(
        name="enrichment-pool-maintenance",
        defaults={
            "task": "warships.tasks.enrichment_pool_maintenance_task",
            "crontab": pool_maint_schedule,
            "interval": None,
            "enabled": pool_maint_enabled,
            "args": json.dumps([]),
            "kwargs": json.dumps({}),
            "description": "Daily DB-only, index-backed pass that re-queues empty enrichment false-negatives (with a per-row cooldown) so the pending pool stays complete. Coexists with crawls. Drift reclassify is a separate striped per-realm task.",
        },
    )

    # -- Incremental drift reclassify (per realm, striped, daily) --
    # The skipped_* drift rescue (un-hidden / 500-battle crossers / WR recoveries),
    # scoped to recently-fetched rows via player_last_fetch_idx. ~2.5-6 min/realm —
    # striped 20 min apart so the 1-vCPU PG sees one realm's scan at a time, not a
    # multi-realm burst. Gated by the same ENRICHMENT_POOL_MAINTENANCE_ENABLED flag.
    reclass_drift_times = {"na": ("20", "8"), "eu": ("40", "8"), "asia": ("0", "9")}
    for realm in sorted(VALID_REALMS):
        minute_str, hour_str = reclass_drift_times.get(realm, ("20", "8"))
        reclass_drift_schedule, _ = CrontabSchedule.objects.get_or_create(
            minute=minute_str,
            hour=hour_str,
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone="UTC",
        )
        PeriodicTask.objects.update_or_create(
            name=f"enrichment-reclassify-drift-{realm}",
            defaults={
                "task": "warships.tasks.enrichment_reclassify_drift_task",
                "crontab": reclass_drift_schedule,
                "interval": None,
                "enabled": pool_maint_enabled,
                "args": json.dumps([]),
                "kwargs": json.dumps({"realm": realm}),
                "description": f"Daily incremental enrichment_status drift rescue ({realm.upper()}) over last_fetch<=25h — index-backed, DB-only, coexists with crawls. Striped per realm.",
            },
        )

    # -- Clan Crawl + Incremental Refresh Families --
    # Gated by ENABLE_CRAWLER_SCHEDULES. These four families were retired on
    # 2026-04-04 for the DO Functions migration and restored on 2026-04-11
    # after the revert — see the historical arc comment on _RETIRED_SCHEDULE_NAMES
    # above and `agents/runbooks/runbook-periodic-task-topology-2026-04-11.md`.
    crawler_schedules_enabled = _env_flag("ENABLE_CRAWLER_SCHEDULES", False)

    # -- Daily Clan Crawl (per realm, staggered via REALM_CRAWL_CRON_HOURS) --
    # Each realm's crawl should sit in that realm's measured activity *trough*
    # so it doesn't throttle the battle-history floor into coexist mode while
    # fresh battles are landing. The default REALM_CRAWL_CRON_HOURS offset put
    # ASIA at 15:00 UTC — squarely inside ASIA's 12:00-15:00 peak — so the
    # ASIA crawl hour is overridden to its 20:00-04:00 quiet window (default
    # 22:00) without disturbing the snapshot/tier-dist families that also read
    # REALM_CRAWL_CRON_HOURS. See F5 in
    # agents/runbooks/analysis-feed-schedule-optimization-2026-06-08.md.
    clan_crawl_base_hour = int(os.getenv("CLAN_CRAWL_SCHEDULE_HOUR", "3"))
    clan_crawl_minute = os.getenv("CLAN_CRAWL_SCHEDULE_MINUTE", "0")
    clan_crawl_hour_override = {
        "asia": int(os.getenv("CLAN_CRAWL_SCHEDULE_HOUR_ASIA", "22")),
    }
    for realm in sorted(VALID_REALMS):
        realm_hour = clan_crawl_hour_override.get(
            realm,
            (clan_crawl_base_hour + REALM_CRAWL_CRON_HOURS.get(realm, 0)) % 24,
        )
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
                # Beat fires the lightweight dispatcher (on `default`), which
                # enqueues the heavy crawl_all_clans_task only if one isn't
                # already running/queued for the realm — so the daily schedule
                # can't pile up duplicate crawl messages behind the single-slot
                # crawls worker. The dispatcher always uses resume=True so a
                # deploy/SIGTERM mid-pass (acks_late redelivery) or the watchdog
                # re-dispatch continues the interrupted pass via the run-scoped
                # marker instead of restarting from clan 0; the marker clears on
                # pass completion so each new pass still re-crawls every clan.
                # See runbook-crawls-queue-depth-alarm-2026-06-12.md.
                "task": "warships.tasks.dispatch_clan_crawl_task",
                "crontab": clan_crawl_schedule,
                "enabled": crawler_schedules_enabled,
                "args": json.dumps([]),
                "kwargs": json.dumps({"realm": realm}),
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
    # Default 180 min cycle, striped per realm via REALM_INTERVAL_OFFSETS so
    # NA fires at hours 0,3,6,…, EU at hours 1,4,7,…, ASIA at hours 2,5,8,….
    # The base_minute=5 lane (and the distinct lanes on every other striped
    # family) keeps these off the minute-0 boundary so they don't stack onto
    # the 1-vCPU DB at the top of the hour — see the minute-lane de-pile in
    # agents/runbooks/analysis-feed-schedule-optimization-2026-06-08.md (F1).
    # Each cycle walks ~1200 players × 6 WG API calls + DB writes and takes
    # 35-78 min/realm. Striping keeps at most one realm mid-cycle so the
    # background worker stays utilised but not stacked.
    player_refresh_minutes = int(
        os.getenv("PLAYER_REFRESH_INTERVAL_MINUTES", "180"))
    for realm in sorted(VALID_REALMS):
        minute_str, hour_str = _realm_crontab_for_cycle(
            realm, player_refresh_minutes, base_minute=5)
        player_refresh_schedule, _ = CrontabSchedule.objects.get_or_create(
            minute=minute_str,
            hour=hour_str,
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone="UTC",
        )
        PeriodicTask.objects.update_or_create(
            name=f"incremental-player-refresh-{realm}",
            defaults={
                "task": "warships.tasks.incremental_player_refresh_task",
                "crontab": player_refresh_schedule,
                "interval": None,
                "enabled": crawler_schedules_enabled,
                "args": json.dumps([]),
                "kwargs": json.dumps({"realm": realm}),
                "description": f"Graduated hot/active/warm incremental player refresh ({realm.upper()}). Defers while a clan crawl holds its realm lock.",
            },
        )

    # -- Daily Active-Player Snapshots (per realm) --
    # The value-prop engine: writes a daily Snapshot row (cumulative battles/
    # wins + day-over-day interval) for every active player so progress tracking
    # has no gaps. Light (bulk account/info, ~1 WG call per 100 players) and it
    # COEXISTS with clan crawls (unlike incremental refresh, which defers), so
    # coverage is guaranteed each UTC day. Idempotent per day → frequent runs
    # converge on full coverage. Always enabled (independent of
    # ENABLE_CRAWLER_SCHEDULES); kill via SNAPSHOT_ACTIVE_PLAYERS_ENABLED=0. The
    # base_minute=15 lane keeps it off the incremental (:05) and minute-0
    # boundaries on the 1-vCPU DB.
    snapshot_active_minutes = int(
        os.getenv("SNAPSHOT_ACTIVE_INTERVAL_MINUTES", "30"))
    for realm in sorted(VALID_REALMS):
        minute_str, hour_str = _realm_crontab_for_cycle(
            realm, snapshot_active_minutes, base_minute=15)
        snapshot_active_schedule, _ = CrontabSchedule.objects.get_or_create(
            minute=minute_str,
            hour=hour_str,
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone="UTC",
        )
        PeriodicTask.objects.update_or_create(
            name=f"snapshot-active-players-{realm}",
            defaults={
                "task": "warships.tasks.snapshot_active_players_task",
                "crontab": snapshot_active_schedule,
                "interval": None,
                "enabled": True,
                "args": json.dumps([]),
                "kwargs": json.dumps({"realm": realm}),
                "description": f"Daily active-player snapshot engine ({realm.upper()}). Coexists with clan crawls; kill via SNAPSHOT_ACTIVE_PLAYERS_ENABLED=0.",
            },
        )

    # -- Hot-Players Engagement Capture Queue (per realm) --
    # The engagement-capture loop: durable visitor interest (recurrence of
    # deduped detail-page views across distinct days) qualifies a player for
    # guaranteed daily battle-history capture, independent of their own activity
    # or skill. Two tasks (see runbook-hot-players-engagement-queue-2026-06-10):
    #
    #  * maintain (DB-only "brain") — promote/evict/re-score the HotPlayer set
    #    from EntityVisitDaily. Coexists with crawls; ALWAYS enabled like the
    #    snapshot/enrichment-maintenance families (still respects
    #    HOT_PLAYERS_ENABLED). Striped in the 08:00-09:00 UTC maintenance band
    #    (after the visit-daily rollup settles) so the analytical load clusters
    #    with enrichment pool maintenance. na :30, eu :50 of 08, asia :10 of 09.
    #  * capture (background "hands") — sweep the hot set, skip-if-fresh, write a
    #    BattleObservation + a gap-free daily Snapshot. It is a crawler-class WG
    #    consumer, so it gates on ENABLE_CRAWLER_SCHEDULES like the floor. Striped
    #    via REALM_INTERVAL_OFFSETS on a daily cycle.
    hot_maintain_enabled = _env_flag("HOT_PLAYERS_ENABLED", True)
    hot_maintain_times = {"na": ("30", "8"), "eu": ("50", "8"), "asia": ("10", "9")}
    for realm in sorted(VALID_REALMS):
        minute_str, hour_str = hot_maintain_times.get(realm, ("30", "8"))
        hot_maintain_schedule, _ = CrontabSchedule.objects.get_or_create(
            minute=minute_str,
            hour=hour_str,
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone="UTC",
        )
        PeriodicTask.objects.update_or_create(
            name=f"hot-players-maintain-{realm}",
            defaults={
                "task": "warships.tasks.maintain_hot_players_task",
                "crontab": hot_maintain_schedule,
                "interval": None,
                "enabled": hot_maintain_enabled,
                "args": json.dumps([]),
                "kwargs": json.dumps({"realm": realm}),
                "description": f"Daily DB-only promote/evict/re-score of the engagement-capture queue ({realm.upper()}) from EntityVisitDaily. Coexists with crawls; kill via HOT_PLAYERS_ENABLED=0.",
            },
        )

    # capture: daily cron, striped per realm via REALM_INTERVAL_OFFSETS so realms
    # don't overlap. Cycle = 1440 (once/day); base_minute=10:35 puts the NA fire
    # on minute lane :35 — a FREE lane (the de-pile invariant in
    # test_periodic_schedule_topology.py enforces this: :45 collided with
    # player-correlation-warmer, :05/:15/:25/:50/:55 are the other taken lanes on
    # the 1-vCPU DB).
    hot_capture_cycle_minutes = int(
        os.getenv("HOT_PLAYERS_CAPTURE_CYCLE_MINUTES", "1440"))
    for realm in sorted(VALID_REALMS):
        minute_str, hour_str = _realm_crontab_for_cycle(
            realm, hot_capture_cycle_minutes, base_minute=10 * 60 + 35)
        hot_capture_schedule, _ = CrontabSchedule.objects.get_or_create(
            minute=minute_str,
            hour=hour_str,
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone="UTC",
        )
        PeriodicTask.objects.update_or_create(
            name=f"hot-players-capture-{realm}",
            defaults={
                "task": "warships.tasks.capture_hot_player_observations_task",
                "crontab": hot_capture_schedule,
                "interval": None,
                "enabled": crawler_schedules_enabled,
                "args": json.dumps([]),
                "kwargs": json.dumps({"realm": realm}),
                "description": f"Daily sweep of the engagement-capture queue ({realm.upper()}): skip-if-fresh BattleObservation + gap-free daily Snapshot. Coexists with crawls; kill via HOT_PLAYERS_ENABLED=0.",
            },
        )

    # freshness (Tier 3 of runbook-player-refresh-latency-2026-06-10): a SEPARATE
    # frequent (<15-min) sweep that advances Player.battles_updated_at for hot
    # members so a visit lands at x-player-refresh-pending:false and resolves
    # sub-second (no live WG refresh on the request thread). Cadence MUST be under
    # the 15-min PLAYER_BATTLE_DATA_STALE_AFTER window — default 12 min. The 12-min
    # cycle stripes cleanly via _realm_crontab_for_cycle (stride=4): NA :00,12,24,
    # 36,48 / EU :04,16,28,40,52 / ASIA :08,20,32,44,56 (hour wildcard) — distinct
    # minute lanes, no collision. It is a crawler-class WG consumer, so it gates on
    # ENABLE_CRAWLER_SCHEDULES like the floor / capture. Sub-hourly minute-list
    # family, so (like recently-viewed-player-warmer) it is NOT in the NA-lane
    # de-pile list — it does not anchor a single hour-multiple minute.
    hot_fresh_cycle_minutes = int(
        os.getenv("HOT_PLAYERS_FRESH_CYCLE_MINUTES", "12"))
    for realm in sorted(VALID_REALMS):
        minute_str, hour_str = _realm_crontab_for_cycle(
            realm, hot_fresh_cycle_minutes)
        hot_fresh_schedule, _ = CrontabSchedule.objects.get_or_create(
            minute=minute_str,
            hour=hour_str,
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone="UTC",
        )
        PeriodicTask.objects.update_or_create(
            name=f"hot-players-freshness-{realm}",
            defaults={
                "task": "warships.tasks.refresh_hot_player_freshness_task",
                "crontab": hot_fresh_schedule,
                "interval": None,
                "enabled": crawler_schedules_enabled,
                "args": json.dumps([]),
                "kwargs": json.dumps({"realm": realm}),
                "description": f"Frequent (<15min) freshness sweep of the hot set ({realm.upper()}): advance battles_updated_at so visits resolve sub-second. Coexists with crawls; kill via HOT_PLAYERS_ENABLED=0.",
            },
        )

    # -- Incremental Ranked Refresh (per realm) --
    # Default 120 min cycle, striped per realm. With 3 realms the stride is
    # 40 min, then shifted by the base_minute=25 lane: NA at :25 of even
    # hours, EU at :05 of odd hours, ASIA at :45 of odd hours. The lane keeps
    # ranked off the minute-0 DB-CPU stack (F1 minute-lane de-pile).
    ranked_refresh_minutes = int(
        os.getenv("RANKED_REFRESH_INTERVAL_MINUTES", "120"))
    for realm in sorted(VALID_REALMS):
        minute_str, hour_str = _realm_crontab_for_cycle(
            realm, ranked_refresh_minutes, base_minute=25)
        ranked_refresh_schedule, _ = CrontabSchedule.objects.get_or_create(
            minute=minute_str,
            hour=hour_str,
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone="UTC",
        )
        PeriodicTask.objects.update_or_create(
            name=f"incremental-ranked-refresh-{realm}",
            defaults={
                "task": "warships.tasks.incremental_ranked_data_task",
                "crontab": ranked_refresh_schedule,
                "interval": None,
                "enabled": crawler_schedules_enabled,
                "args": json.dumps([]),
                "kwargs": json.dumps({"realm": realm}),
                "description": f"Incremental ranked data refresh ({realm.upper()}). Defers while a clan crawl holds its realm lock.",
            },
        )

    # -- Rolling BattleObservation Floor (per realm, configurable cadence) --
    # Promoted from a daily cron to a 6-hourly cron on 2026-05-09, then made
    # cadence-configurable via BATTLE_OBSERVATION_FLOOR_CYCLE_MINUTES (prod
    # runs 180 = 3h / 8 cycles per day as of R3, 2026-06-08; code default 360
    # = 6h / 4 cycles). Each realm fires 1440 // cycle_minutes times per day,
    # striped via REALM_INTERVAL_OFFSETS (cycle/3 stride) and wrapped modulo
    # 1440 so the largest-offset realm (ASIA) gets its full cycle count with
    # no overnight hole — see the F2 wrap fix in
    # agents/runbooks/analysis-feed-schedule-optimization-2026-06-08.md. The
    # floor walks active-7d players whose latest BattleObservation is older
    # than BATTLE_OBSERVATION_FLOOR_HOURS (prod 8h). Most cycles return few
    # candidates because the tiered crawler covers hot players within 12h.
    # Runbook: agents/runbooks/runbook-battle-observation-floor-2026-05-02.md
    obs_floor_minute_str = os.getenv("BATTLE_OBSERVATION_FLOOR_MINUTE", "15")
    obs_floor_base_hour = int(os.getenv("BATTLE_OBSERVATION_FLOOR_HOUR", "1"))
    obs_floor_base_minute = (
        obs_floor_base_hour * 60 + int(obs_floor_minute_str))
    # Cycle length (minutes) is configurable so the floor frequency can be
    # raised to use idle worker capacity once the clan crawl stops monopolising
    # the box (R2 core-only crawl). Default 360 (6h). Set to 180 (3h, 2x) / 120
    # (2h, 3x) etc. The realms stay striped `cycle_minutes // 3` apart so they
    # don't pile onto the 1-vCPU DB at once. Use a divisor-friendly value
    # (60/120/180/360/720) per _realm_crontab_for_cycle's contract.
    obs_floor_cycle_minutes = int(
        os.getenv("BATTLE_OBSERVATION_FLOOR_CYCLE_MINUTES", "360"))
    for realm in sorted(VALID_REALMS):
        minute_str, hour_str = _realm_crontab_for_cycle(
            realm, obs_floor_cycle_minutes, base_minute=obs_floor_base_minute)
        obs_floor_schedule, _ = CrontabSchedule.objects.get_or_create(
            minute=minute_str,
            hour=hour_str,
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone="UTC",
        )
        PeriodicTask.objects.update_or_create(
            name=f"observation-floor-{realm}",
            defaults={
                "task": "warships.tasks.ensure_daily_battle_observations_task",
                "crontab": obs_floor_schedule,
                "interval": None,
                "enabled": crawler_schedules_enabled,
                "args": json.dumps([]),
                "kwargs": json.dumps({"realm": realm}),
                "description": (
                    f"Rolling 6-hourly floor for BattleObservation coverage "
                    f"({realm.upper()}). Walks active-7d players whose "
                    f"latest observation exceeds BATTLE_OBSERVATION_FLOOR_HOURS. "
                    f"Defers while a clan crawl holds its realm lock."
                ),
            },
        )

    # -- Daily BattleObservation payload compaction (disk retention) --
    # NULLs stale ships_stats_json / ranked_ships_stats_json blobs so the
    # append-only observation capture stops filling the cluster disk. Does
    # NOT delete rows (BattleEvent FKs cascade). Disabled by default —
    # BATTLE_OBSERVATION_COMPACT_ENABLED=1 flips it on after an operator has
    # dry-run + run it manually once. Scheduled at the histogram's quietest
    # UTC hour (12:30) to stay clear of the 03:00 / 23:00 CPU peaks.
    # Runbook: agents/runbooks/runbook-db-cpu-saturation-2026-05-24.md
    compact_enabled = os.getenv(
        "BATTLE_OBSERVATION_COMPACT_ENABLED", "0") == "1"
    compact_hour = os.getenv("BATTLE_OBSERVATION_COMPACT_HOUR", "12")
    compact_minute = os.getenv("BATTLE_OBSERVATION_COMPACT_MINUTE", "30")
    compact_schedule, _ = CrontabSchedule.objects.get_or_create(
        minute=compact_minute,
        hour=compact_hour,
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
        timezone="UTC",
    )
    PeriodicTask.objects.update_or_create(
        name="prune-battle-observations",
        defaults={
            "task": "warships.tasks.prune_battle_observations_task",
            "crontab": compact_schedule,
            "interval": None,
            "enabled": compact_enabled,
            "args": json.dumps([]),
            "kwargs": json.dumps({}),
            "description": (
                "Daily compaction of stale BattleObservation JSON payloads "
                "to reclaim disk. Keeps the latest N observations + latest "
                "non-NULL-ranked per player; clears older payloads without "
                "deleting rows. Gated by BATTLE_OBSERVATION_COMPACT_ENABLED."
            ),
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
    # short-circuits with no work. Production leaves it unset — so only enable
    # the every-60s beat entry when names are configured. Otherwise the no-op
    # task was being dispatched 1440x/day and, while the worker was saturated,
    # piled up in the background queue (it was ~48% of a 1.6K-message backlog
    # on 2026-05-24). See runbook-db-cpu-saturation-2026-05-24.md.
    poll_tracking_enabled = bool(
        os.getenv("BATTLE_TRACKING_PLAYER_NAMES", "").strip())
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
            "enabled": poll_tracking_enabled,
            "args": json.dumps([]),
            "kwargs": json.dumps({}),
            "description": "Incremental battle capture PoC dispatcher. No-op unless BATTLE_TRACKING_PLAYER_NAMES is set; beat entry disabled when unset.",
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
            "description": "Nightly rebuild of PlayerDailyShipStats from BattleEvent (self-healing trailing window). No-op unless BATTLE_HISTORY_ROLLUP_ENABLED=1.",
        },
    )

    # -- Player activity-curve aggregate (peak-aware scheduling input) --
    # Runbook: agents/runbooks/analysis-feed-schedule-optimization-2026-06-08.md (F3)
    # Nightly rebuild of the per-realm hour-of-day histogram from
    # BattleObservation.last_battle_time. Cheap DB-only aggregate, scheduled in
    # the 04:00 UTC quiet window. No-op unless ACTIVITY_CURVE_ENABLED=1.
    activity_curve_hour = os.getenv("ACTIVITY_CURVE_HOUR", "4")
    activity_curve_minute = os.getenv("ACTIVITY_CURVE_MINUTE", "0")
    activity_curve_schedule, _ = CrontabSchedule.objects.get_or_create(
        minute=str(activity_curve_minute),
        hour=str(activity_curve_hour),
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
        timezone="UTC",
    )
    PeriodicTask.objects.update_or_create(
        name="player-activity-curve-aggregate",
        defaults={
            "task": "warships.tasks.aggregate_player_activity_curve_task",
            "crontab": activity_curve_schedule,
            "interval": None,
            "enabled": True,
            "args": json.dumps([]),
            "kwargs": json.dumps({}),
            "description": "Nightly per-realm hour-of-day activity histogram (PlayerActivityHourly) from BattleObservation. No-op unless ACTIVITY_CURVE_ENABLED=1.",
        },
    )

    # -- Battle History Rollup reconciliation (alert-only) --
    # Runbook: agents/runbooks/runbook-battle-history-rollup-durability-2026-06-06.md
    # Gate-independent of BATTLE_HISTORY_ROLLUP_ENABLED so it can surface holes
    # even when the rollup gate is off. No-op unless
    # BATTLE_HISTORY_RECONCILE_ENABLED=1. Fires after the rollup window (04:30)
    # completes.
    reconcile_hour = os.getenv("BATTLE_HISTORY_RECONCILE_HOUR", "5")
    reconcile_minute = os.getenv("BATTLE_HISTORY_RECONCILE_MINUTE", "0")
    battle_history_reconcile_schedule, _ = CrontabSchedule.objects.get_or_create(
        minute=str(reconcile_minute),
        hour=str(reconcile_hour),
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
        timezone="UTC",
    )
    PeriodicTask.objects.update_or_create(
        name="battle-history-rollup-reconcile",
        defaults={
            "task": "warships.tasks.reconcile_battle_history_rollup_task",
            "crontab": battle_history_reconcile_schedule,
            "enabled": True,
            "args": json.dumps([]),
            "kwargs": json.dumps({}),
            "description": "Alert-only reconciliation of PlayerDailyShipStats vs BattleEvent. No-op unless BATTLE_HISTORY_RECONCILE_ENABLED=1.",
        },
    )
