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
    VALID_REALMS,
)

FLOOR_FLAGS = [
    "BATTLE_OBSERVATION_FLOOR_BULK_ENABLED",
    "BATTLE_OBSERVATION_FLOOR_BULK_REALMS",
    "BATTLE_OBSERVATION_FLOOR_CHANGE_GATE_ENABLED",
    "BATTLE_OBSERVATION_FLOOR_RANKED_GATE_ENABLED",
    "BATTLE_OBSERVATION_FLOOR_LIMIT",
    "BATTLE_OBSERVATION_FLOOR_HOURS",
    "BATTLE_OBSERVATION_FLOOR_DAYS",
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
        result = {
            "captured_at": now.isoformat(),
            "window_hours": window_h,
            "config": {k: os.getenv(k, "<unset>") for k in FLOOR_FLAGS},
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
