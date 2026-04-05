"""Backfill clan battle data for enriched players missing CB stats.

Fetches per-player clan battle season stats from the WG API and persists
the summary (total_battles, seasons_participated, win_rate) to
PlayerExplorerSummary.  Only targets players who already have enrichment
data (battles_json) but are missing CB fields.

Designed to run as a DO Function or management command with the same
batch/delay/partition pattern as enrich_player_data.
"""
from __future__ import annotations

import logging
import time

from django.core.management.base import BaseCommand
from django.db.models import F

from warships.models import DEFAULT_REALM, Player, PlayerExplorerSummary, VALID_REALMS

log = logging.getLogger("backfill_cb")

DEFAULT_BATCH = 500
DEFAULT_DELAY = 0.1


def _candidates(realm: str, batch: int, partition: int = 0, num_partitions: int = 1):
    """Return enriched players missing CB data, ordered by WR desc."""
    qs = (
        PlayerExplorerSummary.objects.filter(
            player__realm=realm,
            clan_battle_total_battles__isnull=True,
        )
        .exclude(player__battles_json__isnull=True)
        .exclude(player__is_hidden=True)
        .order_by(
            F("player__pvp_ratio").desc(nulls_last=True),
            F("player__pvp_battles").desc(nulls_last=True),
        )
    )

    if num_partitions > 1:
        from django.db.models import Value
        from django.db.models.functions import Mod
        qs = qs.annotate(
            _partition=Mod(F("player__player_id"), Value(num_partitions))
        ).filter(_partition=partition)

    return list(
        qs.values_list(
            "player__player_id", "player__name", "player__realm",
        )[:batch]
    )


def backfill_cb(
    batch: int = DEFAULT_BATCH,
    delay: float = DEFAULT_DELAY,
    dry_run: bool = False,
    realm: str | None = None,
    partition: int = 0,
    num_partitions: int = 1,
    heartbeat_callback=None,
) -> dict:
    """Run one CB backfill pass. Returns summary dict."""
    from warships.data import fetch_player_clan_battle_seasons

    target_realm = realm or 'eu'
    candidates = _candidates(
        target_realm, batch,
        partition=partition, num_partitions=num_partitions,
    )

    log.info(
        "CB backfill: %d candidates for realm=%s (partition=%d/%d)",
        len(candidates), target_realm, partition, num_partitions,
    )

    if dry_run:
        return {
            "status": "dry_run",
            "realm": target_realm,
            "candidates": len(candidates),
        }

    enriched = 0
    errors = 0

    for player_id, name, r in candidates:
        if heartbeat_callback:
            heartbeat_callback()

        try:
            seasons = fetch_player_clan_battle_seasons(
                int(player_id), realm=r)
            enriched += 1
            season_count = len(seasons) if seasons else 0
            log.info(
                "CB backfill %s [%s]: %d seasons (%d/%d)",
                name, r.upper(), season_count, enriched, len(candidates),
            )
        except Exception:
            log.exception(
                "CB backfill failed player_id=%s name=%s realm=%s",
                player_id, name, r,
            )
            errors += 1

        if delay > 0:
            time.sleep(delay)

    summary = {
        "status": "completed",
        "realm": target_realm,
        "enriched": enriched,
        "errors": errors,
        "candidates": len(candidates),
    }
    log.info("CB backfill complete: %s", summary)
    return summary


class Command(BaseCommand):
    help = "Backfill clan battle data for enriched players missing CB stats."

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch", type=int, default=DEFAULT_BATCH,
            help=f"Max players to process (default {DEFAULT_BATCH}).",
        )
        parser.add_argument(
            "--delay", type=float, default=DEFAULT_DELAY,
            help=f"Seconds between players (default {DEFAULT_DELAY}).",
        )
        parser.add_argument(
            "--realm", choices=sorted(VALID_REALMS), default=None,
            help="Target realm (default: eu).",
        )
        parser.add_argument(
            "--partition", type=int, default=0,
            help="Partition index (0-based) for parallel invocations.",
        )
        parser.add_argument(
            "--num-partitions", type=int, default=1,
            help="Total number of partitions.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report candidates without processing.",
        )

    def handle(self, *args, **options):
        summary = backfill_cb(
            batch=options["batch"],
            delay=options["delay"],
            dry_run=options["dry_run"],
            realm=options["realm"],
            partition=options["partition"],
            num_partitions=options["num_partitions"],
        )
        self.stdout.write(self.style.SUCCESS(str(summary)))
