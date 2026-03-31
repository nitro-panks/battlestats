from __future__ import absolute_import, unicode_literals
import logging
import os
import time

from django.core.cache import cache
from django.core.management import call_command

from battlestats.celery import app


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
CLAN_CRAWL_LOCK_KEY = "warships:tasks:crawl_all_clans:lock"
CLAN_CRAWL_LOCK_TIMEOUT = 8 * 60 * 60
CLAN_CRAWL_HEARTBEAT_KEY = "warships:tasks:crawl_all_clans:heartbeat"
CLAN_CRAWL_HEARTBEAT_STALE_AFTER = 15 * 60
RESOURCE_TASK_LOCK_TIMEOUT = 15 * 60
RANKED_INCREMENTAL_LOCK_KEY = "warships:tasks:incremental_ranked_data:lock"
RANKED_INCREMENTAL_LOCK_TIMEOUT = 6 * 60 * 60
PLAYER_REFRESH_LOCK_KEY = "warships:tasks:incremental_player_refresh:lock"
PLAYER_REFRESH_LOCK_TIMEOUT = 6 * 60 * 60
RANKED_REFRESH_DISPATCH_TIMEOUT = 15 * 60
CLAN_BATTLE_REFRESH_DISPATCH_TIMEOUT = 15 * 60
EFFICIENCY_REFRESH_DISPATCH_TIMEOUT = 15 * 60
EFFICIENCY_SNAPSHOT_REFRESH_DISPATCH_TIMEOUT = 15 * 60
PLAYER_RANKED_WR_BATTLES_CORRELATION_REFRESH_DISPATCH_TIMEOUT = 15 * 60
BROKER_DISPATCH_FAILURE_COOLDOWN = 60
LANDING_PAGE_WARM_LOCK_KEY = "warships:tasks:warm_landing_page_content:lock"
LANDING_PAGE_WARM_LOCK_TIMEOUT = 20 * 60
LANDING_PAGE_WARM_DISPATCH_KEY = "warships:tasks:warm_landing_page_content:dispatch"
LANDING_PAGE_WARM_DISPATCH_TIMEOUT = 5 * 60
HOT_ENTITY_CACHE_WARM_LOCK_KEY = "warships:tasks:warm_hot_entity_caches:lock"
HOT_ENTITY_CACHE_WARM_LOCK_TIMEOUT = 30 * 60
LANDING_BEST_ENTITY_WARM_LOCK_KEY = "warships:tasks:warm_landing_best_entity_caches:lock"
LANDING_BEST_ENTITY_WARM_LOCK_TIMEOUT = 30 * 60
LANDING_BEST_ENTITY_WARM_DISPATCH_KEY = "warships:tasks:warm_landing_best_entity_caches:dispatch"
LANDING_BEST_ENTITY_WARM_DISPATCH_TIMEOUT = 5 * 60
CLAN_BATTLE_SUMMARY_REFRESH_DISPATCH_TIMEOUT = 10 * 60
LANDING_RANDOM_PLAYER_QUEUE_REFILL_DISPATCH_KEY = "warships:tasks:landing_random_player_queue_refill:dispatch"
LANDING_RANDOM_PLAYER_QUEUE_REFILL_DISPATCH_TIMEOUT = 10 * 60
LANDING_RANDOM_CLAN_QUEUE_REFILL_DISPATCH_KEY = "warships:tasks:landing_random_clan_queue_refill:dispatch"
LANDING_RANDOM_CLAN_QUEUE_REFILL_DISPATCH_TIMEOUT = 10 * 60
BULK_CACHE_LOAD_LOCK_KEY = "warships:tasks:bulk_load_entity_caches:lock"
BULK_CACHE_LOAD_LOCK_TIMEOUT = 30 * 60
RECENTLY_VIEWED_WARM_LOCK_KEY = "warships:tasks:warm_recently_viewed_players:lock"
RECENTLY_VIEWED_WARM_LOCK_TIMEOUT = 15 * 60


def _configured_clan_battle_warm_ids(raw_value=None):
    value = os.getenv("CLAN_BATTLE_WARM_CLAN_IDS",
                      "1000055908") if raw_value is None else raw_value
    return [clan_id.strip() for clan_id in str(value).split(",") if clan_id.strip()]


