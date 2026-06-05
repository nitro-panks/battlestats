"""Backfill fixed 2-week ship-standings seasons.

Rebuilds `ShipTopPlayerSnapshot` (the /ship board + profile badges) and the
durable `ShipAward` ledger (Ship Honors) for each completed fixed season from
W20-21 (Mon 11 May 2026) onward, by replaying `compute_ship_top_player_snapshot`
with each season's explicit `[window_start, window_end)`.

Use `--wipe` once when pivoting off the old rolling-weekly scheme: it deletes the
rolling-era rows (keyed by arbitrary run-days) so the ledger rebuilds cleanly
with one award-set per season (`times_first` then counts seasons held #1).

Idempotent: each season is keyed by its start date, so re-running overwrites that
season's rows. Walk is sequential per (realm, season) — one current snapshot's
worth of aggregation each, fine on the droplet/background.

  python manage.py backfill_ship_seasons --wipe                  # all realms, all completed seasons
  python manage.py backfill_ship_seasons --realms na --through 2026-05-25
"""
from __future__ import annotations

import logging
from datetime import date, datetime

from django.core.management.base import BaseCommand, CommandError

from warships.data import (
    SHIP_SEASON_EPOCH,
    compute_ship_top_player_snapshot,
    current_season_index,
    ship_season_bounds,
)
from warships.models import VALID_REALMS, ShipAward, ShipTopPlayerSnapshot

log = logging.getLogger("backfill_ship_seasons")


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise CommandError(f"invalid date {value!r}, expected YYYY-MM-DD") from exc


class Command(BaseCommand):
    help = "Backfill fixed 2-week ship-standings seasons (board + award ledger)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--realms", default=None,
            help="Comma-separated realms (default: all of na,eu,asia).")
        parser.add_argument(
            "--from", dest="from_date", default=None,
            help="First season start date (default: the epoch, 2026-05-11).")
        parser.add_argument(
            "--through", default=None,
            help="Only backfill seasons that start on or before this date "
                 "(default: the most recently completed season).")
        parser.add_argument(
            "--wipe", action="store_true",
            help="Delete existing ShipTopPlayerSnapshot + ShipAward rows for the "
                 "target realms before backfilling (clears rolling-era rows).")

    def handle(self, *args, **options):
        realms = (
            [r.strip().lower() for r in options["realms"].split(",") if r.strip()]
            if options["realms"] else sorted(VALID_REALMS)
        )
        unknown = [r for r in realms if r not in VALID_REALMS]
        if unknown:
            raise CommandError(f"unknown realm(s): {', '.join(unknown)}")

        from_date = _parse_date(options["from_date"]) if options["from_date"] else SHIP_SEASON_EPOCH
        # Last season to write = the most recently completed one (current index - 1),
        # optionally capped by --through.
        last_completed_idx = max(0, current_season_index() - 1)
        last_idx = last_completed_idx
        if options["through"]:
            through = _parse_date(options["through"])
            through_idx = (through - SHIP_SEASON_EPOCH).days // 14
            last_idx = min(last_idx, through_idx)

        first_idx = (from_date - SHIP_SEASON_EPOCH).days // 14
        if first_idx < 0:
            first_idx = 0
        if last_idx < first_idx:
            self.stdout.write(self.style.WARNING(
                f"No completed seasons in range (first_idx={first_idx} > "
                f"last_idx={last_idx}); nothing to do."))
            return

        if options["wipe"]:
            snap_n = ShipTopPlayerSnapshot.objects.filter(realm__in=realms).delete()[0]
            award_n = ShipAward.objects.filter(realm__in=realms).delete()[0]
            self.stdout.write(self.style.WARNING(
                f"Wiped {snap_n} snapshot + {award_n} award rows for "
                f"realms={','.join(realms)}."))

        results = []
        for realm in realms:
            for idx in range(first_idx, last_idx + 1):
                start, end = ship_season_bounds(idx)
                res = compute_ship_top_player_snapshot(
                    realm=realm, window_start=start, window_end=end,
                    captured_on=start)
                results.append(res)
                self.stdout.write(
                    f"{realm} W{idx} {start}..{end}: "
                    f"ranked={res.get('ranked_rows', 0)} "
                    f"badges={res.get('badges', 0)} "
                    f"ships={res.get('ships_qualified', 0)}/{res.get('ships_total', 0)}")

        total_badges = sum(r.get("badges", 0) for r in results)
        self.stdout.write(self.style.SUCCESS(
            f"Backfilled seasons W{first_idx}..W{last_idx} for "
            f"{len(realms)} realm(s): {len(results)} runs, {total_badges} badges."))
