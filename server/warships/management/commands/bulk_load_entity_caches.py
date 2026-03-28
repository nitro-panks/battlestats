import json

from django.core.management.base import BaseCommand

from warships.data import BULK_CACHE_CLAN_LIMIT, BULK_CACHE_CLAN_MEMBER_CLANS, BULK_CACHE_TOP_PLAYER_LIMIT, bulk_load_entity_caches


class Command(BaseCommand):
    help = 'Bulk-load top players, best-clan members, and top clans into Redis from DB. No API calls, no Celery tasks.'

    def add_arguments(self, parser):
        parser.add_argument('--top-players', type=int, default=BULK_CACHE_TOP_PLAYER_LIMIT)
        parser.add_argument('--clan-member-clans', type=int, default=BULK_CACHE_CLAN_MEMBER_CLANS)
        parser.add_argument('--clan-limit', type=int, default=BULK_CACHE_CLAN_LIMIT)

    def handle(self, *args, **options):
        result = bulk_load_entity_caches(
            top_player_limit=options['top_players'],
            clan_member_clans=options['clan_member_clans'],
            clan_limit=options['clan_limit'],
        )
        self.stdout.write(json.dumps(result, sort_keys=True, indent=2))
