from __future__ import absolute_import, unicode_literals
import logging
import os
import time

from io import StringIO

from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.utils import timezone as django_timezone

from battlestats.celery import app

# Duplicated here (instead of importing from warships.models) to avoid
# importing models at module scope — gunicorn's when_ready hook loads this
# file before Django's app registry is ready.
DEFAULT_REALM = "na"


logger = logging.getLogger(__name__)
TASK_OPTS = {
    "time_limit": 600,
    "soft_time_limit": 540,
    "ignore_result": True,
}
CRAWL_TASK_OPTS = {
    "time_limit": 6 * 60 * 60,
    "soft_time_limit": 5 * 60 * 60 + 45 * 60,
    "ignore_result": True,
}
# The per-bucket win-rate-percentile warmer walks every tier×type bucket for a
# realm serially (each a ~10–28s per-(ship,player) aggregation for popular T10
# buckets, ~15 buckets in prod), so it needs far more headroom than TASK_OPTS'
# 540s soft limit. Generous ceiling; skip-if-warm keeps the common run cheap.
SHIP_PCT_WARM_TASK_OPTS = {
    "time_limit": 30 * 60,        # 30 min hard
    "soft_time_limit": 27 * 60,   # 27 min soft
    "ignore_result": True,
}
# Short DB-breather between heavy percentile buckets (load-spreading on the shared
# 2-vCPU managed Postgres). Tunable; 0 disables.
SHIP_PCT_WARM_PAUSE_SECONDS = float(
    os.getenv("SHIP_LIST_WR_PCT_WARM_PAUSE_SECONDS", "5"))
# Per-realm lock for the pct warmer — must OUTLIVE the task's hard time_limit so a
# slow run can't be duplicated by a concurrent trigger (snapshot fires 2×/day +
# the nightly top-ships Beat both chain it).
SHIP_PCT_WARM_LOCK_TIMEOUT = int(
    os.getenv("SHIP_LIST_WR_PCT_WARM_LOCK_TIMEOUT", str(40 * 60)))
CLAN_CRAWL_LOCK_TIMEOUT = 8 * 60 * 60
CLAN_CRAWL_HEARTBEAT_STALE_AFTER = 15 * 60
# Run-scoped resume marker: timestamp at which the current full crawl pass
# began. A pass takes ~14 days; the marker must outlive that plus restart gaps,
# so give it a generous TTL well beyond a single pass. Cleared on pass
# completion so the next scheduled pass starts fresh. See
# runbook-na-crawl-restart-loop-starves-refresh.
CLAN_CRAWL_PASS_MARKER_TTL = 21 * 24 * 60 * 60
# Enqueue dedup: at most one crawl_all_clans_task per realm in flight (queued or
# running), so the daily Beat cron + the 5-min watchdog can't pile up duplicate
# crawl messages behind the single-slot (-c 1) crawls worker. The pending flag is
# set at enqueue and cleared when the task starts; its TTL must outlive the time a
# realm sits queued behind the other realms' passes — MAX_CONCURRENT_REALM_CRAWLS=1
# serialises them and a pass is ~12-18h (post core_only), so a realm waits at most
# ~2 passes — with generous margin. The watchdog also clears a stale flag if the
# broker dropped the queued message. See runbook-crawls-queue-depth-alarm-2026-06-12.
CLAN_CRAWL_PENDING_TTL = 4 * 24 * 60 * 60
CLAN_CRAWL_PENDING_STALE_AFTER = 15 * 60
RESOURCE_TASK_LOCK_TIMEOUT = 15 * 60
RANKED_INCREMENTAL_LOCK_TIMEOUT = 6 * 60 * 60
PLAYER_REFRESH_LOCK_TIMEOUT = 6 * 60 * 60
# enrich-on-view: debounce so repeated profile views don't re-enqueue the same
# player. A failed attempt still falls back to the daily drift reclassify, so a
# generous window is fine.
ENRICH_ON_VIEW_COOLDOWN = 6 * 60 * 60
RANKED_REFRESH_DISPATCH_TIMEOUT = 15 * 60
# Ranked-observation refresh fires on profile render to capture a fresh
# `BattleObservation.ranked_ships_stats_json` (3-WG-call path). Dedup
# matches the random-side 15-min cooldown
# (data.RANKED_OBSERVATION_RENDER_STALE_AFTER) so both modes refresh on
# the same cadence when a user visits a profile.
RANKED_OBSERVATION_REFRESH_DISPATCH_TIMEOUT = 15 * 60
RANKED_OBSERVATION_REFRESH_STALE_AFTER_SECONDS = 15 * 60
CLAN_BATTLE_REFRESH_DISPATCH_TIMEOUT = 15 * 60
EFFICIENCY_REFRESH_DISPATCH_TIMEOUT = 15 * 60
EFFICIENCY_SNAPSHOT_REFRESH_DISPATCH_TIMEOUT = 15 * 60
PLAYER_RANKED_WR_BATTLES_CORRELATION_REFRESH_DISPATCH_TIMEOUT = 15 * 60
BROKER_DISPATCH_FAILURE_COOLDOWN = 60
LANDING_PAGE_WARM_LOCK_TIMEOUT = 20 * 60
LANDING_PAGE_WARM_DISPATCH_TIMEOUT = 30
LANDING_PLAYER_BEST_SNAPSHOT_REFRESH_LOCK_TIMEOUT = 2 * 60 * 60
DISTRIBUTION_WARM_LOCK_TIMEOUT = 15 * 60
CORRELATION_WARM_LOCK_TIMEOUT = 20 * 60
CORRELATION_WARM_DISPATCH_TIMEOUT = 30  # Matches landing — coalesces cold-cache fanout
# Coalesces the cold-cache fanout when a window-rotation gap or Redis eviction
# leaves the treemap / tier-type fresh keys cold and the published fallback is
# served (data.compute_realm_top_ships / _ships_by_tier_type queue a warm).
REALM_TOP_SHIPS_WARM_DISPATCH_TIMEOUT = 60
HOT_ENTITY_CACHE_WARM_LOCK_TIMEOUT = 30 * 60
LANDING_BEST_ENTITY_WARM_LOCK_TIMEOUT = 30 * 60
LANDING_BEST_ENTITY_WARM_DISPATCH_TIMEOUT = 5 * 60
CLAN_BATTLE_SUMMARY_REFRESH_DISPATCH_TIMEOUT = 10 * 60
# Clan roster idle refresh: bulk account/info pass that corrects every member's
# last_battle_date so "X days idle" is right without a per-member profile view.
# In-flight key dedups concurrent dispatch + signals FE pending; the cooldown
# gates the whole roster to ~once/hour.
CLAN_MEMBER_IDLE_REFRESH_DISPATCH_TIMEOUT = 5 * 60
CLAN_MEMBER_IDLE_REFRESH_COOLDOWN = 60 * 60
CLAN_MEMBER_IDLE_BULK_BATCH_SIZE = 100
BULK_CACHE_LOAD_LOCK_TIMEOUT = 30 * 60
RECENTLY_VIEWED_WARM_LOCK_TIMEOUT = 15 * 60
CLAN_TIER_DIST_WARM_LOCK_TIMEOUT = 3 * 60 * 60  # 3h — iterates all clans
ENRICH_PLAYER_DATA_LOCK_TIMEOUT = 6 * 60 * 60
# Daily floor for BattleObservation coverage. Walks active-7d players in a
# realm and fills any whose latest observation is older than the staleness
# threshold. Sits alongside the tiered incremental crawler — that's
# best-effort, this is a guaranteed daily floor so the diff lane never
# collapses 3+ days of activity into a single huge event.
DAILY_OBSERVATION_FLOOR_LOCK_TIMEOUT = 3 * 60 * 60


MAX_CONCURRENT_REALM_CRAWLS = int(
    os.getenv("MAX_CONCURRENT_REALM_CRAWLS", "1"))


# ---------------------------------------------------------------------------
# Realm-scoped lock / dispatch / heartbeat key helpers
# ---------------------------------------------------------------------------

def _clan_crawl_lock_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:crawl_all_clans:{realm}:lock"


def _clan_crawl_heartbeat_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:crawl_all_clans:{realm}:heartbeat"


def _clan_crawl_pass_marker_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:crawl_all_clans:{realm}:pass_started_at"


def _clan_crawl_pending_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:crawl_all_clans:{realm}:pending"


def _ranked_incremental_lock_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:incremental_ranked_data:{realm}:lock"


def _player_refresh_lock_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:incremental_player_refresh:{realm}:lock"


def _daily_observation_floor_lock_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:daily_observation_floor:{realm}:lock"


def _hot_players_capture_lock_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:hot_players_capture:{realm}:lock"


def _snapshot_active_players_lock_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:snapshot_active_players:{realm}:lock"


def _recapture_lapsed_lock_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:recapture_lapsed_players:{realm}:lock"


def _landing_page_warm_lock_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:warm_landing_page_content:{realm}:lock"


def _landing_page_warm_dispatch_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:warm_landing_page_content:{realm}:dispatch"


def _distribution_warm_lock_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:warm_player_distributions:{realm}:lock"


def _realm_top_ships_warm_lock_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:warm_realm_top_ships:{realm}:lock"


def _realm_top_ships_warm_dispatch_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:warm_realm_top_ships:{realm}:dispatch"


def _ships_by_pct_warm_lock_key(realm, tier, ship_type, mode) -> str:
    return (f"warships:tasks:warm_ships_by_pct:{realm}:{mode}"
            f":t{tier}:{ship_type}:lock")


def _ships_by_pct_warm_dispatch_key(realm, tier, ship_type, mode) -> str:
    return (f"warships:tasks:warm_ships_by_pct:{realm}:{mode}"
            f":t{tier}:{ship_type}:dispatch")


def _realm_ships_pct_warm_lock_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:warm_realm_ships_pct:{realm}:lock"


def _realm_ships_pct_warm_dispatch_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:warm_realm_ships_pct:{realm}:dispatch"


def _landing_player_best_snapshot_refresh_lock_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:materialize_landing_player_best_snapshots:{realm}:lock"


def _correlation_warm_lock_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:warm_player_correlations:{realm}:lock"


def _correlation_warm_dispatch_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:warm_player_correlations:{realm}:dispatch"


def _hot_entity_cache_warm_lock_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:warm_hot_entity_caches:{realm}:lock"


def _landing_best_entity_warm_lock_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:warm_landing_best_entity_caches:{realm}:lock"


def _landing_best_entity_warm_dispatch_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:warm_landing_best_entity_caches:{realm}:dispatch"


def _bulk_cache_load_lock_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:bulk_load_entity_caches:{realm}:lock"


def _recently_viewed_warm_lock_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:warm_recently_viewed_players:{realm}:lock"


def _enrich_player_data_lock_key() -> str:
    return "warships:tasks:enrich_player_data:lock"


def _clan_tier_dist_warm_lock_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:warm_all_clan_tier_distributions:{realm}:lock"


def _configured_clan_battle_warm_ids(raw_value=None):
    value = os.getenv("CLAN_BATTLE_WARM_CLAN_IDS",
                      "1000055908") if raw_value is None else raw_value
    return [clan_id.strip() for clan_id in str(value).split(",") if clan_id.strip()]


def _task_lock_key(task_name: str, resource_id: object) -> str:
    return f"warships:tasks:{task_name}:{resource_id}:lock"


# ---------------------------------------------------------------------------
# Realm-scoped dispatch / failure key helpers
# ---------------------------------------------------------------------------

