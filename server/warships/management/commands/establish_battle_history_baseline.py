"""Establish a BattleObservation baseline for active players missing one.

The battle-history capture pipeline writes a `BattleObservation` as a side
effect of `update_battle_data`, but that path early-bails when the player
was refreshed within `PLAYER_BATTLE_DATA_STALE_AFTER` (15 min). It also
respects the graduated-tier staleness used by `incremental_player_refresh`,
so freshly-refreshed players are skipped on subsequent ticks.

That means players whose stats were just refreshed by the crawl (without
an observation written, e.g. before capture flag flipped) can sit without
a baseline until their tier-cadence next allows another fetch — hours to
a day.

This command bypasses both gates by calling `record_observation_and_diff`
directly. It targets active players (default: played in last 7 days) of a
given realm who have no `BattleObservation` yet, fetches their stats from
the Wargaming API, and writes a baseline observation.

Usage:
    python manage.py establish_battle_history_baseline --realm na --days 7
    python manage.py establish_battle_history_baseline --realm na --dry-run
    python manage.py establish_battle_history_baseline --realm na --limit 100 --delay 0.5

WG API budget: ~2 calls per player (account/info + ships/stats). The
default 0.3s delay paces a 1,000-player run to ~5 min and stays well
under the application_id rate budget. Bump `--delay` higher if running
alongside the enrichment crawler or other heavy WG-API consumers.

The first observation is always a baseline (no diff against prior); no
`BattleEvent` rows are written. Future observations from the regular
crawl will diff against this baseline and emit events as players play.
"""
from __future__ import annotations

import logging
import time
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from warships.models import DEFAULT_REALM, Player, VALID_REALMS

log = logging.getLogger("baseline_battle_history")

DEFAULT_DAYS = 7
DEFAULT_DELAY = 0.3
DEFAULT_LIMIT = 0  # 0 = no limit


def _candidates(realm: str, days: int, limit: int):
    """Return active visible players in `realm` who have no BattleObservation yet."""
    cutoff = (timezone.now() - timedelta(days=days)).date()
    qs = (
        Player.objects.filter(
            realm=realm,
            is_hidden=False,
            last_battle_date__gte=cutoff,
            battle_observations__isnull=True,
        )
        .order_by("-last_battle_date", "name")
        .values_list("player_id", "name")
    )
    if limit and limit > 0:
        qs = qs[:limit]
    return list(qs)


class Command(BaseCommand):
    help = (
        "Walk active visible players in a realm with no BattleObservation, "
        "fetch from WG API, and write a baseline observation. Bypasses the "
        "15-min staleness gate that update_battle_data normally enforces."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--realm",
            default=DEFAULT_REALM,
            choices=sorted(VALID_REALMS),
            help=f"Realm to baseline. Default: {DEFAULT_REALM}.",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=DEFAULT_DAYS,
            help=(
                f"Activity window: only players whose last_battle_date is "
                f"within the last N days are eligible. Default: {DEFAULT_DAYS}."
            ),
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=DEFAULT_LIMIT,
            help="Max players to process. 0 = no limit (default).",
        )
        parser.add_argument(
            "--delay",
            type=float,
            default=DEFAULT_DELAY,
            help=(
                f"Per-player delay (seconds) between WG API calls. Default: "
                f"{DEFAULT_DELAY}. Bump higher if running alongside the "
                f"enrichment crawler."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the candidate count without making WG API calls.",
        )

    def handle(self, *args, **options):
        from warships.incremental_battles import record_observation_and_diff

        realm = options["realm"]
        days = options["days"]
        limit = options["limit"]
        delay = options["delay"]
        dry_run = options["dry_run"]

        if days < 1:
            raise CommandError("--days must be >= 1")
        if delay < 0:
            raise CommandError("--delay must be >= 0")

        candidates = _candidates(realm, days, limit)
        total = len(candidates)
        self.stdout.write(
            f"realm={realm} active-{days}d-without-baseline: {total} candidates"
            + (f" (limited to {limit})" if limit else "")
        )

        if dry_run:
            self.stdout.write(self.style.WARNING("--dry-run: no WG calls made"))
            return

        if total == 0:
            self.stdout.write(self.style.SUCCESS("nothing to do"))
            return

        completed = 0
        baseline = 0
        wg_failed = 0
        not_found = 0
        other = 0
        started = time.time()

        for index, (player_id, name) in enumerate(candidates, start=1):
            try:
                result = record_observation_and_diff(player_id, realm=realm)
            except Exception as exc:
                log.exception(
                    "baseline-fill exception for player_id=%s realm=%s: %s",
                    player_id, realm, exc,
                )
                other += 1
                if delay:
                    time.sleep(delay)
                continue

            status = result.get("status")
            reason = result.get("reason")
            if status == "completed":
                completed += 1
                if reason == "baseline":
                    baseline += 1
            elif reason == "wg-fetch-failed-or-hidden":
                wg_failed += 1
            elif reason == "player-not-found":
                not_found += 1
            else:
                other += 1

            if index % 50 == 0 or index == total:
                elapsed = time.time() - started
                rate = index / elapsed if elapsed else 0.0
                self.stdout.write(
                    f"  [{index}/{total}] completed={completed} "
                    f"baseline={baseline} wg_failed={wg_failed} "
                    f"not_found={not_found} other={other} "
                    f"rate={rate:.1f}/s"
                )

            if delay:
                time.sleep(delay)

        elapsed = time.time() - started
        self.stdout.write(self.style.SUCCESS(
            f"done in {elapsed:.0f}s — completed={completed} "
            f"(baseline={baseline}) wg_failed={wg_failed} "
            f"not_found={not_found} other={other}"
        ))
