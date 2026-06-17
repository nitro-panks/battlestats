"""Monthly cold-archive + prune of old battle-history rows.

Exports BattleEvent / PlayerDailyShipStats rows older than the retention window
to a gzip CSV + manifest on local disk, verifies the archive (count + sha256),
then deletes ONLY the rows that physically landed in the verified archive, and
runs VACUUM (ANALYZE). BattleObservation is out of scope (see the core docstring
in warships/incremental_battles.py and the runbook).

Runbook: agents/runbooks/runbook-battle-history-archive-prune-2026-06-17.md

Usage:
    # Always dry-run first — reports candidate counts + destination, no writes.
    python manage.py archive_battle_history --dry-run

    # Live (requires BATTLE_HISTORY_ARCHIVE_ENABLED=1, or pass --force):
    python manage.py archive_battle_history --sleep 0.5

    # Throttled rollout slice:
    python manage.py archive_battle_history --force --max-rows 10000 --sleep 0.5
"""
from __future__ import annotations

import os

from django.core.management.base import BaseCommand, CommandError

from warships.incremental_battles import (
    ARCHIVE_BATCH_SIZE_DEFAULT,
    ARCHIVE_RETENTION_DAYS_DEFAULT,
    ARCHIVE_STATEMENT_TIMEOUT_DEFAULT,
    ARCHIVE_TABLES,
    archive_and_prune_battle_history,
)


def _default_archive_dir() -> str:
    explicit = os.getenv("BATTLE_HISTORY_ARCHIVE_DIR")
    if explicit:
        return explicit
    app_root = os.getenv("APP_ROOT", "/opt/battlestats-server")
    return os.path.join(app_root, "shared", "archives", "battle_history")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


class Command(BaseCommand):
    help = (
        "Cold-archive (gzip CSV + manifest) then prune BattleEvent / "
        "PlayerDailyShipStats rows older than the retention window. Verifies "
        "the archive before deleting; deletes only archived+verified rows."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--retention-days", type=int, dest="retention_days",
            default=_env_int("BATTLE_HISTORY_ARCHIVE_RETENTION_DAYS",
                             ARCHIVE_RETENTION_DAYS_DEFAULT),
            help=("Keep this many days live; rows strictly older than "
                  "midnight-UTC(now) - N are archived + deleted. "
                  f"Default (env BATTLE_HISTORY_ARCHIVE_RETENTION_DAYS): "
                  f"{ARCHIVE_RETENTION_DAYS_DEFAULT}."),
        )
        parser.add_argument(
            "--tables", nargs="+", choices=sorted(ARCHIVE_TABLES.keys()),
            default=None,
            help="Subset of tables to process. Default: all.",
        )
        parser.add_argument(
            "--archive-dir", dest="archive_dir", default=_default_archive_dir(),
            help="Output root. Default: env BATTLE_HISTORY_ARCHIVE_DIR.",
        )
        parser.add_argument(
            "--batch-size", type=int, dest="batch_size",
            default=_env_int("BATTLE_HISTORY_ARCHIVE_BATCH_SIZE",
                             ARCHIVE_BATCH_SIZE_DEFAULT),
            help=f"PKs deleted per transaction. Default: {ARCHIVE_BATCH_SIZE_DEFAULT}.",
        )
        parser.add_argument(
            "--max-rows", type=int, dest="max_rows", default=0,
            help="Cap rows archived+deleted per table this run (0 = unlimited).",
        )
        parser.add_argument(
            "--sleep", type=float, dest="sleep",
            default=_env_float("BATTLE_HISTORY_ARCHIVE_SLEEP", 0.0),
            help="Seconds to pause between delete batches. Default: 0.",
        )
        parser.add_argument(
            "--statement-timeout", type=int, dest="statement_timeout",
            default=_env_int("BATTLE_HISTORY_ARCHIVE_STATEMENT_TIMEOUT",
                             ARCHIVE_STATEMENT_TIMEOUT_DEFAULT),
            help=("Postgres per-query timeout in seconds for the count + "
                  "delete batches (0 = none; the long COPY is never bounded). "
                  f"Default: {ARCHIVE_STATEMENT_TIMEOUT_DEFAULT}."),
        )
        parser.add_argument(
            "--skip-vacuum", action="store_true", dest="skip_vacuum",
            help="Skip the post-delete VACUUM (ANALYZE).",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report candidate counts + destination; write/delete nothing.",
        )
        parser.add_argument(
            "--force", action="store_true",
            help="Run live even if BATTLE_HISTORY_ARCHIVE_ENABLED != 1.",
        )

    def handle(self, *args, **options):
        if options["retention_days"] < 0:
            raise CommandError("--retention-days must be >= 0")
        if options["batch_size"] < 1:
            raise CommandError("--batch-size must be >= 1")
        if options["sleep"] < 0:
            raise CommandError("--sleep must be >= 0")
        if options["statement_timeout"] < 0:
            raise CommandError("--statement-timeout must be >= 0")
        if options["max_rows"] < 0:
            raise CommandError("--max-rows must be >= 0")

        dry_run = options["dry_run"]
        enabled = os.getenv("BATTLE_HISTORY_ARCHIVE_ENABLED", "0") == "1"
        if not dry_run and not enabled and not options["force"]:
            self.stdout.write(self.style.WARNING(
                "archive_battle_history disabled "
                "(BATTLE_HISTORY_ARCHIVE_ENABLED != 1). "
                "Pass --force to run live, or --dry-run to preview. No-op."))
            return

        result = archive_and_prune_battle_history(
            retention_days=options["retention_days"],
            tables=options["tables"],
            archive_dir=options["archive_dir"],
            batch_size=options["batch_size"],
            max_rows=options["max_rows"],
            dry_run=dry_run,
            sleep_between_batches=options["sleep"],
            statement_timeout_s=options["statement_timeout"],
            skip_vacuum=options["skip_vacuum"],
        )

        if result.get("status") == "skipped":
            self.stdout.write(self.style.WARNING(
                f"Skipped: {result.get('reason')}"))
            return

        for t in result.get("tables", []):
            status = t.get("status")
            if status == "dry_run":
                self.stdout.write(
                    f"DRY-RUN [{t['table']}]: {t['candidates']:,} rows "
                    f"< {t['cutoff']} would be archived to {t['archive_file']} "
                    f"and deleted (range {t['min_date']}..{t['max_date']}).")
            elif status == "completed":
                self.stdout.write(self.style.SUCCESS(
                    f"[{t['table']}]: archived {t['exported']:,} rows "
                    f"(sha256 {t['sha256'][:12]}…), deleted {t['deleted']:,}, "
                    f"vacuumed={t.get('vacuumed')} -> {t['archive_file']}"))
            elif status == "skipped":
                self.stdout.write(
                    f"[{t['table']}]: skipped ({t.get('reason')}).")
            else:
                self.stdout.write(self.style.ERROR(
                    f"[{t['table']}]: FAILED ({t.get('reason')}) — "
                    f"archive kept, nothing deleted."))

        if dry_run:
            self.stdout.write(self.style.WARNING("--dry-run: no rows written"))
            return

        if result.get("status") != "completed":
            raise CommandError(
                "archive_battle_history completed with failures (see above).")