def _ranked_refresh_dispatch_key(player_id: object, realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:update_ranked_data_dispatch:{realm}:{player_id}"


def _ranked_observation_refresh_dispatch_key(player_id: object, realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:refresh_ranked_observation_dispatch:{realm}:{player_id}"


def _ranked_observation_refresh_failure_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:refresh_ranked_observation_dispatch:{realm}:cooldown"


def _clan_battle_refresh_dispatch_key(player_id: object, realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:update_player_clan_battle_data_dispatch:{realm}:{player_id}"


def _efficiency_refresh_dispatch_key(player_id: object, realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:update_player_efficiency_data_dispatch:{realm}:{player_id}"


def _efficiency_snapshot_refresh_dispatch_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:refresh_efficiency_rank_snapshot_dispatch:{realm}"


def _player_ranked_wr_battles_correlation_refresh_dispatch_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:warm_player_ranked_wr_battles_correlation_dispatch:{realm}"


def _ranked_refresh_failure_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:update_ranked_data_dispatch:{realm}:cooldown"


def _clan_battle_refresh_failure_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:update_player_clan_battle_data_dispatch:{realm}:cooldown"


def _efficiency_refresh_failure_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:update_player_efficiency_data_dispatch:{realm}:cooldown"


def _efficiency_snapshot_refresh_failure_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:refresh_efficiency_rank_snapshot_dispatch:{realm}:cooldown"


def _player_ranked_wr_battles_correlation_refresh_failure_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:warm_player_ranked_wr_battles_correlation_dispatch:{realm}:cooldown"


def _clan_battle_summary_refresh_dispatch_key(clan_id: object, realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:update_clan_battle_summary_dispatch:{realm}:{clan_id}"


def _clan_member_idle_refresh_dispatch_key(clan_id: object, realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:refresh_clan_member_idle_dispatch:{realm}:{clan_id}"


def _clan_member_idle_refresh_cooldown_key(clan_id: object, realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:refresh_clan_member_idle_dispatch:{realm}:{clan_id}:cooldown"


# ---------------------------------------------------------------------------
# Queue / dispatch helpers
# ---------------------------------------------------------------------------

def queue_clan_battle_summary_refresh(clan_id: object, realm: str = DEFAULT_REALM):
    dispatch_key = _clan_battle_summary_refresh_dispatch_key(
        clan_id, realm=realm)
    if not cache.add(
        dispatch_key,
        "queued",
        timeout=CLAN_BATTLE_SUMMARY_REFRESH_DISPATCH_TIMEOUT,
    ):
        return {"status": "skipped", "reason": "already-queued"}

    try:
        update_clan_battle_summary_task.delay(clan_id=clan_id, realm=realm)
        return {"status": "queued"}
    except Exception as error:
        cache.delete(dispatch_key)
        logger.warning(
            "Skipping clan battle summary refresh enqueue because broker dispatch failed: %s",
            error,
        )
        return {"status": "skipped", "reason": "enqueue-failed"}


def is_clan_battle_summary_refresh_pending(clan_id: object, realm: str = DEFAULT_REALM) -> bool:
    return bool(cache.get(_clan_battle_summary_refresh_dispatch_key(clan_id, realm=realm)))


def is_clan_member_idle_refresh_pending(clan_id: object, realm: str = DEFAULT_REALM) -> bool:
    return bool(cache.get(_clan_member_idle_refresh_dispatch_key(clan_id, realm=realm)))


def queue_clan_member_idle_refresh(clan_id: object, realm: str = DEFAULT_REALM):
    """Queue a bulk roster idle refresh, gated to ~once/hour/clan.

    The cooldown key prevents re-refreshing a complete roster on every cache
    miss; the in-flight dispatch key dedups concurrent misses and is what the
    view reads for the X-Clan-Idle-Pending header (cleared in the task's
    finally). Returns a status dict mirroring the other queue helpers.
    """
    cooldown_key = _clan_member_idle_refresh_cooldown_key(clan_id, realm=realm)
    if cache.get(cooldown_key):
        return {"status": "skipped", "reason": "cooldown"}

    dispatch_key = _clan_member_idle_refresh_dispatch_key(clan_id, realm=realm)
    if not cache.add(dispatch_key, "queued", timeout=CLAN_MEMBER_IDLE_REFRESH_DISPATCH_TIMEOUT):
        return {"status": "skipped", "reason": "already-queued"}

    try:
        refresh_clan_member_idle_task.delay(clan_id=clan_id, realm=realm)
        cache.set(cooldown_key, "1", timeout=CLAN_MEMBER_IDLE_REFRESH_COOLDOWN)
        return {"status": "queued"}
    except Exception as error:
        cache.delete(dispatch_key)
        cache.delete(cooldown_key)
        logger.warning(
            "Skipping clan member idle refresh enqueue for clan_id=%s because broker dispatch failed: %s",
            clan_id,
            error,
        )
        return {"status": "skipped", "reason": "enqueue-failed"}


def queue_landing_page_warm(realm: str = DEFAULT_REALM, scope: str = 'all'):
    # If a warm is already executing for this realm, skip enqueue. The 30s
    # dispatch dedup expires while the 1200s task runs, so without this gate,
    # cache-fallback paths invoked from inside the warm itself would re-enqueue
    # in a loop (root cause of the 4581-message background-queue pileup
    # observed on 2026-04-27).
    if cache.get(_landing_page_warm_lock_key(realm)):
        return {"status": "skipped", "reason": "already-running"}

    dispatch_key = _landing_page_warm_dispatch_key(realm)
    if not cache.add(
        dispatch_key,
        "queued",
        timeout=LANDING_PAGE_WARM_DISPATCH_TIMEOUT,
    ):
        return {"status": "skipped", "reason": "already-queued"}

    try:
        warm_landing_page_content_task.delay(realm=realm, scope=scope)
        return {"status": "queued"}
    except Exception as error:
        cache.delete(dispatch_key)
        logger.warning(
            "Skipping landing page warm enqueue because broker dispatch failed: %s",
            error,
        )
        return {"status": "skipped", "reason": "enqueue-failed"}


def queue_realm_top_ships_warm(realm: str = DEFAULT_REALM):
    """Lock- + dispatch-aware enqueue of the realm top-ships/tier-type warmer.

    Called from the cold-cache read path (data.compute_realm_top_ships /
    compute_realm_ships_by_tier_type) when a window-rotation gap or Redis
    eviction leaves the window-keyed fresh key cold and the durable
    `:published` fallback is served instead. Keeps the published numbers
    served-old while a single warm recomputes the new window — without every
    cold request spawning its own full warm (the dedup + the task's own 300s
    lock coalesce the fanout). Mirrors queue_warm_player_correlations.
    """
    if cache.get(_realm_top_ships_warm_lock_key(realm)):
        return {"status": "skipped", "reason": "already-running"}

    dispatch_key = _realm_top_ships_warm_dispatch_key(realm)
    if not cache.add(
        dispatch_key,
        "queued",
        timeout=REALM_TOP_SHIPS_WARM_DISPATCH_TIMEOUT,
    ):
        return {"status": "skipped", "reason": "already-queued"}

    try:
        warm_realm_top_ships_task.delay(realm=realm)
        return {"status": "queued"}
    except Exception as error:
        cache.delete(dispatch_key)
        logger.warning(
            "Skipping realm top-ships warm enqueue because broker dispatch failed: %s",
            error,
        )
        return {"status": "skipped", "reason": "enqueue-failed"}


def queue_ships_by_pct_warm(realm=DEFAULT_REALM, tier=None, ship_type=None,
                            mode="random"):
    """Lock- + dispatch-aware enqueue of the win-rate-percentile ship-list warmer.

    Called from the cold-cache read path (data.compute_realm_ships_by_tier_type)
    when a percentile bucket's window-keyed fresh key is cold. The percentile
    recompute is a heavy per-(ship,player) aggregation (~10–28s for popular T10
    buckets — over the client's 15s timeout), so the request thread NEVER computes
    it: it serves a `pending` payload and the client polls while this background
    warm fills the fresh key. The per-bucket lock + dispatch dedup coalesce the
    fanout so a burst of polling clients enqueues at most one warm. Mirrors
    queue_realm_top_ships_warm (per bucket rather than per realm).
    """
    lock_key = _ships_by_pct_warm_lock_key(realm, tier, ship_type, mode)
    if cache.get(lock_key):
        return {"status": "skipped", "reason": "already-running"}

    dispatch_key = _ships_by_pct_warm_dispatch_key(realm, tier, ship_type, mode)
    if not cache.add(
        dispatch_key, "queued", timeout=REALM_TOP_SHIPS_WARM_DISPATCH_TIMEOUT,
    ):
        return {"status": "skipped", "reason": "already-queued"}

    try:
        warm_ships_by_pct_task.delay(
            realm=realm, tier=tier, ship_type=ship_type, mode=mode)
        return {"status": "queued"}
    except Exception as error:
        cache.delete(dispatch_key)
        logger.warning(
            "Skipping ships-by-pct warm enqueue because broker dispatch failed: %s",
            error,
        )
        return {"status": "skipped", "reason": "enqueue-failed"}


def queue_realm_ships_pct_warm(realm: str = DEFAULT_REALM):
    """Lock- + dispatch-aware enqueue of the per-realm all-buckets pct warmer.

    Chained from warm_realm_top_ships_task (which itself fires from the nightly
    Beat + after each ship snapshot) so every tier×type win-rate-percentile
    bucket is pre-warmed right after the window rotates — visitors never pay the
    ~20s crunch. The task's own lock + skip-if-warm make the repeated triggers
    cheap; this dispatch dedup just stops closely-timed fires from piling up.
    Mirrors queue_realm_top_ships_warm.
    """
    if cache.get(_realm_ships_pct_warm_lock_key(realm)):
        return {"status": "skipped", "reason": "already-running"}

    dispatch_key = _realm_ships_pct_warm_dispatch_key(realm)
    if not cache.add(
        dispatch_key, "queued", timeout=REALM_TOP_SHIPS_WARM_DISPATCH_TIMEOUT,
    ):
        return {"status": "skipped", "reason": "already-queued"}

    try:
        warm_realm_ships_pct_task.delay(realm=realm)
        return {"status": "queued"}
    except Exception as error:
        cache.delete(dispatch_key)
        logger.warning(
            "Skipping realm ships-pct warm enqueue because broker dispatch failed: %s",
            error,
        )
        return {"status": "skipped", "reason": "enqueue-failed"}


def queue_warm_player_correlations(realm: str = DEFAULT_REALM):
    # Lock-aware gate for the cold-cache user-traffic dispatch path
    # (server/warships/data.py:3400 fetch_player_tier_type_correlation).
    # Without this gate, every player-page load on a cold correlation cache
    # enqueues another full warm task — the same request-driven fanout that
    # caused the 4581-message landing-warmer pileup on 2026-04-27 (fixed
    # in commit f0e51d8 for landing). See agents/runbooks/runbook-post-rollout-followups-2026-05-01.md.
    if cache.get(_correlation_warm_lock_key(realm)):
        return {"status": "skipped", "reason": "already-running"}

    dispatch_key = _correlation_warm_dispatch_key(realm)
    if not cache.add(
        dispatch_key,
        "queued",
        timeout=CORRELATION_WARM_DISPATCH_TIMEOUT,
    ):
        return {"status": "skipped", "reason": "already-queued"}

    try:
        warm_player_correlations_task.delay(realm=realm)
        return {"status": "queued"}
    except Exception as error:
        cache.delete(dispatch_key)
        logger.warning(
            "Skipping correlation warm enqueue because broker dispatch failed: %s",
            error,
        )
        return {"status": "skipped", "reason": "enqueue-failed"}


def queue_landing_best_entity_warm(player_limit=25, clan_limit=25, force_refresh=False, realm: str = DEFAULT_REALM):
    dispatch_key = _landing_best_entity_warm_dispatch_key(realm)
    if not cache.add(
        dispatch_key,
        "queued",
        timeout=LANDING_BEST_ENTITY_WARM_DISPATCH_TIMEOUT,
    ):
        return {"status": "skipped", "reason": "already-queued"}

    try:
        warm_landing_best_entity_caches_task.delay(
            player_limit=int(player_limit),
            clan_limit=int(clan_limit),
            force_refresh=bool(force_refresh),
            realm=realm,
        )
        return {"status": "queued"}
    except Exception as error:
        cache.delete(dispatch_key)
        logger.warning(
            "Skipping landing best entity warm enqueue because broker dispatch failed: %s",
            error,
        )
        return {"status": "skipped", "reason": "enqueue-failed"}


def touch_clan_crawl_heartbeat(timestamp: float | None = None, realm: str = DEFAULT_REALM) -> float:
    heartbeat = time.time() if timestamp is None else float(timestamp)
    cache.set(_clan_crawl_heartbeat_key(realm), heartbeat,
              timeout=CLAN_CRAWL_LOCK_TIMEOUT)
    return heartbeat


def _crawl_heartbeat_is_fresh(heartbeat, now_ts: float) -> bool:
    if heartbeat is None:
        return False
    try:
        return now_ts - float(heartbeat) <= CLAN_CRAWL_HEARTBEAT_STALE_AFTER
    except (TypeError, ValueError):
        return False


def _run_locked_task(task_name: str, resource_id: object, request_id: str, callback):
    lock_key = _task_lock_key(task_name, resource_id)
    if not cache.add(lock_key, request_id, timeout=RESOURCE_TASK_LOCK_TIMEOUT):
        logger.info(
            "Skipping %s for resource=%s because another refresh is already running",
            task_name,
            resource_id,
        )
        return {"status": "skipped", "reason": "already-running"}

    try:
        result = callback()
        if isinstance(result, dict):
            result.setdefault("status", "completed")
            return result
        return {"status": "completed"}
    finally:
        cache.delete(lock_key)


def is_ranked_data_refresh_pending(player_id: object, realm: str = DEFAULT_REALM) -> bool:
    return bool(cache.get(_ranked_refresh_dispatch_key(player_id, realm=realm)))


def is_ranked_observation_refresh_pending(
    player_id: object, realm: str = DEFAULT_REALM,
) -> bool:
    return bool(cache.get(
        _ranked_observation_refresh_dispatch_key(player_id, realm=realm)
    ))


def queue_ranked_observation_refresh(
    player_id: object, realm: str = DEFAULT_REALM,
):
    """Dispatch a fresh BattleObservation + ranked capture for `player_id`.

    Lock-aware-gate pattern (mirrors `queue_ranked_data_refresh`):
      * Short-circuits when broker dispatch is in cooldown.
      * Dedup `cache.add` so a profile render burst (multiple endpoints
        firing within seconds of each other) coalesces into a single
        Celery enqueue.
      * Cleans up the dispatch key on enqueue failure so the next
        render can retry.

    The dispatcher itself is unconditionally fired by callers — the
    caller is responsible for the staleness check (typically: skip if
    the latest BattleObservation has a non-empty ranked payload less
    than RANKED_OBSERVATION_REFRESH_STALE_AFTER_SECONDS old).
    """
    if cache.get(_ranked_observation_refresh_failure_key(realm=realm)):
        return {"status": "skipped", "reason": "broker-unavailable"}

    dispatch_key = _ranked_observation_refresh_dispatch_key(
        player_id, realm=realm)
    if not cache.add(
        dispatch_key, "queued",
        timeout=RANKED_OBSERVATION_REFRESH_DISPATCH_TIMEOUT,
    ):
        return {"status": "skipped", "reason": "already-queued"}

    try:
        refresh_ranked_observation_task.delay(
            player_id=player_id, realm=realm)
        return {"status": "queued"}
    except Exception as error:
        cache.delete(dispatch_key)
        cache.set(
            _ranked_observation_refresh_failure_key(realm=realm),
            True, timeout=BROKER_DISPATCH_FAILURE_COOLDOWN,
        )
        logger.warning(
            "Skipping ranked-observation refresh enqueue for "
            "player_id=%s because broker dispatch failed: %s",
            player_id, error,
        )
        return {"status": "skipped", "reason": "enqueue-failed"}


def queue_ranked_data_refresh(player_id: object, realm: str = DEFAULT_REALM):
    if cache.get(_ranked_refresh_failure_key(realm=realm)):
        return {"status": "skipped", "reason": "broker-unavailable"}

    dispatch_key = _ranked_refresh_dispatch_key(player_id, realm=realm)
    if not cache.add(dispatch_key, "queued", timeout=RANKED_REFRESH_DISPATCH_TIMEOUT):
        return {"status": "skipped", "reason": "already-queued"}

    try:
        update_ranked_data_task.delay(player_id=player_id, realm=realm)
        return {"status": "queued"}
    except Exception as error:
        cache.delete(dispatch_key)
        cache.set(_ranked_refresh_failure_key(realm=realm), True,
                  timeout=BROKER_DISPATCH_FAILURE_COOLDOWN)
        logger.warning(
            "Skipping ranked refresh enqueue for player_id=%s because broker dispatch failed: %s",
            player_id,
            error,
        )
        return {"status": "skipped", "reason": "enqueue-failed"}


def is_clan_battle_data_refresh_pending(player_id: object, realm: str = DEFAULT_REALM) -> bool:
    return bool(cache.get(_clan_battle_refresh_dispatch_key(player_id, realm=realm)))


def queue_clan_battle_data_refresh(player_id: object, realm: str = DEFAULT_REALM):
    if cache.get(_clan_battle_refresh_failure_key(realm=realm)):
        return {"status": "skipped", "reason": "broker-unavailable"}

    dispatch_key = _clan_battle_refresh_dispatch_key(player_id, realm=realm)
    if not cache.add(dispatch_key, "queued", timeout=CLAN_BATTLE_REFRESH_DISPATCH_TIMEOUT):
        return {"status": "skipped", "reason": "already-queued"}

    try:
        update_player_clan_battle_data_task.delay(
            player_id=player_id, realm=realm)
        return {"status": "queued"}
    except Exception as error:
        cache.delete(dispatch_key)
        cache.set(_clan_battle_refresh_failure_key(realm=realm), True,
                  timeout=BROKER_DISPATCH_FAILURE_COOLDOWN)
        logger.warning(
            "Skipping clan battle refresh enqueue for player_id=%s because broker dispatch failed: %s",
            player_id,
            error,
        )
        return {"status": "skipped", "reason": "enqueue-failed"}


def is_efficiency_data_refresh_pending(player_id: object, realm: str = DEFAULT_REALM) -> bool:
    return bool(cache.get(_efficiency_refresh_dispatch_key(player_id, realm=realm)))


def queue_efficiency_data_refresh(player_id: object, realm: str = DEFAULT_REALM):
    if cache.get(_efficiency_refresh_failure_key(realm=realm)):
        return {"status": "skipped", "reason": "broker-unavailable"}

    dispatch_key = _efficiency_refresh_dispatch_key(player_id, realm=realm)
    if not cache.add(dispatch_key, "queued", timeout=EFFICIENCY_REFRESH_DISPATCH_TIMEOUT):
        return {"status": "skipped", "reason": "already-queued"}

    try:
        update_player_efficiency_data_task.delay(
            player_id=player_id, realm=realm)
        return {"status": "queued"}
    except Exception as error:
        cache.delete(dispatch_key)
        cache.set(_efficiency_refresh_failure_key(realm=realm), True,
                  timeout=BROKER_DISPATCH_FAILURE_COOLDOWN)
        logger.warning(
            "Skipping efficiency refresh enqueue for player_id=%s because broker dispatch failed: %s",
            player_id,
            error,
        )
        return {"status": "skipped", "reason": "enqueue-failed"}


def is_efficiency_rank_snapshot_refresh_pending(realm: str = DEFAULT_REALM) -> bool:
    return bool(cache.get(_efficiency_snapshot_refresh_dispatch_key(realm=realm)))


def queue_efficiency_rank_snapshot_refresh(realm: str = DEFAULT_REALM):
    # Event-driven efficiency-rank recompute is gated OFF by default (2026-06-20).
    # The rank is a slowly-changing population percentile, now recomputed once daily
    # by the efficiency-rank-snapshot-warmer-{realm} Beat task (signals.py). Per-event
    # recompute (clan crawl / efficiency-data refresh / per-player efficiency update)
    # was the #1 DB WAL hog (~488 GB cumulative) for negligible benefit — a single
    # player's data change does not move population percentiles. Badges/landing read
    # the persisted snapshot columns (get_published_efficiency_rank_payload), so they
    # never blank between recomputes. Flip EFFICIENCY_RANK_EVENT_TRIGGER_ENABLED=1 to
    # restore event-triggering. The daily Beat calls the task directly (not this
    # helper), so it is unaffected by this gate.
    if os.getenv("EFFICIENCY_RANK_EVENT_TRIGGER_ENABLED", "0") != "1":
        return {"status": "skipped", "reason": "event-trigger-disabled"}
    if cache.get(_efficiency_snapshot_refresh_failure_key(realm=realm)):
        return {"status": "skipped", "reason": "broker-unavailable"}

    dispatch_key = _efficiency_snapshot_refresh_dispatch_key(realm=realm)
    if not cache.add(dispatch_key, "queued", timeout=EFFICIENCY_SNAPSHOT_REFRESH_DISPATCH_TIMEOUT):
        return {"status": "skipped", "reason": "already-queued"}

    try:
        refresh_efficiency_rank_snapshot_task.delay(realm=realm)
        return {"status": "queued"}
    except Exception as error:
        cache.delete(dispatch_key)
        cache.set(_efficiency_snapshot_refresh_failure_key(realm=realm), True,
                  timeout=BROKER_DISPATCH_FAILURE_COOLDOWN)
        logger.warning(
            "Skipping efficiency-rank snapshot refresh enqueue because broker dispatch failed: %s",
            error,
        )
        return {"status": "skipped", "reason": "enqueue-failed"}


def queue_player_ranked_wr_battles_correlation_refresh(realm: str = DEFAULT_REALM):
    if cache.get(_player_ranked_wr_battles_correlation_refresh_failure_key(realm=realm)):
        return {"status": "skipped", "reason": "broker-unavailable"}

    dispatch_key = _player_ranked_wr_battles_correlation_refresh_dispatch_key(
        realm=realm)
    if not cache.add(dispatch_key, "queued", timeout=PLAYER_RANKED_WR_BATTLES_CORRELATION_REFRESH_DISPATCH_TIMEOUT):
        return {"status": "skipped", "reason": "already-queued"}

    try:
        warm_player_ranked_wr_battles_correlation_task.delay(realm=realm)
        return {"status": "queued"}
    except Exception as error:
        cache.delete(dispatch_key)
        cache.set(_player_ranked_wr_battles_correlation_refresh_failure_key(realm=realm), True,
                  timeout=BROKER_DISPATCH_FAILURE_COOLDOWN)
        logger.warning(
            "Skipping ranked heatmap correlation refresh enqueue because broker dispatch failed: %s",
            error,
        )
        return {"status": "skipped", "reason": "enqueue-failed"}


# ---------------------------------------------------------------------------
# Celery tasks
# ---------------------------------------------------------------------------

@app.task(bind=True, **TASK_OPTS)
def update_clan_data_task(self, clan_id, realm=DEFAULT_REALM):
    from warships.data import update_clan_data

    logger.info(
        "Starting update_clan_data_task for clan_id=%s realm=%s", clan_id, realm)
    return _run_locked_task(
        "update_clan_data",
        clan_id,
        self.request.id,
        lambda: update_clan_data(clan_id=clan_id, realm=realm),
    )


@app.task(bind=True, **TASK_OPTS)
def update_clan_members_task(self, clan_id, realm=DEFAULT_REALM):
    from warships.data import update_clan_members

    logger.info(
        "Starting update_clan_members_task for clan_id=%s realm=%s", clan_id, realm)
    return _run_locked_task(
        "update_clan_members",
        clan_id,
        self.request.id,
        lambda: update_clan_members(clan_id=clan_id, realm=realm),
    )


@app.task(bind=True, **TASK_OPTS)
def update_clan_tier_distribution_task(self, clan_id, realm=DEFAULT_REALM):
    from warships.data import update_clan_tier_distribution

    logger.info(
        "Starting update_clan_tier_distribution_task for clan_id=%s realm=%s", clan_id, realm)
    return _run_locked_task(
        "update_clan_tier_distribution",
        clan_id,
        self.request.id,
        lambda: update_clan_tier_distribution(clan_id=clan_id, realm=realm),
    )


@app.task(bind=True, **TASK_OPTS)
def update_player_data_task(self, player_id, realm=DEFAULT_REALM, force_refresh=False):
    from warships.data import update_player_data
    from warships.models import Player

    logger.info(
        "Starting update_player_data_task for player_id=%s realm=%s force_refresh=%s",
        player_id,
        realm,
        force_refresh,
    )

    def _refresh_player():
        player = Player.objects.get(player_id=player_id, realm=realm)
        update_player_data(
            player=player, force_refresh=force_refresh, realm=realm)
        # The refresh just reset the activity clock (days_since_last_battle /
        # last_fetch). If this now-fresh player is eligible but never enriched,
        # fast-path it instead of waiting up to ~24h for the daily drift
        # reclassify. update_player_data_task is dispatched only from the
        # request/view path, so this is genuinely "enrich on view".
        _maybe_enrich_on_view(player, realm)

    return _run_locked_task(
        "update_player_data",
        player_id,
        self.request.id,
        _refresh_player,
    )


def _maybe_enrich_on_view(player, realm):
    """Enqueue a single-player enrich for a just-viewed, eligible, un-enriched
    player. Debounced (one enqueue per player per cooldown); the task re-checks
    eligibility authoritatively. Kill switch: ENRICH_ON_VIEW_ENABLED."""
    if os.getenv("ENRICH_ON_VIEW_ENABLED", "0") != "1":
        return
    # battles_json non-null => already enriched (data) or empty (no ships); both
    # are terminal. Only null rows are still enrichment candidates.
    if player.battles_json is not None:
        return
    # Best-effort: this runs inside the refresh task, so a broker/redis hiccup
    # must never fail the (primary) refresh — the daily drift reclassify is the
    # fallback enrichment path.
    try:
        if not cache.add(
                f"enrich_on_view_seen:{realm}:{player.player_id}", "1",
                ENRICH_ON_VIEW_COOLDOWN):
            return
        enrich_player_on_view_task.apply_async(
            (player.player_id, realm), queue="background")
    except Exception as exc:
        logger.warning(
            "enrich-on-view enqueue failed for %s/%s: %s",
            realm, player.player_id, exc)


@app.task(**TASK_OPTS)
def enrich_player_on_view_task(player_id, realm=DEFAULT_REALM):
    """Fast-path enrich a single un-enriched but now-eligible player, triggered
    by a profile view's refresh. Closes the daily-drift-reclassify latency for
    on-demand views. Self-guards (idempotent no-op) if disabled, already
    enriched/empty, or no longer eligible. Eligibility mirrors the crawler gate
    in ``enrich_player_data._candidates`` (same env thresholds)."""
    from warships.models import Player

    if os.getenv("ENRICH_ON_VIEW_ENABLED", "0") != "1":
        return {"status": "skipped", "reason": "disabled"}

    min_pvp = int(os.getenv("ENRICH_MIN_PVP_BATTLES", "500"))
    min_wr = float(os.getenv("ENRICH_MIN_WR", "48.0"))
    max_inactive = int(os.getenv("ENRICH_MAX_INACTIVE_DAYS", "365"))

    try:
        player = Player.objects.get(player_id=player_id, realm=realm)
    except Player.DoesNotExist:
        return {"status": "skipped", "reason": "missing"}

    if player.battles_json is not None:
        return {"status": "skipped", "reason": "already-enriched"}
    days = player.days_since_last_battle
    if (player.is_hidden or not player.name
            or (player.pvp_battles or 0) < min_pvp
            or (player.pvp_ratio or 0) < min_wr
            or days is None or days > max_inactive):
        return {"status": "skipped", "reason": "ineligible"}

    from warships.management.commands.enrich_player_data import (
        _enrich_player_parallel,
    )
    try:
        _enrich_player_parallel(player_id, realm)
    except Exception as exc:  # network/WG hiccup — drift reclassify will retry
        logger.warning(
            "enrich_player_on_view_task failed for %s/%s: %s",
            realm, player_id, exc)
        return {"status": "error", "reason": str(exc)}
    logger.info("enrich_player_on_view_task enriched %s/%s", realm, player_id)
    return {"status": "enriched", "player_id": player_id}


@app.task(bind=True, **TASK_OPTS)
def update_battle_data_task(self, player_id, realm=DEFAULT_REALM):
    from warships.data import update_battle_data

    logger.info(
        "Starting update_battle_data_task for player_id=%s realm=%s", player_id, realm)
    return _run_locked_task(
        "update_battle_data",
        player_id,
        self.request.id,
        lambda: update_battle_data(player_id=player_id, realm=realm),
    )


@app.task(**TASK_OPTS)
def update_randoms_data_task(player_id, realm=DEFAULT_REALM):
    from warships.data import update_randoms_data
    logger.info(
        "Starting update_randoms_data_task for player_id=%s realm=%s", player_id, realm)
    update_randoms_data(player_id=player_id, realm=realm)


@app.task(**TASK_OPTS)
def update_tiers_data_task(player_id, realm=DEFAULT_REALM):
    from warships.data import update_tiers_data
    logger.info(
        "Starting update_tiers_data_task for player_id=%s realm=%s", player_id, realm)
    update_tiers_data(player_id=player_id, realm=realm)


@app.task(**TASK_OPTS)
def update_snapshot_data_task(player_id, realm=DEFAULT_REALM):
    from warships.data import update_snapshot_data
    logger.info(
        "Starting update_snapshot_data_task for player_id=%s realm=%s", player_id, realm)
    update_snapshot_data(player_id=player_id, realm=realm)


@app.task(**TASK_OPTS)
def update_activity_data_task(player_id, realm=DEFAULT_REALM):
    from warships.data import update_activity_data
    logger.info(
        "Starting update_activity_data_task for player_id=%s realm=%s", player_id, realm)
    update_activity_data(player_id=player_id, realm=realm)


@app.task(**TASK_OPTS)
def update_type_data_task(player_id, realm=DEFAULT_REALM):
    from warships.data import update_type_data
    logger.info(
        "Starting update_type_data_task for player_id=%s realm=%s", player_id, realm)
    update_type_data(player_id=player_id, realm=realm)


@app.task(bind=True, **TASK_OPTS)
def update_ranked_data_task(self, player_id, realm=DEFAULT_REALM):
    from warships.data import update_ranked_data

    logger.info(
        "Starting update_ranked_data_task for player_id=%s realm=%s", player_id, realm)
    try:
        return _run_locked_task(
            "update_ranked_data",
            player_id,
            self.request.id,
            lambda: update_ranked_data(player_id=player_id, realm=realm),
        )
    finally:
        cache.delete(_ranked_refresh_dispatch_key(player_id, realm=realm))


@app.task(bind=True, **TASK_OPTS)
def refresh_ranked_observation_task(self, player_id, realm=DEFAULT_REALM):
    """Force a fresh BattleObservation + ranked capture for `player_id`.

    Direct path to `record_ranked_observation_and_diff` (3 WG calls).
    Used by the on-render dispatch in `fetch_player_summary` so the
    BattleHistoryCard's Ranked / All views always reflect the latest
    state without waiting for the next regular crawl tick.

    The dispatcher (`queue_ranked_observation_refresh`) coalesces bursts
    via cache.add dedup, so a profile-render fanout dispatches once.
    """
    from warships.incremental_battles import record_ranked_observation_and_diff

    logger.info(
        "Starting refresh_ranked_observation_task for player_id=%s realm=%s",
        player_id, realm,
    )
    try:
        return record_ranked_observation_and_diff(
            int(player_id), realm=realm)
    finally:
        cache.delete(_ranked_observation_refresh_dispatch_key(
            player_id, realm=realm,
        ))


@app.task(bind=True, **TASK_OPTS)
def update_player_clan_battle_data_task(self, player_id, realm=DEFAULT_REALM):
    from warships.data import fetch_player_clan_battle_seasons

    logger.info(
        "Starting update_player_clan_battle_data_task for player_id=%s realm=%s", player_id, realm)
    try:
        return _run_locked_task(
            "update_player_clan_battle_data",
            player_id,
            self.request.id,
            lambda: fetch_player_clan_battle_seasons(player_id, realm=realm),
        )
    finally:
        cache.delete(_clan_battle_refresh_dispatch_key(player_id, realm=realm))


@app.task(bind=True, **TASK_OPTS)
def refresh_clan_member_idle_task(self, clan_id, realm=DEFAULT_REALM):
    """Bulk-refresh roster idle fields so a clan's "X days idle" is correct
    without a per-member profile view.

    One account/info request per <=100 members. Writes ONLY last_battle_date +
    days_since_last_battle via bulk_update — never last_fetch (bumping it would
    suppress the real per-player full refresh for ~23h, data.py:4717). On a
    transient WG error the affected batch's stored values are left untouched
    (no clobber); a poison batch falls back to per-player isolation.
    """
    from datetime import datetime, timezone as dt_timezone

    from warships.api.players import (
        _bulk_fetch_account_info,
        _per_player_account_fallback,
    )
    from warships.models import Clan, Player, realm_cache_key

    def _refresh():
        try:
            clan = Clan.objects.get(clan_id=clan_id, realm=realm)
        except Clan.DoesNotExist:
            return {"status": "skipped", "reason": "clan-missing"}

        members = list(
            clan.player_set.exclude(name='').exclude(player_id__isnull=True)
        )
        if not members:
            return {"status": "skipped", "reason": "empty-roster"}

        by_id = {m.player_id: m for m in members}
        ids = list(by_id.keys())
        today = django_timezone.now().date()
        updated = []

        for start in range(0, len(ids), CLAN_MEMBER_IDLE_BULK_BATCH_SIZE):
            chunk = ids[start:start + CLAN_MEMBER_IDLE_BULK_BATCH_SIZE]
            data, err = _bulk_fetch_account_info(chunk, realm)
            if err == "INVALID_ACCOUNT_ID":
                data = _per_player_account_fallback(chunk, realm)
            elif err:
                logger.warning(
                    "clan member idle refresh batch failed clan_id=%s realm=%s err=%s",
                    clan_id, realm, err,
                )
                continue

            for pid in chunk:
                info = data.get(str(pid)) if data else None
                if not info:
                    continue
                member = by_id.get(pid)
                if member is None:
                    continue
                last_battle_time = info.get("last_battle_time")
                new_date = (
                    datetime.fromtimestamp(last_battle_time, tz=dt_timezone.utc).date()
                    if last_battle_time else None
                )
                member.last_battle_date = new_date
                if new_date:
                    member.days_since_last_battle = (today - new_date).days
                updated.append(member)

        if updated:
            Player.objects.bulk_update(
                updated, ["last_battle_date", "days_since_last_battle"])
            # Drop the cached clan_members payload so the next poll re-derives
            # idle from the fresh last_battle_date.
            cache.delete(realm_cache_key(realm, f'clan:members:v3:{clan_id}'))
        return {"status": "completed", "updated": len(updated)}

    try:
        return _run_locked_task(
            "refresh_clan_member_idle",
            clan_id,
            self.request.id,
            _refresh,
        )
    finally:
        cache.delete(_clan_member_idle_refresh_dispatch_key(clan_id, realm=realm))


@app.task(bind=True, **TASK_OPTS)
def update_player_efficiency_data_task(self, player_id, realm=DEFAULT_REALM):
    from warships.data import refresh_player_explorer_summary, update_player_efficiency_data
    from warships.models import Player

    logger.info(
        "Starting update_player_efficiency_data_task for player_id=%s realm=%s", player_id, realm)

    def _refresh_player_efficiency():
        player = Player.objects.get(player_id=player_id, realm=realm)
        update_player_efficiency_data(player=player, realm=realm)
        refresh_player_explorer_summary(player)
        queue_efficiency_rank_snapshot_refresh(realm=realm)

    try:
        return _run_locked_task(
            "update_player_efficiency_data",
            player_id,
            self.request.id,
            _refresh_player_efficiency,
        )
    finally:
        cache.delete(_efficiency_refresh_dispatch_key(player_id, realm=realm))


@app.task(bind=True, **TASK_OPTS)
def refresh_efficiency_rank_snapshot_task(self, realm=DEFAULT_REALM):
    from warships.data import recompute_efficiency_rank_snapshot, invalidate_player_detail_cache

    logger.info("Starting refresh_efficiency_rank_snapshot_task realm=%s", realm)
    try:
        result = _run_locked_task(
            "refresh_efficiency_rank_snapshot",
            "global",
            self.request.id,
            lambda: recompute_efficiency_rank_snapshot(
                skip_refresh=True, realm=realm),
        )
        # Invalidate cached player detail payloads for players whose
        # efficiency rank was just recomputed, so the next request
        # serves the updated icon/tier instead of stale cached data.
        if isinstance(result, dict) and result.get('status') == 'completed':
            from warships.models import PlayerExplorerSummary
            updated_at = result.get('snapshot_updated_at')
            if updated_at:
                ranked_player_ids = list(
                    PlayerExplorerSummary.objects
                    .filter(efficiency_rank_updated_at=updated_at)
                    .values_list('player__player_id', flat=True)
                )
                for pid in ranked_player_ids:
                    invalidate_player_detail_cache(pid, realm=realm)
                logger.info(
                    "Invalidated %d player detail caches after rank snapshot (realm=%s)",
                    len(ranked_player_ids), realm,
                )
        return result
    finally:
        cache.delete(_efficiency_snapshot_refresh_dispatch_key(realm=realm))


@app.task(bind=True, **TASK_OPTS)
def snapshot_ship_top_players_task(self, realm=DEFAULT_REALM):
    """Per-realm T10 top-player snapshot over the trailing SHIP_LEADERBOARD_WINDOW_DAYS window.

    No-op unless SHIP_BADGE_SNAPSHOT_ENABLED=1 (the rollout switch). The beat
    schedule fires **nightly** (per-realm striped); each run recomputes the
    rolling board for `[today - SHIP_LEADERBOARD_WINDOW_DAYS, today)` so the
    profile badges + `/ship` standings evolve daily. Delegates to
    data.compute_ship_top_player_snapshot (default window = trailing window
    ending today). See agents/runbooks/runbook-ship-badges-rolling-2026-06-14.md.
    """
    if os.getenv("SHIP_BADGE_SNAPSHOT_ENABLED", "0") != "1":
        logger.info(
            "snapshot_ship_top_players_task skipped "
            "(SHIP_BADGE_SNAPSHOT_ENABLED!=1) realm=%s", realm)
        return {"status": "disabled", "realm": realm}

    from warships.data import compute_ship_top_player_snapshot

    logger.info("Starting snapshot_ship_top_players_task realm=%s", realm)
    result = _run_locked_task(
        "snapshot_ship_top_players",
        realm,
        self.request.id,
        lambda: compute_ship_top_player_snapshot(realm=realm),
    )

    # A real snapshot just rewrote this realm's top-3 ship standings, so
    # re-materialize the landing Best-player snapshots to refresh their baked-in
    # `ship_badges` (added/removed medals) without waiting for the next daily
    # materializer — that task then self-republishes the Redis payloads. The
    # snapshot rows commit inside compute_ship_top_player_snapshot's
    # transaction.atomic() before _run_locked_task returns, so the materialize
    # reads committed data. `status != "completed"` means a lock-skip (the
    # disabled path already returned above) — nothing was rewritten, so skip.
    if isinstance(result, dict) and result.get("status") == "completed":
        materialize_landing_player_best_snapshots_task.apply_async(
            kwargs={"realm": realm},
            queue="background",
        )
        # The snapshot just advanced the window-end date, so the treemap +
        # tier-type list cache keys (keyed by that date) have rotated cold.
        # Warm them now — overwriting the durable `:published` fallback with
        # the new numbers — instead of waiting ~1h for the scheduled warmer.
        # The published fallback keeps the previous numbers served until this
        # warm lands (warm-before-evict). The dispatch dedup + the warmer's
        # own lock coalesce against the scheduled run / any cold-read enqueue.
        queue_realm_top_ships_warm(realm)

    return result


@app.task(bind=True, **TASK_OPTS)
def warm_player_ranked_wr_battles_correlation_task(self, realm=DEFAULT_REALM):
    from warships.data import warm_player_ranked_wr_battles_population_correlation

    logger.info(
        "Starting warm_player_ranked_wr_battles_correlation_task realm=%s", realm)
    try:
        return _run_locked_task(
            "warm_player_ranked_wr_battles_correlation",
            "population",
            self.request.id,
            lambda: warm_player_ranked_wr_battles_population_correlation(
                realm=realm),
        )
    finally:
        cache.delete(
            _player_ranked_wr_battles_correlation_refresh_dispatch_key(realm=realm))


@app.task(bind=True, **TASK_OPTS)
def update_clan_battle_summary_task(self, clan_id, realm=DEFAULT_REALM):
    from warships.data import refresh_clan_battle_seasons_cache

    logger.info(
        "Starting update_clan_battle_summary_task for clan_id=%s realm=%s", clan_id, realm)
    try:
        return _run_locked_task(
            "update_clan_battle_summary",
            clan_id,
            self.request.id,
            lambda: refresh_clan_battle_seasons_cache(clan_id, realm=realm),
        )
    finally:
        cache.delete(_clan_battle_summary_refresh_dispatch_key(
            clan_id, realm=realm))


@app.task(bind=True, **TASK_OPTS)
def warm_clan_battle_summaries_task(self, clan_ids=None, realm=DEFAULT_REALM):
    from warships.data import refresh_clan_battle_seasons_cache

    configured_ids = clan_ids or _configured_clan_battle_warm_ids()
    if not configured_ids:
        logger.info(
            "Skipping warm_clan_battle_summaries_task because no clan ids are configured")
        return {"status": "skipped", "reason": "no-clans-configured"}

    results = []
    for clan_id in configured_ids:
        result = _run_locked_task(
            "update_clan_battle_summary",
            clan_id,
            self.request.id,
            lambda clan_id=clan_id: refresh_clan_battle_seasons_cache(
                clan_id, realm=realm),
        )
        results.append({"clan_id": str(clan_id), **result})

    logger.info(
        "Finished warm_clan_battle_summaries_task for clan_ids=%s", configured_ids)
    return {"status": "completed", "results": results}


@app.task(bind=True, **TASK_OPTS)
def warm_landing_page_content_task(self, realm=DEFAULT_REALM, scope='all'):
    from warships.landing import warm_landing_page_content

    logger.info(
        "Starting warm_landing_page_content_task realm=%s scope=%s",
        realm,
        scope,
    )

    lock_key = _landing_page_warm_lock_key(realm)
    if not cache.add(lock_key, self.request.id, timeout=LANDING_PAGE_WARM_LOCK_TIMEOUT):
        logger.info(
            "Skipping warm_landing_page_content_task because another landing warm is already running"
        )
        return {"status": "skipped", "reason": "already-running"}

    try:
        result = warm_landing_page_content(
            force_refresh=True,
            realm=realm,
            scope=scope,
        )
        logger.info("Finished warm_landing_page_content_task: %s", result)
        return result
    finally:
        cache.delete(lock_key)
        cache.delete(_landing_page_warm_dispatch_key(realm))


@app.task(bind=True, **TASK_OPTS)
def warm_realm_top_ships_task(self, realm=DEFAULT_REALM):
    """Pre-populate the realm top-ships treemap + tier/type list caches.

    Recomputes both treemap modes (random + ranked) and every tier×type ship
    list bucket for the realm once per day (force-refresh) and writes them to
    Redis under their window-end-tagged keys, so the first visitor hits a warm
    cache instead of the aggregation. Both surfaces aggregate over the rolling
    trailing window the /ship leaderboards read (anchored on the latest snapshot's
    captured_on); the daily warm runs after the nightly snapshot so it warms the
    current window. Mirrors the other per-realm landing warmers.

    The tier/type list (`compute_realm_ships_by_tier_type`, backing the landing
    drill-down filter) is a live `BattleEvent` GROUP-BY — expensive to compute
    cold. Without this warm, every first click of a new tier/type combination
    paid that aggregation on the request path. Only `mode="random"` is warmed:
    the landing `ShipLeaderboard` never passes a mode, so random is the only
    bucket ever requested. Runbook: runbook-leaderboard-updates.md.
    """
    from warships.data import (
        compute_realm_top_ships, compute_realm_ships_by_tier_type,
        _badge_tiers, SHIP_LEADERBOARD_TYPES,
        SHIP_LIST_DEFAULT_TIER, SHIP_LIST_DEFAULT_TYPE,
    )

    lock_key = _realm_top_ships_warm_lock_key(realm)
    if not cache.add(lock_key, self.request.id, timeout=300):
        logger.info(
            "Skipping warm_realm_top_ships_task realm=%s — already running", realm)
        return {"status": "skipped", "reason": "already-running"}

    try:
        results = {}
        for mode in ("random", "ranked"):
            payload = compute_realm_top_ships(
                realm, limit=25, mode=mode, use_cache=False)
            results[mode] = len(payload.get("ships", []))

        # Tier/type list buckets — only mode="random" is ever requested.
        bucket_count = 0
        for tier in sorted(_badge_tiers()):
            for ship_type in SHIP_LEADERBOARD_TYPES:
                compute_realm_ships_by_tier_type(
                    realm, tier=tier, ship_type=ship_type,
                    mode="random", use_cache=False)
                bucket_count += 1
        results["tier_type_buckets"] = bucket_count

        # The landing list now defaults to the top-50% WR view, so pre-warm the
        # ONE default bucket's percentile (one heavy per-(ship,player) query that
        # materializes both 50 & 25) to keep the primary landing view instant.
        # Every other pct bucket stays lazy (queue + poll on first view). Guard
        # the default tier against the realm's badge tiers so a misconfigured
        # default can't 400 the warm.
        if SHIP_LIST_DEFAULT_TIER in _badge_tiers():
            compute_realm_ships_by_tier_type(
                realm, tier=SHIP_LIST_DEFAULT_TIER,
                ship_type=SHIP_LIST_DEFAULT_TYPE, mode="random",
                wr_pct=50, use_cache=False)
            results["default_pct_bucket"] = (
                f"t{SHIP_LIST_DEFAULT_TIER}/{SHIP_LIST_DEFAULT_TYPE}")

        # Chain the per-realm all-buckets pct warmer so EVERY other tier×type
        # win-rate-percentile bucket is pre-warmed too (visitors never trigger the
        # ~20s crunch). It skip-if-warms, so it won't recompute the default bucket
        # just warmed above, and the repeated triggers (Beat + 2×/day snapshot)
        # collapse to one real pass per window. See queue_realm_ships_pct_warm.
        results["pct_warm_chain"] = queue_realm_ships_pct_warm(realm)

        logger.info(
            "Warmed top-ships realm=%s modes+buckets=%s", realm, results)
        return {"status": "completed", "realm": realm, "results": results}
    finally:
        cache.delete(lock_key)
        # Let the next cold-read enqueue fire (mirrors the other warmers).
        cache.delete(_realm_top_ships_warm_dispatch_key(realm))


@app.task(bind=True, **TASK_OPTS)
def warm_ships_by_pct_task(self, realm=DEFAULT_REALM, tier=None, ship_type=None,
                           mode="random"):
    """Compute + cache the win-rate-percentile ship-list buckets for one tier+type.

    Backs the inline ship list's WR filter (top 50% / 25% of each ship's players).
    The recompute is a per-(ship,player) BattleEvent aggregation — too heavy for
    the request thread — so the cold read path (compute_realm_ships_by_tier_type)
    queues THIS background task and serves a pending payload; the client polls
    until the fresh key is filled. One run materializes BOTH offered percentiles
    (50 & 25) from a single query (wr_pct=50 derives 25 too). Per-bucket lock so
    concurrent enqueues for the same bucket coalesce. Runs on the `background`
    queue (off the user-facing lanes). See queue_ships_by_pct_warm.
    """
    from warships.data import compute_realm_ships_by_tier_type

    lock_key = _ships_by_pct_warm_lock_key(realm, tier, ship_type, mode)
    if not cache.add(lock_key, self.request.id, timeout=300):
        logger.info(
            "Skipping warm_ships_by_pct_task realm=%s t%s/%s — already running",
            realm, tier, ship_type)
        return {"status": "skipped", "reason": "already-running"}

    try:
        # use_cache=False forces the heavy compute; the function caches both the
        # 50 and 25 fresh keys and returns the wr_pct=50 payload.
        payload = compute_realm_ships_by_tier_type(
            realm, tier=tier, ship_type=ship_type, mode=mode,
            wr_pct=50, use_cache=False)
        ships = len(payload.get("ships", []))
        logger.info(
            "Warmed ships-by-pct realm=%s t%s/%s ships=%s",
            realm, tier, ship_type, ships)
        return {"status": "completed", "realm": realm, "tier": tier,
                "ship_type": ship_type, "ships": ships}
    finally:
        cache.delete(lock_key)
        # Let the next cold-read enqueue fire (mirrors the other warmers).
        cache.delete(
            _ships_by_pct_warm_dispatch_key(realm, tier, ship_type, mode))


@app.task(bind=True, **SHIP_PCT_WARM_TASK_OPTS)
def warm_realm_ships_pct_task(self, realm=DEFAULT_REALM):
    """Pre-warm EVERY tier×type win-rate-percentile ship-list bucket for a realm.

    The inline ship list defaults to the top-50% WR view, and the recompute is a
    heavy per-(ship,player) BattleEvent aggregation (~10–28s for popular T10
    buckets). Originally only the default bucket was warmed and every other bucket
    was lazy (queue + ~20s poll on first view) — too much burden on visitors. This
    walks all tier×type buckets serially, computing each at wr_pct=50 (which
    materializes BOTH 50 & 25 from one query), so visitors never trigger the
    crunch.

    Load discipline on the shared 2-vCPU Postgres:
    - **skip-if-warm** — buckets whose fresh key already exists for the current
      window are skipped, so the repeated triggers (nightly Beat + the 2×/day
      snapshot, all chaining this) collapse to ONE real pass per window, and an
      ACKS_LATE redelivery after a mid-loop crash resumes where it stopped.
    - **default bucket first** — the primary landing view warms before the rest.
    - **per-bucket pause** — a short DB breather between heavy buckets.
    - **per-bucket lock** — grabs the lazy task's lock before computing, so a
      visitor opening a still-cold bucket mid-pass doesn't double-run the query.

    Per-realm lock; the realms are hour-striped upstream so two never overlap on
    the DB. Runs on the `background` queue. Chained via queue_realm_ships_pct_warm.
    """
    from warships.data import (
        compute_realm_ships_by_tier_type, ship_pct_bucket_cache_key,
        _badge_tiers, SHIP_LEADERBOARD_TYPES,
        SHIP_LIST_DEFAULT_TIER, SHIP_LIST_DEFAULT_TYPE,
    )

    lock_key = _realm_ships_pct_warm_lock_key(realm)
    if not cache.add(lock_key, self.request.id, timeout=SHIP_PCT_WARM_LOCK_TIMEOUT):
        logger.info(
            "Skipping warm_realm_ships_pct_task realm=%s — already running", realm)
        return {"status": "skipped", "reason": "already-running"}

    try:
        # Default bucket first so the primary landing view warms before the rest.
        buckets = [(t, ty) for t in sorted(_badge_tiers())
                   for ty in SHIP_LEADERBOARD_TYPES]
        default = (SHIP_LIST_DEFAULT_TIER, SHIP_LIST_DEFAULT_TYPE)
        if default in buckets:
            buckets.remove(default)
            buckets.insert(0, default)

        warmed = skipped = 0
        for tier, ship_type in buckets:
            # Skip-if-warm: don't recompute a bucket already filled this window.
            if cache.get(ship_pct_bucket_cache_key(realm, tier, ship_type)) is not None:
                skipped += 1
                continue
            # Coalesce with the lazy per-bucket task: if a visitor's cold read is
            # already warming this bucket, let it — don't run the query twice.
            bucket_lock = _ships_by_pct_warm_lock_key(
                realm, tier, ship_type, "random")
            if not cache.add(bucket_lock, self.request.id, timeout=300):
                skipped += 1
                continue
            try:
                compute_realm_ships_by_tier_type(
                    realm, tier=tier, ship_type=ship_type, mode="random",
                    wr_pct=50, use_cache=False)
                warmed += 1
            finally:
                cache.delete(bucket_lock)
            if SHIP_PCT_WARM_PAUSE_SECONDS > 0:
                time.sleep(SHIP_PCT_WARM_PAUSE_SECONDS)

        result = {"status": "completed", "realm": realm,
                  "warmed": warmed, "skipped": skipped}
        logger.info("Warmed realm ships-pct realm=%s %s", realm, result)
        return result
    finally:
        cache.delete(lock_key)
        cache.delete(_realm_ships_pct_warm_dispatch_key(realm))


@app.task(bind=True, **TASK_OPTS)
def warm_player_distributions_task(self, realm=DEFAULT_REALM):
    from warships.data import warm_player_distributions

    logger.info("Starting warm_player_distributions_task realm=%s", realm)

    lock_key = _distribution_warm_lock_key(realm)
    if not cache.add(lock_key, self.request.id, timeout=DISTRIBUTION_WARM_LOCK_TIMEOUT):
        logger.info(
            "Skipping warm_player_distributions_task because another distribution warm is already running"
        )
        return {"status": "skipped", "reason": "already-running"}

    try:
        result = warm_player_distributions(realm=realm)
        logger.info("Finished warm_player_distributions_task: %s", result)
        return result
    finally:
        cache.delete(lock_key)


@app.task(bind=True, **TASK_OPTS)
def materialize_landing_player_best_snapshots_task(self, realm=DEFAULT_REALM, sorts=None, warm_after=True):
    from warships.landing import materialize_landing_player_best_snapshots

    logger.info(
        "Starting materialize_landing_player_best_snapshots_task realm=%s sorts=%s warm_after=%s",
        realm,
        sorts,
        warm_after,
    )

    lock_key = _landing_player_best_snapshot_refresh_lock_key(realm)
    if not cache.add(lock_key, self.request.id, timeout=LANDING_PLAYER_BEST_SNAPSHOT_REFRESH_LOCK_TIMEOUT):
        logger.info(
            "Skipping materialize_landing_player_best_snapshots_task because another snapshot refresh is already running"
        )
        return {"status": "skipped", "reason": "already-running"}

    normalized_sorts = None
    if isinstance(sorts, str):
        normalized_sorts = [sort.strip()
                            for sort in sorts.split(',') if sort.strip()]
    elif isinstance(sorts, (list, tuple)):
        normalized_sorts = list(sorts)

    try:
        result = materialize_landing_player_best_snapshots(
            realm=realm,
            sorts=normalized_sorts,
        )
        logger.info(
            "Finished materialize_landing_player_best_snapshots_task: %s",
            result,
        )
    finally:
        cache.delete(lock_key)

    # Republish the Redis Best-player payloads straight from the snapshot we just
    # materialized, so changes (notably new ship badges after the weekly ship
    # snapshot) reach the live API within seconds instead of waiting up to a full
    # landing-warmer cycle (~55 min). The warmer holds a *different* lock
    # (`_landing_page_warm_lock_key`); if an all-scope warm is already mid-flight
    # this players-scope republish no-ops and the in-flight/next warmer picks up
    # the fresh snapshot — a bounded ≤1-cycle fallback, never a stale strand.
    if warm_after:
        warm_landing_page_content_task.apply_async(
            kwargs={"realm": realm, "scope": "players"},
            queue="background",
        )

    return result


@app.task(bind=True, **TASK_OPTS)
def warm_player_correlations_task(self, realm=DEFAULT_REALM):
    from warships.data import warm_player_correlations

    logger.info("Starting warm_player_correlations_task realm=%s", realm)

    lock_key = _correlation_warm_lock_key(realm)
    if not cache.add(lock_key, self.request.id, timeout=CORRELATION_WARM_LOCK_TIMEOUT):
        logger.info(
            "Skipping warm_player_correlations_task because another correlation warm is already running"
        )
        return {"status": "skipped", "reason": "already-running"}

    try:
        result = warm_player_correlations(realm=realm)
        logger.info("Finished warm_player_correlations_task: %s", result)
        return result
    finally:
        cache.delete(lock_key)


@app.task(bind=True, **TASK_OPTS)
def warm_hot_entity_caches_task(self, player_limit=None, clan_limit=None, force_refresh=False, realm=DEFAULT_REALM):
    from warships.data import HOT_ENTITY_CLAN_LIMIT, HOT_ENTITY_PLAYER_LIMIT, warm_hot_entity_caches

    logger.info(
        "Starting warm_hot_entity_caches_task player_limit=%s clan_limit=%s force_refresh=%s realm=%s",
        player_limit,
        clan_limit,
        force_refresh,
        realm,
    )

    lock_key = _hot_entity_cache_warm_lock_key(realm)
    if not cache.add(lock_key, self.request.id, timeout=HOT_ENTITY_CACHE_WARM_LOCK_TIMEOUT):
        logger.info(
            "Skipping warm_hot_entity_caches_task because another hot cache warm is already running"
        )
        return {"status": "skipped", "reason": "already-running"}

    try:
        result = warm_hot_entity_caches(
            player_limit=int(player_limit or HOT_ENTITY_PLAYER_LIMIT),
            clan_limit=int(clan_limit or HOT_ENTITY_CLAN_LIMIT),
            force_refresh=bool(force_refresh),
            realm=realm,
        )
        logger.info("Finished warm_hot_entity_caches_task: %s", result)
        return result
    finally:
        cache.delete(lock_key)


@app.task(bind=True, **TASK_OPTS)
def warm_landing_best_entity_caches_task(self, player_limit=25, clan_limit=25, force_refresh=False, realm=DEFAULT_REALM):
    from warships.data import warm_landing_best_entity_caches

    logger.info(
        "Starting warm_landing_best_entity_caches_task player_limit=%s clan_limit=%s force_refresh=%s realm=%s",
        player_limit,
        clan_limit,
        force_refresh,
        realm,
    )

    lock_key = _landing_best_entity_warm_lock_key(realm)
    if not cache.add(lock_key, self.request.id, timeout=LANDING_BEST_ENTITY_WARM_LOCK_TIMEOUT):
        logger.info(
            "Skipping warm_landing_best_entity_caches_task because another landing best warm is already running"
        )
        return {"status": "skipped", "reason": "already-running"}

    try:
        result = warm_landing_best_entity_caches(
            player_limit=int(player_limit or 25),
            clan_limit=int(clan_limit or 25),
            force_refresh=bool(force_refresh),
            realm=realm,
        )
        logger.info("Finished warm_landing_best_entity_caches_task: %s", result)
        return result
    finally:
        cache.delete(lock_key)
        cache.delete(_landing_best_entity_warm_dispatch_key(realm))


@app.task(bind=True, **TASK_OPTS)
def bulk_load_entity_caches_task(self, realm=DEFAULT_REALM):
    from warships.data import bulk_load_entity_caches

    logger.info("Starting bulk_load_entity_caches_task realm=%s", realm)

    lock_key = _bulk_cache_load_lock_key(realm)
    if not cache.add(lock_key, self.request.id, timeout=BULK_CACHE_LOAD_LOCK_TIMEOUT):
        logger.info(
            "Skipping bulk_load_entity_caches_task because another bulk load is already running")
        return {"status": "skipped", "reason": "already-running"}

    try:
        result = bulk_load_entity_caches(realm=realm)
        logger.info("Finished bulk_load_entity_caches_task: %s", result)
        return result
    finally:
        cache.delete(lock_key)


@app.task(bind=True, **TASK_OPTS)
def warm_recently_viewed_players_task(self, realm=DEFAULT_REALM):
    from warships.data import warm_recently_viewed_players

    logger.info("Starting warm_recently_viewed_players_task realm=%s", realm)

    lock_key = _recently_viewed_warm_lock_key(realm)
    if not cache.add(lock_key, self.request.id, timeout=RECENTLY_VIEWED_WARM_LOCK_TIMEOUT):
        logger.info(
            "Skipping warm_recently_viewed_players_task because another run is already active")
        return {"status": "skipped", "reason": "already-running"}

    try:
        result = warm_recently_viewed_players(realm=realm)
        logger.info("Finished warm_recently_viewed_players_task: %s", result)
        return result
    finally:
        cache.delete(lock_key)


CLAN_TIER_DIST_WARM_TASK_OPTS = {
    "time_limit": 3 * 60 * 60,       # 3h hard limit
    "soft_time_limit": 2 * 60 * 60 + 45 * 60,  # 2h45m soft limit
    "ignore_result": True,
}


@app.task(bind=True, **CLAN_TIER_DIST_WARM_TASK_OPTS)
def warm_all_clan_tier_distributions_task(self, realm=DEFAULT_REALM):
    from warships.data import warm_all_clan_tier_distributions

    logger.info("Starting warm_all_clan_tier_distributions_task realm=%s", realm)

    lock_key = _clan_tier_dist_warm_lock_key(realm)
    if not cache.add(lock_key, self.request.id, timeout=CLAN_TIER_DIST_WARM_LOCK_TIMEOUT):
        logger.info(
            "Skipping warm_all_clan_tier_distributions_task — another run is already active")
        return {"status": "skipped", "reason": "already-running"}

    try:
        result = warm_all_clan_tier_distributions(realm=realm)
        logger.info(
            "Finished warm_all_clan_tier_distributions_task: %s", result)
        return result
    finally:
        cache.delete(lock_key)


@app.task(bind=True, **CRAWL_TASK_OPTS)
def crawl_all_clans_task(self, resume=True, dry_run=False, limit=None, realm=DEFAULT_REALM, core_only=False):
    from warships.clan_crawl import run_clan_crawl
    from warships.models import VALID_REALMS as _realms

    # R2: gut the expensive per-player enrichment (efficiency + achievements,
    # ~85% of the crawl's WG cost) that's redundant with the dedicated
    # enrichment crawler and makes the crawl hold its realm lock for hours,
    # pre-empting the battle-history floor. Read the flag here (not just the
    # arg) so BOTH the Beat schedule and the watchdog re-dispatch honour it.
    # Discovery (Player/Clan rows) + clan cached aggregates still run.
    core_only = core_only or os.getenv("CLAN_CRAWL_CORE_ONLY", "0") == "1"

    lock_key = _clan_crawl_lock_key(realm)
    heartbeat_key = _clan_crawl_heartbeat_key(realm)

    # This message is now being processed, so it is no longer "pending in the
    # queue" — clear the enqueue-dedup flag up front, BEFORE any early-return skip
    # path below, so the next daily/watchdog tick can enqueue this realm's next
    # pass instead of being suppressed by a stuck flag.
    cache.delete(_clan_crawl_pending_key(realm))

    # Cross-realm crawl mutex: limit concurrent full crawls to prevent OOM.
    active_crawl_realms = [
        r for r in sorted(_realms)
        if r != realm and cache.get(_clan_crawl_lock_key(r)) is not None
    ]
    if len(active_crawl_realms) >= MAX_CONCURRENT_REALM_CRAWLS:
        logger.warning(
            "Skipping crawl for realm=%s — %d other crawl(s) active: %s (max=%d)",
            realm, len(
                active_crawl_realms), active_crawl_realms, MAX_CONCURRENT_REALM_CRAWLS,
        )
        return {"status": "skipped", "reason": "cross-realm-mutex", "active": active_crawl_realms}

    if not cache.add(lock_key, self.request.id, timeout=CLAN_CRAWL_LOCK_TIMEOUT):
        logger.warning(
            "Skipping crawl_all_clans_task because another crawl is already running")
        return {"status": "skipped", "reason": "already-running"}

    pass_marker_key = _clan_crawl_pass_marker_key(realm)
    try:
        touch_clan_crawl_heartbeat(realm=realm)

        # Run-scoped resume. A full pass takes ~12-18h (post core_only; it was
        # ~14 days before R2 gutted the per-clan enrichment cost) and is regularly
        # interrupted by deploys (which SIGTERM the crawls worker) and the
        # per-task soft time limit. With acks_late the same crawl message is
        # redelivered on restart; honoring a stored pass marker lets that
        # redelivery (and the watchdog's resume=True re-dispatch) continue the
        # interrupted pass instead of restarting from clan 0 — which is why the
        # crawl was previously never finishing and holding its realm lock 24/7,
        # starving the observation floor + incrementals. A fresh pass (resume
        # False, or no marker yet) stamps a new marker so every clan is
        # re-crawled for periodic refresh. dry_run never touches the marker.
        fresh_after = None
        if not dry_run:
            stored_marker = cache.get(pass_marker_key)
            if resume and stored_marker is not None:
                fresh_after = stored_marker
                logger.info(
                    "Resuming clan crawl pass for realm=%s started at %s",
                    realm, fresh_after)
            else:
                fresh_after = django_timezone.now()
                logger.info(
                    "Starting fresh clan crawl pass for realm=%s at %s",
                    realm, fresh_after)
            cache.set(pass_marker_key, fresh_after,
                      timeout=CLAN_CRAWL_PASS_MARKER_TTL)

        logger.info(
            "Starting crawl_all_clans_task resume=%s dry_run=%s limit=%s realm=%s core_only=%s",
            resume,
            dry_run,
            limit,
            realm,
            core_only,
        )
        summary = run_clan_crawl(
            resume=resume,
            dry_run=dry_run,
            limit=limit,
            heartbeat_callback=lambda ts=None: touch_clan_crawl_heartbeat(
                timestamp=ts, realm=realm),
            realm=realm,
            core_only=core_only,
            fresh_after=fresh_after,
        )
        # A normal return means the pass walked the entire clan list, so emit
        # the per-pass yield snapshot (then clear) and clear the marker; the
        # next scheduled run starts a fresh full pass. An interrupting exception
        # (SoftTimeLimit / SIGTERM) skips this, leaving the marker so the
        # redelivered task resumes where this one stopped (and keeps the partial
        # yield aggregate accumulating into the same pass).
        if not dry_run:
            try:
                from warships.clan_crawl import emit_crawl_yield_snapshot
                emit_crawl_yield_snapshot(realm, fresh_after)
            except Exception:
                logger.warning(
                    "crawl-yield emit failed (realm=%s)", realm, exc_info=True)
            cache.delete(pass_marker_key)
        logger.info("Finished crawl_all_clans_task: %s", summary)
        return {"status": "completed", **summary}
    finally:
        cache.delete(lock_key)
        cache.delete(heartbeat_key)


def _enqueue_clan_crawl_if_absent(realm=DEFAULT_REALM, resume=True):
    """Enqueue crawl_all_clans_task for `realm` unless one is already running or
    already queued for that realm.

    Per-realm dedup: at most one crawl_all_clans_task per realm is ever in flight
    (queued or running), so the daily Beat cron and the 5-min watchdog can fire
    freely without stacking duplicate crawl messages behind the single-slot
    crawls worker. The `cache.add` pending flag is the atomic set-if-absent gate
    (cleared at task start); the lock check skips when the realm is already
    crawling. Returns an outcome string for logging/tests.
    See runbook-crawls-queue-depth-alarm-2026-06-12.md.
    """
    if cache.get(_clan_crawl_lock_key(realm)) is not None:
        return "skipped-running"
    if not cache.add(_clan_crawl_pending_key(realm), time.time(),
                     timeout=CLAN_CRAWL_PENDING_TTL):
        return "skipped-already-queued"
    crawl_all_clans_task.delay(resume=resume, realm=realm)
    return "enqueued"


@app.task(**TASK_OPTS)
def dispatch_clan_crawl_task(realm=DEFAULT_REALM):
    """Lightweight Beat entrypoint for the daily clan crawl.

    Runs on `default` (NOT the single-slot `crawls` queue) and enqueues the heavy
    crawl_all_clans_task only when one isn't already running/queued for the realm,
    so the daily schedule can't pile up duplicate crawl messages (the old
    behaviour that kept the crawls queue chronically above the healthcheck
    threshold). See runbook-crawls-queue-depth-alarm-2026-06-12.md.
    """
    outcome = _enqueue_clan_crawl_if_absent(realm, resume=True)
    logger.info("dispatch_clan_crawl_task realm=%s -> %s", realm, outcome)
    return {"status": outcome, "realm": realm}


@app.task(**TASK_OPTS)
def ensure_crawl_all_clans_running_task(realm=DEFAULT_REALM):
    from warships.models import VALID_REALMS

    heartbeat_key = _clan_crawl_heartbeat_key(realm)
    lock_key = _clan_crawl_lock_key(realm)
    pending_key = _clan_crawl_pending_key(realm)
    heartbeat = cache.get(heartbeat_key)
    lock_value = cache.get(lock_key)
    now_ts = time.time()

    if lock_value is not None:
        if _crawl_heartbeat_is_fresh(heartbeat, now_ts):
            logger.info(
                "Crawl watchdog found active crawl with fresh heartbeat")
            return {"status": "skipped", "reason": "running"}

        logger.warning(
            "Crawl watchdog found stale crawl lock; clearing it and resuming crawl")
        cache.delete(lock_key)
        cache.delete(heartbeat_key)
        # Route the resume through the dedup gate so a redelivered crawl message
        # already sitting in the queue isn't duplicated.
        outcome = _enqueue_clan_crawl_if_absent(realm, resume=True)
        return {"status": "scheduled", "reason": "stale-lock", "enqueue": outcome}

    # No lock for this realm. A pending flag here is normal while the realm waits
    # its turn behind another realm's pass (MAX_CONCURRENT_REALM_CRAWLS=1). But if
    # NO crawl is running on ANY realm and the flag is stale, the queued message
    # was lost (broker purge/restart/deploy) — clear it so the next tick can
    # re-enqueue instead of the realm staying wedged until the pending TTL.
    pending_ts = cache.get(pending_key)
    if pending_ts is not None:
        any_crawl_running = any(
            cache.get(_clan_crawl_lock_key(r)) is not None for r in VALID_REALMS)
        if (not any_crawl_running
                and now_ts - float(pending_ts) > CLAN_CRAWL_PENDING_STALE_AFTER):
            logger.warning(
                "Crawl watchdog clearing stale pending flag for realm=%s "
                "(no crawl running anywhere; queued message likely lost)", realm)
            cache.delete(pending_key)
            return {"status": "recovered", "reason": "stale-pending-cleared"}
        return {"status": "skipped", "reason": "pending"}

    logger.info(
        "Crawl watchdog found no active crawl; leaving the scheduler to start the next full crawl")
    return {"status": "skipped", "reason": "idle"}


@app.task(bind=True, **CRAWL_TASK_OPTS)
def incremental_player_refresh_task(self, realm=DEFAULT_REALM):
    if cache.get(_clan_crawl_lock_key(realm)) is not None:
        logger.info(
            "Skipping incremental_player_refresh_task because clan crawl is currently running"
        )
        return {"status": "skipped", "reason": "crawl-running"}

    lock_key = _player_refresh_lock_key(realm)
    if not cache.add(lock_key, self.request.id, timeout=PLAYER_REFRESH_LOCK_TIMEOUT):
        logger.info(
            "Skipping incremental_player_refresh_task because another player refresh is already running"
        )
        return {"status": "skipped", "reason": "already-running"}

    try:
        call_command(
            'incremental_player_refresh',
            state_file=os.getenv(
                'PLAYER_REFRESH_STATE_FILE', 'logs/incremental_player_refresh_state.json'),
            limit=int(os.getenv('PLAYER_REFRESH_TOTAL_LIMIT', '1200')),
            batch_size=int(os.getenv('PLAYER_REFRESH_BATCH_SIZE', '50')),
            hot_stale_hours=int(
                os.getenv('PLAYER_REFRESH_HOT_STALE_HOURS', '12')),
            active_stale_hours=int(
                os.getenv('PLAYER_REFRESH_ACTIVE_STALE_HOURS', '24')),
            warm_stale_hours=int(
                os.getenv('PLAYER_REFRESH_WARM_STALE_HOURS', '72')),
            active_limit=int(
                os.getenv('PLAYER_REFRESH_ACTIVE_LIMIT', '500')),
            warm_limit=int(
                os.getenv('PLAYER_REFRESH_WARM_LIMIT', '200')),
            hot_lookback_days=int(
                os.getenv('PLAYER_REFRESH_HOT_LOOKBACK_DAYS', '14')),
            active_lookback_days=int(
                os.getenv('PLAYER_REFRESH_ACTIVE_LOOKBACK_DAYS', '30')),
            warm_lookback_days=int(
                os.getenv('PLAYER_REFRESH_WARM_LOOKBACK_DAYS', '90')),
            max_errors=int(os.getenv('PLAYER_REFRESH_MAX_ERRORS', '25')),
            realm=realm,
        )
        return {"status": "completed"}
    finally:
        cache.delete(lock_key)


@app.task(bind=True, **CRAWL_TASK_OPTS)
def snapshot_active_players_task(self, realm=DEFAULT_REALM):
    """Write daily Snapshot rows for the active base.

    Deliberately COEXISTS with clan crawls (no deferral) — it is light
    (bulk account/info, ~1 WG call per 100 players) and must run every UTC
    day regardless of crawl windows so day-over-day tracking has no gaps.
    Idempotent per day: players already snapshotted today are skipped, so
    frequent runs converge on full coverage without redundant work.
    """
    if os.getenv("SNAPSHOT_ACTIVE_PLAYERS_ENABLED", "1") != "1":
        return {"status": "skipped", "reason": "disabled"}

    lock_key = _snapshot_active_players_lock_key(realm)
    if not cache.add(lock_key, self.request.id, timeout=PLAYER_REFRESH_LOCK_TIMEOUT):
        logger.info(
            "Skipping snapshot_active_players_task — another snapshot run is active for %s", realm)
        return {"status": "skipped", "reason": "already-running"}

    try:
        call_command(
            'snapshot_active_players',
            realm=realm,
            active_days=int(os.getenv('SNAPSHOT_ACTIVE_DAYS', '7')),
            limit=int(os.getenv('SNAPSHOT_ACTIVE_LIMIT', '3000')),
            min_battles=int(os.getenv('SNAPSHOT_ACTIVE_MIN_BATTLES', '0')),
            delay=float(os.getenv('SNAPSHOT_ACTIVE_DELAY', '0.2')),
        )
        return {"status": "completed"}
    finally:
        cache.delete(lock_key)


@app.task(bind=True, **TASK_OPTS)
def recapture_lapsed_players_task(self, realm=DEFAULT_REALM):
    """Cheap bulk re-discovery of returning ("lapsed") players.

    Dormant players (last battle > the floor's active window) fall out of the
    observation floor's scope, so a returning player stays invisible to battle
    capture until a profile view or clan crawl forces a refresh. This sweep
    re-checks the dormant pool via bulk account/info (~1 WG call per 100
    players); when a player's WG last_battle_time has advanced back inside
    active_7d, it rewrites last_battle_date so the *existing floor* harvests them
    next cycle (the "let the floor catch it" design — no new harvest path).

    Writes ONLY last_battle_date + days_since_last_battle (for returners) and
    last_idle_check_at (the LRU rotation cursor, for every checked row) — NEVER
    last_fetch, so the floor's real per-player refresh stays armed. Coexists with
    clan crawls (light, DB cost is minor). Two flags: RECAPTURE_LAPSED_ENABLED
    gates running at all; RECAPTURE_LAPSED_APPLY gates writes (off => detect-only
    yield measurement in prod before trusting writes). Sizing knobs:
    RECAPTURE_LAPSED_{MIN,MAX}_DAYS / _LIMIT / _DELAY.
    """
    if os.getenv("RECAPTURE_LAPSED_ENABLED", "0") != "1":
        return {"status": "skipped", "reason": "disabled"}

    lock_key = _recapture_lapsed_lock_key(realm)
    if not cache.add(lock_key, self.request.id, timeout=PLAYER_REFRESH_LOCK_TIMEOUT):
        logger.info(
            "Skipping recapture_lapsed_players_task — another run is active for %s", realm)
        return {"status": "skipped", "reason": "already-running"}

    try:
        kwargs = dict(
            realm=realm,
            min_days=int(os.getenv('RECAPTURE_LAPSED_MIN_DAYS', '8')),
            max_days=int(os.getenv('RECAPTURE_LAPSED_MAX_DAYS', '365')),
            limit=int(os.getenv('RECAPTURE_LAPSED_LIMIT', '30000')),
            delay=float(os.getenv('RECAPTURE_LAPSED_DELAY', '0.2')),
        )
        if os.getenv('RECAPTURE_LAPSED_APPLY', '0') == '1':
            kwargs['apply'] = True
        call_command('recapture_lapsed_players', **kwargs)
        return {"status": "completed"}
    finally:
        cache.delete(lock_key)


def _bulk_floor_active_for_realm(realm: str) -> bool:
    """Whether the bulk observation-floor path (R1) is enabled for `realm`.

    Master switch `BATTLE_OBSERVATION_FLOOR_BULK_ENABLED` defaults off; the
    realm must also be listed in `BATTLE_OBSERVATION_FLOOR_BULK_REALMS` (csv)
    so rollout is per-realm. Mirrors `_ranked_capture_active_for_realm`. Flag
    off => the legacy per-player floor runs, unchanged (instant rollback).
    """
    if os.getenv("BATTLE_OBSERVATION_FLOOR_BULK_ENABLED", "0") != "1":
        return False
    realms = {
        r.strip() for r in os.getenv(
            "BATTLE_OBSERVATION_FLOOR_BULK_REALMS", "",
        ).split(",") if r.strip()
    }
    return realm in realms


# Earliest 6h observation-floor slot per realm (mirrors the striped crontabs in
# signals.py: na :15 of 1/7/13/19h, eu 3/9/15/21h, asia 5/11/17/23h). The daily
# ranked sweep runs only on this slot.
_RANKED_DAILY_HOUR = {"na": 1, "eu": 3, "asia": 5}


def _is_ranked_daily_slot(realm: str) -> bool:
    """True when the current cycle is `realm`'s once-a-day ranked slot."""
    from django.utils import timezone as _tz
    return _tz.now().hour == _RANKED_DAILY_HOUR.get(realm, 1)


@app.task(bind=True, **CRAWL_TASK_OPTS)
def ensure_daily_battle_observations_task(self, realm=DEFAULT_REALM):
    """Daily floor for BattleObservation coverage on active-7d players.

    Walks `Player.objects.filter(realm=..., is_hidden=False,
    last_battle_date >= today - DAYS)` and dispatches a fresh observation
    for any whose latest BattleObservation is older than
    `BATTLE_OBSERVATION_FLOOR_HOURS` (default 8h, tightened from 22h on
    2026-05-09 alongside the promotion to a rolling 6-hourly Beat
    schedule). Sits alongside the tiered incremental crawler which is
    best-effort.

    Issues 2 WG calls/player when ranked capture is off for the realm,
    3 calls when it's on (random + ranked baseline rolled into the same
    observation).

    Crawl coexistence: a clan crawl holds its realm lock for hours per pass,
    so the floor used to *skip entirely* whenever a crawl was running — which
    starved active-player observations for days and produced lumpy battle
    history. Instead, when a crawl is running we still run the floor but at a
    slower per-player delay so combined WG load stays under the ~10 req/s app
    budget (the crawl paces ~4 req/s; running both at full tilt previously
    tripped a 407 REQUEST_LIMIT_EXCEEDED). See
    runbook-na-crawl-restart-loop-starves-refresh-2026-06-05.
    """
    crawl_running = cache.get(_clan_crawl_lock_key(realm)) is not None

    lock_key = _daily_observation_floor_lock_key(realm)
    if not cache.add(
        lock_key, self.request.id,
        timeout=DAILY_OBSERVATION_FLOOR_LOCK_TIMEOUT,
    ):
        logger.info(
            "Skipping ensure_daily_battle_observations_task because another floor sweep is already running"
        )
        return {"status": "skipped", "reason": "already-running"}

    try:
        from django.core.management import call_command
        bulk = _bulk_floor_active_for_realm(realm)
        if crawl_running:
            # Coexist with the crawl at a reduced pace instead of skipping.
            delay = float(os.getenv(
                "BATTLE_OBSERVATION_FLOOR_CRAWL_DELAY", "0.8"))
            limit = int(os.getenv(
                "BATTLE_OBSERVATION_FLOOR_CRAWL_LIMIT",
                os.getenv("BATTLE_OBSERVATION_FLOOR_LIMIT", "3000")))
            # Bulk path: raise per-chunk pacing while the crawl holds the lock.
            chunk_delay = float(os.getenv(
                "BATTLE_OBSERVATION_FLOOR_BULK_CRAWL_CHUNK_DELAY", "1.0"))
            logger.info(
                "Running observation floor in crawl-coexist mode "
                "(realm=%s, delay=%s, limit=%s, bulk=%s, chunk_delay=%s)",
                realm, delay, limit, bulk, chunk_delay)
        else:
            delay = float(os.getenv("BATTLE_OBSERVATION_FLOOR_DELAY", "0.3"))
            limit = int(os.getenv("BATTLE_OBSERVATION_FLOOR_LIMIT", "3000"))
            chunk_delay = float(os.getenv(
                "BATTLE_OBSERVATION_FLOOR_BULK_CHUNK_DELAY", "0.5"))
        kwargs = dict(
            realm=realm,
            days=int(os.getenv("BATTLE_OBSERVATION_FLOOR_DAYS", "7")),
            stale_hours=int(os.getenv("BATTLE_OBSERVATION_FLOOR_HOURS", "8")),
            limit=limit,
            delay=delay,
        )
        if bulk:
            # R1: random observations via the bulk path; ranked-known players
            # still go per-player inside the command. chunk_delay paces the
            # bulk sweep (per-chunk), delay paces the ranked subset (per-player).
            kwargs["bulk"] = True
            kwargs["chunk_delay"] = chunk_delay
            # Change-detector gate: only fetch per-player ships/stats for
            # players whose battle count moved (cuts the ~half wasted ships
            # calls). Separate flag so it can roll out / be measured on its own.
            if os.getenv(
                "BATTLE_OBSERVATION_FLOOR_CHANGE_GATE_ENABLED", "0") == "1":
                kwargs["change_gate"] = True
            # Ranked-sweep gate: skip the 3-WG-call ranked worker for ranked-
            # known players who haven't played since their last observation.
            if os.getenv(
                "BATTLE_OBSERVATION_FLOOR_RANKED_GATE_ENABLED", "0") == "1":
                kwargs["ranked_gate"] = True
            # Random-first routing: heavy ranked path only for current-season
            # players; everyone else (incl. lapsed ranked) takes the fast random
            # path so a niche mode stops throttling random coverage. Optional
            # per-realm gate (_REALMS csv) for a staged rollout; empty = all.
            if os.getenv(
                    "BATTLE_OBSERVATION_FLOOR_RANDOM_FIRST_ENABLED", "0") == "1":
                rf_realms = {
                    r.strip() for r in os.getenv(
                        "BATTLE_OBSERVATION_FLOOR_RANDOM_FIRST_REALMS", "",
                    ).split(",") if r.strip()
                }
                if not rf_realms or realm in rf_realms:
                    kwargs["random_first"] = True
            # Ranked sweep keeps its own modest bound as the random FLOOR_LIMIT
            # scales up (R3).
            kwargs["ranked_sweep_limit"] = int(os.getenv(
                "BATTLE_OBSERVATION_FLOOR_RANKED_SWEEP_LIMIT", "5000"))
            # Daily ranked cadence: ranked is niche/less time-sensitive, so run
            # the heavy per-player sweep only on the realm's earliest 6h slot,
            # not all four. Random keeps the full 6h cadence.
            if (os.getenv("BATTLE_OBSERVATION_FLOOR_RANKED_DAILY_ENABLED", "0")
                    == "1" and not _is_ranked_daily_slot(realm)):
                kwargs["skip_ranked"] = True
        call_command("ensure_daily_battle_observations", **kwargs)
        # Phase 2 (flag-gated): when self-chaining is on for this realm and no
        # crawl is competing for the WG budget, re-dispatch while a stale
        # backlog remains. This adaptively fills idle floor capacity and
        # naturally runs more often in the realm's post-peak window (where the
        # stale pool is largest), mirroring enrich_player_data_task. The fixed
        # Beat schedule stays as the guaranteed backstop. See F2/Phase-2 in
        # agents/runbooks/analysis-feed-schedule-optimization-2026-06-08.md.
        chained = False
        if not crawl_running and _floor_self_chain_enabled(realm):
            chained = _maybe_redispatch_floor(
                realm,
                int(os.getenv("BATTLE_OBSERVATION_FLOOR_DAYS", "7")),
                int(os.getenv("BATTLE_OBSERVATION_FLOOR_HOURS", "8")),
            )
        return {
            "status": "completed",
            "crawl_coexist": crawl_running,
            "bulk": bulk,
            "self_chained": chained,
        }
    finally:
        cache.delete(lock_key)


def _floor_self_chain_enabled(realm: str) -> bool:
    """Whether the observation floor should self-chain for `realm`.

    Gated by BATTLE_OBSERVATION_FLOOR_SELF_CHAIN_ENABLED, with an optional
    per-realm allowlist (BATTLE_OBSERVATION_FLOOR_SELF_CHAIN_REALMS csv, empty
    = all realms) for a staged NA-first rollout.
    """
    if os.getenv("BATTLE_OBSERVATION_FLOOR_SELF_CHAIN_ENABLED", "0") != "1":
        return False
    realms_csv = os.getenv(
        "BATTLE_OBSERVATION_FLOOR_SELF_CHAIN_REALMS", "").strip()
    if not realms_csv:
        return True
    gate = {r.strip() for r in realms_csv.split(",") if r.strip()}
    return realm in gate


def _floor_self_chain_interval(realm: str, base_interval: float) -> float:
    """Optionally shorten the re-dispatch countdown during the realm's busy
    hours so capture tightens when fresh battles are landing.

    Reads the persisted PlayerActivityHourly curve (F3). Scales the interval
    between 0.5× (at the realm's peak hour) and 1.0× (its quiet hours). A
    no-op (returns base_interval) when the curve is empty or unavailable.
    """
    try:
        from warships.models import PlayerActivityHourly
        rows = list(
            PlayerActivityHourly.objects.filter(realm=realm)
            .values_list("hour", "player_count"))
        if not rows:
            return base_interval
        peak = max((c for _, c in rows), default=0) or 1
        current_hour = django_timezone.now().hour
        current = next((c for h, c in rows if h == current_hour), 0)
        ratio = min(max(current / peak, 0.0), 1.0)
        return max(base_interval * (1.0 - 0.5 * ratio), base_interval * 0.5)
    except Exception:
        logger.exception(
            "Floor self-chain interval lookup failed (realm=%s)", realm)
        return base_interval


def _maybe_redispatch_floor(realm: str, days: int, stale_hours: int) -> bool:
    """Re-dispatch the observation floor for `realm` while a stale backlog
    remains. Returns True when a re-dispatch was enqueued.

    Bounded exactly like enrichment: the per-realm single-flight lock (released
    in the caller's finally before the countdown elapses) plus a min-interval
    countdown stop this from fanning out. Stops once the remaining stale pool
    falls below BATTLE_OBSERVATION_FLOOR_SELF_CHAIN_THRESHOLD.
    """
    try:
        from warships.management.commands.ensure_daily_battle_observations import (
            _candidates,
        )
        threshold = int(os.getenv(
            "BATTLE_OBSERVATION_FLOOR_SELF_CHAIN_THRESHOLD", "500"))
        # _candidates slices to `threshold`, so len == threshold means at least
        # `threshold` stale players still remain after the sweep just ran.
        remaining = len(_candidates(realm, days, stale_hours, threshold))
        if remaining < threshold:
            logger.info(
                "Floor self-chain stop (realm=%s, remaining=%d < %d)",
                realm, remaining, threshold)
            return False

        base_interval = float(os.getenv(
            "BATTLE_OBSERVATION_FLOOR_SELF_CHAIN_INTERVAL", "120"))
        interval = _floor_self_chain_interval(realm, base_interval)
        for attempt in range(3):
            try:
                ensure_daily_battle_observations_task.apply_async(
                    kwargs={"realm": realm}, countdown=interval)
                logger.info(
                    "Floor self-chain re-dispatched (realm=%s, %.0fs countdown, "
                    "remaining>=%d)", realm, interval, threshold)
                return True
            except Exception:
                wait = 5 * (attempt + 1)
                logger.warning(
                    "Floor self-chain dispatch failed (attempt %d/3), retry %ds",
                    attempt + 1, wait)
                time.sleep(wait)
        logger.error(
            "Floor self-chain re-dispatch failed after 3 attempts (realm=%s) — "
            "Beat kickstart will recover", realm)
        return False
    except Exception:
        logger.exception(
            "Floor self-chain check failed (realm=%s)", realm)
        return False


@app.task(bind=True, **CRAWL_TASK_OPTS)
def incremental_ranked_data_task(self, realm=DEFAULT_REALM):
    if cache.get(_clan_crawl_lock_key(realm)) is not None:
        logger.info(
            "Skipping incremental_ranked_data_task because clan crawl is currently running"
        )
        return {"status": "skipped", "reason": "crawl-running"}

    lock_key = _ranked_incremental_lock_key(realm)
    if not cache.add(lock_key, self.request.id, timeout=RANKED_INCREMENTAL_LOCK_TIMEOUT):
        logger.info(
            "Skipping incremental_ranked_data_task because another incremental ranked refresh is already running"
        )
        return {"status": "skipped", "reason": "already-running"}

    try:
        call_command(
            'incremental_ranked_data',
            state_file=os.getenv(
                'RANKED_INCREMENTAL_STATE_FILE', 'logs/incremental_ranked_data_state.json'),
            limit=int(os.getenv('RANKED_INCREMENTAL_LIMIT', '150')),
            batch_size=int(os.getenv('RANKED_INCREMENTAL_BATCH_SIZE', '50')),
            skip_fresh_hours=int(
                os.getenv('RANKED_INCREMENTAL_SKIP_FRESH_HOURS', '24')),
            known_limit=int(
                os.getenv('RANKED_INCREMENTAL_KNOWN_LIMIT', '300')),
            discovery_limit=int(
                os.getenv('RANKED_INCREMENTAL_DISCOVERY_LIMIT', '75')),
            recent_lookup_days=int(
                os.getenv('RANKED_INCREMENTAL_RECENT_LOOKUP_DAYS', '14')),
            recent_battle_days=int(
                os.getenv('RANKED_INCREMENTAL_RECENT_BATTLE_DAYS', '30')),
            min_discovery_pvp_battles=int(
                os.getenv('RANKED_INCREMENTAL_MIN_DISCOVERY_PVP_BATTLES', '1000')),
            max_errors=int(os.getenv('RANKED_INCREMENTAL_MAX_ERRORS', '25')),
            realm=realm,
        )
        return {"status": "completed"}
    finally:
        cache.delete(lock_key)


def _maybe_redispatch_enrichment(made_progress=True):
    """Check for remaining candidates and re-dispatch the enrichment task.

    Retries the broker dispatch up to 3 times with backoff to survive
    transient RabbitMQ blips after worker restarts.

    ``made_progress`` guards against an unbounded self-chain spin: when the
    just-completed batch changed zero state (every candidate skipped — nothing
    enriched, nothing marked empty), re-selecting candidates returns the SAME
    rows next pass (e.g. private-at-fetch ``PENDING``/``battles_json IS NULL``
    players that ``_candidates`` keeps surfacing but enrichment can't resolve).
    Self-chaining on those spins every ~37s burning WG calls + a background
    worker slot. Stop instead and let the 15-min Beat kickstart
    (``player-enrichment-kickstart``) retry. Real backlog produces
    enriched/empty > 0 and keeps the chain alive uninterrupted. See
    agents/runbooks/runbook-floor-throughput-tuning-2026-06-13.md.
    """
    try:
        if not made_progress:
            logger.info(
                "Enrichment batch made no progress (all candidates skipped) — "
                "not self-chaining; Beat kickstart will retry")
            return
        from warships.management.commands.enrich_player_data import _candidates
        from warships.models import VALID_REALMS as _realms
        remaining = sum(
            len(_candidates(r, int(os.getenv("ENRICH_MIN_PVP_BATTLES", "500")),
                            float(os.getenv("ENRICH_MIN_WR", "48.0")), 1))
            for r in sorted(_realms)
        )
        if remaining == 0:
            logger.info("Enrichment complete — no more candidates")
            return

        pause = float(os.getenv("ENRICH_PAUSE_BETWEEN_BATCHES", "10"))
        for attempt in range(3):
            try:
                enrich_player_data_task.apply_async(countdown=pause)
                logger.info(
                    "Enrichment re-dispatched (%.0fs countdown)", pause)
                return
            except Exception:
                wait = 5 * (attempt + 1)
                logger.warning(
                    "Broker dispatch failed (attempt %d/3), retrying in %ds",
                    attempt + 1, wait,
                )
                time.sleep(wait)
        logger.error(
            "Enrichment re-dispatch failed after 3 attempts — Beat kickstart will recover")
    except Exception:
        logger.exception("Failed to check for remaining enrichment candidates")


@app.task(bind=True, **CRAWL_TASK_OPTS)
def enrich_player_data_task(self):
    """Continuous background enrichment of players missing battle/ranked/snapshot data.

    Processes a batch of players (default 500), then immediately re-dispatches
    itself for the next batch.  Runs until no candidates remain — or a batch
    makes no progress (every candidate skipped; see _maybe_redispatch_enrichment)
    — then stops.  The Beat schedule or a deploy restart kicks it off again.

    Defers while a clan crawl is active to avoid competing for WG API rate
    limits.  Runs on the dedicated background worker so it never competes
    with user-facing tasks on the default/hydration queues.
    """
    # Acquire the single-flight lock FIRST. Duplicate dispatches (the 15-min
    # Beat kickstart, acks_late redelivery, and the startup-warmer kickstart can
    # coincide) return here without re-enqueuing — so deferrals can't fan out
    # into accumulating 300s-recurring chains (the 2026-05-27 ~1,190/hr churn
    # while a clan crawl was active). See runbook-db-cpu-saturation-2026-05-24.md.
    lock_key = _enrich_player_data_lock_key()
    if not cache.add(lock_key, self.request.id, timeout=ENRICH_PLAYER_DATA_LOCK_TIMEOUT):
        logger.info(
            "Skipping enrich_player_data_task — another enrichment is already running")
        return {"status": "skipped", "reason": "already-running"}

    # Coexist with clan crawls by default. The old blanket defer here predates
    # any per-request WG throttling and was starving backlog drain through
    # multi-day crawls — enrichment made ZERO progress for as long as a crawl
    # held its realm lock (the `pending` pool piled up while `enriched` stayed
    # flat). WG has rate headroom, so we run alongside the crawl but at a gentler
    # per-player delay (ENRICH_DELAY_DURING_CRAWL) to bound the combined request
    # rate and avoid 407s. Kill switch: ENRICH_DEFER_DURING_CRAWL=1 restores the
    # old defer-entirely behavior. The lock-first acquisition above still
    # prevents the 2026-05-27 deferral fan-out regardless of which branch runs.
    from warships.models import VALID_REALMS as _realms
    active_crawls = [
        r for r in sorted(_realms)
        if cache.get(_clan_crawl_lock_key(r)) is not None
    ]
    if active_crawls and os.getenv("ENRICH_DEFER_DURING_CRAWL", "0") == "1":
        cache.delete(lock_key)
        logger.info(
            "Deferring enrichment — clan crawl active for %s; Beat kickstart will retry",
            active_crawls,
        )
        return {"status": "deferred", "reason": "crawl-running", "active_crawls": active_crawls}

    summary = None
    try:
        from warships.management.commands.enrich_player_data import enrich_players

        batch_size = int(os.getenv("ENRICH_BATCH_SIZE", "500"))
        realms_env = os.getenv("ENRICH_REALMS", "").strip()
        realms = tuple(r.strip()
                       for r in realms_env.split(",") if r.strip()) or None
        # Gentler per-player pacing while a crawl shares the WG budget.
        delay = (float(os.getenv("ENRICH_DELAY_DURING_CRAWL", "0.5"))
                 if active_crawls else float(os.getenv("ENRICH_DELAY", "0.2")))
        if active_crawls:
            logger.info(
                "Enrichment coexisting with active clan crawl(s) %s (delay=%.2fs)",
                active_crawls, delay)
        summary = enrich_players(
            batch=batch_size,
            min_pvp_battles=int(os.getenv("ENRICH_MIN_PVP_BATTLES", "500")),
            min_wr=float(os.getenv("ENRICH_MIN_WR", "48.0")),
            delay=delay,
            realms=realms,
            heartbeat_callback=lambda: cache.set(
                lock_key, self.request.id, timeout=ENRICH_PLAYER_DATA_LOCK_TIMEOUT,
            ),
        )
        logger.info("enrich_player_data_task batch completed: %s", summary)
        return summary
    finally:
        cache.delete(lock_key)

        # Re-dispatch if there's more work to do.  The lock is released
        # above so the next invocation can acquire it cleanly. Skip the
        # self-chain when the batch changed zero state (every candidate
        # skipped) so we don't spin every ~37s on candidates that can't be
        # enriched — Beat kickstart still retries every 15 min. On an
        # exception (summary unbound) keep the old retry behavior.
        made_progress = (
            bool(summary.get("enriched") or summary.get("empty"))
            if isinstance(summary, dict) else True
        )
        _maybe_redispatch_enrichment(made_progress=made_progress)


# ---------------------------------------------------------------------------
# Incremental battle capture PoC (lil_boots tracking)
# Runbook: agents/runbooks/runbook-incremental-battle-poc-2026-04-27.md
# ---------------------------------------------------------------------------

POLL_TRACKED_BATTLES_LOCK_TIMEOUT = 5 * 60


def _battle_tracking_player_names() -> list[str]:
    raw_value = os.getenv("BATTLE_TRACKING_PLAYER_NAMES", "")
    return [name.strip() for name in raw_value.split(",") if name.strip()]


@app.task(bind=True, queue='background', **TASK_OPTS)
def poll_tracked_player_battles_task(self, player_id, realm=DEFAULT_REALM):
    """Poll WG for one tracked player, write an observation, diff vs prior."""
    from warships.incremental_battles import record_observation_and_diff

    logger.info(
        "Starting poll_tracked_player_battles_task for player_id=%s realm=%s",
        player_id,
        realm,
    )

    lock_key = _task_lock_key("poll_tracked_player_battles", player_id)
    if not cache.add(lock_key, self.request.id, timeout=POLL_TRACKED_BATTLES_LOCK_TIMEOUT):
        logger.info(
            "Skipping poll_tracked_player_battles for player_id=%s — already running",
            player_id,
        )
        return {"status": "skipped", "reason": "already-running"}

    try:
        return record_observation_and_diff(player_id=int(player_id), realm=realm)
    finally:
        cache.delete(lock_key)


@app.task(queue='background', **TASK_OPTS)
def dispatch_tracked_player_polls_task():
    """Beat-driven dispatcher: resolve BATTLE_TRACKING_PLAYER_NAMES → tasks.

    No-op when the env var is empty/unset, which is the default in production.
    """
    from django.db.models.functions import Lower

    from warships.models import Player

    names = _battle_tracking_player_names()
    if not names:
        return {"status": "skipped", "reason": "no-tracked-players"}

    name_set = {name.casefold() for name in names}
    players = list(
        Player.objects
        .alias(name_lower=Lower("name"))
        .filter(name_lower__in=name_set)
        .values("player_id", "realm", "name")
    )

    if not players:
        logger.warning(
            "BATTLE_TRACKING_PLAYER_NAMES set to %s but no Player rows matched", names)
        return {"status": "skipped", "reason": "no-matching-players"}

    dispatched = 0
    for player in players:
        try:
            poll_tracked_player_battles_task.delay(
                player_id=player["player_id"], realm=player["realm"])
            dispatched += 1
        except Exception:
            logger.exception(
                "Failed to dispatch poll_tracked_player_battles_task for %s", player)
    return {"status": "completed", "dispatched": dispatched, "tracked": len(players)}


@app.task(bind=True, queue='background', **TASK_OPTS)
def roll_up_player_daily_ship_stats_task(self, target_date_iso=None):
    """Nightly sweeper: rebuild PlayerDailyShipStats from BattleEvent rows.

    Self-healing trailing window — rebuilds the last
    BATTLE_HISTORY_ROLLUP_LOOKBACK_DAYS calendar days (default 3), not just
    yesterday, so a short outage (disabled gate / down worker / Beat misfire)
    no longer leaves a permanent hole: each nightly run re-closes the trailing
    window. Idempotent delete+rebuild makes re-running an already-correct day
    a no-op-equivalent. No-op when BATTLE_HISTORY_ROLLUP_ENABLED != 1.

    A single explicit `target_date_iso` collapses the window to that one day
    (manual single-date repair), matching the legacy behaviour.

    The weekly/monthly/yearly period rollup tier was removed 2026-06-15
    (DB-growth followup, step 2 KILL): the tables were dropped and the
    period writer deleted, so this task rebuilds the daily layer only.

    See agents/runbooks/runbook-battle-history-rollup-durability-2026-06-06.md.
    """
    if os.getenv("BATTLE_HISTORY_ROLLUP_ENABLED", "0") != "1":
        return {"status": "skipped", "reason": "rollup-disabled"}

    from datetime import datetime, timedelta, timezone as dt_timezone

    from warships.incremental_battles import rebuild_daily_ship_stats_for_date

    if target_date_iso:
        last_date = datetime.strptime(target_date_iso, "%Y-%m-%d").date()
        lookback = 1
    else:
        last_date = (
            datetime.now(dt_timezone.utc) - timedelta(days=1)
        ).date()
        lookback = max(
            1, int(os.getenv("BATTLE_HISTORY_ROLLUP_LOOKBACK_DAYS", "3")))

    # Oldest -> yesterday, so logs read in calendar order.
    dates = [
        last_date - timedelta(days=offset)
        for offset in range(lookback - 1, -1, -1)
    ]

    # Single-run global lock — the windowed run outlasts a single day and
    # could overlap a slow prior run. Lock auto-expires if the worker dies.
    lock_key = _task_lock_key("roll_up_player_daily_ship_stats", "global")
    if not cache.add(lock_key, self.request.id,
                     timeout=RESOURCE_TASK_LOCK_TIMEOUT):
        logger.info(
            "Skipping roll_up_player_daily_ship_stats_task — another run is active")
        return {"status": "skipped", "reason": "already-running"}

    try:
        logger.info(
            "Starting roll_up_player_daily_ship_stats_task window=%s..%s (%d days)",
            dates[0], dates[-1], len(dates))
        daily_results = [rebuild_daily_ship_stats_for_date(d) for d in dates]
        logger.info(
            "Finished roll_up_player_daily_ship_stats_task: days_rebuilt=%d",
            len(daily_results))
        return {
            "status": "completed",
            "days_rebuilt": len(daily_results),
            "daily": daily_results,
        }
    finally:
        cache.delete(lock_key)


@app.task(bind=True, queue='background', **TASK_OPTS)
def aggregate_player_activity_curve_task(self):
    """Nightly rebuild of the per-realm hour-of-day activity histogram.

    Buckets distinct players by the UTC hour of their `last_battle_time`
    over the trailing ACTIVITY_CURVE_WINDOW_DAYS window (from
    BattleObservation) and writes it to PlayerActivityHourly — the persisted
    activity curve that peak-aware scheduling reads (densest capture in the
    hours after each realm's peak, crawls parked in the trough). Idempotent
    delete-and-replace per realm. No-op unless ACTIVITY_CURVE_ENABLED=1.

    See agents/runbooks/analysis-feed-schedule-optimization-2026-06-08.md (F3).
    """
    if os.getenv("ACTIVITY_CURVE_ENABLED", "0") != "1":
        return {"status": "skipped", "reason": "activity-curve-disabled"}

    from datetime import timedelta

    from django.db.models import Count
    from django.db.models.functions import ExtractHour

    from warships.models import (
        BattleObservation, PlayerActivityHourly, VALID_REALMS)

    lock_key = _task_lock_key("aggregate_player_activity_curve", "global")
    if not cache.add(lock_key, self.request.id,
                     timeout=RESOURCE_TASK_LOCK_TIMEOUT):
        logger.info(
            "Skipping aggregate_player_activity_curve_task — another run is active")
        return {"status": "skipped", "reason": "already-running"}

    try:
        window_days = max(
            1, int(os.getenv("ACTIVITY_CURVE_WINDOW_DAYS", "7")))
        cutoff = django_timezone.now() - timedelta(days=window_days)
        rows = (
            BattleObservation.objects
            .filter(last_battle_time__isnull=False,
                    last_battle_time__gte=cutoff)
            .annotate(hour=ExtractHour('last_battle_time'))
            .values('player__realm', 'hour')
            .annotate(n=Count('player', distinct=True))
        )

        # Bucket into {realm: {hour: count}} so we can rebuild each realm
        # wholesale (a realm with no recent battles is left untouched rather
        # than wiped) and skip rows for unknown realms / null hours.
        by_realm: dict[str, dict[int, int]] = {}
        for row in rows:
            realm = row['player__realm']
            hour = row['hour']
            if realm not in VALID_REALMS or hour is None:
                continue
            by_realm.setdefault(realm, {})[int(hour)] = row['n']

        written = {}
        for realm, hour_counts in by_realm.items():
            PlayerActivityHourly.objects.filter(realm=realm).delete()
            PlayerActivityHourly.objects.bulk_create([
                PlayerActivityHourly(
                    realm=realm, hour=hour,
                    player_count=count, window_days=window_days)
                for hour, count in sorted(hour_counts.items())
            ])
            written[realm] = len(hour_counts)

        logger.info(
            "aggregate_player_activity_curve_task rebuilt window=%dd realms=%s",
            window_days, written)
        return {"status": "completed",
                "window_days": window_days, "realms": written}
    finally:
        cache.delete(lock_key)


@app.task(queue='background', **TASK_OPTS)
def reconcile_battle_history_rollup_task():
    """Alert-only reconciliation of the battle-history daily rollup.

    Compares SUM(BattleEvent.battles_delta) vs SUM(PlayerDailyShipStats.battles)
    per (date, mode) over an audit window and logs any date where BattleEvent
    has battles the daily layer is missing or under-counts. Writes nothing —
    repair beyond the self-heal window is the human-run
    `rebuild_player_daily_ship_stats` command.

    Gated by BATTLE_HISTORY_RECONCILE_ENABLED (default 0), INDEPENDENT of
    BATTLE_HISTORY_ROLLUP_ENABLED so it can surface "rollup is off / holes
    exist" even when the rollup gate is down. Audit window from
    BATTLE_HISTORY_RECONCILE_AUDIT_DAYS (default 30). See
    agents/runbooks/runbook-battle-history-rollup-durability-2026-06-06.md.
    """
    if os.getenv("BATTLE_HISTORY_RECONCILE_ENABLED", "0") != "1":
        return {"status": "skipped", "reason": "reconcile-disabled"}

    from warships.incremental_battles import reconcile_daily_rollup_coverage

    audit_days = max(
        1, int(os.getenv("BATTLE_HISTORY_RECONCILE_AUDIT_DAYS", "30")))
    report = reconcile_daily_rollup_coverage(audit_days=audit_days)
    discrepancies = report["discrepancies"]
    if discrepancies:
        for d in discrepancies:
            logger.warning(
                "battle-history rollup hole: date=%s mode=%s be_battles=%d "
                "pds_battles=%d delta=%d",
                d["date"], d["mode"], d["be_battles"], d["pds_battles"],
                d["delta"])
    else:
        logger.info(
            "battle-history rollup reconciliation clean over %d days",
            audit_days)
    return {"status": "completed", **report}


@app.task(bind=True, queue='background', **TASK_OPTS)
def prune_battle_observations_task(self):
    """Reclaim disk by compacting stale BattleObservation JSON payloads.

    NULLs `ships_stats_json` / `ranked_ships_stats_json` on observations no
    longer needed as a diff baseline (keeps the latest N per player + the
    latest non-NULL-ranked one), without deleting rows — see
    `warships.incremental_battles.compact_battle_observation_payloads` and
    `agents/runbooks/runbook-db-cpu-saturation-2026-05-24.md`.

    Gated by `BATTLE_OBSERVATION_COMPACT_ENABLED` (default off) so the
    schedule is wired but inert until an operator has dry-run and run it
    manually once. Single-run lock prevents overlap. Params come from env so
    they can be tuned without a redeploy.
    """
    if os.getenv("BATTLE_OBSERVATION_COMPACT_ENABLED", "0") != "1":
        return {"status": "skipped", "reason": "compaction-disabled"}

    lock_key = _task_lock_key("prune_battle_observations", "global")
    if not cache.add(lock_key, self.request.id,
                     timeout=DAILY_OBSERVATION_FLOOR_LOCK_TIMEOUT):
        logger.info(
            "Skipping prune_battle_observations_task — another run is active")
        return {"status": "skipped", "reason": "already-running"}

    try:
        from warships.incremental_battles import (
            compact_battle_observation_payloads,
        )
        result = compact_battle_observation_payloads(
            keep_per_player=int(
                os.getenv("BATTLE_OBSERVATION_COMPACT_KEEP", "3")),
            min_age_hours=int(
                os.getenv("BATTLE_OBSERVATION_COMPACT_MIN_AGE_HOURS", "0")),
            batch_size=int(
                os.getenv("BATTLE_OBSERVATION_COMPACT_BATCH_SIZE", "2000")),
            max_rows=int(
                os.getenv("BATTLE_OBSERVATION_COMPACT_MAX_ROWS", "0")),
            sleep_between_batches=float(
                os.getenv("BATTLE_OBSERVATION_COMPACT_SLEEP", "0.5")),
            statement_timeout_s=int(
                os.getenv("BATTLE_OBSERVATION_COMPACT_STATEMENT_TIMEOUT", "180")),
            dry_run=False,
        )
        logger.info("prune_battle_observations_task: %s", result)
        return result
    finally:
        cache.delete(lock_key)


@app.task(
    queue='background',
    time_limit=600,
    soft_time_limit=540,
    ignore_result=True,
)
def startup_warm_caches_task():
    """Run all startup cache warmers as a Celery task instead of a subprocess.

    Dispatched by gunicorn's when_ready hook so the warm runs inside an existing
    background worker rather than spawning a new Python process (~170-500 MB).
    """
    call_command('startup_warm_all_caches', '--delay', '0')

    # Kick off the continuous enrichment chain after startup warmers finish.
    # The task's own lock prevents duplicates if it's already running.
    try:
        enrich_player_data_task.apply_async(countdown=30)
        logger.info("Dispatched enrichment kickstart after startup warm")
    except Exception:
        logger.exception("Failed to dispatch enrichment kickstart")


# Lock must outlive the per-realm reclassify hard time_limit so a slow run can't
# lose its lock mid-pass and let a second invocation start on top of it.
ENRICHMENT_RECLASSIFY_LOCK_TIMEOUT = 25 * 60


@app.task(
    queue='background',
    time_limit=600,
    soft_time_limit=540,
    ignore_result=True,
)
def enrichment_pool_maintenance_task():
    """Daily DB-only re-queue of ``empty`` enrichment false-negatives.

    Runs ``retry_empty_enrichments --apply --retry-after-days N``: an ``empty`` row
    (``battles_json==[]``) is recorded when WG ``ships/stats`` returns no ships —
    overwhelmingly a *transient* failure or an account that was **private at fetch
    time**. Those are excluded from ``_candidates()`` and a reclassify keeps them
    ``empty``, so nothing else ever retries them. This re-queues them
    (``status→pending``, ``battles_json→NULL``). The ``--retry-after-days`` cooldown
    is the convergence guard: a genuinely-empty row is re-fetched at most once per N
    days (each empty write bumps ``battles_updated_at``), bounding WG burn, while an
    account that went public enriches and leaves the pool.

    Index-backed (``enrichment_status``) + no WG calls, so it's cheap and crawl-safe
    (never defers). The ``skipped_*`` drift rescue is a *separate* per-realm task
    (``enrichment_reclassify_drift_task``) — it's heavier and striped to avoid a
    single multi-realm DB burst. Kill switch ``ENRICHMENT_POOL_MAINTENANCE_ENABLED``
    (default on). See ``runbook-enrichment-pool-maintenance-2026-06-09.md``.
    """
    if os.getenv("ENRICHMENT_POOL_MAINTENANCE_ENABLED", "1") != "1":
        return {"status": "skipped", "reason": "disabled"}

    lock_key = _task_lock_key("enrichment_pool_maintenance", "global")
    if not cache.add(lock_key, "1", timeout=RESOURCE_TASK_LOCK_TIMEOUT):
        logger.info(
            "Skipping enrichment_pool_maintenance_task — another run is active")
        return {"status": "skipped", "reason": "already-running"}

    retry_after_days = int(os.getenv("ENRICHMENT_EMPTY_RETRY_AFTER_DAYS", "14"))
    try:
        buf = StringIO()
        call_command(
            "retry_empty_enrichments", "--apply",
            "--retry-after-days", str(retry_after_days), stdout=buf)
        logger.info(
            "enrichment_pool_maintenance retry_empty: %s",
            buf.getvalue().replace("\n", " | ").strip())
    except Exception:
        logger.exception(
            "enrichment_pool_maintenance retry_empty_enrichments failed")
        return {"status": "error", "retry_after_days": retry_after_days}
    finally:
        cache.delete(lock_key)

    return {"status": "ok", "retry_after_days": retry_after_days}


@app.task(
    bind=True,
    queue='background',
    time_limit=1200,        # 20 min hard — generous headroom over the heaviest realm
    soft_time_limit=1080,   # 18 min soft (eu measured ~11 min under load; variance is high)
    ignore_result=True,
)
def enrichment_reclassify_drift_task(self, realm=DEFAULT_REALM):
    """Incremental ``skipped_*`` drift rescue for one realm — recompute
    ``enrichment_status`` for rows fetched within the recency window.

    Drift-relevant fields (is_hidden / pvp_battles / pvp_ratio /
    days_since_last_battle) only change on a WG re-fetch, which bumps ``last_fetch``,
    so reclassifying rows with ``last_fetch >= now - H hours`` covers every row that
    could have **newly** drifted (un-hidden, 500-battle crossers, WR recoveries).
    Index-backed via ``player_last_fetch_idx`` (BitmapAnd with the realm/battles
    index — verified by EXPLAIN). ~2.5–6 min/realm depending on load, vs ~36 min for
    the full catalog.

    Scheduled **per realm, striped** (signals.py) rather than one multi-realm task,
    so the DB sees ~6 min of scan at a time instead of an ~18 min continuous burst on
    the 1-vCPU PG. DB-only, crawl-safe. Does NOT catch pure-calendar inactivity
    crossings (no re-fetch) or the one-time pre-existing backlog (rows not fetched in
    H hours) — those need a supervised full ``reclassify``. Kill switch
    ``ENRICHMENT_POOL_MAINTENANCE_ENABLED``.
    """
    if os.getenv("ENRICHMENT_POOL_MAINTENANCE_ENABLED", "1") != "1":
        return {"status": "skipped", "reason": "disabled"}

    lock_key = _task_lock_key("enrichment_reclassify_drift", realm)
    if not cache.add(lock_key, self.request.id or "1",
                     timeout=ENRICHMENT_RECLASSIFY_LOCK_TIMEOUT):
        logger.info(
            "Skipping enrichment_reclassify_drift_task[%s] — another run is active",
            realm)
        return {"status": "skipped", "reason": "already-running", "realm": realm}

    recent_hours = int(os.getenv("ENRICHMENT_RECLASSIFY_RECENT_HOURS", "25"))
    # Per-statement blast-radius cap. Sized well above a single bucket UPDATE's real
    # cost (~2-3 min under load) so it caps a runaway without aborting normal work —
    # 120s was too tight and silently rolled back the whole pass. statement_timeout
    # is a session GUC (call_command opens its own txns), so set up front + RESET in
    # finally so it can't leak to the next task. Postgres-only.
    timeout_s = int(os.getenv("ENRICHMENT_RECLASSIFY_STATEMENT_TIMEOUT", "420"))
    is_postgres = connection.vendor == "postgresql"
    if is_postgres:
        with connection.cursor() as cur:
            cur.execute("SET statement_timeout = %s", [timeout_s * 1000])
    try:
        buf = StringIO()
        call_command(
            "reclassify_enrichment_status", "--realm", realm,
            "--recent-hours", str(recent_hours), stdout=buf)
        logger.info(
            "enrichment_reclassify_drift[%s]: %s",
            realm, buf.getvalue().replace("\n", " | ").strip())
    except Exception:
        logger.exception(
            "enrichment_reclassify_drift_task failed for %s", realm)
        return {"status": "error", "realm": realm}
    finally:
        if is_postgres:
            try:
                with connection.cursor() as cur:
                    cur.execute("RESET statement_timeout")
            except Exception:
                logger.exception(
                    "enrichment_reclassify_drift failed to reset statement_timeout")
        cache.delete(lock_key)

    return {"status": "ok", "realm": realm, "recent_hours": recent_hours}


@app.task(
    queue='background',
    time_limit=600,
    soft_time_limit=540,
    ignore_result=True,
)
def maintain_hot_players_task(realm=DEFAULT_REALM):
    """Daily DB-only promote/evict/re-score of the engagement-capture queue.

    The "brain" of the Hot-Players loop: mirrors ``enrichment_pool_maintenance_task``
    (pure DB, no WG calls, coexists with crawls). Computes an active-days
    ``GROUP BY`` over ``EntityVisitDaily`` (recurrence across distinct days, NOT
    summed views) for the trailing ``HOT_PLAYERS_WINDOW_DAYS`` and applies the
    promotion rule, the eviction rule (with hysteresis), and the
    ``HOT_PLAYERS_MAX`` cap/trim by ``hot_score``. Kill switch
    ``HOT_PLAYERS_ENABLED`` (default on). Runbook:
    ``agents/runbooks/runbook-hot-players-engagement-queue-2026-06-10.md``.
    """
    if os.getenv("HOT_PLAYERS_ENABLED", "1") != "1":
        return {"status": "skipped", "reason": "disabled"}

    lock_key = _task_lock_key("maintain_hot_players", realm)
    if not cache.add(lock_key, "1", timeout=RESOURCE_TASK_LOCK_TIMEOUT):
        logger.info(
            "Skipping maintain_hot_players_task[%s] — another run is active", realm)
        return {"status": "skipped", "reason": "already-running", "realm": realm}

    try:
        from warships.hot_players import maintain_hot_players
        result = maintain_hot_players(realm)
    except Exception:
        logger.exception("maintain_hot_players_task failed for %s", realm)
        return {"status": "error", "realm": realm}
    finally:
        cache.delete(lock_key)

    return {"status": "ok", **result}


@app.task(bind=True, queue='background', **TASK_OPTS)
def capture_hot_player_observations_task(self, realm=DEFAULT_REALM):
    """Sweep the HotPlayer set, guaranteeing the two daily capture artifacts.

    The "hands" of the Hot-Players loop. For each hot member: skip-if-fresh
    against the latest ``BattleObservation`` within ``HOT_OBSERVE_FLOOR_HOURS``
    (the observation floor already covers active hot players, so this costs them
    nothing) else ``record_observation_and_diff``; and write a gap-free daily
    ``Snapshot`` via ``update_snapshot_data(refresh_player=False)`` when today's
    row is missing. Bounded by ``HOT_PLAYERS_MAX``, single-flight per realm,
    paced by ``HOT_PLAYERS_CAPTURE_DELAY``. **Coexists with clan crawls** (no
    deferral) — guaranteed coverage is the whole point. Hidden accounts return
    nothing from WG and are recorded as skipped (no retry storm). Kill switch
    ``HOT_PLAYERS_ENABLED``. Runbook:
    ``agents/runbooks/runbook-hot-players-engagement-queue-2026-06-10.md``.
    """
    if os.getenv("HOT_PLAYERS_ENABLED", "1") != "1":
        return {"status": "skipped", "reason": "disabled"}

    lock_key = _hot_players_capture_lock_key(realm)
    if not cache.add(lock_key, self.request.id or "1",
                     timeout=RESOURCE_TASK_LOCK_TIMEOUT):
        logger.info(
            "Skipping capture_hot_player_observations_task[%s] — another sweep is active",
            realm)
        return {"status": "skipped", "reason": "already-running", "realm": realm}

    try:
        from warships.hot_players import capture_hot_players
        result = capture_hot_players(realm)
    except Exception:
        logger.exception(
            "capture_hot_player_observations_task failed for %s", realm)
        return {"status": "error", "realm": realm}
    finally:
        cache.delete(lock_key)

    return {"status": "ok", **result}
