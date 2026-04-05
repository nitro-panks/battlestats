import json
import os

from django.core.cache import cache
from django.core.management.base import BaseCommand, CommandError

from warships.data import warm_landing_best_entity_caches
from warships.landing import LANDING_PLAYER_BEST_SORTS, invalidate_landing_clan_caches, invalidate_landing_player_caches, materialize_landing_player_best_snapshots, warm_landing_page_content
from warships.models import LandingPlayerBestSnapshot, VALID_REALMS
from warships.tasks import _bulk_cache_load_lock_key, _correlation_warm_lock_key, _distribution_warm_lock_key, _hot_entity_cache_warm_lock_key, _landing_best_entity_warm_lock_key, _landing_page_warm_lock_key, _landing_player_best_snapshot_refresh_lock_key


LOCK_KEY_FACTORIES = {
    'landing_page': _landing_page_warm_lock_key,
    'best_player_snapshots': _landing_player_best_snapshot_refresh_lock_key,
    'player_distributions': _distribution_warm_lock_key,
    'player_correlations': _correlation_warm_lock_key,
    'hot_entities': _hot_entity_cache_warm_lock_key,
    'best_entities': _landing_best_entity_warm_lock_key,
    'bulk_entities': _bulk_cache_load_lock_key,
}


class Command(BaseCommand):
    help = 'Run bounded post-deploy verification, snapshot rebuilds, cache invalidation, and landing warm operations.'

    def add_arguments(self, parser):
        parser.add_argument(
            'operation',
            choices=('verify', 'snapshots', 'invalidate',
                     'warm-landing', 'warm-best-entities'),
            help='Post-deploy operation to run.',
        )
        parser.add_argument(
            '--realm',
            choices=sorted(VALID_REALMS),
            action='append',
            dest='realms',
            help='Realm to target. Repeat to target multiple realms. Defaults to all realms.',
        )
        parser.add_argument(
            '--sort',
            choices=LANDING_PLAYER_BEST_SORTS,
            action='append',
            dest='sorts',
            help='Best-player sort to rebuild for snapshot operations. Repeat to scope multiple sorts.',
        )
        parser.add_argument('--players', action='store_true', default=False)
        parser.add_argument('--clans', action='store_true', default=False)
        parser.add_argument('--include-recent',
                            action='store_true', default=False)
        parser.add_argument('--force-refresh',
                            action='store_true', default=False)
        parser.add_argument('--player-limit', type=int, default=25)
        parser.add_argument('--clan-limit', type=int, default=25)

    def handle(self, *args, **options):
        operation = options['operation']
        realms = self._get_realms(options.get('realms'))

        if operation == 'verify':
            result = self._handle_verify(realms)
        elif operation == 'snapshots':
            result = self._handle_snapshots(realms, options.get('sorts'))
        elif operation == 'invalidate':
            result = self._handle_invalidate(
                realms,
                players=options['players'],
                clans=options['clans'],
                include_recent=options['include_recent'],
            )
        elif operation == 'warm-landing':
            result = self._handle_warm_landing(
                realms,
                include_recent=options['include_recent'],
                force_refresh=options['force_refresh'],
            )
        else:
            result = self._handle_warm_best_entities(
                realms,
                player_limit=options['player_limit'],
                clan_limit=options['clan_limit'],
                force_refresh=options['force_refresh'],
            )

        self.stdout.write(json.dumps(result, sort_keys=True))

    def _get_realms(self, selected_realms):
        if not selected_realms:
            return sorted(VALID_REALMS)
        if isinstance(selected_realms, str):
            return [selected_realms]
        return list(dict.fromkeys(selected_realms))

    def _handle_verify(self, realms):
        snapshots_by_realm = {realm: [] for realm in realms}
        snapshot_rows = LandingPlayerBestSnapshot.objects.filter(
            realm__in=realms,
        ).values_list('realm', 'sort')
        for realm, sort in snapshot_rows:
            snapshots_by_realm.setdefault(realm, []).append(sort)

        snapshots = {}
        for realm in realms:
            present_sorts = sorted(set(snapshots_by_realm.get(realm, [])))
            missing_sorts = [
                sort for sort in LANDING_PLAYER_BEST_SORTS if sort not in present_sorts]
            snapshots[realm] = {
                'count': len(present_sorts),
                'present_sorts': present_sorts,
                'missing_sorts': missing_sorts,
                'all_present': not missing_sorts,
            }

        locks = {
            realm: {
                lock_name: bool(cache.get(lock_factory(realm)))
                for lock_name, lock_factory in LOCK_KEY_FACTORIES.items()
            }
            for realm in realms
        }

        return {
            'status': 'completed',
            'operation': 'verify',
            'realms': realms,
            'warm_caches_on_startup': os.getenv('WARM_CACHES_ON_STARTUP', ''),
            'snapshots': snapshots,
            'locks': locks,
        }

    def _handle_snapshots(self, realms, sorts):
        if isinstance(sorts, str):
            selected_sorts = [sorts]
        else:
            selected_sorts = list(dict.fromkeys(sorts or [])) or None
        results = {
            realm: materialize_landing_player_best_snapshots(
                realm=realm,
                sorts=selected_sorts,
            )
            for realm in realms
        }
        return {
            'status': 'completed',
            'operation': 'snapshots',
            'realms': realms,
            'sorts': selected_sorts or list(LANDING_PLAYER_BEST_SORTS),
            'results': results,
        }

    def _handle_invalidate(self, realms, players, clans, include_recent):
        if not players and not clans:
            raise CommandError(
                'invalidate requires at least one of --players or --clans')

        invalidated = {
            'players': [],
            'clans': [],
        }

        for realm in realms:
            if players:
                invalidate_landing_player_caches(
                    include_recent=include_recent,
                    realm=realm,
                    queue_republish=False,
                )
                invalidated['players'].append(realm)
            if clans:
                invalidate_landing_clan_caches(
                    realm=realm,
                    queue_republish=False,
                )
                invalidated['clans'].append(realm)

        return {
            'status': 'completed',
            'operation': 'invalidate',
            'realms': realms,
            'include_recent': include_recent,
            'queue_republish': False,
            'invalidated': invalidated,
        }

    def _handle_warm_landing(self, realms, include_recent, force_refresh):
        results = {
            realm: warm_landing_page_content(
                force_refresh=force_refresh,
                include_recent=include_recent,
                realm=realm,
            )
            for realm in realms
        }
        return {
            'status': 'completed',
            'operation': 'warm-landing',
            'realms': realms,
            'include_recent': include_recent,
            'force_refresh': force_refresh,
            'results': results,
        }

    def _handle_warm_best_entities(self, realms, player_limit, clan_limit, force_refresh):
        results = {
            realm: warm_landing_best_entity_caches(
                player_limit=player_limit,
                clan_limit=clan_limit,
                force_refresh=force_refresh,
                realm=realm,
            )
            for realm in realms
        }
        return {
            'status': 'completed',
            'operation': 'warm-best-entities',
            'realms': realms,
            'player_limit': player_limit,
            'clan_limit': clan_limit,
            'force_refresh': force_refresh,
            'results': results,
        }
