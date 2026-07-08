"""Read-only benchmark of battle-observation floor coverage, cost, and freshness.

Captures a REPRODUCIBLE metric set so progress can be measured day-over-day —
e.g. baseline now (gated cadence), again before R3 (floor-limit expansion), and
after R3. Run it identically each time and diff the JSON:

    python manage.py benchmark_observation_floor              # human-readable
    python manage.py benchmark_observation_floor --json       # machine-readable
    python manage.py benchmark_observation_floor --window-hours 24

All capture/throughput metrics are over a trailing window (default 24h, which
averages out the per-realm 6h striped cycles + time-of-day activity). Population
and freshness metrics are point-in-time. ZERO writes.

The headline metric is `coverage_ratio` = distinct players productively captured
(got a BattleEvent) in the window ÷ active-7d players. R3 (raising the floor
limit toward the full active set) should drive this toward 1.0 and cut the
`stale_over_24h` fraction.

`gap_1d` (2026-07-08) decomposes the residual 24h capture gap: active-1d
players with no BattleEvent in the window are split into missed PvP movers
(snapshot battles-delta > 0; with a `pvp_mover_no_event_48h` sub-count for
those still uncaptured at 48h), non-PvP actives (account clock moved, PvP
battles flat — co-op/Operations, invisible to PvP-only extraction), and
no-snapshot-pair (unclassifiable). It answers: is the remaining gap a floor
throughput problem or a capture-surface (game-mode) problem?

A root cron on the droplet runs this command daily (04:30 UTC) via
`server/scripts/snapshot_observation_floor.sh` and saves the JSON to
`/opt/battlestats-server/shared/benchmarks/observation-floor/YYYY-MM-DD_HHMMZ.json`.
See "Benchmarks" in agents/runbooks/runbook-bulk-battle-observation-capture-2026-06-06.md.
"""
from __future__ import annotations

import json
import os
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Count, Max
from django.utils import timezone

from warships.models import (
    BattleEvent,
    BattleObservation,
    Player,
    Snapshot,
    VALID_REALMS,
)

# Buckets of the 24h-gap decomposition (`gap_1d` in the JSON payload).
_GAP_KEYS = (
    "total",                    # active-1d players with no BattleEvent in window
    "pvp_mover",                # snapshot pair shows PvP battles rose → real miss
    "pvp_mover_no_event_48h",   # …and still no event in 48h → genuinely uncaptured
    "non_pvp_active",           # account clock moved, PvP battles flat (co-op/Ops)
    "no_snapshot_pair",         # missing today/prior snapshot row → unclassifiable
)

FLOOR_FLAGS = [
    "BATTLE_OBSERVATION_FLOOR_BULK_ENABLED",
    "BATTLE_OBSERVATION_FLOOR_BULK_REALMS",
    "BATTLE_OBSERVATION_FLOOR_CHANGE_GATE_ENABLED",
    "BATTLE_OBSERVATION_FLOOR_RANKED_GATE_ENABLED",
    "BATTLE_OBSERVATION_FLOOR_RANDOM_FIRST_ENABLED",
    "BATTLE_OBSERVATION_FLOOR_RANKED_DAILY_ENABLED",
    "BATTLE_OBSERVATION_FLOOR_SELF_CHAIN_ENABLED",
    "BATTLE_OBSERVATION_FLOOR_LIMIT",
    "BATTLE_OBSERVATION_FLOOR_HOURS",
    "BATTLE_OBSERVATION_FLOOR_DAYS",
    # Engines that feed the mover-capture KPI denominator: record the config
    # that produced each daily snapshot so the metric is interpretable later.
    "SNAPSHOT_ACTIVE_PLAYERS_ENABLED",
    "SNAPSHOT_ACTIVE_LIMIT",
    "HOT_PLAYERS_ENABLED",
    "HOT_PLAYERS_MAX",
]


