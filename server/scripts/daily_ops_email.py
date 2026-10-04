#!/usr/bin/env python3
"""Battlestats ops ALERT email (exception-only).

Runs unattended on the production droplet from a systemd timer
(`battlestats-ops-digest.timer`, 11:31 UTC). Reads the three durable benchmark
snapshot families the /observation, /crawl-yield and /recapture skills read,
selects the correct comparison points in Python (so the LLM never miscomputes a
delta), applies a DETERMINISTIC verdict in Python (`evaluate()`), and then:

  * all clear -> prints a one-line summary to stdout for the timer journal and
    exits 0 WITHOUT sending anything;
  * tripped   -> asks the Anthropic API to write up ONLY the tripped conditions
    and mails it, with the condition codes named in the subject.

The verdict is never delegated to the LLM. An LLM gate would be
non-deterministically silent on the day it matters; the model is invoked only to
write up an alert Python has already decided to send.

Silence stays distinguishable from breakage three ways:
  1. the fail-loud path is untouched -- any exception still mails
     "[battlestats] daily ops email FAILED" with a traceback, unconditionally.
     Exception-only applies to the DIGEST, not to this script's own errors;
  2. missing / stale / unreadable snapshots are themselves alert conditions, and
     snapshot SHAPE is checked BEFORE any count is trusted -- a recapture pass
     truncated by the soft time limit carries `partial: true` and is otherwise
     numerically identical to a healthy "cursor exhausted the pool" pass;
  3. a weekly heartbeat (`OPS_EMAIL_HEARTBEAT_DOW`, default Monday) mails the
     deterministic table regardless of verdict, so the timer, the SMTP path and
     the snapshot files all still prove themselves end to end. A script cannot
     detect its own non-execution; only a periodic unconditional send can.

Self-contained: stdlib only (urllib + smtplib), no venv, no pip installs. Config
and secrets come from an env file (default /etc/battlestats-ops-email.env, chmod
600), NEVER from this script -- it lives in a public repo.

Thresholds are named constants (`DEFAULT_THRESHOLDS`), each overridable by an
`OPS_ALERT_<NAME>` env var, and every one is derived from the observed historical
distribution of these snapshots. Derivation + backtest:
`agents/runbooks/runbook-ops-email-exception-only-2026-08-09.md`.

Flags:
  --dry-run   Build the email and print it to stdout; do not send. Prints the
              verdict too, so this doubles as "what would fire today".
  --no-llm    Skip the Anthropic call; use the plain deterministic table (also
              the automatic fallback if the API errors under normal runs).
  --force     Send even when the verdict is all-clear (per-invocation twin of
              OPS_EMAIL_ALWAYS_SEND=1).
"""

from __future__ import annotations

import json
import os
import sys
import traceback
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Shared send path. The sys.path insert keeps the no-venv guarantee: opsmail is
# stdlib-only by contract (enforced by test_opsmail.test_module_imports_no_django),
# so a bare python3 can import it straight from the server/ package directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from warships.opsmail import cfg, load_env_file, send_email  # noqa: E402

DEFAULT_ENV_FILE = "/etc/battlestats-ops-email.env"
DEFAULT_BENCH_DIR = "/opt/battlestats-server/shared/benchmarks"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

REALMS = ("na", "eu", "asia")


# config: cfg() and load_env_file() now come from warships.opsmail (imported above).


# --------------------------------------------------------------------------- #
# snapshot loading
# --------------------------------------------------------------------------- #
def _parse_ts(s: str) -> datetime:
    # captured_at looks like 2026-07-01T04:30:04.188264 (naive, UTC by convention)
    return datetime.fromisoformat(s)


def _load_dir(bench_dir: str, sub: str) -> tuple[list[dict], list[str]]:
    """Parse every snapshot in a family directory.

    Returns (parsed, unreadable). Parse failures are RETURNED, not swallowed: a
    corrupt newest file used to vanish silently and an older one took its place,
    which then read either as "stale" or, worse, as "fine". An unreadable file is
    an alert condition in its own right, so the verdict has to be able to see it.
    """
    d = Path(bench_dir) / sub
    out: list[dict] = []
    bad: list[str] = []
    try:
        files = sorted(d.glob("*.json"))
    except OSError:
        return out, bad
    for f in files:
        try:
            obj = json.loads(f.read_text())
            obj["_file"] = f.name
            obj["_ts"] = _parse_ts(obj["captured_at"])
            out.append(obj)
        except Exception as e:
            bad.append(f"{f.name}: {type(e).__name__}: {e}")
            continue
    out.sort(key=lambda o: o["_ts"])
    return out, bad


