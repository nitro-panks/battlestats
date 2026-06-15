"""One-time backfill: seed the Hot-Players queue with the most-active players.

Fills each realm's queue up to ``HOT_PLAYERS_MAX`` with active (within
``HOT_BACKFILL_ACTIVE_DAYS``), non-hidden players ordered by ``pvp_battles`` desc
(recent + high volume), as ``source='backfill'`` rows ranked BELOW every
engagement member. The seeds get the same guaranteed daily capture + freshness as
engaged members but are protected from inactivity-eviction and are the FIRST
trimmed when engaged players need the slots (they also graduate to 'engagement' if
they later earn view-recurrence). Idempotent — re-running tops the queue back up
to the cap without duplicating. No WG calls (pure DB).

Mirrors ``maintain_hot_players`` ergonomics: ``--realm`` + ``--dry-run``, plus
``--all-realms`` to seed na/eu/asia in one invocation. Runbook:
``agents/runbooks/runbook-hot-players-engagement-queue-2026-06-10.md``.
"""
from django.core.management.base import BaseCommand

from warships.hot_players import backfill_hot_players
from warships.models import DEFAULT_REALM, VALID_REALMS


class Command(BaseCommand):
    help = "Seed the Hot-Players queue to the cap with the most-active players for a realm."

    def add_arguments(self, parser):
        parser.add_argument('--realm', default=DEFAULT_REALM,
                            choices=sorted(VALID_REALMS))
        parser.add_argument('--all-realms', action='store_true',
                            help='Seed every realm (overrides --realm).')
        parser.add_argument('--dry-run', action='store_true',
                            help='Size the backfill without writing.')

    def handle(self, *args, **opts):
        dry_run = opts['dry_run']
        realms = sorted(VALID_REALMS) if opts['all_realms'] else [opts['realm']]
        out = self.stdout.write
        label = "DRY RUN " if dry_run else ""
        for realm in realms:
            r = backfill_hot_players(realm, dry_run=dry_run)
            out(f"=== backfill_hot_players {label}realm={realm} ===")
            out(f"Cap: {r['cap']}  Current: {r['current']}  "
                f"Open slots: {r['slots']}  Added: {r['added']}")
