"""Recompute Player.enrichment_status from current row state.

Run periodically to absorb players whose hidden / activity / battle-count /
win-rate state changed since their last enrichment classification. Idempotent
and safe to re-run.

Reclassification rules (most specific wins, ordered to match the
``_candidates()`` gate in ``warships.management.commands.enrich_player_data``
so reclassified rows stay consistent with what the live crawler will pick up):

  battles_json non-empty list           -> enriched
  battles_json == []                    -> empty
  is_hidden=True                        -> skipped_hidden
  pvp_battles < MIN_PVP_BATTLES         -> skipped_low_battles
  days_since_last_battle > MAX_INACTIVE -> skipped_inactive
  pvp_ratio < MIN_WR                    -> skipped_low_wr
  otherwise                             -> pending

``MIN_PVP_BATTLES`` and ``MIN_WR`` read the same env vars as
``warships.tasks.enrich_player_data_task`` so the two stay in lockstep.
"""
import os

from django.core.management.base import BaseCommand
from django.db import transaction

from warships.models import Player


MIN_PVP_BATTLES = int(os.getenv("ENRICH_MIN_PVP_BATTLES", "500"))
MIN_WR = float(os.getenv("ENRICH_MIN_WR", "48.0"))
MAX_INACTIVE_DAYS = 365


class Command(BaseCommand):
    help = "Recompute Player.enrichment_status across the catalog."

    def add_arguments(self, parser):
        parser.add_argument(
            '--realm', default=None,
            help='Limit to a single realm (na/eu/asia). Default: all.',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Print what would change without writing.',
        )

    def handle(self, *args, **opts):
        realm = opts.get('realm')
        dry_run = opts.get('dry_run', False)

        base = Player.objects.all()
        if realm:
            base = base.filter(realm=realm)

        self.stdout.write(
            f"Thresholds: MIN_PVP_BATTLES={MIN_PVP_BATTLES} "
            f"MIN_WR={MIN_WR} MAX_INACTIVE_DAYS={MAX_INACTIVE_DAYS}"
        )

        # Order matters: most specific buckets first so the cheaper updates
        # don't undo a more specific classification.
        plan = [
            (
                'enriched',
                base.filter(battles_json__isnull=False).exclude(battles_json=[]),
            ),
            (
                'empty',
                base.filter(battles_json=[]),
            ),
            (
                'skipped_hidden',
                base.filter(is_hidden=True, battles_json__isnull=True),
            ),
            (
                'skipped_low_battles',
                base.filter(
                    is_hidden=False,
                    battles_json__isnull=True,
                    pvp_battles__lt=MIN_PVP_BATTLES,
                ),
            ),
            (
                'skipped_inactive',
                base.filter(
                    is_hidden=False,
                    battles_json__isnull=True,
                    pvp_battles__gte=MIN_PVP_BATTLES,
                    days_since_last_battle__gt=MAX_INACTIVE_DAYS,
                ),
            ),
            (
                'skipped_low_wr',
                base.filter(
                    is_hidden=False,
                    battles_json__isnull=True,
                    pvp_battles__gte=MIN_PVP_BATTLES,
                    days_since_last_battle__lte=MAX_INACTIVE_DAYS,
                    pvp_ratio__lt=MIN_WR,
                ),
            ),
            (
                'pending',
                base.filter(
                    is_hidden=False,
                    battles_json__isnull=True,
                    pvp_battles__gte=MIN_PVP_BATTLES,
                    days_since_last_battle__lte=MAX_INACTIVE_DAYS,
                    pvp_ratio__gte=MIN_WR,
                ),
            ),
        ]

        results = {}
        with transaction.atomic():
            for status, qs in plan:
                # Only touch rows that aren't already in this bucket.
                changing = qs.exclude(enrichment_status=status)
                count = changing.count() if dry_run else changing.update(
                    enrichment_status=status)
                results[status] = count

            if dry_run:
                transaction.set_rollback(True)

        verb = 'Would update' if dry_run else 'Updated'
        for status, count in results.items():
            self.stdout.write(f"{verb} {count:>8} rows -> {status}")