def utcnow() -> datetime:
    """Naive UTC now, matching the naive `captured_at` the snapshots write.

    Spelled out rather than datetime.utcnow() because that is deprecated from
    3.12 and removed later; this script has to keep running under a bare system
    python for years.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _age_hours(ts: datetime, now: datetime) -> float:
    """Hours between a naive-UTC `captured_at` and `now` (also naive UTC)."""
    return (now - ts).total_seconds() / 3600.0


def _closest(snaps: list[dict], target: datetime, lo_h: float, hi_h: float):
    """Snapshot whose ts is closest to `target`, within [lo_h, hi_h] hours away."""
    best, best_gap = None, None
    for s in snaps:
        gap_h = abs((s["_ts"] - target).total_seconds()) / 3600.0
        if lo_h <= gap_h <= hi_h and (best_gap is None or gap_h < best_gap):
            best, best_gap = s, gap_h
    return best


OBS_FIELDS = (
    "active_1d", "active_7d", "distinct_productive", "coverage_ratio_vs_7d",
    "productive_rate", "fresh_within_24h", "fresh_frac", "stale_over_24h",
    "obs_bulk_floor", "obs_poll", "never_observed",
)


def _obs_scope(node: dict) -> dict:
    return {k: node.get(k) for k in OBS_FIELDS}


def _delta(cur: dict, prev: dict | None) -> dict:
    if not prev:
        return {}
    out = {}
    for k in OBS_FIELDS:
        a, b = cur.get(k), prev.get(k)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            out[k] = round(a - b, 4)
    return out


def gather_observation(bench_dir: str, now: datetime | None = None) -> dict:
    now = now or utcnow()
    snaps, bad = _load_dir(bench_dir, "observation-floor")
    if not snaps:
        return {"available": 0, "unreadable": bad}
    L = snaps[-1]
    d1 = _closest(snaps[:-1], L["_ts"] - timedelta(hours=24), 20, 28)
    d7 = _closest(snaps[:-1], L["_ts"] - timedelta(days=7), 24 * 6, 24 * 8)

    def block(s: dict):
        return {
            "captured_at": s["captured_at"],
            "totals": _obs_scope(s.get("totals", {})),
            "realms": {r: _obs_scope(s.get("realms", {}).get(r, {})) for r in REALMS},
        }

    result = {
        "available": len(snaps),
        "unreadable": bad,
        "config": L.get("config", {}),
        "latest": block(L),
        "latest_age_hours": round(_age_hours(L["_ts"], now), 2),
        "latest_status": L.get("status"),
        "latest_partial": L.get("partial"),
        "latest_failed_buckets": L.get("failed_buckets"),
        "d1": block(d1) if d1 else None,
        "d7": block(d7) if d7 else None,
    }
    if d1:
        result["delta_vs_d1"] = {
            "totals": _delta(L.get("totals", {}), d1.get("totals", {})),
            "realms": {
                r: _delta(L.get("realms", {}).get(r, {}), d1.get("realms", {}).get(r, {}))
                for r in REALMS
            },
        }
    return result


def gather_crawl_yield(bench_dir: str, now: datetime | None = None) -> dict:
    now = now or utcnow()
    snaps, bad = _load_dir(bench_dir, "crawl-yield")
    if not snaps:
        return {"available": 0, "unreadable": bad}
    by_realm: dict[str, list[dict]] = {r: [] for r in REALMS}
    for s in snaps:
        r = s.get("realm")
        if r in by_realm:
            by_realm[r].append(s)

    def scope(s: dict):
        return {
            "captured_at": s.get("captured_at"),
            "pass_started_at": s.get("pass_started_at"),
            "players_classified": s.get("players_classified"),
            "buckets": s.get("buckets", {}),
            "yield_total": s.get("yield_total"),
            "overlap_total": s.get("overlap_total"),
            "yield_frac": s.get("yield_frac"),
            "overlap_frac": s.get("overlap_frac"),
            "status": s.get("status"),
            "partial": s.get("partial"),
            "failed_buckets": s.get("failed_buckets"),
            "age_hours": round(_age_hours(s["_ts"], now), 2),
        }

    out = {"available": len(snaps), "unreadable": bad, "realms": {}}
    for r in REALMS:
        lst = by_realm[r]
        if not lst:
            out["realms"][r] = None
            continue
        out["realms"][r] = {
            "latest": scope(lst[-1]),
            "prev": scope(lst[-2]) if len(lst) > 1 else None,
        }
    return out


RECAP_FIELDS = (
    "mode", "band_days", "limit", "candidates", "scanned", "wg_calls", "no_data",
    "hidden", "chunk_errors", "still_dormant", "advanced", "yield_frac", "into7d",
    "into7d_clanned", "into7d_clanless", "still_lapsed", "still_lapsed_clanless",
    "cursor_stamped",
)


def gather_recapture(bench_dir: str, now: datetime | None = None) -> dict:
    now = now or utcnow()
    snaps, bad = _load_dir(bench_dir, "recapture-lapsed")
    if not snaps:
        return {"available": 0, "unreadable": bad}
    by_realm: dict[str, list[dict]] = {r: [] for r in REALMS}
    for s in snaps:
        r = s.get("realm")
        if r in by_realm:
            by_realm[r].append(s)

    def scope(s: dict):
        d = {k: s.get(k) for k in RECAP_FIELDS}
        d["captured_at"] = s.get("captured_at")
        d["age_hours"] = round(_age_hours(s["_ts"], now), 2)
        # Shape fields, read BEFORE any count is trusted. `partial` is carried
        # explicitly (not via .get default) so a snapshot that LACKS the key is
        # distinguishable from one that carries False -- see evaluate().
        d["partial"] = s.get("partial")
        d["partial_present"] = "partial" in s
        d["status"] = s.get("status")
        d["failed_buckets"] = s.get("failed_buckets")
        # Upstream abort. A separate axis from `partial`: partial means the soft
        # time limit cut the scan, aborted means the upstream died and the pass
        # accounted for nothing. Read via .get so pre-guard snapshots (no key)
        # stay falsy and behave exactly as before.
        d["aborted"] = s.get("aborted")
        d["abort_reason"] = s.get("abort_reason")
        return d

    out = {"available": len(snaps), "unreadable": bad, "realms": {}}
    for r in REALMS:
        lst = by_realm[r]
        out["realms"][r] = scope(lst[-1]) if lst else None
    return out


# --------------------------------------------------------------------------- #
# deterministic verdict
# --------------------------------------------------------------------------- #
# Every number below is derived from the observed historical distribution of
# these snapshots (observation-floor n=47 daily files 2026-06-23..2026-08-08 --
# the current regime, which begins when the bulk floor came online on 06-20/21;
# crawl-yield n=39 passes; recapture-lapsed n=113 runs), NOT invented. The
# derivation, the observed min/max behind each number, and a day-by-day backtest
# of the staleness rules live in
# agents/runbooks/runbook-ops-email-exception-only-2026-08-09.md.
#
# Two families of check, and the distinction matters when reading an alert:
#   * SHAPE + STALENESS -- tuned detectors with real incident backing. The 24h
#     recapture rule would have caught ASIA's 418h silent outage on 2026-07-22
#     instead of 2026-08-06.
#   * NUMERIC FLOORS/CEILINGS -- correlated catastrophe backstops, set well
#     OUTSIDE the observed envelope. None has ever fired on the historical
#     record; that is the point. Day-over-day deltas are deliberately NOT used:
#     the worst clean-day move in the current regime is asia distinct_productive
#     -43%, so any delta rule tight enough to detect anything would cry
#     regression constantly -- exactly what the /observation skill forbids.
DEFAULT_THRESHOLDS: dict[str, float] = {
    # --- staleness (hours), measured at run time against `captured_at` ---
    # observation-floor: cron 04:30 UTC daily, email 11:31 -> healthy age is
    # 7.0h on all 44 backtested days. 24h fires only on a genuinely missed run.
    "obs_max_age_hours": 24.0,
    # crawl-yield: a pass takes DAYS. Observed age-at-11:31 max per realm:
    # na 94.5h, eu 126.1h, asia 131.6h. 168h (7d) never fires historically and
    # still catches a stalled crawl within a week.
    "crawl_max_age_hours": 168.0,
    # recapture: daily Beat at 10:10/10:30/10:50 UTC -> healthy age 0.7-1.4h.
    # A single missed run lands at 24.7-25.4h, so the threshold must sit below
    # 24.7 to catch it. NOTE the healthy margin is only ~40-80 minutes; a run
    # that starts after ~11:15 will false-fire. Raise via env if that shows up.
    "recapture_max_age_hours": 24.0,

    # --- observation floor, TOTAL scope (regime min .. max) ---
    "obs_coverage_min": 0.18,             # observed 0.2441 .. 0.3577
    "obs_distinct_productive_min": 38000,  # observed 51,889 .. 74,632
    "obs_active_7d_min": 150000,          # observed 200,615 .. 221,054
    "obs_active_7d_max": 300000,
    "obs_active_1d_min": 40000,           # observed 75,304 .. 99,632
    "obs_productive_rate_min": 0.60,      # observed 0.8522 .. 0.9510
    "obs_fresh_frac_min": 0.15,           # observed 0.2617 .. 0.3710
    "obs_never_observed_max": 10000,      # observed 14 .. 1,728
    "obs_bulk_floor_min": 30000,          # observed 68,713 .. 107,907
    "obs_poll_max": 60000,                # observed 6,551 .. 15,835
    # stale_over_24h is MOSTLY the change-gate non-mover wall -- by design, not
    # a backlog -- so it gets no standalone rule. It only counts as a signal
    # when capture is ALSO down, per the /observation skill. Observed worst
    # pairing: 156,721 stale with 51,889 productive (2026-07-09), inside both.
    "obs_stale_over_24h_max": 175000,
    "obs_stale_pair_productive_min": 45000,

    # --- observation floor, PER-REALM scope (regime min across realms) ---
    "obs_realm_coverage_min": 0.12,             # lowest observed 0.1918 (asia)
    "obs_realm_distinct_productive_min": 8000,  # lowest observed 13,142 (asia)

    # --- crawl yield, per realm ---
    # players_classified is PER-REALM CATALOG SIZE, and the realms differ by
    # 1.8x, so one global floor cannot be tight for all three. Calibrated
    # 2026-08-11 from the full 42-snapshot corpus. Each realm's healthy band is
    # remarkably narrow -- the spread within a realm is 0.45%-1.5% across seven
    # weeks -- so a floor at ~91% of the observed steady-state minimum leaves
    # 6-19x the observed variation as headroom while catching a ~9% coverage
    # loss instead of the old 45-68%.
    #   na    steady 274,188 .. 275,869 (18 passes)  -> 250,000 = 91.2% of min
    #   eu    steady 471,664 .. 473,814 (9 passes)   -> 430,000 = 91.2% of min
    #   asia  steady 256,847 .. 260,796 (12 passes)  -> 235,000 = 91.5% of min
    # This DELIBERATELY breaks this file's usual "never fired on the historical
    # record" invariant: it fires on the 3 corpus passes that were not healthy
    # full walks -- na 2026-08-10 (93,353, the WG outage that prompted this),
    # eu 2026-07-17 (336,000, a partial the old floor absorbed silently), and
    # eu 2026-06-22 (262,271, the instrumentation-rollout first pass). It stays
    # silent on all 39 healthy passes. Resolution + env override: `thr_realm`.
    "crawl_classified_min:na": 250000,
    "crawl_classified_min:eu": 430000,
    "crawl_classified_min:asia": 235000,
    # Global fallback, used only for a realm with no calibrated band (a new
    # realm). Deliberately loose -- with no observed corpus a tight floor would
    # just false-fire; add a per-realm entry once a band exists.
    "crawl_classified_min": 150000,
    # Yield stays GLOBAL and loose on purpose: unlike classified, yield_total is
    # genuinely volatile (na 1,686..7,234, eu 5,234..25,970 -- a 3-4x swing
    # within one realm), because it tracks real player churn rather than catalog
    # size. A tight yield floor would cry regression constantly.
    "crawl_yield_total_min": 200,    # lowest observed 2,089 (na); 10x below

    # --- recapture, per realm ---
    "recapture_no_data_max": 500,      # observed 2 .. 23 of 30,000 scanned
    "recapture_chunk_errors_max": 0,   # observed 0 on all 113 runs
    "recapture_advanced_min": 10,      # lowest observed 33 (an off-cycle NA run)
    # --- service health (F4, 2026-08-26) ---
    # The writer runs at 11:00 UTC and the digest at 11:31, so a healthy age is
    # ~0.5h. 24h fires only on a genuinely missed run, matching obs/recapture.
    "service_health_max_age_hours": 24.0,
    # A task that raised even once in 24h is worth naming: these are periodic
    # tasks, so one failure is one whole missed run, not a sampled error rate.
    # The finding that motivated this (roll_up) was exactly one failure a night.
    "celery_task_failures_min": 1,
    # Worker timeouts are never routine: each one is a 500 with an empty body.
    # 1 would be honest but flaps on a single cold-cache request, so 2 in 24h.
    "gunicorn_worker_timeouts_min": 2,
}

# Tasks whose unit of work spans MORE THAN ONE dispatch by design, so a 24h
# window legitimately contains zero successes.
#
# The zero-success discriminator below assumes the measurement window bounds the
# work. For these it does not, and the rule then fires on healthy operation the
# same way an any-failure rule fired on cache warmers that fall back to
# `:published` by design. crawl_all_clans_task's own comment puts a full pass at
# ~12-18h against a 20700s (5h45m) per-dispatch soft limit, so truncation is the
# designed steady state and a pass completes every 2-4 dispatches. Measured on
# the droplet journal for the 7 days to 2026-08-28: three completions, so 4 of
# those 7 days contained zero successes and at least one SoftTimeLimitExceeded.
#
# The cost, stated plainly: a task exempted here that is broken in EVERY realm
# trips neither Celery rule, because the per-realm rule requires at least one
# succeeding realm. Cover therefore falls entirely to a staleness rule on the
# task's OUTPUT, which is the honest instrument for work this shape:
#   crawl_all_clans_task -> snapshot_stale:crawl-yield:<realm> at 168h.
# That is 7 days of latency on a rare total failure, traded against a false
# positive four days in seven.
LONG_CYCLE_TASKS = frozenset({"warships.tasks.crawl_all_clans_task"})

# Family cadence labels used in alert text.
FAMILY_CADENCE = {
    "observation-floor": "daily 04:30 UTC",
    "crawl-yield": "per-realm, one completed clan-walk pass every few days",
    "recapture-lapsed": "daily 10:10/10:30/10:50 UTC per realm",
    "service-health": "daily 11:00 UTC, shortly before this digest",
}


def gather_service_health(bench_dir: str, now: datetime | None = None) -> dict:
    """Latest service-health snapshot: Celery task failures and backend 5xx.

    Added 2026-08-26 (F4). Unlike the other three families this one is not about
    data quality at all — it is about whether the machinery that produces the
    data is running. It exists because this digest reported "all clear" for at
    least five consecutive days while a nightly Celery task failed every single
    night and the API returned 500s, and no threshold anywhere could have caught
    that: the digest read only benchmark JSON and never asked systemd anything.

    It still reads only JSON. The snapshot is written by a ROOT-owned timer
    (`scripts/snapshot_service_health.sh`), because this script runs as the
    unprivileged `battlestats` user, which cannot open the journal at all. Do not
    "simplify" this by shelling out to journalctl from here; it returns nothing
    but a permissions error, and a zero count reads as health.
    """
    now = now or utcnow()
    snaps, bad = _load_dir(bench_dir, "service-health")
    if not snaps:
        return {"available": 0, "unreadable": bad}
    latest = snaps[-1]
    return {
        "available": len(snaps),
        "unreadable": bad,
        "captured_at": latest.get("captured_at"),
        "latest_age_hours": round(_age_hours(latest["_ts"], now), 2),
        "window_hours": latest.get("window_hours"),
        # Carried explicitly, not via a .get default: a snapshot that LACKS the
        # key must be distinguishable from one that says False. A missing key
        # means an older writer; False means we genuinely could not read the
        # journal, and then every count below is meaningless.
        "journal_readable": latest.get("journal_readable"),
        "journal_readable_present": "journal_readable" in latest,
        "celery_task_failures": latest.get("celery_task_failures") or [],
        # None (not []) when the writer predates this field, so the
        # evaluator can tell "not measured" from "measured, all zero".
        "celery_realm_successes": latest.get("celery_realm_successes"),
        "celery_failure_total": latest.get("celery_failure_total"),
        "gunicorn_worker_timeouts": latest.get("gunicorn_worker_timeouts"),
        "gunicorn_error_paths": latest.get("gunicorn_error_paths") or [],
        "gunicorn_error_total": latest.get("gunicorn_error_total"),
        "status": latest.get("status"),
        "failed_buckets": latest.get("failed_buckets"),
    }


def thr(name: str) -> float:
    """Threshold value, env-overridable as OPS_ALERT_<NAME>."""
    raw = cfg("OPS_ALERT_" + name.upper(), "")
    if raw.strip():
        try:
            return float(raw)
        except ValueError:
            pass
    return float(DEFAULT_THRESHOLDS[name])


def thr_realm(name: str, realm: str) -> float:
    """Per-realm threshold, falling back to the global one.

    Resolution order, first hit wins:
      1. env  OPS_ALERT_<NAME>_<REALM>
      2.      DEFAULT_THRESHOLDS["<name>:<realm>"]
      3. the global `thr(name)` (env OPS_ALERT_<NAME>, then DEFAULT_THRESHOLDS)

    Exists because some instruments measure a per-realm population rather than a
    rate, and the realms are not the same size. One global floor on such a metric
    is only ever tight for the smallest realm.
    """
    raw = cfg(f"OPS_ALERT_{name.upper()}_{realm.upper()}", "")
    if raw.strip():
        try:
            return float(raw)
        except ValueError:
            pass
    key = f"{name}:{realm}"
    if key in DEFAULT_THRESHOLDS:
        return float(DEFAULT_THRESHOLDS[key])
    return thr(name)


def _cond(code: str, detail: str) -> dict:
    return {"code": code, "detail": detail}


def _check_generic_shape(out: list[dict], prefix: str, node: dict) -> None:
    """Any status/partial/failed_buckets style field, checked before the counts.

    Generalized from the recapture lesson: a truncated pass is numerically
    indistinguishable from a healthy one, so the shape field is the ONLY signal.
    Applied uniformly so a future snapshot family that grows one is covered
    without another edit here.
    """
    status = node.get("status")
    if status is not None and str(status).lower() not in ("ok", "complete", "completed", "success"):
        out.append(_cond(f"snapshot_status:{prefix}", f"status={status!r} (expected ok/complete)"))
    failed = node.get("failed_buckets")
    if failed:
        out.append(_cond(f"snapshot_failed_buckets:{prefix}", f"failed_buckets={failed!r}"))


def evaluate(data: dict) -> list[dict]:
    """The verdict. Pure Python, pure thresholds, no LLM anywhere near it.

    Returns a list of tripped conditions (empty == all clear). Order is
    shape/liveness first, then numbers: a stale or truncated snapshot makes its
    own counts untrustworthy, so it is reported as the cause rather than being
    laundered into a numeric alert about numbers that were never valid.
    """
    out: list[dict] = []

    obs = data.get("observation") or {}
    crawl = data.get("crawl_yield") or {}
    recap = data.get("recapture") or {}
    svc = data.get("service_health") or {}

    # ---- 1. unreadable snapshot files (any family) --------------------------
    for fam, node in (("observation-floor", obs), ("crawl-yield", crawl),
                      ("recapture-lapsed", recap), ("service-health", svc)):
        for bad in node.get("unreadable") or []:
            out.append(_cond(f"snapshot_unreadable:{fam}", bad))

    # ---- 2. observation floor: liveness, shape, then numbers ---------------
    if not obs.get("available"):
        out.append(_cond(
            "snapshots_missing:observation-floor",
            f"no observation-floor snapshots found ({FAMILY_CADENCE['observation-floor']})",
        ))
    else:
        age = obs.get("latest_age_hours")
        limit = thr("obs_max_age_hours")
        if isinstance(age, (int, float)) and age > limit:
            out.append(_cond(
                "snapshot_stale:observation-floor",
                f"newest snapshot is {age:.1f}h old (limit {limit:.0f}h; "
                f"{FAMILY_CADENCE['observation-floor']}, healthy age at run time is ~7h)",
            ))
        _check_generic_shape(out, "observation-floor", {
            "status": obs.get("latest_status"),
            "failed_buckets": obs.get("latest_failed_buckets"),
        })
        if obs.get("latest_partial"):
            out.append(_cond("snapshot_partial:observation-floor", "latest snapshot reports partial=true"))
        out.extend(_evaluate_observation_numbers(obs))

    # ---- 3. crawl yield ----------------------------------------------------
    if not crawl.get("available"):
        out.append(_cond(
            "snapshots_missing:crawl-yield",
            f"no crawl-yield snapshots found ({FAMILY_CADENCE['crawl-yield']})",
        ))
    else:
        out.extend(_evaluate_crawl(crawl))

    # ---- 4. recapture ------------------------------------------------------
    if not recap.get("available"):
        out.append(_cond(
            "snapshots_missing:recapture-lapsed",
            f"no recapture-lapsed snapshots found ({FAMILY_CADENCE['recapture-lapsed']})",
        ))
    else:
        out.extend(_evaluate_recapture(recap))

    # ---- 5. service health (F4) --------------------------------------------
    if not svc.get("available"):
        out.append(_cond(
            "snapshots_missing:service-health",
            f"no service-health snapshots found ({FAMILY_CADENCE['service-health']})",
        ))
    else:
        out.extend(_evaluate_service_health(svc))

    return out


def _evaluate_service_health(svc: dict) -> list[dict]:
    """Liveness and shape first, then counts — the same order as every family.

    The ordering matters more here than anywhere else. Every number in this
    family is a count of bad things, so the failure mode of a broken snapshot is
    a run of zeros, which is indistinguishable from perfect health. Staleness and
    journal access are therefore checked BEFORE any count is believed.
    """
    out: list[dict] = []

    age = svc.get("latest_age_hours")
    limit = thr("service_health_max_age_hours")
    if isinstance(age, (int, float)) and age > limit:
        out.append(_cond(
            "snapshot_stale:service-health",
            f"newest snapshot is {age:.1f}h old (limit {limit:.0f}h; "
            f"{FAMILY_CADENCE['service-health']}, healthy age at run time is ~0.5h)",
        ))

    _check_generic_shape(out, "service-health", {
        "status": svc.get("status"),
        "failed_buckets": svc.get("failed_buckets"),
    })

    # An explicit False means the writer could not open the journal, so every
    # count below is a zero it invented. A MISSING key is an older writer that
    # predates this field; treat that as unknown rather than as a failure.
    if svc.get("journal_readable_present") and not svc.get("journal_readable"):
        out.append(_cond(
            "journal_unreadable:service-health",
            "the snapshot writer could not read the journal, so its zero counts "
            "mean 'not measured', NOT 'nothing failed' — check that the writer "
            "still runs as root",
        ))
        # Counts are known-meaningless; do not also alert on them.
        return out

    # Celery: the discriminator is "never succeeded", NOT "failed at least once".
    #
    # Measured against a real 24h window on 2026-08-26, alerting on any failure
    # trips 8 conditions, 7 of which are cache warmers that fail a fraction of
    # their runs and fall back to the durable :published copy exactly as designed.
    # A digest that fires every morning is a digest nobody reads, and then the one
    # morning it matters is indistinguishable from the rest. A task that failed
    # every single run in the window is a different animal: the nightly rollup
    # failed 5 of 5 nights and produced nothing at all.
    #
    # `succeeded` missing (an older writer) is treated as unknown, and unknown
    # falls back to alerting: better a spurious alert than a silent regression.
    floor = int(thr("celery_task_failures_min"))
    for row in svc.get("celery_task_failures") or []:
        count = row.get("count")
        if not isinstance(count, (int, float)) or count < floor:
            continue
        ok = row.get("succeeded")
        if isinstance(ok, (int, float)) and ok > 0:
            continue  # flaky, not broken: it is still completing runs
        task = row.get("task") or "<unknown task>"
        if task in LONG_CYCLE_TASKS:
            # Zero successes in 24h is normal operation here, not a fault; the
            # staleness rule on this task's output owns the real failure.
            continue
        out.append(_cond(
            f"celery_task_failing:{task}",
            f"{task} raised {row.get('exception') or 'an exception'} "
            f"{int(count)}x in the last {svc.get('window_hours') or 24}h "
            f"on {row.get('unit') or '<unknown unit>'} and succeeded 0 times",
        ))

    # Per-realm: a task striped across realms that fails ONE realm every run is
    # invisible above, because that rule keys on the task name and the other
    # realms' successes satisfy it. Measured 2026-08-26: warm_player_correlations_task
    # sat at 1 failure / 2 successes -- 66.7% -- while `eu` failed every run.
    #
    # A failure-RATE threshold cannot separate that from ordinary flakiness: any
    # rate quiet enough for warmers that fall back to :published by design is
    # also quiet at 66.7%. Realm is the right axis.
    #
    # Requiring at least one SUCCEEDING realm keeps a fully-broken task to the
    # single condition above instead of one per realm.
    realm_rows = svc.get("celery_realm_successes")
    if realm_rows is not None:
        by_task: dict[str, dict[str, int]] = {}
        for row in realm_rows:
            task = row.get("task")
            realm = row.get("realm")
            if not task or not realm:
                continue
            # ACCUMULATE, never assign: the writer tallies per (unit, task,
            # realm) but this keys on (task, realm), so a task seen under two
            # units yields two rows for the same pair. Assigning lets a zero
            # from one unit erase a healthy count from another.
            counts = by_task.setdefault(task, {})
            counts[realm] = counts.get(realm, 0) + int(row.get("count") or 0)
        for task, counts in sorted(by_task.items()):
            if not any(counts.get(r, 0) > 0 for r in REALMS):
                continue  # broken everywhere: the task-name rule owns it
            for realm in REALMS:
                if counts.get(realm, 0) > 0:
                    continue
                out.append(_cond(
                    f"celery_task_realm_failing:{task}:{realm}",
                    f"{task} completed 0 times for realm {realm} in the last "
                    f"{svc.get('window_hours') or 24}h while succeeding on "
                    + ", ".join(r for r in REALMS if counts.get(r, 0) > 0)
                    + " — a striped task that is failing or no longer dispatched "
                    "for one realm reads as healthy on the task-name axis",
                ))

    # gunicorn: a worker timeout is a 500 with an empty body, never routine.
    timeouts = svc.get("gunicorn_worker_timeouts")
    t_floor = thr("gunicorn_worker_timeouts_min")
    if isinstance(timeouts, (int, float)) and timeouts >= t_floor:
        paths = svc.get("gunicorn_error_paths") or []
        named = ", ".join(
            f"{p.get('path')} x{p.get('count')}" for p in paths[:5] if p.get("path")
        )
        out.append(_cond(
            "gunicorn_worker_timeouts",
            f"{int(timeouts)} worker timeouts in the last "
            f"{svc.get('window_hours') or 24}h (limit {t_floor:.0f}); each is a 500 "
            f"with an empty body"
            + (f" — {named}" if named else ""),
        ))

    return out


def _evaluate_observation_numbers(obs: dict) -> list[dict]:
    out: list[dict] = []
    latest = obs.get("latest") or {}
    t = latest.get("totals") or {}

    def num(node, key):
        v = node.get(key)
        return v if isinstance(v, (int, float)) else None

    checks = (
        ("coverage_ratio_vs_7d", "obs_coverage_min", "lt", "obs_low_coverage"),
        ("distinct_productive", "obs_distinct_productive_min", "lt", "obs_low_distinct_productive"),
        ("active_7d", "obs_active_7d_min", "lt", "obs_low_active_7d"),
        ("active_7d", "obs_active_7d_max", "gt", "obs_high_active_7d"),
        ("active_1d", "obs_active_1d_min", "lt", "obs_low_active_1d"),
        ("productive_rate", "obs_productive_rate_min", "lt", "obs_low_productive_rate"),
        ("fresh_frac", "obs_fresh_frac_min", "lt", "obs_low_fresh_frac"),
        ("never_observed", "obs_never_observed_max", "gt", "obs_high_never_observed"),
        ("obs_bulk_floor", "obs_bulk_floor_min", "lt", "obs_low_bulk_floor"),
        ("obs_poll", "obs_poll_max", "gt", "obs_high_poll"),
    )
    for field, tname, op, code in checks:
        v = num(t, field)
        if v is None:
            continue
        limit = thr(tname)
        if (op == "lt" and v < limit) or (op == "gt" and v > limit):
            arrow = "<" if op == "lt" else ">"
            out.append(_cond(code, f"totals.{field}={v} {arrow} {limit:g}"))

    # The one COMBINED rule. stale_over_24h alone is the by-design non-mover
    # wall; it is only a cadence signal when distinct_productive is down too.
    stale = num(t, "stale_over_24h")
    prod = num(t, "distinct_productive")
    if stale is not None and prod is not None:
        if stale > thr("obs_stale_over_24h_max") and prod < thr("obs_stale_pair_productive_min"):
            out.append(_cond(
                "obs_stale_wall_with_capture_drop",
                f"totals.stale_over_24h={stale} > {thr('obs_stale_over_24h_max'):g} WHILE "
                f"distinct_productive={prod} < {thr('obs_stale_pair_productive_min'):g}",
            ))

    for r in REALMS:
        rr = (latest.get("realms") or {}).get(r) or {}
        cov = num(rr, "coverage_ratio_vs_7d")
        if cov is not None and cov < thr("obs_realm_coverage_min"):
            out.append(_cond(f"obs_low_coverage:{r}",
                             f"{r}.coverage_ratio_vs_7d={cov} < {thr('obs_realm_coverage_min'):g}"))
        dp = num(rr, "distinct_productive")
        if dp is not None and dp < thr("obs_realm_distinct_productive_min"):
            out.append(_cond(f"obs_low_distinct_productive:{r}",
                             f"{r}.distinct_productive={dp} < {thr('obs_realm_distinct_productive_min'):g}"))
    return out


def _evaluate_crawl(crawl: dict) -> list[dict]:
    out: list[dict] = []
    limit_age = thr("crawl_max_age_hours")
    for r in REALMS:
        node = (crawl.get("realms") or {}).get(r)
        if not node or not node.get("latest"):
            out.append(_cond(f"realm_snapshot_missing:crawl-yield:{r}",
                             f"no completed clan-walk pass recorded for realm {r}"))
            continue
        L = node["latest"]
        _check_generic_shape(out, f"crawl-yield:{r}", L)
        if L.get("partial"):
            out.append(_cond(f"snapshot_partial:crawl-yield:{r}", f"{r} pass reports partial=true"))
        age = L.get("age_hours")
        if isinstance(age, (int, float)) and age > limit_age:
            out.append(_cond(f"snapshot_stale:crawl-yield:{r}",
                             f"{r}: newest completed pass is {age:.1f}h old (limit {limit_age:.0f}h; "
                             f"worst healthy age observed was 131.6h)"))
        # Shape before numbers: the five buckets partition every classified
        # player, exactly, on all 39 historical passes. A mismatch means the
        # snapshot is not describing what it claims to.
        buckets = L.get("buckets") or {}
        classified = L.get("players_classified")
        bsum = sum(v for v in buckets.values() if isinstance(v, (int, float)))
        if isinstance(classified, (int, float)) and buckets and bsum != classified:
            out.append(_cond(f"crawl_bucket_mismatch:{r}",
                             f"{r}: buckets sum to {bsum} but players_classified={classified}"))
        classified_floor = thr_realm("crawl_classified_min", r)
        if isinstance(classified, (int, float)) and classified < classified_floor:
            out.append(_cond(f"crawl_low_classified:{r}",
                             f"{r}.players_classified={classified} < {classified_floor:g} "
                             f"({classified / classified_floor:.0%} of this realm's floor)"))
        yt = L.get("yield_total")
        if isinstance(yt, (int, float)) and yt < thr("crawl_yield_total_min"):
            out.append(_cond(f"crawl_no_yield:{r}",
                             f"{r}.yield_total={yt} < {thr('crawl_yield_total_min'):g} "
                             f"(discovered_active + reactivated, the floor-impossible value)"))
    return out


def _evaluate_recapture(recap: dict) -> list[dict]:
    out: list[dict] = []
    limit_age = thr("recapture_max_age_hours")
    for r in REALMS:
        node = (recap.get("realms") or {}).get(r)
        if not node:
            out.append(_cond(f"realm_snapshot_missing:recapture-lapsed:{r}",
                             f"no recapture sweep snapshot for realm {r} (task runs daily)"))
            continue

        # ---- shape FIRST. A pass truncated by the soft time limit carries
        # partial=true and is otherwise numerically identical to a healthy
        # "cursor exhausted the pool" pass; that is how EU/ASIA lost every pass
        # unnoticed for two weeks before the 2026-08-06 fix.
        # `is not False`, not `is True`: post-fix snapshots all carry an explicit
        # partial=False, so a LATEST snapshot missing the field means the writer
        # changed or an old code path resurfaced -- silent shape drift, which is
        # exactly what this check exists to catch.
        if node.get("partial") is not False:
            if node.get("partial"):
                out.append(_cond(f"recapture_partial:{r}",
                                 f"{r}: pass was TRUNCATED by the soft time limit (partial=true); it "
                                 f"covered only scanned={node.get('scanned')} of "
                                 f"candidates={node.get('candidates')} and looks numerically identical "
                                 f"to a healthy full pass"))
            else:
                out.append(_cond(f"recapture_partial_field_absent:{r}",
                                 f"{r}: snapshot carries no `partial` field; truncation is undetectable "
                                 f"from this file, so its counts cannot be trusted"))
        _check_generic_shape(out, f"recapture-lapsed:{r}", node)

        age = node.get("age_hours")
        if isinstance(age, (int, float)) and age > limit_age:
            out.append(_cond(f"snapshot_stale:recapture-lapsed:{r}",
                             f"{r}: newest sweep is {age:.1f}h old (limit {limit_age:.0f}h; the sweep is "
                             f"daily, healthy age at run time is ~1h)"))

        mode = node.get("mode")
        if mode is not None and mode != "apply":
            out.append(_cond(f"recapture_mode:{r}",
                             f"{r}: mode={mode!r} -- writes are OFF, returners are being measured, not "
                             f"recaptured (RECAPTURE_LAPSED_APPLY)"))

        # ---- upstream abort, BEFORE any count ----------------------------
        # A pass the guard stopped accounted for nothing, so every numeric check
        # below it describes the same hole. On 2026-08-12 that hole arrived as
        # FOUR separate conditions for one asia outage (chunk_errors, plus a
        # cursor_stamped=0 and a zero component sum that are CORRECT BY DESIGN --
        # a failed chunk deliberately skips the rotation stamp -- plus advanced=0
        # following from both). Report the cause once and stop.
        # Placed after mode/staleness/shape on purpose: those describe faults
        # orthogonal to an outage and must survive it. In particular a sweep that
        # aborts once and then stops running entirely still trips snapshot_stale.
        if node.get("aborted"):
            out.append(_cond(
                f"recapture_aborted:{r}",
                f"{r}: sweep ABORTED on sustained upstream failure "
                f"({node.get('abort_reason') or 'reason not recorded'}); "
                f"chunk_errors={node.get('chunk_errors')}, covered "
                f"scanned={node.get('scanned')} of candidates={node.get('candidates')}. "
                f"Its outcome buckets and cursor stamps are absent BY DESIGN, so "
                f"treat this run as non-informative rather than as zero yield -- the "
                f"unstamped rows retry on the next daily run. Check the transport "
                f"(DNS vs WG status codes), not the sweep"))
            continue

        def num(key):
            v = node.get(key)
            return v if isinstance(v, (int, float)) else None

        errs = num("chunk_errors")
        if errs is not None and errs > thr("recapture_chunk_errors_max"):
            out.append(_cond(f"recapture_chunk_errors:{r}",
                             f"{r}.chunk_errors={errs} > {thr('recapture_chunk_errors_max'):g} (WG trouble)"))
        nd = num("no_data")
        if nd is not None and nd > thr("recapture_no_data_max"):
            out.append(_cond(f"recapture_high_no_data:{r}",
                             f"{r}.no_data={nd} > {thr('recapture_no_data_max'):g} (WG trouble)"))

        scanned = num("scanned")
        if scanned is not None and scanned <= 0:
            out.append(_cond(f"recapture_scanned_zero:{r}", f"{r}.scanned={scanned}: the sweep did nothing"))
        elif scanned:
            wg = num("wg_calls")
            if wg is not None and wg <= 0:
                out.append(_cond(f"recapture_shape:{r}",
                                 f"{r}: scanned={scanned} but wg_calls={wg}; inconsistent snapshot"))
            if mode == "apply":
                stamped = num("cursor_stamped")
                if stamped is not None and stamped <= 0:
                    out.append(_cond(f"recapture_cursor_stalled:{r}",
                                     f"{r}: cursor_stamped={stamped} in apply mode; the LRU rotation "
                                     f"cursor is not advancing, so the pool will never be walked"))
            parts = [num("still_dormant"), num("advanced"), num("hidden"), num("no_data")]
            if all(p is not None for p in parts) and sum(parts) != scanned:
                out.append(_cond(f"recapture_component_mismatch:{r}",
                                 f"{r}: still_dormant+advanced+hidden+no_data={sum(parts)} != "
                                 f"scanned={scanned}"))
            adv = num("advanced")
            if adv is not None and adv < thr("recapture_advanced_min"):
                out.append(_cond(f"recapture_no_returners:{r}",
                                 f"{r}.advanced={adv} < {thr('recapture_advanced_min'):g} returners found"))
    return out


def summarize_conditions(conditions: list[dict], limit: int = 4) -> str:
    codes = [c["code"] for c in conditions]
    shown = ", ".join(codes[:limit])
    if len(codes) > limit:
        shown += f", +{len(codes) - limit} more"
    return shown


def alert_subject(conditions: list[dict]) -> str:
    n = len(conditions)
    subj = f"[battlestats] ops ALERT ({n}): {summarize_conditions(conditions)}"
    return subj[:78]


# --------------------------------------------------------------------------- #
# synthesis
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = """You are the analyst writing a battlestats.online operations \
morning digest. battlestats is a World of Warships player/clan stats platform. \
You are given machine-selected, pre-diffed snapshot data from three independent \
nightly instruments. Write a concise, warm, precise HTML email (voice: Data from \
Star Trek -- analytical, no hype, no emdashes; use colons/semicolons). Lead with \
a 2-3 sentence "what matters today" summary, then a short section per instrument.

