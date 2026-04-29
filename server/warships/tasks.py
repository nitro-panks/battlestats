from __future__ import absolute_import, unicode_literals
import logging
import os
import time

from django.core.cache import cache
from django.core.management import call_command

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
RESOURCE_TASK_LOCK_TIMEOUT = 15 * 60
RANKED_INCREMENTAL_LOCK_TIMEOUT = 6 * 60 * 60
PLAYER_REFRESH_LOCK_TIMEOUT = 6 * 60 * 60
RANKED_REFRESH_DISPATCH_TIMEOUT = 15 * 60
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
HOT_ENTITY_CACHE_WARM_LOCK_TIMEOUT = 30 * 60
LANDING_BEST_ENTITY_WARM_LOCK_TIMEOUT = 30 * 60
LANDING_BEST_ENTITY_WARM_DISPATCH_TIMEOUT = 5 * 60
CLAN_BATTLE_SUMMARY_REFRESH_DISPATCH_TIMEOUT = 10 * 60
LANDING_RANDOM_PLAYER_QUEUE_REFILL_DISPATCH_TIMEOUT = 10 * 60
LANDING_RANDOM_CLAN_QUEUE_REFILL_DISPATCH_TIMEOUT = 10 * 60
BULK_CACHE_LOAD_LOCK_TIMEOUT = 30 * 60
RECENTLY_VIEWED_WARM_LOCK_TIMEOUT = 15 * 60
CLAN_TIER_DIST_WARM_LOCK_TIMEOUT = 3 * 60 * 60  # 3h — iterates all clans
ENRICH_PLAYER_DATA_LOCK_TIMEOUT = 6 * 60 * 60


MAX_CONCURRENT_REALM_CRAWLS = int(
    os.getenv("MAX_CONCURRENT_REALM_CRAWLS", "1"))


# ---------------------------------------------------------------------------
# Realm-scoped lock / dispatch / heartbeat key helpers
# ---------------------------------------------------------------------------

def _clan_crawl_lock_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:crawl_all_clans:{realm}:lock"


def _clan_crawl_heartbeat_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:crawl_all_clans:{realm}:heartbeat"


def _ranked_incremental_lock_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:incremental_ranked_data:{realm}:lock"


def _player_refresh_lock_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:incremental_player_refresh:{realm}:lock"


def _landing_page_warm_lock_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:warm_landing_page_content:{realm}:lock"


def _landing_page_warm_dispatch_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:warm_landing_page_content:{realm}:dispatch"


def _distribution_warm_lock_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:warm_player_distributions:{realm}:lock"


def _landing_player_best_snapshot_refresh_lock_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:materialize_landing_player_best_snapshots:{realm}:lock"


def _correlation_warm_lock_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:warm_player_correlations:{realm}:lock"


def _hot_entity_cache_warm_lock_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:warm_hot_entity_caches:{realm}:lock"


def _landing_best_entity_warm_lock_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:warm_landing_best_entity_caches:{realm}:lock"


def _landing_best_entity_warm_dispatch_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:warm_landing_best_entity_caches:{realm}:dispatch"


def _landing_random_player_queue_refill_dispatch_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:landing_random_player_queue_refill:{realm}:dispatch"


def _landing_random_clan_queue_refill_dispatch_key(realm: str = DEFAULT_REALM) -> str:
    return f"warships:tasks:landing_random_clan_queue_refill:{realm}:dispatch"


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

def queue_random_landing_player_queue_refill(realm: str = DEFAULT_REALM):
    dispatch_key = _landing_random_player_queue_refill_dispatch_key(realm)
    if not cache.add(
        dispatch_key,
        "queued",
        timeout=LANDING_RANDOM_PLAYER_QUEUE_REFILL_DISPATCH_TIMEOUT,
    ):
        return {"status": "skipped", "reason": "already-queued"}

    try:
        refill_landing_random_players_queue_task.delay(realm=realm)
        return {"status": "queued"}
    except Exception as error:
        cache.delete(dispatch_key)
        logger.warning(
            "Skipping random landing player queue refill enqueue because broker dispatch failed: %s",
            error,
        )
        return {"status": "skipped", "reason": "enqueue-failed"}


def queue_random_landing_clan_queue_refill(realm: str = DEFAULT_REALM):
    dispatch_key = _landing_random_clan_queue_refill_dispatch_key(realm)
    if not cache.add(
        dispatch_key,
        "queued",
        timeout=LANDING_RANDOM_CLAN_QUEUE_REFILL_DISPATCH_TIMEOUT,
    ):
        return {"status": "skipped", "reason": "already-queued"}

    try:
        refill_landing_random_clans_queue_task.delay(realm=realm)
        return {"status": "queued"}
    except Exception as error:
        cache.delete(dispatch_key)
        logger.warning(
            "Skipping random landing clan queue refill enqueue because broker dispatch failed: %s",
            error,
        )
        return {"status": "skipped", "reason": "enqueue-failed"}


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


