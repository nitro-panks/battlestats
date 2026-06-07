"""Backfill `Player.ranked_last_season_id` from existing `ranked_json`.

DB-only — **zero WG calls** — so it can run anytime. Needed once after the field
was added (migration 0065) so the observation floor's random-first routing has
a populated signal immediately, instead of waiting ~a day for the per-player
ranked enrichment to fill it. Idempotent + batched + paced for the 1-vCPU DB.

    python manage.py backfill_ranked_last_season --realm na          # active ranked-known in na
    python manage.py backfill_ranked_last_season --active-days 0      # ALL ranked-known
    python manage.py backfill_ranked_last_season --dry-run
"""
from __future__ import annotations

import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from warships.data import ranked_last_season_from_json
from warships.models import Player, VALID_REALMS


class Command(BaseCommand):
    help = (
        "Populate Player.ranked_last_season_id from existing ranked_json "
        "(no WG calls). Batched/paced; idempotent."
    )

    def add_arguments(self, parser):
        parser.add_argument("--realm", choices=sorted(VALID_REALMS),
                            help="Limit to one realm (default: all).")
        parser.add_argument("--active-days", type=int, default=30,
                            dest="active_days",
                            help="Only players active within N days "
                                 "(0 = all ranked-known). Default: 30.")
        parser.add_argument("--batch", type=int, default=2000)
        parser.add_argument("--delay", type=float, default=0.2,
                            help="Pause (s) between batches. Default: 0.2.")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        realm = options["realm"]
        active_days = options["active_days"]
        batch = options["batch"]
        delay = options["delay"]
        dry_run = options["dry_run"]

        qs = (Player.objects
              .exclude(ranked_json__isnull=True)
              .exclude(ranked_json=[]))
        if realm:
            qs = qs.filter(realm=realm)
        if active_days > 0:
            cutoff = (timezone.now() - timedelta(days=active_days)).date()
            qs = qs.filter(is_hidden=False, last_battle_date__gte=cutoff)

        pks = list(qs.values_list("pk", flat=True))
        total = len(pks)
        self.stdout.write(
            f"ranked-known to backfill: {total:,} "
            f"(realm={realm or 'all'}, active_days={active_days})")
        if dry_run:
            self.stdout.write(self.style.WARNING("--dry-run: no writes"))
            return
        if not total:
            self.stdout.write(self.style.SUCCESS("nothing to do"))
            return

        processed = changed = 0
        started = time.time()
        for i in range(0, total, batch):
            chunk = pks[i:i + batch]
            rows = Player.objects.filter(pk__in=chunk).values_list(
                "pk", "ranked_json", "ranked_last_season_id")
            objs = []
            for pk, rj, current in rows:
                val = ranked_last_season_from_json(rj)
                if val != current:
                    objs.append(Player(id=pk, ranked_last_season_id=val))
            if objs:
                Player.objects.bulk_update(objs, ["ranked_last_season_id"])
                changed += len(objs)
            processed += len(chunk)
            if processed % (batch * 5) == 0 or processed >= total:
                elapsed = time.time() - started
                rate = processed / elapsed if elapsed else 0
                self.stdout.write(
                    f"  [{processed:,}/{total:,}] changed={changed:,} "
                    f"rate={rate:.0f}/s")
            if delay:
                time.sleep(delay)

        self.stdout.write(self.style.SUCCESS(
            f"done in {time.time() - started:.0f}s — "
            f"processed={processed:,} changed={changed:,}"))