class Command(BaseCommand):
    help = (
        "Read-only benchmark of observation-floor coverage/cost/freshness. "
        "Reproducible day-over-day; diff the --json output to measure progress."
    )

    def add_arguments(self, parser):
        parser.add_argument("--window-hours", type=int, default=24,
                            dest="window_hours")
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        window_h = options["window_hours"]
        now = timezone.now()
        since = now - timedelta(hours=window_h)
        cut7 = (now - timedelta(days=7)).date()
        cut1 = (now - timedelta(days=1)).date()
        stale_before = now - timedelta(hours=24)

        realms = sorted(VALID_REALMS)

        # Mover-capture KPI denominator: the cheap bulk snapshot engine writes a
        # daily cumulative `Snapshot.battles` for the active base, so a player
        # who battled "today" is one whose cumulative battles rose between the
        # two most-recent snapshot dates. This is the *true mover* set, derived
        # independently of the floor (which supplies the numerator via
        # BattleEvent). `interval_battles` is NOT used: it's only populated on
        # the per-player view path, not by the bulk engine.
        snap_dates = list(
            Snapshot.objects.order_by("-date")
            .values_list("date", flat=True).distinct()[:2]
        )
        latest_date = snap_dates[0] if snap_dates else None
        prior_date = snap_dates[1] if len(snap_dates) > 1 else None

        # Precompute the mover-capture denominator ONCE, set-based. `Snapshot` is
        # ~2.5M rows with no index on `date` (only (player_id, date)), so a
        # correlated Subquery per today-row was O(N) and took >7min at prod scale.
        # Instead: a single `date__in` pass over the two relevant dates, joined to
        # Player for realm/is_hidden, tallied per realm + total in Python.
        snap_today_count: dict[str, int] = {}   # realm/'__total__' -> active-7d w/ snapshot today
        snap_movers: dict[str, int] = {}        # realm/'__total__' -> active-7d cumulative battles rose
        today_b: dict[int, int] = {}
        prior_b: dict[int, int] = {}
        if latest_date is not None:
            # Scope the denominator to non-hidden active-7d players (the population
            # the per-ship-daily goal targets; also makes snapshot_coverage_frac =
            # "active players actually snapshotted / active-7d"). Computed as two
            # independent reads intersected in Python, NOT a Snapshot<->Player join
            # over the 2.5M-row table: the active-player set reuses the same filter
            # the benchmark already runs (a Player seq scan, ~4s), and snapshots are
            # one date__in scan (Snapshot has no `date` index → seq scan, ~3s, no
            # join). A correlated Subquery per today-row was the wrong shape here.
            active_realm = dict(
                Player.objects.filter(is_hidden=False, last_battle_date__gte=cut7)
                .values_list("id", "realm").iterator()
            )
            for pid, sdate, battles in (
                Snapshot.objects
                .filter(date__in=[latest_date] + ([prior_date] if prior_date else []))
                .values_list("player_id", "date", "battles").iterator()
            ):
                if sdate == latest_date:
                    today_b[pid] = battles
                elif sdate == prior_date:
                    prior_b[pid] = battles
            for pid, prealm in active_realm.items():
                tb = today_b.get(pid)
                if tb is None:
                    continue  # active player not snapshotted today (a coverage gap)
                for key in (prealm, "__total__"):
                    snap_today_count[key] = snap_today_count.get(key, 0) + 1
                pb = prior_b.get(pid)
                if pb is not None and tb > pb:
                    for key in (prealm, "__total__"):
                        snap_movers[key] = snap_movers.get(key, 0) + 1

        # 24h-gap decomposition (2026-07-08): of active-1d players who produced
        # NO BattleEvent in the trailing window, how many are (a) real missed
        # PvP movers (snapshot pair shows a cumulative-battles rise), (b) active
        # only outside Random PvP (account clock moved but cumulative PvP
        # battles flat — co-op/Operations/etc., structurally invisible to the
        # PvP-only `ships/stats` extraction), or (c) unclassifiable (no
        # snapshot pair)? `pvp_mover_no_event_48h` narrows (a) to genuinely
        # uncaptured movers: a mover with an event in the trailing 48h (fixed,
        # independent of --window-hours) was captured late across the window
        # boundary, not lost. Caveat: (b) is an upper bound — a player whose
        # battles rose only AFTER today's snapshot was taken lands in (b)
        # today and re-presents as a mover tomorrow.
        gap_stats: dict[str, dict[str, int]] = {}
        if latest_date is not None and prior_date is not None:
            since48 = now - timedelta(hours=48)
            active1_realm = dict(
                Player.objects.filter(is_hidden=False, last_battle_date__gte=cut1)
                .values_list("id", "realm").iterator()
            )
            prod_ids = set(
                BattleEvent.objects.filter(detected_at__gte=since)
                .values_list("player_id", flat=True)
            )
            prod_ids_48h = set(
                BattleEvent.objects.filter(detected_at__gte=since48)
                .values_list("player_id", flat=True)
            )
            for pid, prealm in active1_realm.items():
                if pid in prod_ids:
                    continue
                tb = today_b.get(pid)
                pb = prior_b.get(pid)
                if tb is None or pb is None:
                    bucket = "no_snapshot_pair"
                elif tb > pb:
                    bucket = "pvp_mover"
                else:
                    bucket = "non_pvp_active"
                for key in (prealm, "__total__"):
                    g = gap_stats.setdefault(key, dict.fromkeys(_GAP_KEYS, 0))
                    g["total"] += 1
                    g[bucket] += 1
                    if bucket == "pvp_mover" and pid not in prod_ids_48h:
                        g["pvp_mover_no_event_48h"] += 1

        result = {
            "captured_at": now.isoformat(),
            "window_hours": window_h,
            "config": {k: os.getenv(k, "<unset>") for k in FLOOR_FLAGS},
            "snapshot_dates": [str(d) for d in snap_dates],
            "realms": {},
            "totals": {},
        }

        for realm in realms + ["__total__"]:
            is_total = realm == "__total__"
            pq = Player.objects.filter(is_hidden=False, last_battle_date__gte=cut7)
            if not is_total:
                pq = pq.filter(realm=realm)
            active7 = pq.count()
            active1 = Player.objects.filter(
                is_hidden=False, last_battle_date__gte=cut1,
                **({} if is_total else {"realm": realm}),
            ).count()

            obs_q = BattleObservation.objects.filter(observed_at__gte=since)
            ev_q = BattleEvent.objects.filter(detected_at__gte=since)
            if not is_total:
                obs_q = obs_q.filter(player__realm=realm)
                ev_q = ev_q.filter(player__realm=realm)

            obs = obs_q.count()
            obs_players = obs_q.values("player_id").distinct().count()
            events = ev_q.count()
            prod_players = ev_q.values("player_id").distinct().count()
            by_source = dict(obs_q.values_list("source").annotate(c=Count("id")))
            bulk = by_source.get("bulk_floor", 0)
            poll = by_source.get("poll", 0)

            # Freshness: of active-7d players, how recent is their latest obs?
            fq = pq.annotate(latest=Max("battle_observations__observed_at"))
            fresh = fq.filter(latest__gte=stale_before).count()
            never = fq.filter(latest__isnull=True).count()
            stale = active7 - fresh - never  # has an obs but >24h old

            # Mover-capture KPI: distinct players who actually battled between
            # the two most-recent snapshot dates (cumulative battles rose) =
            # the true denominator; `distinct_productive` (BattleEvent) is the
            # captured numerator. snapshot_coverage_frac validates that the
            # snapshot engine clears the active base (else the denominator is
            # itself incomplete and the KPI under-reports the real gap).
            snapshot_today = None
            snapshot_movers = None
            snapshot_coverage_frac = None
            mover_capture_rate = None
            if latest_date is not None:
                snapshot_today = snap_today_count.get(realm, 0)
                snapshot_coverage_frac = (
                    round(snapshot_today / active7, 4) if active7 else None)
                if prior_date is not None:
                    snapshot_movers = snap_movers.get(realm, 0)
                    mover_capture_rate = (
                        round(prod_players / snapshot_movers, 4)
                        if snapshot_movers else None)

            metrics = {
                "active_1d": active1,
                "active_7d": active7,
                "observations": obs,
                "distinct_observed": obs_players,
                "events": events,
                "distinct_productive": prod_players,
                "obs_bulk_floor": bulk,
                "obs_poll": poll,
                "coverage_ratio_vs_7d": round(prod_players / active7, 4) if active7 else None,
                "coverage_ratio_vs_1d": round(prod_players / active1, 4) if active1 else None,
                "productive_rate": round(prod_players / obs_players, 4) if obs_players else None,
                "fresh_within_24h": fresh,
                "stale_over_24h": stale,
                "never_observed": never,
                "fresh_frac": round(fresh / active7, 4) if active7 else None,
                # Mover-capture KPI (the metric this goal actually needs).
                "snapshot_today": snapshot_today,
                "snapshot_movers": snapshot_movers,
                "snapshot_coverage_frac": snapshot_coverage_frac,
                "mover_capture_rate": mover_capture_rate,
                # 24h-gap decomposition; None until two snapshot days exist.
                "gap_1d": (
                    gap_stats.get(realm, dict.fromkeys(_GAP_KEYS, 0))
                    if latest_date is not None and prior_date is not None
                    else None
                ),
            }
            if is_total:
                result["totals"] = metrics
            else:
                result["realms"][realm] = metrics

        if options["as_json"]:
            self.stdout.write(json.dumps(result, indent=2, default=str))
            return

        t = result["totals"]
        self.stdout.write(self.style.SUCCESS(
            f"=== observation-floor benchmark @ {result['captured_at']} "
            f"(trailing {window_h}h) ==="))
        self.stdout.write("config: " + " ".join(
            f"{k.replace('BATTLE_OBSERVATION_FLOOR_', '')}={v}"
            for k, v in result["config"].items()))
        self.stdout.write("")
        hdr = (f"{'realm':<8}{'active7d':>9}{'observed':>9}{'productive':>11}"
               f"{'cov/7d':>8}{'prodRate':>9}{'fresh<24h':>10}{'stale>24h':>10}"
               f"{'bulk_floor':>11}")
        self.stdout.write(hdr)
        for realm in realms:
            m = result["realms"][realm]
            self.stdout.write(
                f"{realm:<8}{m['active_7d']:>9,}{m['distinct_observed']:>9,}"
                f"{m['distinct_productive']:>11,}{(m['coverage_ratio_vs_7d'] or 0):>8.2%}"
                f"{(m['productive_rate'] or 0):>9.2%}{m['fresh_within_24h']:>10,}"
                f"{m['stale_over_24h']:>10,}{m['obs_bulk_floor']:>11,}")
        self.stdout.write(
            f"{'TOTAL':<8}{t['active_7d']:>9,}{t['distinct_observed']:>9,}"
            f"{t['distinct_productive']:>11,}{(t['coverage_ratio_vs_7d'] or 0):>8.2%}"
            f"{(t['productive_rate'] or 0):>9.2%}{t['fresh_within_24h']:>10,}"
            f"{t['stale_over_24h']:>10,}{t['obs_bulk_floor']:>11,}")
        self.stdout.write("")
        self.stdout.write(
            f"HEADLINE: {t['distinct_productive']:,} of {t['active_7d']:,} "
            f"active-7d players productively captured in {window_h}h "
            f"({(t['coverage_ratio_vs_7d'] or 0):.1%}); "
            f"{(t['fresh_frac'] or 0):.1%} of active-7d have an observation "
            f"<24h old. R3 target: drive both toward 100%.")
        # Mover-capture KPI: the metric defined over players who actually
        # battled (snapshot battles-delta), not over the active-7d population.
        if t.get("snapshot_movers") is not None:
            self.stdout.write(
                f"MOVER-CAPTURE: {t['distinct_productive']:,} of "
                f"{t['snapshot_movers']:,} daily movers captured "
                f"({(t['mover_capture_rate'] or 0):.1%}); snapshot covers "
                f"{(t['snapshot_coverage_frac'] or 0):.1%} of active-7d "
                f"(dates {result['snapshot_dates']}). This is the metric the "
                f"per-ship-daily goal is defined over.")
        else:
            self.stdout.write(
                "MOVER-CAPTURE: insufficient snapshot history "
                f"(dates {result['snapshot_dates']}) — need two snapshot days "
                "to compute the mover denominator.")
        # 24h-gap decomposition: where the uncaptured slice of active-1d
        # actually lives (missed PvP movers vs non-PvP activity).
        g = t.get("gap_1d")
        if g is not None:
            self.stdout.write(
                f"GAP-1D: {g['total']:,} of {t['active_1d']:,} active-1d "
                f"players produced no BattleEvent in {window_h}h — "
                f"{g['non_pvp_active']:,} active outside Random PvP "
                f"(co-op/Operations), {g['pvp_mover']:,} missed PvP movers "
                f"({g['pvp_mover_no_event_48h']:,} still uncaptured at 48h), "
                f"{g['no_snapshot_pair']:,} unclassifiable (no snapshot pair).")
