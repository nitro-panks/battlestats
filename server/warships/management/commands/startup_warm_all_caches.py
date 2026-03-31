import json
import logging
import time

from django.core.management.base import BaseCommand

from warships.data import (
    HOT_ENTITY_CLAN_LIMIT,
    HOT_ENTITY_PLAYER_LIMIT,
    bulk_load_entity_caches,
    warm_hot_entity_caches,
    warm_player_correlations,
    warm_player_distributions,
)
from warships.landing import warm_landing_page_content
from warships.models import VALID_REALMS

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

        all_results = {}
        for realm in sorted(VALID_REALMS):
            logger.info("[startup-warmer] Warming realm=%s ...", realm)

            logger.info("[startup-warmer] [%s] Warming landing page cache...", realm)
            landing_result = warm_landing_page_content(realm=realm)
            logger.info("[startup-warmer] [%s] Landing page warm complete: %s", realm, json.dumps(landing_result, sort_keys=True))

            logger.info("[startup-warmer] [%s] Warming hot entity caches...", realm)
            hot_result = warm_hot_entity_caches(
                player_limit=HOT_ENTITY_PLAYER_LIMIT,
                clan_limit=HOT_ENTITY_CLAN_LIMIT,
                realm=realm,
            )
            logger.info("[startup-warmer] [%s] Hot entity warm complete: %s", realm, json.dumps(hot_result, sort_keys=True))

            logger.info("[startup-warmer] [%s] Bulk loading entity caches...", realm)
            bulk_result = bulk_load_entity_caches(realm=realm)
            logger.info("[startup-warmer] [%s] Bulk load complete: %s", realm, json.dumps(bulk_result, sort_keys=True))

            logger.info("[startup-warmer] [%s] Warming player distribution caches...", realm)
            dist_result = warm_player_distributions(realm=realm)
            logger.info("[startup-warmer] [%s] Distribution warm complete: %s", realm, json.dumps(dist_result, sort_keys=True))

            logger.info("[startup-warmer] [%s] Warming player correlation caches...", realm)
            corr_result = warm_player_correlations(realm=realm)
            logger.info("[startup-warmer] [%s] Correlation warm complete: %s", realm, json.dumps(corr_result, sort_keys=True))

            all_results[realm] = {
                'landing': landing_result,
                'hot_entities': hot_result,
                'bulk_load': bulk_result,
                'distributions': dist_result,
                'correlations': corr_result,
            }

        logger.info("[startup-warmer] All startup cache warmers finished.")
        self.stdout.write(json.dumps(all_results, sort_keys=True, indent=2))
