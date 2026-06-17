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
        # on `default` with the inline observation-floor sweep + crawl
        # dispatchers + warmers. See
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


def test_startup_cache_warm_task_declares_background_queue():
    assert startup_warm_caches_task.queue == "background"