CRITICAL interpretation discipline (these instruments are noisy; do NOT cry \
regression):

OBSERVATION FLOOR -- measures the battle-observation sweep over active-7d players.
- Headline is coverage_ratio_vs_7d = distinct_productive / active_7d. Its \
realistic CEILING is the daily-active fraction active_1d/active_7d (~25-45%), \
because a player who did not battle in the window cannot produce an event. \
Report cov/7d both raw AND as a % of that ceiling.
- Decompose every coverage move: did distinct_productive change (real capture \
shift) or did active_7d change (denominator shift)? Say which.
- stale_over_24h is MOSTLY the change-gate "non-mover wall" -- by design, not a \
backlog. A large/steady value is expected. Only a rising stale WITH falling \
distinct_productive means cadence is slipping.
- NA productive_rate runs below EU/ASIA: known, not a regression.
- Day-to-day variance at fixed config is large. A single down day is noise, NOT \
a regression. Only flag a regression if sustained across multiple clean days AND \
distinct_productive is down while active_7d is flat. Otherwise say "within noise."

CRAWL YIELD -- measures the clan crawl's floor-impossible value: net-new \
discovery + dormant->active re-detection.
- yield_total = discovered_active + reactivated (floor-impossible; the point). \
overlap_total = refreshed_active (the floor already covers these).
- Verdict "saturated / trim cadence" requires BOTH low yield_frac AND low \
discovered_dormant. A low yield_frac with high discovered_dormant means the \
universe is still growing (seed corn for future reactivations): do NOT call that \
saturated. Per-pass counts vary; need >=2-3 same-realm passes before any verdict.
- Passes are per-realm and lagged (a pass runs many hours). Only compare a realm \
against itself.

