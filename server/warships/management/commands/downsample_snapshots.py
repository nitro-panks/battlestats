"""Downsample old ``warships_snapshot`` rows to one-per-player-per-ISO-week.

Keeps full daily granularity for the last ``--retention-days`` (default 90),
collapses older rows to the weekly keeper (latest date in each ISO week). See
``warships.snapshot_retention.downsample_snapshots`` for the policy rationale
and ``runbook-data-lifecycle-architecture-2026-06-21.md``.

Gated by ``SNAPSHOT_DOWNSAMPLE_ENABLED`` (default off): the systemd timer fires
unconditionally but the command no-ops while disabled — matching the
battle-history archive convention. Always ``--dry-run`` first.

Usage:
    python manage.py downsample_snapshots --dry-run
    SNAPSHOT_DOWNSAMPLE_ENABLED=1 python manage.py downsample_snapshots \
        --batch-size 5000 --sleep 0.5
"""
from __future__ import annotations

import json
import os

from django.core.management.base import BaseCommand, CommandError

from warships.snapshot_retention import (
    BATCH_SIZE_DEFAULT,
    RETENTION_DAYS_DEFAULT,
    downsample_snapshots,
)


def _enabled() -> bool:
    return os.getenv("SNAPSHOT_DOWNSAMPLE_ENABLED", "0").strip().lower() in (
        "1", "true", "yes", "on",
    )


class Command(BaseCommand):
    help = (
        "Collapse Snapshot rows older than --retention-days to one row per "
        "player per ISO-week (keeping cumulative battles/wins at week "
        "granularity). Gated by SNAPSHOT_DOWNSAMPLE_ENABLED. Deletes rows."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--retention-days", type=int,
            default=int(os.getenv("SNAPSHOT_DOWNSAMPLE_RETENTION_DAYS",
                                  str(RETENTION_DAYS_DEFAULT))),
            dest="retention_days",
            help=(
                "Keep full daily granularity for this many days; downsample "
                f"older rows. Default: {RETENTION_DAYS_DEFAULT}."
            ),
        )
        parser.add_argument(
            "--batch-size", type=int, default=BATCH_SIZE_DEFAULT,
            dest="batch_size",
            help=f"Rows deleted per transaction. Default: {BATCH_SIZE_DEFAULT}.",
        )
        parser.add_argument(
            "--max-rows", type=int, default=0, dest="max_rows",
            help="Cap rows deleted this run (0 = unlimited). Default: 0.",
        )
        parser.add_argument(
            "--sleep", type=float, default=0.0, dest="sleep",
            help="Seconds to pause between delete batches. Default: 0.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report candidate/keeper/deletable counts without writing.",
        )

    def handle(self, *args, **options):
        dry_run = bool(options.get("dry_run"))

        # The kill switch gates only live deletes — a --dry-run readout is
        # always allowed so the timer/operator can measure without flipping it.
        if not dry_run and not _enabled():
            self.stdout.write(self.style.WARNING(
                "SNAPSHOT_DOWNSAMPLE_ENABLED is not set — no-op. "
                "Re-run with --dry-run to preview, or set the env to delete."
            ))
            return

        try:
            result = downsample_snapshots(
                retention_days=options["retention_days"],
                batch_size=options["batch_size"],
                max_rows=options["max_rows"],
                dry_run=dry_run,
                sleep_between_batches=options["sleep"],
            )
        except ValueError as exc:
            raise CommandError(str(exc))

        if dry_run:
            self.stdout.write(
                f"DRY-RUN: {result['deletable']:,} of {result['candidates']:,} "
                f"pre-cutoff Snapshot rows would be deleted "
                f"(keeping {result['keepers']:,} weekly keepers; "
                f"retention_days={result['retention_days']}, "
                f"cutoff={result['cutoff']})."
            )
            self.stdout.write(self.style.WARNING("--dry-run: no rows written"))
            return

        self.stdout.write(self.style.SUCCESS(
            f"Downsampled: deleted {result['deleted']:,} Snapshot rows in "
            f"{result['batches']} batch(es), kept {result['keepers']:,} weekly "
            f"keepers (cutoff={result['cutoff']})."
        ))
        self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
