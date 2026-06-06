"""Report battle-history daily rollup coverage gaps (alert-only, read-only).

Compares BattleEvent vs PlayerDailyShipStats battle counts per (date, mode)
over an audit window and prints any date the daily layer is missing or
under-counts. Writes nothing — repair is the `rebuild_player_daily_ship_stats`
command (day-by-day for a span).

See agents/runbooks/runbook-battle-history-rollup-durability-2026-06-06.md.

Examples:
    python manage.py reconcile_battle_history_rollup
    python manage.py reconcile_battle_history_rollup --audit-days 60
"""
from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Report PlayerDailyShipStats coverage gaps vs BattleEvent (read-only)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--audit-days", type=int, default=30,
            help="Number of trailing days to audit (default 30).",
        )

    def handle(self, *args, **options):
        from warships.incremental_battles import reconcile_daily_rollup_coverage

        audit_days = max(1, options["audit_days"])
        report = reconcile_daily_rollup_coverage(audit_days=audit_days)
        discrepancies = report["discrepancies"]

        if not discrepancies:
            self.stdout.write(self.style.SUCCESS(
                f"Clean: no rollup gaps over the last {audit_days} days."))
            return

        self.stdout.write(self.style.WARNING(
            f"Found {len(discrepancies)} rollup gap(s) over {audit_days} days:"))
        for d in discrepancies:
            self.stdout.write(self.style.WARNING(
                f"  {d['date']} {d['mode']}: "
                f"be_battles={d['be_battles']} pds_battles={d['pds_battles']} "
                f"delta={d['delta']}"))
        self.stdout.write(
            "Repair with: python manage.py rebuild_player_daily_ship_stats "
            "--since <date> --until <date>")