RECAPTURE -- the cheap daily bulk account/info sweep of the dormant pool.
- advanced = returners found (last_battle_time moved past our stored value). \
into7d = returned inside active-7d (floor harvests them free next cycle). \
into7d_clanless = THE marginal value: returners nothing else recovers (the crawl \
only walks clan rosters). LEAD the recapture section with into7d_clanless.
- A healthy dormant pool is mostly still_dormant, so low single-digit % yield is \
EXPECTED and fine; judge absolute returner count, not the rate.
- mode=detect means writes are off (measuring, not recapturing) -- flag it. High \
errors/no_data = WG trouble. scanned << band = cursor exhausted the pool \
(maintenance steady state, fine).

Output STRICT JSON only, no prose outside it, no markdown fences: \
{"subject": "...", "html_body": "..."}. Subject <=78 chars, start it with \
"[battlestats] ". html_body is a complete <html>...</html> fragment using inline \
styles, readable on mobile, no external images."""


ALERT_SYSTEM_PROMPT = """You are the analyst writing a battlestats.online operations \
ALERT email. battlestats is a World of Warships player/clan stats platform.

A DETERMINISTIC Python check has ALREADY decided this alert is being sent and has \
ALREADY selected exactly which conditions tripped. You are not the gate and you are \
not being asked for a verdict. Your only job is to write up the tripped conditions \
clearly so the operator knows what to look at first.

