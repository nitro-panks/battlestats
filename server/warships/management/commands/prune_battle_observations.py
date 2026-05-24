"""Reclaim disk by compacting stale BattleObservation JSON payloads.

The battle-history rollout (2026-05-01/02) made `BattleObservation` capture
append-only — every visit / crawl / floor refresh writes a row carrying a
full per-ship `ships_stats_json` (and, with ranked capture on, a
`ranked_ships_stats_json`) blob, with no retention. That table is the prime
suspect behind the managed-Postgres disk / read-only alerts documented in
`agents/runbooks/runbook-db-cpu-saturation-2026-05-24.md`.

This command does NOT delete observation rows: `BattleEvent.from_observation`
and `to_observation` are CASCADE FKs, so deleting a row would destroy the
durable per-battle event record. It NULLs the heavy JSON columns on
observations no longer needed as a diff baseline, keeping per player the
latest `--keep-per-player` observations plus the latest non-NULL-ranked one.

Usage:
    # Always dry-run first — reports candidates + reclaimable bytes, no writes.
    python manage.py prune_battle_observations --dry-run

    # Live, gentle: 2000-row batches, 0.5s pause, capped at 200k rows/run.
    python manage.py prune_battle_observations --batch-size 2000 \
        --sleep 0.5 --max-rows 200000

    # Keep a larger margin per player.
    python manage.py prune_battle_observations --keep-per-player 5 --dry-run
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from warships.incremental_battles import (
    COMPACT_BATCH_SIZE_DEFAULT,
    COMPACT_KEEP_PER_PLAYER_DEFAULT,
    compact_battle_observation_payloads,
)


def _human_bytes(n: int | None) -> str:
    if n is None:
        return "n/a (non-Postgres backend)"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


class Command(BaseCommand):
    help = (
        "Compact stale BattleObservation JSON payloads (ships_stats_json / "
        "ranked_ships_stats_json) to reclaim disk, keeping the per-player diff "
        "and ranked walk-back baselines. Does not delete rows."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--keep-per-player", type=int,
            default=COMPACT_KEEP_PER_PLAYER_DEFAULT,
            dest="keep_per_player",
            help=(
                "Observations per player to keep with full JSON (most recent "
                f"first). Default: {COMPACT_KEEP_PER_PLAYER_DEFAULT}. The "
                "latest non-NULL-ranked observation is always preserved too."
            ),
        )
        parser.add_argument(
            "--min-age-hours", type=int, default=0, dest="min_age_hours",
            help=(
                "Only compact observations older than this many hours "
                "(safety floor on top of --keep-per-player). Default: 0."
            ),
        )
        parser.add_argument(
            "--batch-size", type=int, default=COMPACT_BATCH_SIZE_DEFAULT,
            dest="batch_size",
            help=f"Rows cleared per transaction. Default: {COMPACT_BATCH_SIZE_DEFAULT}.",
        )
        parser.add_argument(
            "--max-rows", type=int, default=0, dest="max_rows",
            help="Cap rows cleared this run (0 = unlimited). Default: 0.",
        )
        parser.add_argument(
            "--sleep", type=float, default=0.0, dest="sleep",
            help="Seconds to pause between batches. Default: 0.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report candidates + reclaimable bytes without writing.",
        )

    def handle(self, *args, **options):
        keep_per_player = options["keep_per_player"]
        if keep_per_player < 1:
            raise CommandError("--keep-per-player must be >= 1")
        if options["min_age_hours"] < 0:
            raise CommandError("--min-age-hours must be >= 0")
        if options["batch_size"] < 1:
            raise CommandError("--batch-size must be >= 1")
        if options["sleep"] < 0:
            raise CommandError("--sleep must be >= 0")

        result = compact_battle_observation_payloads(
            keep_per_player=keep_per_player,
            min_age_hours=options["min_age_hours"],
            batch_size=options["batch_size"],
            max_rows=options["max_rows"],
            dry_run=options["dry_run"],
            sleep_between_batches=options["sleep"],
        )

        if result["dry_run"]:
            self.stdout.write(
                f"DRY-RUN: {result['candidates']:,} observation payloads "
                f"across {result['players_affected']:,} players would be "
                f"cleared (keep_per_player={result['keep_per_player']}, "
                f"min_age_hours={result['min_age_hours']}). "
                f"Estimated reclaim: {_human_bytes(result['reclaimable_bytes'])}."
            )
            self.stdout.write(self.style.WARNING("--dry-run: no rows written"))
            return

        self.stdout.write(self.style.SUCCESS(
            f"Compacted {result['cleared']:,} observation payloads in "
            f"{result['batches']} batch(es) "
            f"(keep_per_player={result['keep_per_player']}, "
            f"min_age_hours={result['min_age_hours']})."
        ))
