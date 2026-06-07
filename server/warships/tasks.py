from __future__ import absolute_import, unicode_literals
import logging
import os
import time

from django.core.cache import cache
from django.core.management import call_command
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
CLAN_CRAWL_LOCK_TIMEOUT = 8 * 60 * 60
CLAN_CRAWL_HEARTBEAT_STALE_AFTER = 15 * 60
# Run-scoped resume marker: timestamp at which the current full crawl pass
# began. A pass takes ~14 days; the marker must outlive that plus restart gaps,
# so give it a generous TTL well beyond a single pass. Cleared on pass
# completion so the next scheduled pass starts fresh. See
# runbook-na-crawl-restart-loop-starves-refresh.
CLAN_CRAWL_PASS_MARKER_TTL = 21 * 24 * 60 * 60
RESOURCE_TASK_LOCK_TIMEOUT = 15 * 60
RANKED_INCREMENTAL_LOCK_TIMEOUT = 6 * 60 * 60
PLAYER_REFRESH_LOCK_TIMEOUT = 6 * 60 * 60
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
LANDING_RECENT_PLAYERS_WARM_LOCK_TIMEOUT = 15 * 60
LANDING_PLAYER_BEST_SNAPSHOT_REFRESH_LOCK_TIMEOUT = 2 * 60 * 60
DISTRIBUTION_WARM_LOCK_TIMEOUT = 15 * 60
CORRELATION_WARM_LOCK_TIMEOUT = 20 * 60
CORRELATION_WARM_DISPATCH_TIMEOUT = 30  # Matches landing — coalesces cold-cache fanout
HOT_ENTITY_CACHE_WARM_LOCK_TIMEOUT = 30 * 60
LANDING_BEST_ENTITY_WARM_LOCK_TIMEOUT = 30 * 60
LANDING_BEST_ENTITY_WARM_DISPATCH_TIMEOUT = 5 * 60
CLAN_BATTLE_SUMMARY_REFRESH_DISPATCH_TIMEOUT = 10 * 60
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


def _ranked_incremental_lock_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:incremental_ranked_data:{realm}:lock"


def _player_refresh_lock_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:incremental_player_refresh:{realm}:lock"


def _daily_observation_floor_lock_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:daily_observation_floor:{realm}:lock"


def _landing_page_warm_lock_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:warm_landing_page_content:{realm}:lock"


def _landing_page_warm_dispatch_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:warm_landing_page_content:{realm}:dispatch"


def _landing_recent_players_warm_lock_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:warm_landing_recent_players:{realm}:lock"


def _distribution_warm_lock_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:warm_player_distributions:{realm}:lock"


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


