import json
import os

from django.core.management.base import BaseCommand, CommandError

from warships.visit_analytics import cleanup_entity_visit_events


def _enabled() -> bool:
    """Env kill switch read at call time. Mirrors downsample_snapshots._enabled
    so the systemd timer calls this command directly (no fragile inline shell
    gate) and it no-ops while disabled. See
    runbook-data-lifecycle-architecture-2026-06-21."""
    return os.getenv("ENTITY_VISIT_CLEANUP_ENABLED", "0").strip().lower() in (
        "1", "true", "yes", "on",
    )


class Command(BaseCommand):
    help = 'Delete old EntityVisitEvent rows while keeping EntityVisitDaily aggregates intact.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--older-than-days',
            type=int,
            default=int(os.getenv('ENTITY_VISIT_CLEANUP_OLDER_THAN_DAYS', '180')),
            help=('Delete raw event rows older than this many days. Defaults to '
                  'ENTITY_VISIT_CLEANUP_OLDER_THAN_DAYS (180) so the timer needs no args.'),
        )
        parser.add_argument('--dry-run', action='store_true',
                            help='Preview the deletion count without deleting rows.')

    def handle(self, *args, **options):
        older_than_days = int(options['older_than_days'])
        if older_than_days < 1:
            raise CommandError('older-than-days must be at least 1.')

        # Kill switch gates only live deletes; --dry-run is always allowed.
        if not options.get('dry_run') and not _enabled():
            self.stdout.write(self.style.WARNING(
                'ENTITY_VISIT_CLEANUP_ENABLED is not set — no-op. '
                'Re-run with --dry-run to preview, or set the env to delete.'))
            return

        result = cleanup_entity_visit_events(
            older_than_days=older_than_days,
            dry_run=bool(options.get('dry_run')),
        )
        self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
