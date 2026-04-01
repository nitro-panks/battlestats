"""Management command: per-realm health summary."""
from datetime import timedelta

from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.db.models import Count, Max, Q
from django.utils import timezone

from warships.models import Clan, Player, VALID_REALMS
from warships.tasks import _clan_crawl_lock_key


class Command(BaseCommand):
    help = 'Report per-realm health: player/clan counts, freshness, crawl status.'

    def handle(self, *args, **options):
        now = timezone.now()
        fresh_cutoff = now - timedelta(days=7)

        for realm in sorted(VALID_REALMS):
            player_qs = Player.objects.filter(realm=realm)
            clan_qs = Clan.objects.filter(realm=realm)

            player_count = player_qs.count()
            clan_count = clan_qs.count()

            agg = player_qs.aggregate(
                latest_fetch=Max('last_fetch'),
                fresh_count=Count('pk', filter=Q(last_fetch__gte=fresh_cutoff)),
            )
            latest_fetch = agg['latest_fetch']
            fresh_count = agg['fresh_count']
            freshness_pct = (fresh_count / player_count * 100) if player_count else 0

            lock_val = cache.get(_clan_crawl_lock_key(realm))
            crawl_status = f'active (task {lock_val})' if lock_val else 'idle'

            self.stdout.write(self.style.SUCCESS(f'\n=== {realm.upper()} ==='))
            self.stdout.write(f'  Players:        {player_count:,}')
            self.stdout.write(f'  Clans:          {clan_count:,}')
            self.stdout.write(f'  Latest fetch:   {latest_fetch or "never"}')
            self.stdout.write(f'  Fresh (7d):     {fresh_count:,} / {player_count:,} ({freshness_pct:.1f}%)')
            self.stdout.write(f'  Crawl status:   {crawl_status}')
