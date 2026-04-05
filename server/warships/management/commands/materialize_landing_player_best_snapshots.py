import json

from django.core.management.base import BaseCommand

from warships.landing import LANDING_PLAYER_BEST_SORTS, materialize_landing_player_best_snapshots
from warships.models import VALID_REALMS


class Command(BaseCommand):
    help = 'Materialize DB-backed landing Best-player snapshots for one or more realms.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--realm',
            choices=sorted(VALID_REALMS),
            action='append',
            dest='realms',
            help='Realm to materialize. Repeat to materialize multiple realms. Defaults to all realms.',
        )
        parser.add_argument(
            '--sort',
            choices=LANDING_PLAYER_BEST_SORTS,
            action='append',
            dest='sorts',
            help='Best-player sort to materialize. Repeat to scope the run. Defaults to all best sorts.',
        )

    def handle(self, *args, **options):
        realms = options.get('realms') or sorted(VALID_REALMS)
        sorts = options.get('sorts') or None
        results = {
            realm: materialize_landing_player_best_snapshots(
                realm=realm,
                sorts=sorts,
            )
            for realm in realms
        }
        self.stdout.write(json.dumps(results, sort_keys=True))