RULES, in order of importance:
1. Write up ONLY the conditions listed under `tripped_conditions`. Do not comment on, \
summarize, grade, or reassure about anything else in the data, however interesting. \
No "everything else looks healthy" paragraph.
2. Never contradict, soften, re-litigate or second-guess a tripped condition. If a \
condition says a snapshot is stale or truncated, it is stale or truncated.
3. Do NOT compute your own deltas or ratios. Every number you need is in the payload. \
Quote observed values verbatim.
4. Lead with the single most actionable condition. Order: unreadable/missing snapshots, \
then stale snapshots, then shape problems (partial/status/failed_buckets/count \
mismatches), then numeric threshold breaches.
5. For each condition give: the condition code, what the instrument measures, the \
observed value vs the threshold, and the most likely place to look (named service, \
task or file). Be concrete and short.

DOMAIN NOTES you may use for the "where to look" line:
- observation-floor: the battle-observation sweep over active-7d players. Snapshot is \
written by a droplet cron at 04:30 UTC (snapshot_observation_floor.sh). The floor task \
itself runs on the `floor` queue / battlestats-celery-floor worker.
- crawl-yield: the multi-day clan crawl, `crawls` queue / battlestats-celery-crawls. A \
snapshot is emitted only when a full pass COMPLETES, so passes are days apart by design.
- recapture-lapsed: recapture_lapsed_players_task, daily per realm on the `background` \
queue. `partial: true` means the soft time limit truncated the pass; the writes that \
did happen are real and durable, but the pass covered only `scanned` of `candidates`, \
and it is numerically indistinguishable from a healthy pass that exhausted the pool. \
This is the failure that hid for two weeks before 2026-08-06.