def _task_lock_key(task_name: str, resource_id: object) -> str:
    return f"warships:tasks:{task_name}:{resource_id}:lock"


def _ranked_refresh_dispatch_key(player_id: object) -> str:
    return f"warships:tasks:update_ranked_data_dispatch:{player_id}"


def _clan_battle_refresh_dispatch_key(player_id: object) -> str:
    return f"warships:tasks:update_player_clan_battle_data_dispatch:{player_id}"


def _efficiency_refresh_dispatch_key(player_id: object) -> str:
    return f"warships:tasks:update_player_efficiency_data_dispatch:{player_id}"


def _efficiency_snapshot_refresh_dispatch_key() -> str:
    return "warships:tasks:refresh_efficiency_rank_snapshot_dispatch"


def _player_ranked_wr_battles_correlation_refresh_dispatch_key() -> str:
    return "warships:tasks:warm_player_ranked_wr_battles_correlation_dispatch"


def _ranked_refresh_failure_key() -> str:
    return "warships:tasks:update_ranked_data_dispatch:cooldown"


def _clan_battle_refresh_failure_key() -> str:
    return "warships:tasks:update_player_clan_battle_data_dispatch:cooldown"


def _efficiency_refresh_failure_key() -> str:
    return "warships:tasks:update_player_efficiency_data_dispatch:cooldown"


def _efficiency_snapshot_refresh_failure_key() -> str:
    return "warships:tasks:refresh_efficiency_rank_snapshot_dispatch:cooldown"


def _player_ranked_wr_battles_correlation_refresh_failure_key() -> str:
    return "warships:tasks:warm_player_ranked_wr_battles_correlation_dispatch:cooldown"


def _clan_battle_summary_refresh_dispatch_key(clan_id: object) -> str:
    return f"warships:tasks:update_clan_battle_summary_dispatch:{clan_id}"


def queue_random_landing_player_queue_refill():
    if not cache.add(
        LANDING_RANDOM_PLAYER_QUEUE_REFILL_DISPATCH_KEY,
        "queued",
        timeout=LANDING_RANDOM_PLAYER_QUEUE_REFILL_DISPATCH_TIMEOUT,
    ):
        return {"status": "skipped", "reason": "already-queued"}

    try:
        refill_landing_random_players_queue_task.delay()
        return {"status": "queued"}
    except Exception as error:
        cache.delete(LANDING_RANDOM_PLAYER_QUEUE_REFILL_DISPATCH_KEY)
        logger.warning(
            "Skipping random landing player queue refill enqueue because broker dispatch failed: %s",
            error,
        )
        return {"status": "skipped", "reason": "enqueue-failed"}


def queue_random_landing_clan_queue_refill():
    if not cache.add(
        LANDING_RANDOM_CLAN_QUEUE_REFILL_DISPATCH_KEY,
        "queued",
        timeout=LANDING_RANDOM_CLAN_QUEUE_REFILL_DISPATCH_TIMEOUT,
    ):
        return {"status": "skipped", "reason": "already-queued"}

    try:
        refill_landing_random_clans_queue_task.delay()
        return {"status": "queued"}
    except Exception as error:
        cache.delete(LANDING_RANDOM_CLAN_QUEUE_REFILL_DISPATCH_KEY)
        logger.warning(
            "Skipping random landing clan queue refill enqueue because broker dispatch failed: %s",
            error,
        )
        return {"status": "skipped", "reason": "enqueue-failed"}


def queue_clan_battle_summary_refresh(clan_id: object):
    dispatch_key = _clan_battle_summary_refresh_dispatch_key(clan_id)
    if not cache.add(
        dispatch_key,
        "queued",
        timeout=CLAN_BATTLE_SUMMARY_REFRESH_DISPATCH_TIMEOUT,
    ):
        return {"status": "skipped", "reason": "already-queued"}

    try:
        update_clan_battle_summary_task.delay(clan_id=clan_id)
        return {"status": "queued"}
    except Exception as error:
        cache.delete(dispatch_key)
        logger.warning(
            "Skipping clan battle summary refresh enqueue because broker dispatch failed: %s",
            error,
        )
        return {"status": "skipped", "reason": "enqueue-failed"}


