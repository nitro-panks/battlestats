"""Reclaim disk by NULLing ``battles_json`` on long-inactive players.

The battle-history pipeline (2026-05) repopulates ``Player.battles_json`` on
every visit / floor refresh, so the displayed Random-Battles blob erodes back
onto long-inactive accounts that catch a one-off page view (the 06-15
DB-growth runbook measured ~376K rows bearing the blob again — active +
inactive). For a >cutoff-day-inactive account that blob is dead weight: battle
history is empty anyway, the wire serializer already omits the field
(``PlayerSerializer.Meta.exclude``), and ``/randoms`` falls back to
``randoms_json`` when ``battles_json`` is NULL. This command NULLs **only**
``battles_json`` on those rows (keeping tiers/type/randoms/activity_json),
reclaiming TOAST. Pruned rows refetch ``battles_json`` on the player's next
profile view, so the prune is reversible.

Disjoint from the floor by construction: ``FLOOR_REFRESH_BATTLES_JSON_ENABLED``
only repopulates the active-7d set; the prune cutoff is far older (default
180d), so the two sets never overlap and the prune does not fight the floor.

Enrichment safety (two belt-and-suspenders guards — see the core docstring):
``battles_json IS NULL`` is one of the enrichment candidate match conditions,
so the prune (1) excludes ``enrichment_status = pending`` rows outright and
(2) refuses to run unless ``--inactive-days > ENRICH_MAX_INACTIVE_DAYS``
(prod pins that env to 7; its default is 365, which means this command refuses
at the 180d default in any env that has NOT pinned it — that refusal is the
intended safety, not a bug). NULLing ``battles_json`` on currently-``enriched``
inactive rows will make the next ``reclassify_enrichment_status`` re-bucket
them as ``skipped_inactive`` — a correct-but-cosmetic metrics shift, reversible
on refetch.

Usage:
    # Always dry-run first — reports candidates, the PENDING-intersection
    # (expect 0), and approximate reclaimable bytes; writes nothing.
    python manage.py prune_inactive_player_battles_json --dry-run

    # Live, paced: 5000-row batches, 0.5s pause, statement timeout bounded.
    python manage.py prune_inactive_player_battles_json \
        --batch-size 5000 --sleep 0.5 --statement-timeout 180

    # Follow a live run with a regular VACUUM (ANALYZE) warships_player so the
    # freed TOAST returns to reusable space (VACUUM FULL is a separate op).
"""
from __future__ import annotations

import os

from django.core.management.base import BaseCommand, CommandError

from warships.incremental_battles import (
    PRUNE_BATTLES_JSON_BATCH_SIZE_DEFAULT,
    PRUNE_BATTLES_JSON_INACTIVE_DAYS_DEFAULT,
    PRUNE_BATTLES_JSON_STATEMENT_TIMEOUT_DEFAULT,
    prune_inactive_player_battles_json,
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
        "NULL Player.battles_json on long-inactive, visible, non-PENDING "
        "players to reclaim disk, keeping the derived chart columns. "
        "Reversible (refetched on next visit). Does not delete rows."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--inactive-days", type=int,
            default=PRUNE_BATTLES_JSON_INACTIVE_DAYS_DEFAULT,
            dest="inactive_days",
            help=(
                "Prune rows whose last_battle_date is strictly older than "
                f"today - this many days. Default: "
                f"{PRUNE_BATTLES_JSON_INACTIVE_DAYS_DEFAULT}. Must exceed "
                "ENRICH_MAX_INACTIVE_DAYS or the command refuses to run."
            ),
        )
        parser.add_argument(
            "--batch-size", type=int,
            default=PRUNE_BATTLES_JSON_BATCH_SIZE_DEFAULT,
            dest="batch_size",
            help=(
                "Rows NULLed per transaction. Default: "
                f"{PRUNE_BATTLES_JSON_BATCH_SIZE_DEFAULT}."
            ),
        )
        parser.add_argument(
            "--max-rows", type=int, default=0, dest="max_rows",
            help="Cap rows NULLed this run (0 = unlimited). Default: 0.",
        )
        parser.add_argument(
            "--sleep", type=float, default=0.0, dest="sleep",
            help="Seconds to pause between batches. Default: 0.",
        )
        parser.add_argument(
            "--statement-timeout", type=int,
            default=PRUNE_BATTLES_JSON_STATEMENT_TIMEOUT_DEFAULT,
            dest="statement_timeout",
            help=(
                "Postgres per-query timeout in seconds (0 = none). Bounds the "
                "candidate scan so it fails fast. Default: "
                f"{PRUNE_BATTLES_JSON_STATEMENT_TIMEOUT_DEFAULT}."
            ),
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help=(
                "Report candidates, PENDING-intersection, and approximate "
                "reclaimable bytes without writing."
            ),
        )

    def handle(self, *args, **options):
        if options["batch_size"] < 1:
            raise CommandError("--batch-size must be >= 1")
        if options["sleep"] < 0:
            raise CommandError("--sleep must be >= 0")
        if options["statement_timeout"] < 0:
            raise CommandError("--statement-timeout must be >= 0")
        if options["max_rows"] < 0:
            raise CommandError("--max-rows must be >= 0")

        # Read the enrichment activity ceiling here (not at import time) so the
        # guard reflects the running env and stays testable.
        max_inactive_days = int(os.getenv("ENRICH_MAX_INACTIVE_DAYS", "365"))

        try:
            result = prune_inactive_player_battles_json(
                inactive_days=options["inactive_days"],
                max_inactive_days=max_inactive_days,
                batch_size=options["batch_size"],
                max_rows=options["max_rows"],
                dry_run=options["dry_run"],
                sleep_between_batches=options["sleep"],
                statement_timeout_s=options["statement_timeout"],
            )
        except ValueError as exc:
            raise CommandError(str(exc))

        if result["dry_run"]:
            self.stdout.write(
                f"DRY-RUN: {result['candidates']:,} player battles_json "
                f"payloads would be NULLed "
                f"(inactive_days={result['inactive_days']}, "
                f"cutoff={result['cutoff']}). "
                f"PENDING rows in band EXCLUDED by guard (not pruned): "
                f"{result['pending_intersection']:,} "
                f"(~0 in healthy data; non-zero = odd populated-PENDING rows "
                f"already excluded — safe to proceed). "
                f"Estimated reclaim: "
                f"{_human_bytes(result['reclaimable_bytes'])} (approximate)."
            )
            self.stdout.write(self.style.WARNING("--dry-run: no rows written"))
            return

        self.stdout.write(self.style.SUCCESS(
            f"NULLed {result['cleared']:,} player battles_json payloads in "
            f"{result['batches']} batch(es) "
            f"(inactive_days={result['inactive_days']}, "
            f"cutoff={result['cutoff']})."
        ))
