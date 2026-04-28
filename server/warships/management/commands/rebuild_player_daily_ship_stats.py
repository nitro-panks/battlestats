"""Rebuild PlayerDailyShipStats for a date range from BattleEvent.

Phase 3 of the battle-history rollout. Used for backfills and post-incident
repair. Idempotent: runs the same `rebuild_daily_ship_stats_for_date` logic
as the nightly sweeper task.

Examples:
    # Rebuild a single day:
    python manage.py rebuild_player_daily_ship_stats --since 2026-04-28

    # Rebuild a range:
    python manage.py rebuild_player_daily_ship_stats --since 2026-04-20 --until 2026-04-28
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Rebuild PlayerDailyShipStats from BattleEvent for a date range."

    def add_arguments(self, parser):
        parser.add_argument(
            "--since", required=True,
            help="First date to rebuild (inclusive), YYYY-MM-DD.",
        )
        parser.add_argument(
            "--until", default=None,
            help="Last date to rebuild (inclusive), YYYY-MM-DD. "
                 "Defaults to --since (single day).",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Print planned actions; do not write.",
        )

    def handle(self, *args, **options):
        from warships.incremental_battles import rebuild_daily_ship_stats_for_date
        from warships.models import BattleEvent, PlayerDailyShipStats

        try:
            since = datetime.strptime(options["since"], "%Y-%m-%d").date()
        except ValueError as e:
            raise CommandError(f"--since must be YYYY-MM-DD: {e}") from e

        if options["until"]:
            try:
                until = datetime.strptime(options["until"], "%Y-%m-%d").date()
            except ValueError as e:
                raise CommandError(f"--until must be YYYY-MM-DD: {e}") from e
        else:
            until = since

        if until < since:
            raise CommandError("--until must be >= --since")

        cursor: date = since
        total_events = 0
        total_rows_written = 0
        while cursor <= until:
            event_count = BattleEvent.objects.filter(
                detected_at__date=cursor,
            ).count()
            existing_rows = PlayerDailyShipStats.objects.filter(
                date=cursor,
            ).count()
            if options["dry_run"]:
                self.stdout.write(self.style.WARNING(
                    f"[dry-run] {cursor}: would rebuild "
                    f"{event_count} events into "
                    f"~{existing_rows or '?'} rows"
                ))
            else:
                result = rebuild_daily_ship_stats_for_date(cursor)
                self.stdout.write(self.style.SUCCESS(
                    f"{cursor}: events={result['events_seen']} "
                    f"deleted={result['rows_deleted']} "
                    f"written={result['rows_written']}"
                ))
                total_rows_written += result["rows_written"]
            total_events += event_count
            cursor = cursor + timedelta(days=1)

        self.stdout.write(self.style.SUCCESS(
            f"Done. {since}..{until}: total_events_seen={total_events} "
            f"total_rows_written={total_rows_written}"
        ))