def is_clan_battle_summary_refresh_pending(clan_id: object) -> bool:
    return bool(cache.get(_clan_battle_summary_refresh_dispatch_key(clan_id)))


def queue_landing_page_warm():
    if not cache.add(
        LANDING_PAGE_WARM_DISPATCH_KEY,
        "queued",
        timeout=LANDING_PAGE_WARM_DISPATCH_TIMEOUT,
    ):
        return {"status": "skipped", "reason": "already-queued"}

    try:
        warm_landing_page_content_task.delay(include_recent=True)
        return {"status": "queued"}
    except Exception as error:
        cache.delete(LANDING_PAGE_WARM_DISPATCH_KEY)
        logger.warning(
            "Skipping landing page warm enqueue because broker dispatch failed: %s",
            error,
        )
        return {"status": "skipped", "reason": "enqueue-failed"}


def queue_landing_best_entity_warm(player_limit=25, clan_limit=25, force_refresh=False):
    if not cache.add(
        LANDING_BEST_ENTITY_WARM_DISPATCH_KEY,
        "queued",
        timeout=LANDING_BEST_ENTITY_WARM_DISPATCH_TIMEOUT,
    ):
        return {"status": "skipped", "reason": "already-queued"}

    try:
        warm_landing_best_entity_caches_task.delay(
            player_limit=int(player_limit),
            clan_limit=int(clan_limit),
            force_refresh=bool(force_refresh),
        )
        return {"status": "queued"}
    except Exception as error:
        cache.delete(LANDING_BEST_ENTITY_WARM_DISPATCH_KEY)
        logger.warning(
            "Skipping landing best entity warm enqueue because broker dispatch failed: %s",
            error,
        )
        return {"status": "skipped", "reason": "enqueue-failed"}