Voice: Data from Star Trek -- analytical, precise, warm, no hype, no emdashes; use \
colons and semicolons. Short. This is an alert, not a digest.

Output STRICT JSON only, no prose outside it, no markdown fences: \
{"subject": "...", "html_body": "..."}. Subject <=78 chars, start it with \
"[battlestats] ops ALERT" and name the tripped condition(s). html_body is a complete \
<html>...</html> fragment using inline styles, readable on mobile, no external images."""


def _usage_line(payload: dict) -> str:
    """One journal line recording what an Anthropic call cost.

    The `usage` object on a real response is the only trustworthy record of
    billed tokens, so it is printed on every call. Reads the RESPONSE only: the
    request, and therefore the API key, cannot reach this line. A missing or
    null `usage` prints zeros, because the accounting line must never be the
    reason an email fails.
    """
    usage = payload.get("usage") or {}
    fields = ("input_tokens", "output_tokens",
              "cache_creation_input_tokens", "cache_read_input_tokens")
    counts = " ".join(f"{k}={usage.get(k) or 0}" for k in fields)
    return (f"[llm] model={payload.get('model')} "
            f"stop_reason={payload.get('stop_reason')} {counts}")


def call_anthropic(model: str, api_key: str, data_package: dict,
                   system: str | None = None, instruction: str | None = None) -> dict:
    body = {
        # max_tokens caps thinking AND response text together, which is what
        # produced the earlier empty-text/stop_reason=max_tokens failure. The fix
        # is headroom plus low effort, NOT thinking={"type": "disabled"}: with
        # thinking off, Opus 5 can leak <thinking> tags into the visible response,
        # and this response is parsed as JSON, so a leaked tag breaks the parse.
        "model": model,
        "max_tokens": 8000,
        "output_config": {"effort": "low"},
        "system": system or SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": (
                    (instruction or (
                        "Here is today's machine-selected snapshot data. Deltas are "
                        "pre-computed (delta_vs_d1 = latest minus the ~24h-prior "
                        "snapshot). Write the digest."
                    ))
                    + "\n\n"
                    + json.dumps(data_package, separators=(",", ":"), default=str)
                ),
            }
        ],
    }
    req = urllib.request.Request(
        ANTHROPIC_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    print(_usage_line(payload))
    # A safety-classifier decline is HTTP 200 with stop_reason=refusal and no
    # content, so it must be checked before reading content -- otherwise it
    # surfaces as an opaque JSON parse error instead of a named cause.
    if payload.get("stop_reason") == "refusal":
        raise RuntimeError(
            f"model declined the request (category="
            f"{(payload.get('stop_details') or {}).get('category')})"
        )
    text = "".join(
        blk.get("text", "") for blk in payload.get("content", []) if blk.get("type") == "text"
    ).strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"): text.rfind("}") + 1]
    parsed = json.loads(text)
    return {"subject": parsed["subject"], "html_body": parsed["html_body"]}


# --------------------------------------------------------------------------- #
# deterministic fallback rendering (no LLM)
# --------------------------------------------------------------------------- #
def render_plain(data: dict, conditions: list[dict] | None = None, reason: str = "") -> dict:
    """Deterministic table. Used for --no-llm, the LLM-failure fallback, and the
    weekly heartbeat.

    When conditions are present the tripped list is printed FIRST and the subject
    names them: on the fire path an LLM failure must not downgrade the mail to
    something whose subject reads "digest".
    """
    conditions = conditions or []
    head = "battlestats ops ALERT" if conditions else "battlestats ops digest"
    lines = [f"{head} (deterministic table -- no LLM synthesis)\n"]
    if reason:
        lines.append(f"({reason})\n")
    if conditions:
        lines.append("== TRIPPED CONDITIONS ==")
        for c in conditions:
            lines.append(f"  [{c['code']}] {c['detail']}")
        lines.append("")

    obs = data["observation"]
    lines.append("== Observation floor ==")
    if obs.get("available"):
        t = obs["latest"]["totals"]
        a7, dp = t.get("active_7d"), t.get("distinct_productive")
        a1 = t.get("active_1d")
        cov = t.get("coverage_ratio_vs_7d")
        ceil = (a1 / a7) if (a1 and a7) else None
        lines.append(f"  captured_at: {obs['latest']['captured_at']}")
        lines.append(
            f"  TOTAL active_7d={a7} distinct_productive={dp} "
            f"cov/7d={cov} ceiling(a1/a7)={round(ceil,4) if ceil else 'n/a'}"
        )
        for r in REALMS:
            rr = obs["latest"]["realms"][r]
            lines.append(
                f"    {r}: active_7d={rr.get('active_7d')} "
                f"productive={rr.get('distinct_productive')} cov/7d={rr.get('coverage_ratio_vs_7d')}"
            )
    else:
        lines.append("  (no snapshots)")

    cy = data["crawl_yield"]
    lines.append("\n== Crawl yield ==")
    if cy.get("available"):
        for r in REALMS:
            node = cy["realms"].get(r)
            if not node:
                lines.append(f"  {r}: (no pass)")
                continue
            l = node["latest"]
            lines.append(
                f"  {r}: {l['captured_at']} classified={l['players_classified']} "
                f"yield={l['yield_total']}({l['yield_frac']}) overlap={l['overlap_total']}({l['overlap_frac']}) "
                f"buckets={l['buckets']}"
            )
    else:
        lines.append("  (no snapshots)")

    rc = data["recapture"]
    lines.append("\n== Recapture ==")
    if rc.get("available"):
        for r in REALMS:
            node = rc["realms"].get(r)
            if not node:
                lines.append(f"  {r}: (no run)")
                continue
            lines.append(
                f"  {r}: {node['captured_at']} mode={node['mode']} scanned={node['scanned']} "
                f"advanced={node['advanced']}({node['yield_frac']}) into7d={node['into7d']} "
                f"into7d_clanless={node['into7d_clanless']} still_lapsed={node['still_lapsed']}"
            )
    else:
        lines.append("  (no snapshots)")

    text = "\n".join(lines)
    html = "<html><body><pre style='font:13px/1.4 monospace'>" + _esc(text) + "</pre></body></html>"
    # A no-condition table is only ever a heartbeat or a forced send; main()
    # stamps the heartbeat subject, so keep this one neutral.
    subject = alert_subject(conditions) if conditions else "[battlestats] ops digest"
    return {"subject": subject, "html_body": html, "text": text}


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# email: send_email() now comes from warships.opsmail (imported above).


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
DOW_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def heartbeat_due(now: datetime) -> bool:
    """True on the configured weekly heartbeat day.

    The heartbeat is what keeps SILENCE distinguishable from BREAKAGE. Exception-only
    mail means the timer dying, the SMTP path breaking, or the whole unit being
    disabled all look exactly like a healthy quiet day. A script cannot detect its
    own non-execution; a periodic unconditional send is the only mechanism that
    proves timer + SMTP + snapshot reads end to end. Set OPS_EMAIL_HEARTBEAT_DOW to
    an empty string to disable (not recommended).
    """
    want = cfg("OPS_EMAIL_HEARTBEAT_DOW", "mon").strip().lower()
    if not want:
        return False
    return DOW_NAMES[now.weekday()] == want


def main() -> int:
    args = set(sys.argv[1:])
    dry_run = "--dry-run" in args
    no_llm = "--no-llm" in args
    forced = "--force" in args

    load_env_file(cfg("OPS_EMAIL_ENV_FILE", DEFAULT_ENV_FILE))
    bench_dir = cfg("BENCH_DIR", DEFAULT_BENCH_DIR)

    now = utcnow()  # snapshots write naive UTC captured_at
    data = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "observation": gather_observation(bench_dir, now),
        "crawl_yield": gather_crawl_yield(bench_dir, now),
        "recapture": gather_recapture(bench_dir, now),
        "service_health": gather_service_health(bench_dir, now),
    }

    # ---- the verdict: deterministic Python, never the LLM ------------------
    conditions = evaluate(data)
    data["tripped_conditions"] = conditions

    always = cfg("OPS_EMAIL_ALWAYS_SEND", "0").strip() in ("1", "true", "yes", "on")
    beat = heartbeat_due(now)

    if not conditions and not always and not forced and not beat and not dry_run:
        # All clear: one line for the timer journal, no mail. This is the
        # exception-only contract. Errors still mail via the fail-loud path, and
        # the weekly heartbeat still proves the transport.
        print(f"[ok] all clear at {now.isoformat()}Z: no conditions tripped; no email sent "
              f"(obs={data['observation'].get('available', 0)} snaps, "
              f"crawl={data['crawl_yield'].get('available', 0)}, "
              f"recapture={data['recapture'].get('available', 0)}, "
              f"service={data['service_health'].get('available', 0)})")
        return 0

    alerting = bool(conditions)
    if alerting:
        reason = "alert"
    elif always or forced:
        reason = "forced send (OPS_EMAIL_ALWAYS_SEND / --force)"
    elif beat:
        reason = "weekly heartbeat: proves the timer, the SMTP path and the snapshot reads"
    else:
        # Only reachable under --dry-run, which deliberately skips the early return.
        reason = "all clear (dry run; nothing would have been sent)"

    # A heartbeat-only send is the transport proof and nothing else: no condition
    # tripped and nobody forced a digest. It mails the deterministic table, as the
    # module docstring and render_plain promise, so the proof does not depend on
    # the Anthropic API and does not pay for a narrative about an all-clear day.
    # This is the same predicate that stamps the heartbeat subject below.
    heartbeat_only = beat and not alerting and not (always or forced)

    email = None
    llm_error = None
    if not no_llm and not heartbeat_only:
        api_key = cfg("ANTHROPIC_API_KEY")
        model = cfg("ANTHROPIC_MODEL", "claude-opus-5")
        if not api_key:
            llm_error = "ANTHROPIC_API_KEY not set"
        else:
            try:
                if alerting:
                    out = call_anthropic(
                        model, api_key, data,
                        system=ALERT_SYSTEM_PROMPT,
                        instruction=(
                            "A deterministic Python check has already decided to send this "
                            "alert. `tripped_conditions` lists exactly what tripped. Write up "
                            "ONLY those conditions; ignore every other metric in the payload."
                        ),
                    )
                else:
                    out = call_anthropic(model, api_key, data)
                email = {"subject": out["subject"], "html_body": out["html_body"], "text": ""}
            except Exception as e:
                detail = e
                if isinstance(e, urllib.error.HTTPError):
                    try:
                        detail = f"{e} :: {e.read().decode('utf-8')[:500]}"
                    except Exception:
                        detail = str(e)
                llm_error = f"{type(e).__name__}: {detail}"

    if email is None:
        # Deterministic table: the heartbeat-only send by design, otherwise the
        # fallback (--no-llm, no key, or the API failed). On the
        # fire path render_plain names the conditions in the subject, so an LLM
        # outage cannot downgrade an alert into something reading "digest".
        email = render_plain(data, conditions, reason)
        if llm_error:
            note = ("<p style='color:#b00'>LLM synthesis failed, sent deterministic fallback: "
                    + _esc(llm_error) + "</p>")
            email["html_body"] = email["html_body"].replace("<body>", "<body>" + note)

    # Belt and braces: whatever the model returned, the subject must announce
    # what this mail actually is. An alert must never arrive reading "digest",
    # and the heartbeat must be filterable on a stable string so the operator
    # notices when the weekly proof-of-life stops arriving.
    if alerting:
        if not email["subject"].startswith("[battlestats] ops ALERT"):
            email["subject"] = alert_subject(conditions)
    elif heartbeat_only:
        email["subject"] = "[battlestats] ops heartbeat: all clear"

    if dry_run:
        print(f"VERDICT: {len(conditions)} condition(s) tripped; send={'yes' if (alerting or always or forced or beat) else 'no'} ({reason})")
        for c in conditions:
            print(f"  [{c['code']}] {c['detail']}")
        print("SUBJECT:", email["subject"])
        print("---- HTML ----")
        print(email["html_body"])
        if llm_error:
            print("---- LLM ERROR ----\n", llm_error)
        return 0

    send_email(email["subject"], email["html_body"], email.get("text", ""))
    print(f"[ok] sent ({reason}): {email['subject']}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        tb = traceback.format_exc()
        sys.stderr.write(tb + "\n")
        # fail loud: still try to send a failure email
        try:
            body = "<html><body><h2>battlestats daily ops email FAILED</h2><pre>" + \
                _esc(tb) + "</pre></body></html>"
            send_email("[battlestats] daily ops email FAILED", body, tb)
            print("[warn] sent failure notification email")
        except Exception:
            sys.stderr.write("could not send failure email:\n" + traceback.format_exc() + "\n")
        sys.exit(1)
