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
    }

    for task_name in expected_tasks:
        assert routes[task_name]["queue"] == "hydration"


def test_long_running_maintenance_tasks_route_to_background():
    routes = settings.CELERY_TASK_ROUTES

    expected_tasks = {
        "warships.tasks.crawl_all_clans_task",
        "warships.tasks.ensure_crawl_all_clans_running_task",
        "warships.tasks.incremental_player_refresh_task",
        "warships.tasks.incremental_ranked_data_task",
        "warships.tasks.warm_all_clan_tier_distributions_task",
        "warships.tasks.enrich_player_data_task",
    }

    for task_name in expected_tasks:
        assert routes[task_name]["queue"] == "background"


def test_startup_cache_warm_task_declares_background_queue():
    assert startup_warm_caches_task.queue == "background"