"""Promote / evict / re-score the Hot-Players engagement-capture queue.

The DB-only "brain" of the engagement-capture loop. Computes an active-days
``GROUP BY`` over ``EntityVisitDaily`` (recurrence across distinct days, NOT
summed views) for the trailing ``HOT_PLAYERS_WINDOW_DAYS`` and applies the
promotion rule, the eviction rule (with hysteresis), and the ``HOT_PLAYERS_MAX``
cap/trim by ``hot_score``. No WG calls — coexists with crawls.

Mirrors ``snapshot_active_players`` ergonomics: ``--realm`` + ``--dry-run``
(dry-run sizes the promote/evict/trim deltas without writing). Runbook:
``agents/runbooks/runbook-hot-players-engagement-queue-2026-06-10.md``.
"""
from django.core.management.base import BaseCommand

from warships.hot_players import maintain_hot_players
from warships.models import DEFAULT_REALM, VALID_REALMS


class Command(BaseCommand):
    help = "Promote/evict/re-score the Hot-Players engagement-capture queue for a realm."

    def add_arguments(self, parser):
        parser.add_argument('--realm', default=DEFAULT_REALM,
                            choices=sorted(VALID_REALMS))
        parser.add_argument('--dry-run', action='store_true',
                            help='Size the promote/evict/trim deltas without writing.')

    def handle(self, *args, **opts):
        realm = opts['realm']
        dry_run = opts['dry_run']
        result = maintain_hot_players(realm, dry_run=dry_run)
        out = self.stdout.write
        label = "DRY RUN " if dry_run else ""
        out(f"=== maintain_hot_players {label}realm={realm} ===")
        out(f"Promoted: {result['promoted']}  Evicted: {result['evicted']}  "
            f"Re-scored: {result['updated']}  Trimmed (over cap): {result['trimmed']}")
        out(f"Hot-set size: {result['hot_set_size']}")
