"""Health/observability report for the Hot-Players engagement-capture queue.

Read-only. Per realm reports: hot-set size, today's promotions (by
``promoted_at``), oldest ``last_engaged_at`` (the eviction frontier), the count
of hot players who are NOT active-7d (the marginal-cost set the floor doesn't
already cover), and today's capture coverage (how many got a fresh observation +
a fresh snapshot). Belongs to the ``check_*`` / ``enrichment-status`` family.

Runbook: ``agents/runbooks/runbook-hot-players-engagement-queue-2026-06-10.md``.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from warships.models import HotPlayer, Player, VALID_REALMS


class Command(BaseCommand):
    help = "Report Hot-Players engagement-capture queue health per realm."

    def add_arguments(self, parser):
        parser.add_argument('--realm', default=None, choices=sorted(VALID_REALMS),
                            help='Limit to one realm (default: all realms).')

    def handle(self, *args, **opts):
        realms = [opts['realm']] if opts['realm'] else sorted(VALID_REALMS)
        now = timezone.now()
        today = now.date()
        active_cutoff = today - timedelta(days=7)
        out = self.stdout.write

        for realm in realms:
            members = HotPlayer.objects.filter(realm=realm)
            size = members.count()
            pinned = members.filter(source=HotPlayer.SOURCE_PINNED).count()
            promoted_today = members.filter(promoted_at__date=today).count()

            oldest = (
                members.exclude(last_engaged_at__isnull=True)
                .order_by('last_engaged_at')
                .values_list('last_engaged_at', flat=True)
                .first()
            )

            # Hot players who are NOT active-7d — the marginal-cost set the
            # observation floor no longer touches. Use the WG account id
            # (Player.player_id), NOT the HotPlayer FK pk.
            hot_ids = list(members.values_list('player__player_id', flat=True))
            active_7d_ids = set(
                Player.objects
                .filter(realm=realm, player_id__in=hot_ids,
                        last_battle_date__isnull=False,
                        last_battle_date__gte=active_cutoff)
                .values_list('player_id', flat=True)
            )
            not_active_7d = len([pid for pid in hot_ids if pid not in active_7d_ids])

            observed_today = members.filter(last_observed_at__date=today).count()
            snapshotted_today = members.filter(
                last_snapshotted_at__date=today).count()

            out(f"=== hot_players_status realm={realm} ===")
            out(f"Hot-set size: {size}  (pinned: {pinned})")
            out(f"Promoted today: {promoted_today}")
            out(f"Oldest last_engaged_at: {oldest if oldest else 'n/a'}")
            out(f"Hot players NOT active-7d (marginal-cost set): {not_active_7d}")
            out(f"Capture coverage today: observed={observed_today} "
                f"snapshotted={snapshotted_today}")
