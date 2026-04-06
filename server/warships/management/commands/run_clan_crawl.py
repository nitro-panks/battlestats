import json
import logging

from django.core.management.base import BaseCommand

from warships.models import VALID_REALMS


class Command(BaseCommand):
    help = 'Run a full clan crawl for a realm (populates clans + players).'

    def add_arguments(self, parser):
        parser.add_argument('--realm', choices=sorted(VALID_REALMS), default='asia')
        parser.add_argument('--core-only', action='store_true', default=True,
                            help='Save core player stats only, skip enrichment (default)')
        parser.add_argument('--full', action='store_true',
                            help='Disable core-only mode — run enrichment per player')
        parser.add_argument('--dry-run', action='store_true',
                            help='Discover clans but do not save any data')
        parser.add_argument('--resume', action='store_true',
                            help='Skip clans already in the database')
        parser.add_argument('--limit', type=int, default=None,
                            help='Max number of clans to crawl')
        parser.add_argument('--partition', type=int, default=0,
                            help='Partition index (0-based) for parallel invocations')
        parser.add_argument('--num-partitions', type=int, default=1,
                            help='Total number of partitions')

    def handle(self, *args, **options):
        from warships.clan_crawl import crawl_clan_ids, crawl_clan_members, _crawl_request_delay

        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')

        realm = options['realm']
        core_only = not options['full']
        partition = options['partition']
        num_partitions = options['num_partitions']
        request_delay = _crawl_request_delay(core_only=core_only)

        clan_stubs = crawl_clan_ids(
            limit=options['limit'],
            realm=realm,
            request_delay=request_delay,
        )
        if not clan_stubs:
            self.stderr.write('Failed to fetch clan list')
            return

        if options['dry_run']:
            result = {'realm': realm, 'dry_run': True, 'clans_found': len(clan_stubs)}
            self.stdout.write(json.dumps(result, default=str, sort_keys=True))
            return

        # Partition the clan list for parallel execution
        if num_partitions > 1:
            total = len(clan_stubs)
            chunk_size = total // num_partitions
            start = partition * chunk_size
            end = total if partition == num_partitions - 1 else start + chunk_size
            clan_stubs = clan_stubs[start:end]
            logging.getLogger('crawl').info(
                'Partition %d/%d: clans %d-%d (%d of %d)',
                partition, num_partitions, start, end, len(clan_stubs), total,
            )

        summary = crawl_clan_members(
            clan_stubs,
            resume=options['resume'],
            realm=realm,
            core_only=core_only,
            request_delay=request_delay,
        )
        summary['realm'] = realm
        summary['partition'] = partition
        summary['num_partitions'] = num_partitions
        self.stdout.write(json.dumps(summary, default=str, sort_keys=True))
