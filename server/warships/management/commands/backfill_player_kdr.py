"""One-time backfill: populate pvp_frags, pvp_survived_battles, pvp_deaths,
and actual_kdr for players ingested before the clan crawl was fixed to
persist these fields.

Usage:
    python manage.py backfill_player_kdr
    python manage.py backfill_player_kdr --batch-size 50 --limit 1000 --dry-run
"""

import logging
import time

from django.core.management.base import BaseCommand

from warships.api.client import make_api_request
from warships.data import _calculate_actual_kdr
from warships.models import Player

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 100  # WG API max for account/info
DEFAULT_RATE_DELAY = 0.25  # seconds between batches


class Command(BaseCommand):
    help = "Backfill KDR fields for players missing actual_kdr."

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
            help="Players per WG API request (max 100).",
        )
        parser.add_argument(
            "--limit", type=int, default=0,
            help="Max players to process (0 = all).",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report counts without making API calls or DB writes.",
        )
        parser.add_argument(
            "--rate-delay", type=float, default=DEFAULT_RATE_DELAY,
            help="Seconds to sleep between API batches.",
        )

    def handle(self, *args, **options):
        batch_size = min(options["batch_size"], 100)
        limit = options["limit"]
        dry_run = options["dry_run"]
        rate_delay = options["rate_delay"]

        qs = (
            Player.objects
            .filter(is_hidden=False, actual_kdr__isnull=True, pvp_battles__gt=0)
            .order_by("id")
            .values_list("id", "player_id", flat=False)
        )
        if limit:
            qs = qs[:limit]

        player_rows = list(qs)
        total = len(player_rows)
        self.stdout.write(f"Players to backfill: {total}")

        if dry_run or total == 0:
            return

        updated = 0
        skipped = 0
        errors = 0

        for batch_start in range(0, total, batch_size):
            batch = player_rows[batch_start:batch_start + batch_size]
            id_map = {str(player_id): pk for pk, player_id in batch}
            account_ids = ",".join(str(pid) for _, pid in batch)

            data = make_api_request("account/info/", {
                "account_id": account_ids,
                "fields": "statistics.pvp.frags,statistics.pvp.survived_battles,statistics.pvp.battles,hidden_profile",
            })

            if not data:
                errors += len(batch)
                logger.warning(
                    "API returned no data for batch starting at offset %d", batch_start)
                if rate_delay:
                    time.sleep(rate_delay)
                continue

            bulk_updates = []
            for player_id_str, pk in id_map.items():
                player_data = data.get(player_id_str)
                if not player_data or player_data.get("hidden_profile"):
                    skipped += 1
                    continue

                pvp = (player_data.get("statistics") or {}).get("pvp") or {}
                pvp_battles = pvp.get("battles", 0)
                pvp_frags = pvp.get("frags", 0)
                pvp_survived = pvp.get("survived_battles", 0)

                if pvp_battles <= 0:
                    skipped += 1
                    continue

                pvp_deaths, actual_kdr = _calculate_actual_kdr(
                    pvp_battles, pvp_frags, pvp_survived)

                player_obj = Player(pk=pk)
                player_obj.pvp_frags = pvp_frags
                player_obj.pvp_survived_battles = pvp_survived
                player_obj.pvp_deaths = pvp_deaths
                player_obj.actual_kdr = actual_kdr
                bulk_updates.append(player_obj)

            if bulk_updates:
                Player.objects.bulk_update(
                    bulk_updates,
                    ["pvp_frags", "pvp_survived_battles", "pvp_deaths", "actual_kdr"],
                    batch_size=500,
                )
                updated += len(bulk_updates)

            processed = batch_start + len(batch)
            if processed % 5000 < batch_size or processed == total:
                self.stdout.write(
                    f"  Progress: {processed}/{total}  updated={updated}  skipped={skipped}  errors={errors}")

            if rate_delay:
                time.sleep(rate_delay)

        self.stdout.write(self.style.SUCCESS(
            f"Done. updated={updated}  skipped={skipped}  errors={errors}  total={total}"))
