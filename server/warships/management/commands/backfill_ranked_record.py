"""Backfill `Player.ranked_total_battles` / `ranked_win_rate` from `ranked_json`.

DB-only — **zero WG calls**. Needed once after migration 0090 so the ranked
correlation warm can read two narrow columns instead of detoasting every
ranked_json payload (eu: 237k rows, past its 1080s soft limit on 2026-09-23).
Idempotent, batched, paced. Reads each payload once, so it costs about what
one legacy warm per realm costs; run it off-peak.

When a realm is processed in full (no --active-days filter, no --dry-run) the
command stamps the realm's backfill marker, and from the next warm on
`data._iter_ranked_records` reads the materialised columns. A partial run
never stamps: the warm would otherwise publish a short population.

    python manage.py backfill_ranked_record --realm eu     # one realm, stamps eu
    python manage.py backfill_ranked_record                # all realms, stamps each
    python manage.py backfill_ranked_record --dry-run
"""
from __future__ import annotations

import time
from datetime import timedelta

from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.utils import timezone

from warships.data import ranked_record_backfill_marker_key, ranked_record_from_json
from warships.models import Player, VALID_REALMS


class Command(BaseCommand):
    help = (
        "Populate Player.ranked_total_battles / ranked_win_rate from existing "
        "ranked_json (no WG calls). Batched/paced; idempotent; stamps the "
        "per-realm backfill marker after a full pass."
    )

    def add_arguments(self, parser):
        parser.add_argument("--realm", choices=sorted(VALID_REALMS),
                            help="Limit to one realm (default: all).")
        parser.add_argument("--active-days", type=int, default=0,
                            dest="active_days",
                            help="Only players active within N days "
                                 "(0 = every ranked_json row; the only mode "
                                 "that stamps the marker). Default: 0.")
        parser.add_argument("--batch", type=int, default=2000)
        parser.add_argument("--delay", type=float, default=0.2,
                            help="Pause (s) between batches. Default: 0.2.")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        realm_filter = options["realm"]
        active_days = options["active_days"]
        batch = options["batch"]
        delay = options["delay"]
        dry_run = options["dry_run"]

        realms = [realm_filter] if realm_filter else sorted(VALID_REALMS)
        for realm in realms:
            self._backfill_realm(
                realm, active_days=active_days, batch=batch, delay=delay,
                dry_run=dry_run)

    def _backfill_realm(self, realm, *, active_days, batch, delay, dry_run):
        qs = Player.objects.filter(realm=realm, ranked_json__isnull=False)
        if active_days > 0:
            cutoff = (timezone.now() - timedelta(days=active_days)).date()
            qs = qs.filter(is_hidden=False, last_battle_date__gte=cutoff)

        pks = list(qs.values_list("pk", flat=True))
        total = len(pks)
        self.stdout.write(
            f"[{realm}] ranked_json rows to backfill: {total:,} "
            f"(active_days={active_days})")
        if dry_run:
            self.stdout.write(self.style.WARNING("--dry-run: no writes, no marker"))
            return

        processed = changed = 0
        started = time.time()
        for i in range(0, total, batch):
            chunk = pks[i:i + batch]
            rows = Player.objects.filter(pk__in=chunk).values_list(
                "pk", "ranked_json", "ranked_total_battles", "ranked_win_rate")
            objs = []
            for pk, ranked_json, current_total, current_wr in rows:
                total_battles, win_rate = ranked_record_from_json(ranked_json)
                if (total_battles, win_rate) != (current_total, current_wr):
                    objs.append(Player(
                        id=pk, ranked_total_battles=total_battles,
                        ranked_win_rate=win_rate))
            if objs:
                Player.objects.bulk_update(
                    objs, ["ranked_total_battles", "ranked_win_rate"])
                changed += len(objs)
            processed += len(chunk)
            if processed % (batch * 5) == 0 or processed >= total:
                elapsed = time.time() - started
                rate = processed / elapsed if elapsed else 0
                self.stdout.write(
                    f"  [{realm}] [{processed:,}/{total:,}] changed={changed:,} "
                    f"rate={rate:.0f}/s")
            if delay and processed < total:
                time.sleep(delay)

        if active_days == 0:
            cache.set(ranked_record_backfill_marker_key(realm=realm),
                      timezone.now().isoformat(), timeout=None)
            marker = "marker stamped"
        else:
            marker = "marker NOT stamped (partial pass)"
        self.stdout.write(self.style.SUCCESS(
            f"[{realm}] done in {time.time() - started:.0f}s — "
            f"processed={processed:,} changed={changed:,}; {marker}"))