def touch_clan_crawl_heartbeat(timestamp: float | None = None) -> float:
    heartbeat = time.time() if timestamp is None else float(timestamp)
    cache.set(CLAN_CRAWL_HEARTBEAT_KEY, heartbeat,
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
        callback()
        return {"status": "completed"}
    finally:
        cache.delete(lock_key)


def is_ranked_data_refresh_pending(player_id: object) -> bool:
    return bool(cache.get(_ranked_refresh_dispatch_key(player_id)))


def queue_ranked_data_refresh(player_id: object):
    if cache.get(_ranked_refresh_failure_key()):
        return {"status": "skipped", "reason": "broker-unavailable"}

    dispatch_key = _ranked_refresh_dispatch_key(player_id)
    if not cache.add(dispatch_key, "queued", timeout=RANKED_REFRESH_DISPATCH_TIMEOUT):
        return {"status": "skipped", "reason": "already-queued"}

    try:
        update_ranked_data_task.delay(player_id=player_id)
        return {"status": "queued"}
    except Exception as error:
        cache.delete(dispatch_key)
        cache.set(_ranked_refresh_failure_key(), True,
                  timeout=BROKER_DISPATCH_FAILURE_COOLDOWN)
        logger.warning(
            "Skipping ranked refresh enqueue for player_id=%s because broker dispatch failed: %s",
            player_id,
            error,
        )
        return {"status": "skipped", "reason": "enqueue-failed"}


def queue_clan_battle_data_refresh(player_id: object):
    if cache.get(_clan_battle_refresh_failure_key()):
        return {"status": "skipped", "reason": "broker-unavailable"}

    dispatch_key = _clan_battle_refresh_dispatch_key(player_id)
    if not cache.add(dispatch_key, "queued", timeout=CLAN_BATTLE_REFRESH_DISPATCH_TIMEOUT):
        return {"status": "skipped", "reason": "already-queued"}

    try:
        update_player_clan_battle_data_task.delay(player_id=player_id)
        return {"status": "queued"}
    except Exception as error:
        cache.delete(dispatch_key)
        cache.set(_clan_battle_refresh_failure_key(), True,
                  timeout=BROKER_DISPATCH_FAILURE_COOLDOWN)
        logger.warning(
            "Skipping clan battle refresh enqueue for player_id=%s because broker dispatch failed: %s",
            player_id,
            error,
        )
        return {"status": "skipped", "reason": "enqueue-failed"}


def is_efficiency_data_refresh_pending(player_id: object) -> bool:
    return bool(cache.get(_efficiency_refresh_dispatch_key(player_id)))


def queue_efficiency_data_refresh(player_id: object):
    if cache.get(_efficiency_refresh_failure_key()):
        return {"status": "skipped", "reason": "broker-unavailable"}

    dispatch_key = _efficiency_refresh_dispatch_key(player_id)
    if not cache.add(dispatch_key, "queued", timeout=EFFICIENCY_REFRESH_DISPATCH_TIMEOUT):
        return {"status": "skipped", "reason": "already-queued"}

    try:
        update_player_efficiency_data_task.delay(player_id=player_id)
        return {"status": "queued"}
    except Exception as error:
        cache.delete(dispatch_key)
        cache.set(_efficiency_refresh_failure_key(), True,
                  timeout=BROKER_DISPATCH_FAILURE_COOLDOWN)
        logger.warning(
            "Skipping efficiency refresh enqueue for player_id=%s because broker dispatch failed: %s",
            player_id,
            error,
        )
        return {"status": "skipped", "reason": "enqueue-failed"}


def is_efficiency_rank_snapshot_refresh_pending() -> bool:
    return bool(cache.get(_efficiency_snapshot_refresh_dispatch_key()))


def queue_efficiency_rank_snapshot_refresh():
    if cache.get(_efficiency_snapshot_refresh_failure_key()):
        return {"status": "skipped", "reason": "broker-unavailable"}

    dispatch_key = _efficiency_snapshot_refresh_dispatch_key()
    if not cache.add(dispatch_key, "queued", timeout=EFFICIENCY_SNAPSHOT_REFRESH_DISPATCH_TIMEOUT):
        return {"status": "skipped", "reason": "already-queued"}

    try:
        refresh_efficiency_rank_snapshot_task.delay()
        return {"status": "queued"}
    except Exception as error:
        cache.delete(dispatch_key)
        cache.set(_efficiency_snapshot_refresh_failure_key(), True,
                  timeout=BROKER_DISPATCH_FAILURE_COOLDOWN)
        logger.warning(
            "Skipping efficiency-rank snapshot refresh enqueue because broker dispatch failed: %s",
            error,
        )
        return {"status": "skipped", "reason": "enqueue-failed"}


def queue_player_ranked_wr_battles_correlation_refresh():
    if cache.get(_player_ranked_wr_battles_correlation_refresh_failure_key()):
        return {"status": "skipped", "reason": "broker-unavailable"}

    dispatch_key = _player_ranked_wr_battles_correlation_refresh_dispatch_key()
    if not cache.add(dispatch_key, "queued", timeout=PLAYER_RANKED_WR_BATTLES_CORRELATION_REFRESH_DISPATCH_TIMEOUT):
        return {"status": "skipped", "reason": "already-queued"}

    try:
        warm_player_ranked_wr_battles_correlation_task.delay()
        return {"status": "queued"}
    except Exception as error:
        cache.delete(dispatch_key)
        cache.set(_player_ranked_wr_battles_correlation_refresh_failure_key(), True,
                  timeout=BROKER_DISPATCH_FAILURE_COOLDOWN)
        logger.warning(
            "Skipping ranked heatmap correlation refresh enqueue because broker dispatch failed: %s",
            error,
        )
        return {"status": "skipped", "reason": "enqueue-failed"}


@app.task(bind=True, **TASK_OPTS)
def update_clan_data_task(self, clan_id):
    from warships.data import update_clan_data

    logger.info("Starting update_clan_data_task for clan_id=%s", clan_id)
    return _run_locked_task(
        "update_clan_data",
        clan_id,
        self.request.id,
        lambda: update_clan_data(clan_id=clan_id),
    )


@app.task(bind=True, **TASK_OPTS)
def update_clan_members_task(self, clan_id):
    from warships.data import update_clan_members

    logger.info("Starting update_clan_members_task for clan_id=%s", clan_id)
    return _run_locked_task(
        "update_clan_members",
        clan_id,
        self.request.id,
        lambda: update_clan_members(clan_id=clan_id),
    )


@app.task(bind=True, **TASK_OPTS)
def update_player_data_task(self, player_id, force_refresh=False):
    from warships.data import update_player_data
    from warships.models import Player

    logger.info(
        "Starting update_player_data_task for player_id=%s force_refresh=%s",
        player_id,
        force_refresh,
    )

    def _refresh_player():
        player = Player.objects.get(player_id=player_id)
        update_player_data(player=player, force_refresh=force_refresh)

    return _run_locked_task(
        "update_player_data",
        player_id,
        self.request.id,
        _refresh_player,
    )


@app.task(bind=True, **TASK_OPTS)
def update_battle_data_task(self, player_id):
    from warships.data import update_battle_data

    logger.info("Starting update_battle_data_task for player_id=%s", player_id)
    return _run_locked_task(
        "update_battle_data",
        player_id,
        self.request.id,
        lambda: update_battle_data(player_id=player_id),
    )


@app.task(**TASK_OPTS)
def preload_battles_json_task():
    from warships.data import preload_battles_json
    logger.info("Starting preload_battles_json_task")
    preload_battles_json()


@app.task(**TASK_OPTS)
def preload_activity_data_task():
    from warships.data import preload_activity_data
    logger.info("Starting preload_activity_data_task")
    preload_activity_data()


@app.task(**TASK_OPTS)
def update_randoms_data_task(player_id):
    from warships.data import update_randoms_data
    logger.info("Starting update_randoms_data_task for player_id=%s", player_id)
    update_randoms_data(player_id=player_id)


@app.task(**TASK_OPTS)
def update_tiers_data_task(player_id):
    from warships.data import update_tiers_data
    logger.info("Starting update_tiers_data_task for player_id=%s", player_id)
    update_tiers_data(player_id=player_id)


@app.task(**TASK_OPTS)
def update_snapshot_data_task(player_id):
    from warships.data import update_snapshot_data
    logger.info("Starting update_snapshot_data_task for player_id=%s", player_id)
    update_snapshot_data(player_id=player_id)


@app.task(**TASK_OPTS)
def update_activity_data_task(player_id):
    from warships.data import update_activity_data
    logger.info("Starting update_activity_data_task for player_id=%s", player_id)
    update_activity_data(player_id=player_id)


@app.task(**TASK_OPTS)
def update_type_data_task(player_id):
    from warships.data import update_type_data
    logger.info("Starting update_type_data_task for player_id=%s", player_id)
    update_type_data(player_id=player_id)


@app.task(bind=True, **TASK_OPTS)
def update_ranked_data_task(self, player_id):
    from warships.data import update_ranked_data

    logger.info("Starting update_ranked_data_task for player_id=%s", player_id)
    try:
        return _run_locked_task(
            "update_ranked_data",
            player_id,
            self.request.id,
            lambda: update_ranked_data(player_id=player_id),
        )
    finally:
        cache.delete(_ranked_refresh_dispatch_key(player_id))


@app.task(bind=True, **TASK_OPTS)
def update_player_clan_battle_data_task(self, player_id):
    from warships.data import fetch_player_clan_battle_seasons

    logger.info(
        "Starting update_player_clan_battle_data_task for player_id=%s", player_id)
    try:
        return _run_locked_task(
            "update_player_clan_battle_data",
            player_id,
            self.request.id,
            lambda: fetch_player_clan_battle_seasons(player_id),
        )
    finally:
        cache.delete(_clan_battle_refresh_dispatch_key(player_id))


@app.task(bind=True, **TASK_OPTS)
def update_player_efficiency_data_task(self, player_id):
    from warships.data import refresh_player_explorer_summary, update_player_efficiency_data
    from warships.models import Player

    logger.info(
        "Starting update_player_efficiency_data_task for player_id=%s", player_id)

    def _refresh_player_efficiency():
        player = Player.objects.get(player_id=player_id)
        update_player_efficiency_data(player=player)
        refresh_player_explorer_summary(player)
        queue_efficiency_rank_snapshot_refresh()

    try:
        return _run_locked_task(
            "update_player_efficiency_data",
            player_id,
            self.request.id,
            _refresh_player_efficiency,
        )
    finally:
        cache.delete(_efficiency_refresh_dispatch_key(player_id))


@app.task(bind=True, **TASK_OPTS)
def refresh_efficiency_rank_snapshot_task(self):
    from warships.data import recompute_efficiency_rank_snapshot

    logger.info("Starting refresh_efficiency_rank_snapshot_task")
    try:
        return _run_locked_task(
            "refresh_efficiency_rank_snapshot",
            "global",
            self.request.id,
            lambda: recompute_efficiency_rank_snapshot(skip_refresh=True),
        )
    finally:
        cache.delete(_efficiency_snapshot_refresh_dispatch_key())


@app.task(bind=True, **TASK_OPTS)
def warm_player_ranked_wr_battles_correlation_task(self):
    from warships.data import warm_player_ranked_wr_battles_population_correlation

    logger.info("Starting warm_player_ranked_wr_battles_correlation_task")
    try:
        return _run_locked_task(
            "warm_player_ranked_wr_battles_correlation",
            "population",
            self.request.id,
            warm_player_ranked_wr_battles_population_correlation,
        )
    finally:
        cache.delete(
            _player_ranked_wr_battles_correlation_refresh_dispatch_key())


@app.task(bind=True, **TASK_OPTS)
def update_clan_battle_summary_task(self, clan_id):
    from warships.data import refresh_clan_battle_seasons_cache

    logger.info(
        "Starting update_clan_battle_summary_task for clan_id=%s", clan_id)
    try:
        return _run_locked_task(
            "update_clan_battle_summary",
            clan_id,
            self.request.id,
            lambda: refresh_clan_battle_seasons_cache(clan_id),
        )
    finally:
        cache.delete(_clan_battle_summary_refresh_dispatch_key(clan_id))


@app.task(bind=True, **TASK_OPTS)
def warm_clan_battle_summaries_task(self, clan_ids=None):
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
            lambda clan_id=clan_id: refresh_clan_battle_seasons_cache(clan_id),
        )
        results.append({"clan_id": str(clan_id), **result})

    logger.info(
        "Finished warm_clan_battle_summaries_task for clan_ids=%s", configured_ids)
    return {"status": "completed", "results": results}


