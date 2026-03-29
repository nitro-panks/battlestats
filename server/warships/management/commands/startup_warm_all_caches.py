import json
import logging
import time

from django.core.management.base import BaseCommand

from warships.data import (
    HOT_ENTITY_CLAN_LIMIT,
    HOT_ENTITY_PLAYER_LIMIT,
    bulk_load_entity_caches,
    warm_hot_entity_caches,
    warm_player_distributions,
)
from warships.landing import warm_landing_page_content

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Run all startup cache warmers sequentially: landing page, hot entities, bulk loader.'

    def add_arguments(self, parser):
        parser.add_argument('--delay', type=int, default=5,
                            help='Seconds to wait before starting (default: 5)')

    def handle(self, *args, **options):
        delay = options['delay']
        if delay > 0:
            logger.info("[startup-warmer] Waiting %ds for gunicorn to settle...", delay)
            time.sleep(delay)

        logger.info("[startup-warmer] Warming landing page cache...")
        landing_result = warm_landing_page_content()
        logger.info("[startup-warmer] Landing page warm complete: %s", json.dumps(landing_result, sort_keys=True))

        logger.info("[startup-warmer] Warming hot entity caches...")
        hot_result = warm_hot_entity_caches(
            player_limit=HOT_ENTITY_PLAYER_LIMIT,
            clan_limit=HOT_ENTITY_CLAN_LIMIT,
        )
        logger.info("[startup-warmer] Hot entity warm complete: %s", json.dumps(hot_result, sort_keys=True))

        logger.info("[startup-warmer] Bulk loading entity caches...")
        bulk_result = bulk_load_entity_caches()
        logger.info("[startup-warmer] Bulk load complete: %s", json.dumps(bulk_result, sort_keys=True))

        logger.info("[startup-warmer] Warming player distribution caches...")
        dist_result = warm_player_distributions()
        logger.info("[startup-warmer] Distribution warm complete: %s", json.dumps(dist_result, sort_keys=True))

        logger.info("[startup-warmer] All startup cache warmers finished.")
        self.stdout.write(json.dumps({
            'landing': landing_result,
            'hot_entities': hot_result,
            'bulk_load': bulk_result,
            'distributions': dist_result,
        }, sort_keys=True, indent=2))
