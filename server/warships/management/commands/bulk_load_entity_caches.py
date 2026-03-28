import json

from django.core.management.base import BaseCommand

from warships.data import BULK_CACHE_CLAN_LIMIT, BULK_CACHE_PLAYER_LIMIT, bulk_load_entity_caches


class Command(BaseCommand):
    help = 'Bulk-load top player and clan detail payloads into Redis from DB. No API calls, no Celery tasks.'

    def add_arguments(self, parser):
        parser.add_argument('--player-limit', type=int, default=BULK_CACHE_PLAYER_LIMIT)
        parser.add_argument('--clan-limit', type=int, default=BULK_CACHE_CLAN_LIMIT)

    def handle(self, *args, **options):
        result = bulk_load_entity_caches(
            player_limit=options['player_limit'],
            clan_limit=options['clan_limit'],
        )
        self.stdout.write(json.dumps(result, sort_keys=True))
