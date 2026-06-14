"""Read-only benchmark of clan-crawl productivity, coverage, and liveness.

Captures a REPRODUCIBLE metric set so crawl progress can be measured
day-over-day. Run it identically each time and diff the JSON:

    python manage.py benchmark_crawl_productivity              # human-readable
    python manage.py benchmark_crawl_productivity --json       # machine-readable
    python manage.py benchmark_crawl_productivity --window-hours 24

Scope — this measures the **multi-day clan crawl** (`crawl_all_clans_task` →
`warships.clan_crawl`), the discovery/refresh sweep that walks every clan per
realm and re-fetches its members. It is NOT the enrichment crawler (battles_json
build, gated by ENRICH_*) — for that use `check_enrichment_crawler.sh` /
`enrichment-status`.

The headline metric is `clans_fetched_24h` per realm (clans whose `last_fetch`
landed in the trailing window) and its `clan_coverage_pct` of the realm's clan
catalog. Because the crawl walks the whole catalog while clan-page refreshes
touch only a handful, `last_fetch` is crawl-attributable here; `implied_full_pass_days`
projects that rate into a full-catalog cadence.

Attribution honesty (read before trusting a number):
  * `clans_fetched_24h` / `clan_coverage_pct` — crawl-attributable headline.
  * `players_total` / `clans_total` — cumulative row counts. Their day-over-day
    DELTA is a *net* discovery proxy (net new rows = discovered − GDPR-deleted),
    NOT a clean per-pass discovery count; the schema has no crawl-discovery
    timestamp, so true per-pass discovery is not measurable here.
  * `clans_never_fetched` — clans discovered but never yet crawled (first-crawl
    backlog).
  * Player `last_fetch` is written by enrichment/floor/visits too, so it is NOT
    crawl-specific and is deliberately omitted.

Liveness is point-in-time, read from the same Redis cache keys the crawl uses
(`lock` / `heartbeat` / `pass_started_at` / `pending`). A low-coverage day with
`crawl_lock_held=false` and no fresh heartbeat means the crawl was paused/between
passes during the window — NOT a regression. Cross-check liveness before any
verdict.

A root cron on the droplet runs this daily (04:35 UTC) via
`server/scripts/snapshot_crawl_productivity.sh`, saving JSON to
`/opt/battlestats-server/shared/benchmarks/crawl-productivity/YYYY-MM-DD_HHMMZ.json`.
ZERO writes to the database.
"""
from __future__ import annotations

import json
import os
from datetime import timedelta

from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.utils import timezone

from warships.models import Clan, Player, VALID_REALMS
from warships.tasks import (
    _clan_crawl_heartbeat_key,
    _clan_crawl_lock_key,
    _clan_crawl_pass_marker_key,
    _clan_crawl_pending_key,
)

CRAWL_FLAGS = [
    "ENABLE_CRAWLER_SCHEDULES",
    "CLAN_CRAWL_CORE_ONLY",
]


def _heartbeat_age_s(hb, now) -> float | None:
    """Crawl heartbeat is a float epoch (see touch_clan_crawl_heartbeat)."""
    if hb is None:
        return None
    try:
        return round(now.timestamp() - float(hb), 1)
    except (TypeError, ValueError):
        return None


def _marker_age_s(marker, now) -> float | None:
    """Pass marker is a tz-aware datetime (django_timezone.now() at pass start)."""
    if marker is None:
        return None
    try:
        return round((now - marker).total_seconds(), 1)
    except TypeError:
        return None


