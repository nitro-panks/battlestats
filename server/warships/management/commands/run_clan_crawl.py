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

    def handle(self, *args, **options):
        from warships.clan_crawl import run_clan_crawl

        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')

        core_only = not options['full']
        result = run_clan_crawl(
            realm=options['realm'],
            core_only=core_only,
            dry_run=options['dry_run'],
            resume=options['resume'],
            limit=options['limit'],
        )
        self.stdout.write(json.dumps(result, default=str, sort_keys=True))