def queue_landing_page_warm(realm: str = DEFAULT_REALM):
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
        warm_landing_page_content_task.delay(include_recent=True, realm=realm)
        return {"status": "queued"}
    except Exception as error:
        cache.delete(dispatch_key)
        logger.warning(
            "Skipping landing page warm enqueue because broker dispatch failed: %s",
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
def warm_landing_page_content_task(self, include_recent=True, realm=DEFAULT_REALM):
    from warships.landing import warm_landing_page_content

    logger.info(
        "Starting warm_landing_page_content_task include_recent=%s realm=%s",
        include_recent,
        realm,
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
        )
        logger.info("Finished warm_landing_page_content_task: %s", result)
        return result
    finally:
        cache.delete(lock_key)
        cache.delete(_landing_page_warm_dispatch_key(realm))


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
def materialize_landing_player_best_snapshots_task(self, realm=DEFAULT_REALM, sorts=None):
    from warships.landing import materialize_landing_player_best_snapshots

    logger.info(
        "Starting materialize_landing_player_best_snapshots_task realm=%s sorts=%s",
        realm,
        sorts,
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
        return result
    finally:
        cache.delete(lock_key)


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
def refill_landing_random_players_queue_task(self, realm=DEFAULT_REALM):
    from warships.landing import refill_random_landing_player_queue

    logger.info(
        "Starting refill_landing_random_players_queue_task realm=%s", realm)
    try:
        result = refill_random_landing_player_queue(realm=realm)
        logger.info(
            "Finished refill_landing_random_players_queue_task: %s",
            result,
        )
        return result
    finally:
        cache.delete(_landing_random_player_queue_refill_dispatch_key(realm))


@app.task(bind=True, **TASK_OPTS)
def refill_landing_random_clans_queue_task(self, realm=DEFAULT_REALM):
    from warships.landing import refill_random_landing_clan_queue, warm_random_landing_clan_queue_preview

    logger.info(
        "Starting refill_landing_random_clans_queue_task realm=%s", realm)
    try:
        result = refill_random_landing_clan_queue(realm=realm)
        if result.get("status") == "completed":
            preview_payload, preview_metadata = warm_random_landing_clan_queue_preview(
                realm=realm)
            result = {
                **result,
                "preview_count": len(preview_payload),
                "preview_queue_remaining": int(preview_metadata.get("queue_remaining", 0)),
            }
        logger.info(
            "Finished refill_landing_random_clans_queue_task: %s",
            result,
        )
        return result
    finally:
        cache.delete(_landing_random_clan_queue_refill_dispatch_key(realm))


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

    try:
        touch_clan_crawl_heartbeat(realm=realm)
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
        )
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
    # Defer if any clan crawl is running — they share the WG API rate limit.
    from warships.models import VALID_REALMS as _realms
    active_crawls = [
        r for r in sorted(_realms)
        if cache.get(_clan_crawl_lock_key(r)) is not None
    ]
    if active_crawls:
        retry_delay = 300  # check again in 5 minutes
        logger.info(
            "Deferring enrichment — clan crawl active for %s, retrying in %ds",
            active_crawls, retry_delay,
        )
        enrich_player_data_task.apply_async(countdown=retry_delay)
        return {"status": "deferred", "reason": "crawl-running", "active_crawls": active_crawls}

    lock_key = _enrich_player_data_lock_key()
    if not cache.add(lock_key, self.request.id, timeout=ENRICH_PLAYER_DATA_LOCK_TIMEOUT):
        logger.info(
            "Skipping enrich_player_data_task — another enrichment is already running")
        return {"status": "skipped", "reason": "already-running"}

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


@app.task(queue='background', **TASK_OPTS)
def roll_up_player_daily_ship_stats_task(target_date_iso=None):
    """Nightly sweeper: rebuild PlayerDailyShipStats for the previous calendar
    day from BattleEvent rows. No-op when BATTLE_HISTORY_ROLLUP_ENABLED!=1.

    Phase 3 of the battle-history rollout. Idempotent — re-running produces
    identical row counts and values for the same target date.
    """
    if os.getenv("BATTLE_HISTORY_ROLLUP_ENABLED", "0") != "1":
        return {"status": "skipped", "reason": "rollup-disabled"}

    from datetime import datetime, timedelta, timezone as dt_timezone

    from warships.incremental_battles import (
        rebuild_daily_ship_stats_for_date,
        rebuild_period_rollups_for_date,
    )

    if target_date_iso:
        target_date = datetime.strptime(target_date_iso, "%Y-%m-%d").date()
    else:
        target_date = (
            datetime.now(dt_timezone.utc) - timedelta(days=1)
        ).date()

    logger.info(
        "Starting roll_up_player_daily_ship_stats_task for date=%s", target_date)
    daily_result = rebuild_daily_ship_stats_for_date(target_date)
    # Cascade into the weekly / monthly / yearly tiers covering the same
    # date, so coarser views always reflect the latest daily layer.
    period_result = rebuild_period_rollups_for_date(target_date)
    logger.info(
        "Finished roll_up_player_daily_ship_stats_task: daily=%s period=%s",
        daily_result, period_result)
    return {
        "status": "completed",
        "daily": daily_result,
        "period": period_result,
    }


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