@app.task(bind=True, **TASK_OPTS)
def warm_landing_page_content_task(self, include_recent=True):
    from warships.landing import warm_landing_page_content

    logger.info(
        "Starting warm_landing_page_content_task include_recent=%s",
        include_recent,
    )

    if not cache.add(LANDING_PAGE_WARM_LOCK_KEY, self.request.id, timeout=LANDING_PAGE_WARM_LOCK_TIMEOUT):
        logger.info(
            "Skipping warm_landing_page_content_task because another landing warm is already running"
        )
        return {"status": "skipped", "reason": "already-running"}

    try:
        result = warm_landing_page_content(
            force_refresh=True,
            include_recent=bool(include_recent),
        )
        logger.info("Finished warm_landing_page_content_task: %s", result)

        from warships.data import warm_player_correlations, warm_player_distributions
        logger.info("Warming player distribution caches...")
        dist_result = warm_player_distributions()
        logger.info("Player distribution warm complete: %s", dist_result)
        result['distributions'] = dist_result

        logger.info("Warming player correlation caches...")
        corr_result = warm_player_correlations()
        logger.info("Player correlation warm complete: %s", corr_result)
        result['correlations'] = corr_result

        return result
    finally:
        cache.delete(LANDING_PAGE_WARM_LOCK_KEY)
        cache.delete(LANDING_PAGE_WARM_DISPATCH_KEY)


