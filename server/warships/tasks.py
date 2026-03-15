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
RANKED_REFRESH_DISPATCH_TIMEOUT = 15 * 60


def _configured_clan_battle_warm_ids(raw_value=None):
    value = os.getenv("CLAN_BATTLE_WARM_CLAN_IDS",
                      "1000055908") if raw_value is None else raw_value
    return [clan_id.strip() for clan_id in str(value).split(",") if clan_id.strip()]


def _task_lock_key(task_name: str, resource_id: object) -> str:
    return f"warships:tasks:{task_name}:{resource_id}:lock"


def _ranked_refresh_dispatch_key(player_id: object) -> str:
    return f"warships:tasks:update_ranked_data_dispatch:{player_id}"


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
    dispatch_key = _ranked_refresh_dispatch_key(player_id)
    if not cache.add(dispatch_key, "queued", timeout=RANKED_REFRESH_DISPATCH_TIMEOUT):
        return {"status": "skipped", "reason": "already-queued"}

    try:
        update_ranked_data_task.delay(player_id=player_id)
        return {"status": "queued"}
    except Exception as error:
        cache.delete(dispatch_key)
        logger.warning(
            "Skipping ranked refresh enqueue for player_id=%s because broker dispatch failed: %s",
            player_id,
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
def update_clan_battle_summary_task(self, clan_id):
    from warships.data import refresh_clan_battle_seasons_cache

    logger.info(
        "Starting update_clan_battle_summary_task for clan_id=%s", clan_id)
    return _run_locked_task(
        "update_clan_battle_summary",
        clan_id,
        self.request.id,
        lambda: refresh_clan_battle_seasons_cache(clan_id),
    )


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


@app.task(bind=True, **CRAWL_TASK_OPTS)
def crawl_all_clans_task(self, resume=True, dry_run=False, limit=None):
    from warships.clan_crawl import run_clan_crawl

    if not cache.add(CLAN_CRAWL_LOCK_KEY, self.request.id, timeout=CLAN_CRAWL_LOCK_TIMEOUT):
        logger.warning(
            "Skipping crawl_all_clans_task because another crawl is already running")
        return {"status": "skipped", "reason": "already-running"}

    try:
        cache.set(CLAN_CRAWL_HEARTBEAT_KEY, time.time(),
                  timeout=CLAN_CRAWL_LOCK_TIMEOUT)
        logger.info(
            "Starting crawl_all_clans_task resume=%s dry_run=%s limit=%s",
            resume,
            dry_run,
            limit,
        )
        summary = run_clan_crawl(resume=resume, dry_run=dry_run, limit=limit)
        logger.info("Finished crawl_all_clans_task: %s", summary)
        return {"status": "completed", **summary}
    finally:
        cache.delete(CLAN_CRAWL_LOCK_KEY)


@app.task(**TASK_OPTS)
def ensure_crawl_all_clans_running_task():
    heartbeat = cache.get(CLAN_CRAWL_HEARTBEAT_KEY)
    lock_value = cache.get(CLAN_CRAWL_LOCK_KEY)
    now_ts = time.time()

    if lock_value is not None:
        if heartbeat is not None and now_ts - float(heartbeat) <= CLAN_CRAWL_HEARTBEAT_STALE_AFTER:
            logger.info(
                "Crawl watchdog found active crawl with fresh heartbeat")
            return {"status": "skipped", "reason": "running"}

        logger.warning(
            "Crawl watchdog found stale crawl lock; clearing it and resuming crawl")
        cache.delete(CLAN_CRAWL_LOCK_KEY)
        cache.delete(CLAN_CRAWL_HEARTBEAT_KEY)
        crawl_all_clans_task.delay(resume=True)
        return {"status": "scheduled", "reason": "stale-lock"}

    logger.info("Crawl watchdog found no active crawl; scheduling resume crawl")
    crawl_all_clans_task.delay(resume=True)
    return {"status": "scheduled", "reason": "not-running"}


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
