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
# The per-player ranked sweep gets its OWN bound (not --limit) so it can't grow
# with FLOOR_LIMIT as random coverage (R3) scales up.
DEFAULT_RANKED_SWEEP_LIMIT = 5000


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


def _current_ranked_season_ids():
    """The live ranked season(s), as a 2-element window `[max, max-1]`.

    Derived from the **highest `Player.ranked_last_season_id` in the playerbase**
    — the latest season anyone currently has ranked battles in. This is fed by
    `data.update_ranked_data` from WG `seasons/shipstats` (the same source as the
    live season), so it tracks the *actual* current season — unlike `seasons/info`
    dates, which lag (observed 2026-06-07: `seasons/info` topped out at season
    1028 'ended' May 20 while players were accumulating battles in 1029). The
    window of 2 covers a concurrent season+sprint and the rollover boundary.

    Returns `None` when no ranked data exists yet (cold field) → the caller falls
    back to the broad ever-ranked marker so ranked capture is never dropped.
    """
    from django.db.models import Max

    mx = (
        Player.objects.filter(ranked_last_season_id__isnull=False)
        .aggregate(m=Max("ranked_last_season_id"))["m"]
    )
    if mx is None:
        return None
    return [mx, mx - 1]


def _ranked_active_ids(realm: str, candidate_ids: list[int],
                       current_season_ids) -> set[int]:
    """Subset of `candidate_ids` who have ranked battles in a CURRENTLY-ACTIVE
    season — the random-first routing marker.

    Uses the denormalized `Player.ranked_last_season_id` (highest season with
    ranked battles, refreshed every ~2h by `data.update_ranked_data`), so the
    query is a simple indexed `IN` bounded by `player_id__in=<candidates>`. Empty
    `current_season_ids` (off-season) → empty set (everyone goes random).
    """
    if not current_season_ids:
        return set()
    return set(
        Player.objects.filter(
            realm=realm, player_id__in=candidate_ids,
            ranked_last_season_id__in=current_season_ids,
        ).values_list("player_id", flat=True)
    )


def _lbt_to_unix(dt) -> int | None:
    """BattleObservation.last_battle_time → unix seconds, tz-robust.

    USE_TZ=False stores naive UTC datetimes, but coerce builds aware ones —
    handle both. naive is treated as UTC (server is TIME_ZONE=UTC).
    """
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return int(dt.timestamp())
    import calendar
    return calendar.timegm(dt.timetuple())


