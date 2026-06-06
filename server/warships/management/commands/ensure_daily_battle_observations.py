"""Daily floor for BattleObservation coverage on active players.

The tiered incremental crawler (`incremental_player_refresh_task`) is
best-effort — under sustained load or after a worker restart it can
miss a player for >24h, which collapses several days of activity into
a single huge BattleEvent the next time the diff lane runs. This
command targets that gap directly.

Walks active visible players in `--realm` whose `last_battle_date` is
within `--days` (default 7) and whose most recent `BattleObservation`
is older than `--stale-hours` (default 8, tightened from 22h alongside
the 2026-05-09 promotion to a 6-hourly Beat schedule). For each
candidate, dispatches a fresh WG fetch + observation via
`record_observation_and_diff`, OR `record_ranked_observation_and_diff`
when ranked capture is on for the realm.

Usage (manual):
    python manage.py ensure_daily_battle_observations --realm na --dry-run
    python manage.py ensure_daily_battle_observations --realm na
    python manage.py ensure_daily_battle_observations --realm na --stale-hours 12 --limit 500

Beat-scheduled every 6h per realm by `signals.py` so the floor is automatic.
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
DEFAULT_STALE_HOURS = 8
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


def _ranked_known_ids(realm: str, candidate_ids: list[int]) -> set[int]:
    """Subset of `candidate_ids` (in `realm`) that have a non-empty ranked_json.

    Marks "ranked-known" players so the bulk floor (D6) routes them to the
    per-player ranked path instead of the random-only bulk sweep — they must
    not get two observations per tick. Mirrors the ranked-known marker in
    `management/commands/incremental_ranked_data.py`.
    """
    return set(
        Player.objects.filter(realm=realm, player_id__in=candidate_ids)
        .exclude(ranked_json__isnull=True)
        .exclude(ranked_json=[])
        .values_list("player_id", flat=True)
    )


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
            "--bulk", action="store_true",
            help=(
                "Capture random observations via the bulk ships/stats + "
                "account/info path (R1, ~100x fewer WG calls). Ranked-known "
                "players still go through the per-player ranked path."
            ),
        )
        parser.add_argument(
            "--ranked-limit", type=int, default=None, dest="ranked_limit",
            help=(
                "Max ranked-known players to sweep per-player when --bulk is on "
                "and ranked capture is active for the realm. Defaults to --limit."
            ),
        )
        parser.add_argument(
            "--chunk-delay", type=float, default=0.0, dest="chunk_delay",
            help=(
                "Per-CHUNK pacing (s) for the --bulk path. Distinct from the "
                "legacy per-player --delay. Default: 0.0."
            ),
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
        bulk = options["bulk"]
        ranked_limit = options["ranked_limit"]
        chunk_delay = options["chunk_delay"]

        if days < 1:
            raise CommandError("--days must be >= 1")
        if stale_hours < 1:
            raise CommandError("--stale-hours must be >= 1")
        if delay < 0:
            raise CommandError("--delay must be >= 0")
        if chunk_delay < 0:
            raise CommandError("--chunk-delay must be >= 0")

        ranked = _ranked_capture_active_for_realm(realm)

        if bulk:
            self._handle_bulk(
                realm=realm, days=days, stale_hours=stale_hours, limit=limit,
                delay=delay, ranked_limit=ranked_limit, chunk_delay=chunk_delay,
                ranked=ranked, dry_run=dry_run,
                ranked_worker=record_ranked_observation_and_diff,
            )
            return
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

    def _handle_bulk(self, *, realm, days, stale_hours, limit, delay,
                     ranked_limit, chunk_delay, ranked, dry_run, ranked_worker):
        """Bulk capture path (R1, D6).

        Random observations go through the bulk engine; ranked-known players
        (only when ranked capture is active for the realm) go through the
        per-player ranked worker. The two candidate sets are mutually exclusive
        so a ranked-known player never gets two observations per tick.
        """
        from warships.incremental_battles import record_observations_bulk

        candidates = _candidates(realm, days, stale_hours, limit)
        candidate_ids = [row[0] for row in candidates]

        if ranked:
            ranked_known = _ranked_known_ids(realm, candidate_ids)
            bulk_ids = [pid for pid in candidate_ids if pid not in ranked_known]
            ranked_ids = [pid for pid in candidate_ids if pid in ranked_known]
            # Ranked sweep gets its own bound (defaults to --limit). Players
            # trimmed here are simply deferred — they are still excluded from
            # the bulk sweep, so they never double-capture.
            eff_ranked_limit = ranked_limit if ranked_limit is not None else limit
            if eff_ranked_limit and eff_ranked_limit > 0:
                ranked_ids = ranked_ids[:eff_ranked_limit]
        else:
            bulk_ids = candidate_ids
            ranked_ids = []

        self.stdout.write(
            f"realm={realm} active-{days}d stale>{stale_hours}h: "
            f"{len(candidate_ids)} candidates — bulk_random={len(bulk_ids)} "
            f"(~{(len(bulk_ids) + 99) // 100 * 2:,} WG calls @ {chunk_delay}s/chunk), "
            f"ranked_known={len(ranked_ids)} per-player "
            f"(~{len(ranked_ids) * 3:,} WG calls @ {delay}s) "
            f"(ranked_capture={'on' if ranked else 'off'})"
        )

        if dry_run:
            self.stdout.write(self.style.WARNING("--dry-run: no WG calls made"))
            return

        started = time.time()

        # ── Bulk random sweep ────────────────────────────────────────────
        tally = {}
        if bulk_ids:
            tally = record_observations_bulk(
                bulk_ids, realm, chunk_delay=chunk_delay,
            )
            self.stdout.write(
                f"  bulk random: completed={tally.get('completed', 0)} "
                f"(baseline={tally.get('baseline', 0)}, "
                f"events={tally.get('events', 0)}) "
                f"wg_failed={tally.get('wg_failed', 0)} "
                f"not_found={tally.get('not_found', 0)} "
                f"skipped_missing={tally.get('skipped_missing', 0)} "
                f"other={tally.get('other', 0)} "
                f"aborted={tally.get('aborted', False)}"
            )

        # ── Per-player ranked sweep (ranked-known subset) ─────────────────
        r_completed = r_events = r_wg_failed = r_not_found = r_other = 0
        for index, player_id in enumerate(ranked_ids, start=1):
            try:
                result = ranked_worker(player_id, realm=realm)
            except Exception as exc:
                log.exception(
                    "observation-floor ranked exception for player_id=%s "
                    "realm=%s: %s", player_id, realm, exc,
                )
                r_other += 1
                if delay:
                    time.sleep(delay)
                continue

            status = result.get("status")
            reason = result.get("reason")
            if status == "completed":
                r_completed += 1
                r_events += (
                    int(result.get("random_events_created") or 0)
                    + int(result.get("ranked_events_created") or 0)
                )
            elif reason == "wg-fetch-failed-or-hidden":
                r_wg_failed += 1
            elif reason == "player-not-found":
                r_not_found += 1
            else:
                r_other += 1

            if delay:
                time.sleep(delay)

        if ranked_ids:
            self.stdout.write(
                f"  ranked per-player: completed={r_completed} "
                f"events={r_events} wg_failed={r_wg_failed} "
                f"not_found={r_not_found} other={r_other}"
            )

        elapsed = time.time() - started
        self.stdout.write(self.style.SUCCESS(
            f"bulk done in {elapsed:.0f}s — "
            f"random_completed={tally.get('completed', 0)} "
            f"ranked_completed={r_completed} "
            f"aborted={tally.get('aborted', False)}"
        ))