@app.task(bind=True, **TASK_OPTS)
def warm_hot_entity_caches_task(self, player_limit=None, clan_limit=None, force_refresh=False):
    from warships.data import HOT_ENTITY_CLAN_LIMIT, HOT_ENTITY_PLAYER_LIMIT, warm_hot_entity_caches

    logger.info(
        "Starting warm_hot_entity_caches_task player_limit=%s clan_limit=%s force_refresh=%s",
        player_limit,
        clan_limit,
        force_refresh,
    )

    if not cache.add(HOT_ENTITY_CACHE_WARM_LOCK_KEY, self.request.id, timeout=HOT_ENTITY_CACHE_WARM_LOCK_TIMEOUT):
        logger.info(
            "Skipping warm_hot_entity_caches_task because another hot cache warm is already running"
        )
        return {"status": "skipped", "reason": "already-running"}

    try:
        result = warm_hot_entity_caches(
            player_limit=int(player_limit or HOT_ENTITY_PLAYER_LIMIT),
            clan_limit=int(clan_limit or HOT_ENTITY_CLAN_LIMIT),
            force_refresh=bool(force_refresh),
        )
        logger.info("Finished warm_hot_entity_caches_task: %s", result)
        return result
    finally:
        cache.delete(HOT_ENTITY_CACHE_WARM_LOCK_KEY)