def _ranked_movers(realm: str, ranked_ids: list[int]) -> list[int]:
    """Subset of `ranked_ids` who actually played since their last observation.

    The ranked sweep costs 3 WG calls/player (account/info + ships/stats +
    seasons/shipstats), so gate it with the cheap bulk `account/info`: keep only
    players whose `last_battle_time` advanced past their latest observation (or
    who have no prior → baseline). `last_battle_time` advances on ANY battle, so
    unlike a `pvp.battles` (random-only) check it never drops ranked-only
    activity. Hidden/absent accounts are dropped (the ranked worker would skip
    them anyway). On a bulk-fetch error we keep the whole chunk (never miss a
    capture because the gate couldn't read the signal).
    """
    from django.db.models import OuterRef, Subquery

    from warships.api.players import _bulk_fetch_account_info

    latest = (
        BattleObservation.objects.filter(player=OuterRef("pk"))
        .order_by("-observed_at").values("last_battle_time")[:1]
    )
    prior = dict(
        Player.objects.filter(realm=realm, player_id__in=ranked_ids)
        .annotate(_lbt=Subquery(latest))
        .values_list("player_id", "_lbt")
    )

    movers: list[int] = []
    for start in range(0, len(ranked_ids), 100):
        chunk = ranked_ids[start:start + 100]
        data, err = _bulk_fetch_account_info(chunk, realm)
        if err:
            # Can't read the signal — sweep the whole chunk rather than risk
            # dropping a real capture.
            movers.extend(chunk)
            continue
        for pid in chunk:
            v = data.get(str(pid))
            if not v or v.get("hidden_profile"):
                continue  # ranked worker would skip these anyway
            cur = v.get("last_battle_time")
            if cur is None:
                movers.append(pid)  # no signal → be safe, fetch
                continue
            prior_unix = _lbt_to_unix(prior.get(pid))
            if prior_unix is None or int(cur) > prior_unix:
                movers.append(pid)  # no prior, or played since → fetch
    return movers


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
            "--ranked-sweep-limit", "--ranked-limit", type=int,
            default=DEFAULT_RANKED_SWEEP_LIMIT, dest="ranked_sweep_limit",
            help=(
                "Max players to sweep per-player on the heavy 3-call ranked "
                f"path when --bulk + ranked capture are on. Default: "
                f"{DEFAULT_RANKED_SWEEP_LIMIT} (its own bound, NOT --limit, so it "
                "stays small as the random FLOOR_LIMIT scales). "
                "(--ranked-limit is a deprecated alias.)"
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
            "--change-gate", action="store_true", dest="change_gate",
            help=(
                "With --bulk: use the cheap bulk account/info as a change "
                "detector — only fetch the expensive per-player ships/stats for "
                "players whose random battle count moved since their last "
                "observation (or who have no prior). Skips the ~half who "
                "didn't play."
            ),
        )
        parser.add_argument(
            "--ranked-gate", action="store_true", dest="ranked_gate",
            help=(
                "With --bulk and ranked capture on: gate the per-player ranked "
                "sweep too — bulk account/info, run the 3-call ranked worker "
                "only for ranked-known players whose last_battle_time advanced "
                "(any battle type) since their last observation."
            ),
        )
        parser.add_argument(
            "--random-first", action="store_true", dest="random_first",
            help=(
                "Random-first routing: send a player to the heavy 3-call ranked "
                "path only if they have ranked battles in a CURRENTLY-ACTIVE "
                "season (not just any old ranked history). Everyone else — incl. "
                "lapsed ranked players — goes the fast bulk-random path, so a "
                "niche mode stops throttling random coverage for the majority."
            ),
        )
        parser.add_argument(
            "--skip-ranked", action="store_true", dest="skip_ranked",
            help=(
                "Skip the per-player ranked sweep entirely (random only). Lets "
                "the scheduler run ranked on a less-frequent cadence than the "
                "6h random floor."
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
        ranked_sweep_limit = options["ranked_sweep_limit"]
        chunk_delay = options["chunk_delay"]
        change_gate = options["change_gate"]
        ranked_gate = options["ranked_gate"]
        random_first = options["random_first"]
        skip_ranked = options["skip_ranked"]

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
                delay=delay, ranked_sweep_limit=ranked_sweep_limit,
                chunk_delay=chunk_delay, change_gate=change_gate,
                ranked_gate=ranked_gate, random_first=random_first,
                skip_ranked=skip_ranked, ranked=ranked, dry_run=dry_run,
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
                     ranked_sweep_limit, chunk_delay, change_gate, ranked_gate,
                     random_first, skip_ranked, ranked, dry_run, ranked_worker):
        """Bulk capture path (R1, D6).

        Random observations go through the fast bulk engine; only a subset goes
        the heavy per-player ranked worker. The two sets are mutually exclusive
        so a player never gets two observations per tick.

        Routing — `--random-first`: a player takes the ranked path only if they
        have ranked battles in a CURRENTLY-ACTIVE season (`_ranked_active_ids`);
        everyone else, including lapsed ranked players, takes the fast random
        path so a niche mode stops throttling random coverage. Falls back to the
        broad ever-ranked marker (`_ranked_known_ids`) when season metadata is
        unavailable, so ranked capture is never silently dropped. Without
        `--random-first`, routing is the legacy ever-ranked marker.
        """
        from warships.incremental_battles import record_observations_bulk

        candidates = _candidates(realm, days, stale_hours, limit)
        candidate_ids = [row[0] for row in candidates]

        routing = "ever-ranked"
        if not ranked or skip_ranked:
            bulk_ids = candidate_ids
            ranked_ids = []
            if skip_ranked:
                routing = "skip-ranked"
        else:
            if random_first:
                current_seasons = _current_ranked_season_ids()
                if current_seasons is not None:
                    ranked_set = _ranked_active_ids(
                        realm, candidate_ids, current_seasons)
                    routing = "current-season(" + (
                        ",".join(map(str, current_seasons)) or "none") + ")"
                else:
                    ranked_set = _ranked_known_ids(realm, candidate_ids)
                    routing = "ever-ranked(season-meta-unavailable)"
            else:
                ranked_set = _ranked_known_ids(realm, candidate_ids)
            bulk_ids = [pid for pid in candidate_ids if pid not in ranked_set]
            ranked_ids = [pid for pid in candidate_ids if pid in ranked_set]
            # Ranked sweep gets its OWN bound (not --limit) so it stays small as
            # the random FLOOR_LIMIT scales (R3). Trimmed players are deferred —
            # still excluded from the bulk sweep, so they never double-capture.
            if ranked_sweep_limit and ranked_sweep_limit > 0:
                ranked_ids = ranked_ids[:ranked_sweep_limit]

        # Ranked-sweep gate: drop ranked players who haven't played since their
        # last observation (last_battle_time unchanged), so the 3-WG-call ranked
        # worker only runs for movers. Done after the limit + before the summary
        # so the reported count reflects what we'll actually sweep.
        ranked_known_total = len(ranked_ids)
        ranked_gated = 0
        if ranked_gate and ranked_ids and not dry_run:
            movers = _ranked_movers(realm, ranked_ids)
            ranked_gated = ranked_known_total - len(movers)
            ranked_ids = movers

        self.stdout.write(
            f"realm={realm} active-{days}d stale>{stale_hours}h: "
            f"{len(candidate_ids)} candidates — bulk_random={len(bulk_ids)} "
            f"(~{(len(bulk_ids) + 99) // 100 * 2:,} WG calls @ {chunk_delay}s/chunk), "
            f"ranked={ranked_known_total} → sweeping {len(ranked_ids)} "
            f"(gated_out={ranked_gated}) per-player "
            f"(~{len(ranked_ids) * 3:,} WG calls @ {delay}s) "
            f"(routing={routing}, ranked_capture={'on' if ranked else 'off'}, "
            f"change_gate={'on' if change_gate else 'off'}, "
            f"ranked_gate={'on' if ranked_gate else 'off'})"
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
                change_gate=change_gate,
            )
            self.stdout.write(
                f"  bulk random: completed={tally.get('completed', 0)} "
                f"(baseline={tally.get('baseline', 0)}, "
                f"events={tally.get('events', 0)}) "
                f"wg_failed={tally.get('wg_failed', 0)} "
                f"not_found={tally.get('not_found', 0)} "
                f"skipped_missing={tally.get('skipped_missing', 0)} "
                f"gated_skipped={tally.get('gated_skipped', 0)} "
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
