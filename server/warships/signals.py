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
    """
    realm_count = max(len(REALM_INTERVAL_OFFSETS), 1)
    stride = max(cycle_minutes // realm_count, 1)
    offset_idx = REALM_INTERVAL_OFFSETS.get(realm, 0)
    start_minute_of_day = (base_minute + offset_idx * stride) % 1440

    fire_times = []
    t = start_minute_of_day
    while t < 1440:
        fire_times.append(t)
        t += cycle_minutes

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
            realm, landing_warm_minutes)
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
                "kwargs": json.dumps({"include_recent": True, "realm": realm}),
                "description": f"Refreshes landing page caches ({realm.upper()}).",
            },
        )

    PeriodicTask.objects.filter(name="landing-page-warmer").delete()

    # -- Realm top-ships treemap warmer --
    # Pre-populates the landing treemap caches (random + ranked) per realm so a
    # visit never eats the ~1s BattleEvent aggregation. Hourly, striped per realm.
    top_ships_warm_minutes = int(os.getenv("TOP_SHIPS_WARM_MINUTES", "60"))
    for realm in sorted(VALID_REALMS):
        minute_str, hour_str = _realm_crontab_for_cycle(
            realm, top_ships_warm_minutes)
        top_ships_warm_schedule, _ = CrontabSchedule.objects.get_or_create(
            minute=minute_str,
            hour=hour_str,
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
                "description": f"Pre-populates top-ships treemap caches, random+ranked ({realm.upper()}).",
            },
        )

    # -- Recent-players warmer (7-day random-battle leaders) --
    # Pure-cache read path on the landing endpoint, rebuilt every 3h
    # out-of-band so a rebuild never adds latency to a request.
    recent_players_warm_minutes = int(
        os.getenv("LANDING_RECENT_PLAYERS_WARM_MINUTES", "180"))
    for realm in sorted(VALID_REALMS):
        minute_str, hour_str = _realm_crontab_for_cycle(
            realm, recent_players_warm_minutes)
        recent_players_warm_schedule, _ = CrontabSchedule.objects.get_or_create(
            minute=minute_str,
            hour=hour_str,
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone="UTC",
        )
        PeriodicTask.objects.update_or_create(
            name=f"recent-players-warmer-{realm}",
            defaults={
                "task": "warships.tasks.warm_landing_recent_players_task",
                "crontab": recent_players_warm_schedule,
                "interval": None,
                "enabled": True,
                "args": json.dumps([]),
                "kwargs": json.dumps({"realm": realm}),
                "description": f"Rebuilds the landing recent-players 7-day rollup ({realm.upper()}).",
            },
        )

    # -- Recent-clans warmer --
    # recent-clans is lazily rebuilt on request (multi-second Clan aggregation)
    # with a 6h TTL + dirty-invalidation on clan updates, so without a warmer the
    # cold rebuild periodically lands on a user. Rebuild it out-of-band, striped
    # per realm. Default hourly (well inside the 6h TTL, covers dirty churn).
    recent_clans_warm_minutes = int(
        os.getenv("LANDING_RECENT_CLANS_WARM_MINUTES", "60"))
    for realm in sorted(VALID_REALMS):
        minute_str, hour_str = _realm_crontab_for_cycle(
            realm, recent_clans_warm_minutes)
        recent_clans_warm_schedule, _ = CrontabSchedule.objects.get_or_create(
            minute=minute_str,
            hour=hour_str,
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone="UTC",
        )
        PeriodicTask.objects.update_or_create(
            name=f"recent-clans-warmer-{realm}",
            defaults={
                "task": "warships.tasks.warm_landing_recent_clans_task",
                "crontab": recent_clans_warm_schedule,
                "interval": None,
                "enabled": True,
                "args": json.dumps([]),
                "kwargs": json.dumps({"realm": realm}),
                "description": f"Rebuilds the landing recent-clans payload out-of-band ({realm.upper()}).",
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

    # -- Player Distribution Warmer (split from landing warmer) --
    dist_warm_minutes = int(os.getenv("DISTRIBUTION_WARM_MINUTES", "360"))
    for realm in sorted(VALID_REALMS):
        minute_str, hour_str = _realm_crontab_for_cycle(
            realm, dist_warm_minutes)
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
            realm, corr_warm_minutes)
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
            realm, hot_entity_warm_minutes)
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
            realm, recently_viewed_warm_minutes)
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
    # Default 180 min cycle, striped per realm via REALM_INTERVAL_OFFSETS so
    # NA fires at minute 0 of hours 0,3,6,…, EU at hours 1,4,7,…, ASIA at
    # hours 2,5,8,…. Each cycle walks ~1200 players × 6 WG API calls + DB
    # writes and takes 35-78 min/realm. Striping keeps at most one realm
    # mid-cycle so the background worker stays utilised but not stacked.
    player_refresh_minutes = int(
        os.getenv("PLAYER_REFRESH_INTERVAL_MINUTES", "180"))
    for realm in sorted(VALID_REALMS):
        minute_str, hour_str = _realm_crontab_for_cycle(
            realm, player_refresh_minutes)
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

    # -- Incremental Ranked Refresh (per realm) --
    # Default 120 min cycle, striped per realm. With 3 realms the stride is
    # 40 min: NA at minute 0 of even hours, EU at minute 40 of even hours,
    # ASIA at minute 20 of odd hours.
    ranked_refresh_minutes = int(
        os.getenv("RANKED_REFRESH_INTERVAL_MINUTES", "120"))
    for realm in sorted(VALID_REALMS):
        minute_str, hour_str = _realm_crontab_for_cycle(
            realm, ranked_refresh_minutes)
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

    # -- Rolling BattleObservation Floor (per realm, every 6h) --
    # Promoted from a daily cron to a 6-hourly cron on 2026-05-09 to tighten
    # battle pickup. Each realm fires 4× per day, striped via
    # REALM_INTERVAL_OFFSETS (2h stride within the 6h cycle) so NA, EU,
    # and ASIA never run concurrently. The floor walks active-7d players
    # whose latest BattleObservation is older than
    # BATTLE_OBSERVATION_FLOOR_HOURS (default tightened to 8h alongside the
    # cadence change). Most cycles return <100 candidates because the
    # tiered crawler covers hot players within 12h.
    # Runbook: agents/runbooks/runbook-battle-observation-floor-2026-05-02.md
    obs_floor_minute_str = os.getenv("BATTLE_OBSERVATION_FLOOR_MINUTE", "15")
    obs_floor_base_hour = int(os.getenv("BATTLE_OBSERVATION_FLOOR_HOUR", "1"))
    obs_floor_base_minute = (
        obs_floor_base_hour * 60 + int(obs_floor_minute_str))
    for realm in sorted(VALID_REALMS):
        # 6h cycle = 360 minutes. Striped offsets: NA at base, EU at
        # base+2h, ASIA at base+4h.
        minute_str, hour_str = _realm_crontab_for_cycle(
            realm, 360, base_minute=obs_floor_base_minute)
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
            "description": "Nightly rebuild of PlayerDailyShipStats from BattleEvent. No-op unless BATTLE_HISTORY_ROLLUP_ENABLED=1.",
        },
    )