class Command(BaseCommand):
    help = (
        "Read-only benchmark of clan-crawl productivity (catalog coverage, "
        "throughput, discovery delta, liveness). Reproducible day-over-day; "
        "diff the --json output to measure progress."
    )

    def add_arguments(self, parser):
        parser.add_argument("--window-hours", type=int, default=24,
                            dest="window_hours")
        parser.add_argument("--json", action="store_true", dest="as_json")

    def _realm_metrics(self, realm, since, now, is_total):
        clan_q = Clan.objects.all() if is_total else Clan.objects.filter(realm=realm)
        player_q = Player.objects.all() if is_total else Player.objects.filter(realm=realm)

        clans_total = clan_q.count()
        clans_fetched = clan_q.filter(last_fetch__gte=since).count()
        clans_never = clan_q.filter(last_fetch__isnull=True).count()
        players_total = player_q.count()

        coverage = round(clans_fetched / clans_total, 4) if clans_total else None
        # Project the windowed rate into a full-catalog cadence (in days).
        if clans_fetched:
            window_days = (now - since).total_seconds() / 86400.0
            implied_days = round(clans_total / clans_fetched * window_days, 2)
        else:
            implied_days = None

        metrics = {
            "clans_total": clans_total,
            "clans_fetched_24h": clans_fetched,
            "clan_coverage_pct": coverage,
            "implied_full_pass_days": implied_days,
            "clans_never_fetched": clans_never,
            "players_total": players_total,
        }

        if not is_total:
            lock = cache.get(_clan_crawl_lock_key(realm))
            hb = cache.get(_clan_crawl_heartbeat_key(realm))
            marker = cache.get(_clan_crawl_pass_marker_key(realm))
            pending = cache.get(_clan_crawl_pending_key(realm))
            metrics["liveness"] = {
                "crawl_lock_held": lock is not None,
                "heartbeat_age_s": _heartbeat_age_s(hb, now),
                "pass_marker_age_s": _marker_age_s(marker, now),
                "pending": pending is not None,
            }
        return metrics

    def handle(self, *args, **options):
        window_h = options["window_hours"]
        now = timezone.now()
        since = now - timedelta(hours=window_h)
        realms = sorted(VALID_REALMS)

        result = {
            "captured_at": now.isoformat(),
            "window_hours": window_h,
            "config": {k: os.getenv(k, "<unset>") for k in CRAWL_FLAGS},
            "realms": {},
            "totals": {},
        }
        realms_crawling = 0
        for realm in realms:
            m = self._realm_metrics(realm, since, now, is_total=False)
            result["realms"][realm] = m
            if m["liveness"]["crawl_lock_held"]:
                realms_crawling += 1
        totals = self._realm_metrics("__total__", since, now, is_total=True)
        totals["realms_crawling"] = realms_crawling
        result["totals"] = totals

        if options["as_json"]:
            self.stdout.write(json.dumps(result, indent=2, default=str))
            return

        t = result["totals"]
        self.stdout.write(self.style.SUCCESS(
            f"=== clan-crawl productivity benchmark @ {result['captured_at']} "
            f"(trailing {window_h}h) ==="))
        self.stdout.write("config: " + " ".join(
            f"{k}={v}" for k, v in result["config"].items()))
        self.stdout.write("")
        hdr = (f"{'realm':<8}{'clans':>9}{'fetched':>10}{'cov':>8}"
               f"{'passETA':>9}{'never':>8}{'players':>11}  liveness")
        self.stdout.write(hdr)
        for realm in realms:
            m = result["realms"][realm]
            self.stdout.write(
                f"{realm:<8}{m['clans_total']:>9,}{m['clans_fetched_24h']:>10,}"
                f"{(m['clan_coverage_pct'] or 0):>8.1%}"
                f"{self._eta_str(m['implied_full_pass_days']):>9}"
                f"{m['clans_never_fetched']:>8,}{m['players_total']:>11,}  "
                f"{self._liveness_str(m['liveness'])}")
        self.stdout.write(
            f"{'TOTAL':<8}{t['clans_total']:>9,}{t['clans_fetched_24h']:>10,}"
            f"{(t['clan_coverage_pct'] or 0):>8.1%}"
            f"{self._eta_str(t['implied_full_pass_days']):>9}"
            f"{t['clans_never_fetched']:>8,}{t['players_total']:>11,}  "
            f"{t['realms_crawling']} realm(s) crawling")
        self.stdout.write("")
        self.stdout.write(
            f"HEADLINE: {t['clans_fetched_24h']:,} of {t['clans_total']:,} clans "
            f"refreshed in {window_h}h ({(t['clan_coverage_pct'] or 0):.1%}); "
            f"full-catalog cadence ~{self._eta_str(t['implied_full_pass_days'])}. "
            f"Δ players_total / Δ clans_total vs the prior snapshot = net discovery.")

    @staticmethod
    def _eta_str(days) -> str:
        return f"{days:g}d" if days else "n/a"

    @staticmethod
    def _liveness_str(lv) -> str:
        if lv["crawl_lock_held"]:
            hb = lv["heartbeat_age_s"]
            return f"CRAWLING (hb {hb:g}s)" if hb is not None else "CRAWLING"
        age = lv["pass_marker_age_s"]
        if age is not None:
            return f"idle (pass {age / 3600:.1f}h)"
        return "no active pass"
