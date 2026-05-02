"""Daily floor for BattleObservation coverage on active players.

The tiered incremental crawler (`incremental_player_refresh_task`) is
best-effort — under sustained load or after a worker restart it can
miss a player for >24h, which collapses several days of activity into
a single huge BattleEvent the next time the diff lane runs. This
command targets that gap directly.

Walks active visible players in `--realm` whose `last_battle_date` is
within `--days` (default 7) and whose most recent `BattleObservation`
is older than `--stale-hours` (default 22, leaving ~2h of slack vs.
the 24h floor target). For each candidate, dispatches a fresh WG
fetch + observation via `record_observation_and_diff`, OR
`record_ranked_observation_and_diff` when ranked capture is on for
the realm.

Usage (manual):
    python manage.py ensure_daily_battle_observations --realm na --dry-run
    python manage.py ensure_daily_battle_observations --realm na
    python manage.py ensure_daily_battle_observations --realm na --stale-hours 12 --limit 500

Beat-scheduled daily by `signals.py` so the floor is automatic.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Max
from django.utils import timezone

from warships.models import BattleObservation, DEFAULT_REALM, Player, VALID_REALMS

log = logging.getLogger("battle_observation_floor")

DEFAULT_DAYS = 7
DEFAULT_STALE_HOURS = 22
DEFAULT_LIMIT = 3000
DEFAULT_DELAY = 0.3


def _candidates(realm: str, days: int, stale_hours: int, limit: int):
    """Active-N-day players in `realm` whose latest BattleObservation is
    missing OR older than `stale_hours`.

    Single SQL pass: annotate `latest_obs_at` then filter. Order by
    `latest_obs_at NULLS FIRST, last_battle_date DESC, name` so:
      * Players who've never been observed go first.
      * Then the longest-stale active players.
      * Stable name tiebreaker for deterministic ordering.
    """
    cutoff_date = (timezone.now() - timedelta(days=days)).date()
    stale_before = timezone.now() - timedelta(hours=stale_hours)

    qs = (
        Player.objects.filter(
            realm=realm,
            is_hidden=False,
            last_battle_date__gte=cutoff_date,
        )
        .annotate(latest_obs_at=Max("battle_observations__observed_at"))
        .filter(
            # Either no observation at all, or most recent is too old.
            # Both branches need the floor sweep to fill them.
            **{}
        )
    )
    # Two-step filter: Q-or covers null + stale.
    from django.db.models import Q
    qs = qs.filter(
        Q(latest_obs_at__isnull=True) | Q(latest_obs_at__lt=stale_before)
    ).order_by(
        "latest_obs_at",  # NULLS FIRST in Postgres for ASC by default
        "-last_battle_date",
        "name",
    ).values_list("player_id", "name", "latest_obs_at")

    if limit and limit > 0:
        qs = qs[:limit]
    return list(qs)


def _ranked_capture_active_for_realm(realm: str) -> bool:
    if os.getenv("BATTLE_HISTORY_RANKED_CAPTURE_ENABLED", "0") != "1":
        return False
    realms = {
        r.strip() for r in os.getenv(
            "BATTLE_HISTORY_RANKED_CAPTURE_REALMS", "",
        ).split(",") if r.strip()
    }
    return realm in realms


class Command(BaseCommand):
    help = (
        "Daily floor for BattleObservation coverage on active players. "
        "Targets players whose latest observation is older than the "
        "staleness threshold and dispatches a fresh WG fetch + observation."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--realm", default=DEFAULT_REALM,
            choices=sorted(VALID_REALMS),
            help=f"Realm to sweep. Default: {DEFAULT_REALM}.",
        )
        parser.add_argument(
            "--days", type=int, default=DEFAULT_DAYS,
            help=(
                f"Activity window: only players whose last_battle_date is "
                f"within the last N days are eligible. Default: {DEFAULT_DAYS}."
            ),
        )
        parser.add_argument(
            "--stale-hours", type=int, default=DEFAULT_STALE_HOURS,
            dest="stale_hours",
            help=(
                f"Refresh players whose latest BattleObservation is "
                f"older than N hours. Default: {DEFAULT_STALE_HOURS}."
            ),
        )
        parser.add_argument(
            "--limit", type=int, default=DEFAULT_LIMIT,
            help=f"Max players to process. Default: {DEFAULT_LIMIT}.",
        )
        parser.add_argument(
            "--delay", type=float, default=DEFAULT_DELAY,
            help=f"Per-player delay (s) for WG-budget pacing. Default: {DEFAULT_DELAY}.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Print the candidate count without making WG API calls.",
        )

    def handle(self, *args, **options):
        from warships.incremental_battles import (
            record_observation_and_diff,
            record_ranked_observation_and_diff,
        )

        realm = options["realm"]
        days = options["days"]
        stale_hours = options["stale_hours"]
        limit = options["limit"]
        delay = options["delay"]
        dry_run = options["dry_run"]

        if days < 1:
            raise CommandError("--days must be >= 1")
        if stale_hours < 1:
            raise CommandError("--stale-hours must be >= 1")
        if delay < 0:
            raise CommandError("--delay must be >= 0")

        ranked = _ranked_capture_active_for_realm(realm)
        worker = (
            record_ranked_observation_and_diff if ranked
            else record_observation_and_diff
        )
        wg_calls_per = 3 if ranked else 2

        candidates = _candidates(realm, days, stale_hours, limit)
        total = len(candidates)
        self.stdout.write(
            f"realm={realm} active-{days}d stale>{stale_hours}h: "
            f"{total} candidates "
            f"(ranked_capture={'on' if ranked else 'off'}, "
            f"~{total * wg_calls_per:,} WG calls @ {delay}s pacing)"
            + (f" (limited to {limit})" if limit and total == limit else "")
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

        for index, (player_id, name, latest_obs_at) in enumerate(candidates, start=1):
            try:
                result = worker(player_id, realm=realm)
            except Exception as exc:
                log.exception(
                    "observation-floor exception for player_id=%s realm=%s: %s",
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
                ev_total = (
                    int(result.get("random_events_created") or 0)
                    + int(result.get("ranked_events_created") or 0)
                )
                events_emitted += ev_total
            elif reason == "wg-fetch-failed-or-hidden":
                wg_failed += 1
            elif reason == "player-not-found":
                not_found += 1
            else:
                other += 1

            if index % 100 == 0 or index == total:
                elapsed = time.time() - started
                rate = index / elapsed if elapsed else 0.0
                self.stdout.write(
                    f"  [{index}/{total}] completed={completed} "
                    f"baseline={baseline} events={events_emitted} "
                    f"wg_failed={wg_failed} not_found={not_found} "
                    f"other={other} rate={rate:.1f}/s"
                )

            if delay:
                time.sleep(delay)

        elapsed = time.time() - started
        self.stdout.write(self.style.SUCCESS(
            f"done in {elapsed:.0f}s — completed={completed} "
            f"(baseline={baseline}, events={events_emitted}) "
            f"wg_failed={wg_failed} not_found={not_found} other={other}"
        ))
