"""Force-seed BattleObservation.ranked_ships_stats_json for active ranked players.

The ranked battle-history capture pipeline (Phase 1 of
runbook-ranked-battle-history-rollout-2026-05-02.md) writes
`BattleObservation.ranked_ships_stats_json` as a side effect of
`update_battle_data` when `BATTLE_HISTORY_RANKED_CAPTURE_ENABLED=1` and
the player's realm is in `BATTLE_HISTORY_RANKED_CAPTURE_REALMS`.

The diff lane requires a prior observation with a non-empty ranked
payload to compute deltas. For players who haven't been refreshed since
ranked capture flipped on, the first regular crawl writes a baseline
(no events) and the second produces the first events — for low-traffic
ranked players this can mean days of waiting before any data shows up
in the BattleHistoryCard's Ranked / All views.

This command short-circuits that wait by directly invoking
`record_ranked_observation_and_diff` for active ranked players who have
no prior observation OR whose prior observation has no ranked payload.

Targets active visible players in `--realm` whose
`PlayerExplorerSummary.latest_ranked_battles >= --min-ranked-battles`
and whose `last_battle_date` is within the last `--days` days.
Order is `-latest_ranked_battles, -last_battle_date, name` so the most
active ranked players seed first — even if a partial run is interrupted
by rate-limit or operator action, the highest-value seeds are already
in place.

Usage:
    python manage.py establish_ranked_baseline --realm na --days 14 --dry-run
    python manage.py establish_ranked_baseline --realm na --days 14
    python manage.py establish_ranked_baseline --realm na --min-ranked-battles 1 --limit 200 --delay 0.5

WG API budget: 3 calls per player (account/info + ships/stats + seasons/shipstats).
At the default 0.5s delay a 600-player run takes ~5 min and stays well
under the application_id rate budget.
"""
from __future__ import annotations

import logging
import time
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from warships.models import DEFAULT_REALM, Player, VALID_REALMS

log = logging.getLogger("baseline_ranked")

DEFAULT_DAYS = 14
DEFAULT_DELAY = 0.5
DEFAULT_LIMIT = 0  # 0 = no limit
DEFAULT_MIN_RANKED_BATTLES = 100


def _candidates(realm: str, days: int, min_ranked_battles: int, limit: int):
    """Return active ranked players in `realm` who need a ranked baseline.

    A player needs seeding when:
      * they have no `BattleObservation` at all, OR
      * their most recent observation has `ranked_ships_stats_json` NULL or empty.

    Order: `-latest_ranked_battles, -last_battle_date, name` so most
    active ranked players seed first.
    """
    cutoff = (timezone.now() - timedelta(days=days)).date()
    qs = (
        Player.objects.filter(
            realm=realm,
            is_hidden=False,
            last_battle_date__gte=cutoff,
            explorer_summary__latest_ranked_battles__gte=min_ranked_battles,
        )
        # No observation at all OR latest observation lacks ranked payload.
        .filter(
            Q(battle_observations__isnull=True)
            | Q(battle_observations__ranked_ships_stats_json__isnull=True)
            | Q(battle_observations__ranked_ships_stats_json=[]),
        )
        .distinct()
        .order_by(
            "-explorer_summary__latest_ranked_battles",
            "-last_battle_date",
            "name",
        )
        .values_list("player_id", "name", "explorer_summary__latest_ranked_battles")
    )
    if limit and limit > 0:
        qs = qs[:limit]
    return list(qs)


class Command(BaseCommand):
    help = (
        "Force-seed BattleObservation.ranked_ships_stats_json for active "
        "ranked players. Targets players whose latest_ranked_battles meets "
        "the threshold and who have no prior ranked-baseline observation. "
        "Bypasses the staleness gate that update_battle_data normally enforces."
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
            "--min-ranked-battles",
            type=int,
            default=DEFAULT_MIN_RANKED_BATTLES,
            dest="min_ranked_battles",
            help=(
                f"Volume gate: only players with latest_ranked_battles >= N "
                f"are eligible. Default: {DEFAULT_MIN_RANKED_BATTLES}."
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
        from warships.incremental_battles import record_ranked_observation_and_diff

        realm = options["realm"]
        days = options["days"]
        min_ranked_battles = options["min_ranked_battles"]
        limit = options["limit"]
        delay = options["delay"]
        dry_run = options["dry_run"]

        if days < 1:
            raise CommandError("--days must be >= 1")
        if min_ranked_battles < 0:
            raise CommandError("--min-ranked-battles must be >= 0")
        if delay < 0:
            raise CommandError("--delay must be >= 0")

        candidates = _candidates(realm, days, min_ranked_battles, limit)
        total = len(candidates)
        self.stdout.write(
            f"realm={realm} active-{days}d min-ranked={min_ranked_battles}: "
            f"{total} candidates"
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
        events_emitted = 0
        wg_failed = 0
        not_found = 0
        other = 0
        started = time.time()

        for index, (player_id, name, latest_ranked) in enumerate(candidates, start=1):
            try:
                result = record_ranked_observation_and_diff(player_id, realm=realm)
            except Exception as exc:
                log.exception(
                    "ranked-baseline exception for player_id=%s realm=%s: %s",
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
                ranked_events = int(result.get("ranked_events_created") or 0)
                if ranked_events:
                    events_emitted += ranked_events
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
                    f"baseline={baseline} ranked_events={events_emitted} "
                    f"wg_failed={wg_failed} not_found={not_found} "
                    f"other={other} rate={rate:.1f}/s"
                )

            if delay:
                time.sleep(delay)

        elapsed = time.time() - started
        self.stdout.write(self.style.SUCCESS(
            f"done in {elapsed:.0f}s — completed={completed} "
            f"(baseline={baseline}, ranked_events={events_emitted}) "
            f"wg_failed={wg_failed} not_found={not_found} other={other}"
        ))