@app.task(bind=True, **TASK_OPTS)
def warm_landing_best_entity_caches_task(self, player_limit=25, clan_limit=25, force_refresh=False):
    from warships.data import warm_landing_best_entity_caches

    logger.info(
        "Starting warm_landing_best_entity_caches_task player_limit=%s clan_limit=%s force_refresh=%s",
        player_limit,
        clan_limit,
        force_refresh,
    )

    if not cache.add(LANDING_BEST_ENTITY_WARM_LOCK_KEY, self.request.id, timeout=LANDING_BEST_ENTITY_WARM_LOCK_TIMEOUT):
        logger.info(
            "Skipping warm_landing_best_entity_caches_task because another landing best warm is already running"
        )
        return {"status": "skipped", "reason": "already-running"}

    try:
        result = warm_landing_best_entity_caches(
            player_limit=int(player_limit or 25),
            clan_limit=int(clan_limit or 25),
            force_refresh=bool(force_refresh),
        )
        logger.info("Finished warm_landing_best_entity_caches_task: %s", result)
        return result
    finally:
        cache.delete(LANDING_BEST_ENTITY_WARM_LOCK_KEY)
        cache.delete(LANDING_BEST_ENTITY_WARM_DISPATCH_KEY)


@app.task(bind=True, **TASK_OPTS)
def refill_landing_random_players_queue_task(self):
    from warships.landing import refill_random_landing_player_queue

    logger.info("Starting refill_landing_random_players_queue_task")
    try:
        result = refill_random_landing_player_queue()
        logger.info(
            "Finished refill_landing_random_players_queue_task: %s",
            result,
        )
        return result
    finally:
        cache.delete(LANDING_RANDOM_PLAYER_QUEUE_REFILL_DISPATCH_KEY)


@app.task(bind=True, **TASK_OPTS)
def refill_landing_random_clans_queue_task(self):
    from warships.landing import refill_random_landing_clan_queue, warm_random_landing_clan_queue_preview

    logger.info("Starting refill_landing_random_clans_queue_task")
    try:
        result = refill_random_landing_clan_queue()
        if result.get("status") == "completed":
            preview_payload, preview_metadata = warm_random_landing_clan_queue_preview()
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
        cache.delete(LANDING_RANDOM_CLAN_QUEUE_REFILL_DISPATCH_KEY)


@app.task(bind=True, **TASK_OPTS)
def bulk_load_entity_caches_task(self):
    from warships.data import bulk_load_entity_caches

    logger.info("Starting bulk_load_entity_caches_task")

    if not cache.add(BULK_CACHE_LOAD_LOCK_KEY, self.request.id, timeout=BULK_CACHE_LOAD_LOCK_TIMEOUT):
        logger.info("Skipping bulk_load_entity_caches_task because another bulk load is already running")
        return {"status": "skipped", "reason": "already-running"}

    try:
        result = bulk_load_entity_caches()
        logger.info("Finished bulk_load_entity_caches_task: %s", result)
        return result
    finally:
        cache.delete(BULK_CACHE_LOAD_LOCK_KEY)


