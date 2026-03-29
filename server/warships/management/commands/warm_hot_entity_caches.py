import json

from django.core.management.base import BaseCommand

from warships.data import HOT_ENTITY_CLAN_LIMIT, HOT_ENTITY_PLAYER_LIMIT, warm_hot_entity_caches


class Command(BaseCommand):
    help = 'Warm caches for top-visited and pinned players/clans.'

    def add_arguments(self, parser):
        parser.add_argument('--player-limit', type=int, default=HOT_ENTITY_PLAYER_LIMIT)
        parser.add_argument('--clan-limit', type=int, default=HOT_ENTITY_CLAN_LIMIT)
        parser.add_argument('--force-refresh', action='store_true', default=False)

    def handle(self, *args, **options):
        result = warm_hot_entity_caches(
            player_limit=options['player_limit'],
            clan_limit=options['clan_limit'],
            force_refresh=options['force_refresh'],
        )
        self.stdout.write(json.dumps(result, sort_keys=True, indent=2))
