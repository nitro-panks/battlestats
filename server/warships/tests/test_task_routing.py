from django.conf import settings

from warships.tasks import startup_warm_caches_task


def test_heavy_request_driven_refreshes_route_to_hydration():
    routes = settings.CELERY_TASK_ROUTES

    expected_tasks = {
        "warships.tasks.update_battle_data_task",
        "warships.tasks.update_clan_members_task",
        "warships.tasks.update_ranked_data_task",
        "warships.tasks.update_player_clan_battle_data_task",
        "warships.tasks.update_player_efficiency_data_task",
        "warships.tasks.update_clan_battle_summary_task",
        # Core on-visit refreshes: dispatched only from the request path, so
        # they belong in the dedicated interactive lane rather than competing
        # on `default` with crawl dispatchers + watchdogs. (The observation
        # floor moved off `default` to its own `floor` queue — see
        # test_observation_floor_routes_to_dedicated_queue.) See
        # runbook-interactive-refresh-lane-2026-06-17.md.
        "warships.tasks.update_player_data_task",
        "warships.tasks.update_clan_data_task",
    }

    for task_name in expected_tasks:
        assert routes[task_name]["queue"] == "hydration"


def test_long_running_maintenance_tasks_route_to_background():
    routes = settings.CELERY_TASK_ROUTES

    expected_tasks = {
        "warships.tasks.incremental_player_refresh_task",
        "warships.tasks.incremental_ranked_data_task",
        "warships.tasks.warm_all_clan_tier_distributions_task",
        "warships.tasks.enrich_player_data_task",
    }

    for task_name in expected_tasks:
        assert routes[task_name]["queue"] == "background"


def test_clan_crawl_routes_to_crawls_but_watchdog_routes_to_default():
    # The days-long crawl_all_clans_task owns the single-slot `crawls` queue.
    # Its watchdog must live on a different queue so it isn't camped behind the
    # crawl (a ~269-deep backlog of no-op checks was observed 2026-05-25) and
    # can still run to detect a zombie crawl holding that slot.
    # See runbook-clan-crawl-blocker-2026-04-30.md.
    routes = settings.CELERY_TASK_ROUTES

    assert routes["warships.tasks.crawl_all_clans_task"]["queue"] == "crawls"
    assert (
        routes["warships.tasks.ensure_crawl_all_clans_running_task"]["queue"]
        == "default"
    )
    # The Beat dedup entrypoint must also stay off the single-slot crawls queue
    # so it dispatches promptly instead of queueing behind the running pass.
    assert (
        routes["warships.tasks.dispatch_clan_crawl_task"]["queue"] == "default"
    )


def test_observation_floor_routes_to_dedicated_queue():
    # The observation floor runs on its own `floor` queue/worker so its heavy,
    # hours-long per-mover capture can't starve the user-facing `default` lane,
    # and so per-realm cycles run concurrently on a dedicated pool. The
    # self-chain re-dispatch routes by task name, so this single route covers it.
    routes = settings.CELERY_TASK_ROUTES
    assert (
        routes["warships.tasks.ensure_daily_battle_observations_task"]["queue"]
        == "floor"
    )


def test_startup_cache_warm_task_declares_background_queue():
    assert startup_warm_caches_task.queue == "background"


def test_ship_standings_warm_chain_routes_to_background():
    # These three back user-visible pending flows (the landing treemap /
    # tier-type list, the pct "Crunching…" poll, and the battle-history damage
    # baseline poll). They were unrouted, landing on `default`, where on
    # 2026-07-13 the post-rotation warm chain sat received-but-unexecuted for
    # 3.5h — every visitor to a cold bucket ate the pending stall meanwhile.
    # `background` is the designed home for warmers/snapshots (see the routes
    # map); the pct sibling (warm_realm_ships_pct_task) was already there.
    routes = settings.CELERY_TASK_ROUTES

    expected_tasks = {
        "warships.tasks.snapshot_ship_top_players_task",
        "warships.tasks.warm_realm_top_ships_task",
        "warships.tasks.warm_ship_pop_avg_damage_task",
        "warships.tasks.warm_all_ship_pop_avg_damage_task",
    }

    for task_name in expected_tasks:
        assert routes[task_name]["queue"] == "background"