@app.task(bind=True, **TASK_OPTS)
def warm_recently_viewed_players_task(self):
    from warships.data import warm_recently_viewed_players

    logger.info("Starting warm_recently_viewed_players_task")

    if not cache.add(RECENTLY_VIEWED_WARM_LOCK_KEY, self.request.id, timeout=RECENTLY_VIEWED_WARM_LOCK_TIMEOUT):
        logger.info("Skipping warm_recently_viewed_players_task because another run is already active")
        return {"status": "skipped", "reason": "already-running"}

    try:
        result = warm_recently_viewed_players()
        logger.info("Finished warm_recently_viewed_players_task: %s", result)
        return result
    finally:
        cache.delete(RECENTLY_VIEWED_WARM_LOCK_KEY)


@app.task(bind=True, **CRAWL_TASK_OPTS)
def crawl_all_clans_task(self, resume=True, dry_run=False, limit=None):
    from warships.clan_crawl import run_clan_crawl

    if not cache.add(CLAN_CRAWL_LOCK_KEY, self.request.id, timeout=CLAN_CRAWL_LOCK_TIMEOUT):
        logger.warning(
            "Skipping crawl_all_clans_task because another crawl is already running")
        return {"status": "skipped", "reason": "already-running"}

    try:
        touch_clan_crawl_heartbeat()
        logger.info(
            "Starting crawl_all_clans_task resume=%s dry_run=%s limit=%s",
            resume,
            dry_run,
            limit,
        )
        summary = run_clan_crawl(
            resume=resume,
            dry_run=dry_run,
            limit=limit,
            heartbeat_callback=touch_clan_crawl_heartbeat,
        )
        logger.info("Finished crawl_all_clans_task: %s", summary)
        return {"status": "completed", **summary}
    finally:
        cache.delete(CLAN_CRAWL_LOCK_KEY)
        cache.delete(CLAN_CRAWL_HEARTBEAT_KEY)


@app.task(**TASK_OPTS)
def ensure_crawl_all_clans_running_task():
    heartbeat = cache.get(CLAN_CRAWL_HEARTBEAT_KEY)
    lock_value = cache.get(CLAN_CRAWL_LOCK_KEY)
    now_ts = time.time()

    if lock_value is not None:
        if _crawl_heartbeat_is_fresh(heartbeat, now_ts):
            logger.info(
                "Crawl watchdog found active crawl with fresh heartbeat")
            return {"status": "skipped", "reason": "running"}

        logger.warning(
            "Crawl watchdog found stale crawl lock; clearing it and resuming crawl")
        cache.delete(CLAN_CRAWL_LOCK_KEY)
        cache.delete(CLAN_CRAWL_HEARTBEAT_KEY)
        crawl_all_clans_task.delay(resume=True)
        return {"status": "scheduled", "reason": "stale-lock"}

    logger.info(
        "Crawl watchdog found no active crawl; leaving the scheduler to start the next full crawl")
    return {"status": "skipped", "reason": "idle"}


@app.task(bind=True, **CRAWL_TASK_OPTS)
def incremental_player_refresh_task(self):
    if cache.get(CLAN_CRAWL_LOCK_KEY) is not None:
        logger.info(
            "Skipping incremental_player_refresh_task because clan crawl is currently running"
        )
        return {"status": "skipped", "reason": "crawl-running"}

    if not cache.add(PLAYER_REFRESH_LOCK_KEY, self.request.id, timeout=PLAYER_REFRESH_LOCK_TIMEOUT):
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
        )
        return {"status": "completed"}
    finally:
        cache.delete(PLAYER_REFRESH_LOCK_KEY)


@app.task(bind=True, **CRAWL_TASK_OPTS)
def incremental_ranked_data_task(self):
    if cache.get(CLAN_CRAWL_LOCK_KEY) is not None:
        logger.info(
            "Skipping incremental_ranked_data_task because clan crawl is currently running"
        )
        return {"status": "skipped", "reason": "crawl-running"}

    if not cache.add(RANKED_INCREMENTAL_LOCK_KEY, self.request.id, timeout=RANKED_INCREMENTAL_LOCK_TIMEOUT):
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
        )
        return {"status": "completed"}
    finally:
        cache.delete(RANKED_INCREMENTAL_LOCK_KEY)


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