def queue_landing_page_warm(realm: str = DEFAULT_REALM, include_recent: bool = True, scope: str = 'all'):
    # If a warm is already executing for this realm, skip enqueue. The 30s
    # dispatch dedup expires while the 1200s task runs, so without this gate,
    # cache-fallback paths invoked from inside the warm itself would re-enqueue
    # in a loop (root cause of the 4581-message background-queue pileup
    # observed on 2026-04-27).
    #
    # `include_recent=False` is used by the invalidation-driven republish path
    # (clan/player writes). Those writes don't change the recent-players 7-day
    # rollup, but rebuilding it force-refreshes the 25s `week_battles` aggregate
    # on every ~120s crawl-driven republish — which saturated the 1-vCPU DB on
    # 2026-05-27 (~20 warms/40min during the ASIA crawl). The recent surfaces
    # stay fresh via the scheduled beat warmers instead.
    # See agents/runbooks/runbook-db-cpu-saturation-2026-05-24.md.
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
        warm_landing_page_content_task.delay(include_recent=include_recent, realm=realm, scope=scope)
        return {"status": "queued"}
    except Exception as error:
        cache.delete(dispatch_key)
        logger.warning(
            "Skipping landing page warm enqueue because broker dispatch failed: %s",
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

    return _run_locked_task(
        "update_player_data",
        player_id,
        self.request.id,
        _refresh_player,
    )


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
    """Per-realm T10 top-player snapshot for the just-closed fixed 2-week season.

    No-op unless SHIP_BADGE_SNAPSHOT_ENABLED=1 (the rollout switch). The beat
    schedule fires weekly (Monday), but the task self-gates on a *season
    boundary* (`is_season_boundary`) so it effectively runs bi-weekly — only the
    Monday a season closes — and finalizes that completed season. Delegates to
    data.compute_ship_top_player_snapshot (default window = most recently
    completed season). See agents/runbooks/runbook-ship-top-player-badges-2026-06-05.md.
    """
    if os.getenv("SHIP_BADGE_SNAPSHOT_ENABLED", "0") != "1":
        logger.info(
            "snapshot_ship_top_players_task skipped "
            "(SHIP_BADGE_SNAPSHOT_ENABLED!=1) realm=%s", realm)
        return {"status": "disabled", "realm": realm}

    from warships.data import compute_ship_top_player_snapshot, is_season_boundary

    # Only finalize on the day a season closes; weekly beat ticks in between are
    # no-ops so each completed season is written exactly once.
    if not is_season_boundary():
        logger.info(
            "snapshot_ship_top_players_task skipped "
            "(not a season boundary) realm=%s", realm)
        return {"status": "skipped", "reason": "not-a-season-boundary",
                "realm": realm}

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
def warm_landing_page_content_task(self, include_recent=True, realm=DEFAULT_REALM, scope='all'):
    from warships.landing import warm_landing_page_content

    logger.info(
        "Starting warm_landing_page_content_task include_recent=%s realm=%s scope=%s",
        include_recent,
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
            include_recent=bool(include_recent),
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
    """Pre-populate the realm top-ships treemap caches (random + ranked).

    Recomputes both modes for the realm once per day (force-refresh) and writes
    them to Redis under the season-tagged key, so the first visitor hits a warm
    cache instead of the aggregation. The treemap is a static per-season count
    over the most recently completed fixed 2-week ship season; the daily warm
    keeps the cache fresh across a season boundary (the key changes when the
    completed season advances). Mirrors the other per-realm landing warmers.
    """
    from warships.data import compute_realm_top_ships

    lock_key = f"warships:tasks:warm_realm_top_ships:{realm}:lock"
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
        logger.info("Warmed top-ships realm=%s modes=%s", realm, results)
        return {"status": "completed", "realm": realm, "results": results}
    finally:
        cache.delete(lock_key)


@app.task(bind=True, **TASK_OPTS)
def warm_landing_recent_players_task(self, realm=DEFAULT_REALM):
    # Rebuilds the landing "recent players" rollup (top 7-day random-battles
    # leaders) and writes BOTH the durable DB snapshot
    # (`LandingRecentPlayersSnapshot`) and the Redis cache. Reads never
    # block on this — they hit Redis (Tier 1), fall back to the DB
    # snapshot (Tier 2) if Redis was evicted, and only run an inline
    # rebuild (Tier 3) when both stores are cold. Scheduled every 3h via
    # `recent-players-warmer-{realm}`.
    from warships.landing import materialize_landing_recent_players_snapshot

    logger.info("Starting warm_landing_recent_players_task realm=%s", realm)

    lock_key = _landing_recent_players_warm_lock_key(realm)
    if not cache.add(lock_key, self.request.id, timeout=LANDING_RECENT_PLAYERS_WARM_LOCK_TIMEOUT):
        logger.info(
            "Skipping warm_landing_recent_players_task because another rebuild is already running"
        )
        return {"status": "skipped", "reason": "already-running"}

    try:
        meta = materialize_landing_recent_players_snapshot(realm=realm)
        result = {
            "status": "completed",
            "rows": meta["count"],
            "realm": realm,
            "generated_at": meta["generated_at"],
        }
        logger.info("Finished warm_landing_recent_players_task: %s", result)
        return result
    finally:
        cache.delete(lock_key)


@app.task(bind=True, **TASK_OPTS)
def warm_landing_recent_clans_task(self, realm=DEFAULT_REALM):
    """Rebuild the landing "recent clans" payload out-of-band so reads stay warm.

    `get_landing_recent_clans_payload` lazily rebuilds (a multi-second Clan
    aggregation) on cache miss / TTL expiry / dirty-invalidation — and without
    this warmer that cold rebuild lands on a user request, delaying the landing
    clan chart (and, before the fetch decoupling, the player chart too). Mirrors
    the recent-players warmer; force_refresh ignores the dirty flag.
    """
    from warships.landing import get_landing_recent_clans_payload

    lock_key = f"warships:tasks:warm_landing_recent_clans:{realm}:lock"
    if not cache.add(lock_key, self.request.id, timeout=300):
        logger.info(
            "Skipping warm_landing_recent_clans_task realm=%s — already running", realm)
        return {"status": "skipped", "reason": "already-running"}

    try:
        payload = get_landing_recent_clans_payload(force_refresh=True, realm=realm)
        result = {"status": "completed", "rows": len(payload), "realm": realm}
        logger.info("Finished warm_landing_recent_clans_task: %s", result)
        return result
    finally:
        cache.delete(lock_key)


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
            kwargs={"include_recent": False, "realm": realm, "scope": "players"},
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

    lock_key = _clan_crawl_lock_key(realm)
    heartbeat_key = _clan_crawl_heartbeat_key(realm)

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

        # Run-scoped resume. A full pass takes ~14 days and is regularly
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
        # A normal return means the pass walked the entire clan list, so clear
        # the marker; the next scheduled run starts a fresh full pass. An
        # interrupting exception (SoftTimeLimit / SIGTERM) skips this, leaving
        # the marker so the redelivered task resumes where this one stopped.
        if not dry_run:
            cache.delete(pass_marker_key)
        logger.info("Finished crawl_all_clans_task: %s", summary)
        return {"status": "completed", **summary}
    finally:
        cache.delete(lock_key)
        cache.delete(heartbeat_key)


@app.task(**TASK_OPTS)
def ensure_crawl_all_clans_running_task(realm=DEFAULT_REALM):
    heartbeat_key = _clan_crawl_heartbeat_key(realm)
    lock_key = _clan_crawl_lock_key(realm)
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
        crawl_all_clans_task.delay(resume=True, realm=realm)
        return {"status": "scheduled", "reason": "stale-lock"}

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
        call_command("ensure_daily_battle_observations", **kwargs)
        return {
            "status": "completed",
            "crawl_coexist": crawl_running,
            "bulk": bulk,
        }
    finally:
        cache.delete(lock_key)


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


def _maybe_redispatch_enrichment():
    """Check for remaining candidates and re-dispatch the enrichment task.

    Retries the broker dispatch up to 3 times with backoff to survive
    transient RabbitMQ blips after worker restarts.
    """
    try:
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
    itself for the next batch.  Runs until no candidates remain, then stops.
    The Beat schedule or a deploy restart kicks it off again.

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

    # Defer while a clan crawl is active (shared WG API rate limit). We hold the
    # lock, so release it and return WITHOUT re-enqueuing: the every-15-min Beat
    # kickstart (player-enrichment-kickstart) is the retry, bounding deferrals to
    # one no-op per cycle instead of a self-multiplying chain. (Benign race: a
    # crawl may start between this check and enrich_players below; that batch
    # competes for the WG budget once and the next dispatch defers. The 6h lock
    # TTL + heartbeat lets a long-running batch hold the lock safely.)
    from warships.models import VALID_REALMS as _realms
    active_crawls = [
        r for r in sorted(_realms)
        if cache.get(_clan_crawl_lock_key(r)) is not None
    ]
    if active_crawls:
        cache.delete(lock_key)
        logger.info(
            "Deferring enrichment — clan crawl active for %s; Beat kickstart will retry",
            active_crawls,
        )
        return {"status": "deferred", "reason": "crawl-running", "active_crawls": active_crawls}

    try:
        from warships.management.commands.enrich_player_data import enrich_players

        batch_size = int(os.getenv("ENRICH_BATCH_SIZE", "500"))
        realms_env = os.getenv("ENRICH_REALMS", "").strip()
        realms = tuple(r.strip()
                       for r in realms_env.split(",") if r.strip()) or None
        summary = enrich_players(
            batch=batch_size,
            min_pvp_battles=int(os.getenv("ENRICH_MIN_PVP_BATTLES", "500")),
            min_wr=float(os.getenv("ENRICH_MIN_WR", "48.0")),
            delay=float(os.getenv("ENRICH_DELAY", "0.2")),
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
        # above so the next invocation can acquire it cleanly.
        _maybe_redispatch_enrichment()


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

    The weekly/monthly/yearly period rebuild is gated OFF by default
    (BATTLE_HISTORY_PERIOD_ROLLUP_ENABLED) — those tiers are dormant + UI-hidden
    and the yearly-YTD aggregate is the long pole that blew the 540s soft time
    limit, while the daily layer (consumed + reconciled) completes in seconds.

    See agents/runbooks/runbook-battle-history-rollup-durability-2026-06-06.md.
    """
    if os.getenv("BATTLE_HISTORY_ROLLUP_ENABLED", "0") != "1":
        return {"status": "skipped", "reason": "rollup-disabled"}

    from datetime import datetime, timedelta, timezone as dt_timezone

    from warships.incremental_battles import (
        rebuild_daily_ship_stats_for_date,
        rebuild_period_rollups_for_window,
    )

    if target_date_iso:
        last_date = datetime.strptime(target_date_iso, "%Y-%m-%d").date()
        lookback = 1
    else:
        last_date = (
            datetime.now(dt_timezone.utc) - timedelta(days=1)
        ).date()
        lookback = max(
            1, int(os.getenv("BATTLE_HISTORY_ROLLUP_LOOKBACK_DAYS", "3")))

    # Oldest -> yesterday, so logs and period dedup read in calendar order.
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
        # Cascade into the weekly / monthly / yearly tiers covering the window,
        # each distinct period rebuilt once. Gated OFF by default: those tiers
        # are dormant + UI-hidden and the yearly-YTD aggregate is the long pole
        # that exceeded the 540s soft time limit, whereas the daily layer above
        # finishes in seconds. Flip BATTLE_HISTORY_PERIOD_ROLLUP_ENABLED=1 to
        # refresh the coarser tiers (pair with the deferred DB-side rewrite when
        # they are reactivated for the UI).
        if os.getenv("BATTLE_HISTORY_PERIOD_ROLLUP_ENABLED", "0") == "1":
            period_result = rebuild_period_rollups_for_window(dates)
            logger.info(
                "Finished roll_up_player_daily_ship_stats_task: days_rebuilt=%d "
                "periods_rebuilt=w%d/m%d/y%d",
                len(daily_results),
                period_result["weeks_rebuilt"],
                period_result["months_rebuilt"],
                period_result["years_rebuilt"])
        else:
            period_result = {"status": "skipped",
                             "reason": "period-rollup-disabled"}
            logger.info(
                "Finished roll_up_player_daily_ship_stats_task: days_rebuilt=%d "
                "period_rebuild=skipped (BATTLE_HISTORY_PERIOD_ROLLUP_ENABLED!=1)",
                len(daily_results))
        return {
            "status": "completed",
            "days_rebuilt": len(daily_results),
            "daily": daily_results,
            "period": period_result,
        }
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
