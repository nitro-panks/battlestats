#!/usr/bin/env python3
"""Weekly battlestats web-traffic email (the completed Mon-Sun UTC week).

Runs unattended on the production droplet from a systemd timer at 10:30 UTC on
Mondays, i.e. after the reported week has closed. An OS-level timer
deliberately, never Celery Beat: three tasks in this repo have already been
truncated by Beat soft-time-limits, and a report that silently half-runs is
worse than none. Reads the Umami analytics Postgres directly, computes every
number in Python/SQL, and emails a summary: weekly totals against the prior
week, a day-by-day breakdown of the week, new vs returning visitors, top pages
ranked by visitors, referrers, and the custom-event roster this app emits.

The reported week is derived as "the most recent Monday at or before now, minus
seven days", NOT as "now minus seven days". The difference matters because the
timer is Persistent=true: a catch-up run on Wednesday after a reboot must still
report the same completed Mon-Sun week the Monday run would have, rather than
silently sliding the window three days.

Sibling of `daily_ops_email.py` (the 11:30 UTC pipeline digest). Same contract:

  * stdlib only -- no venv, no pip installs. Postgres is reached by shelling out
    to `psql` (there is no stdlib PG driver); rows come back as one JSON line per
    query via `json_agg`.
  * config and secrets come from env files, NEVER from this script: it lives in
    a public repo.
  * fail-loud: any error still sends an email tagged FAILED carrying the
    traceback, then exits non-zero for the cron log.

Deliberate difference from the ops digest: the LLM here writes ONLY a short lead
paragraph. Every table, total and delta is rendered by Python from the same dict
the model is shown, so the model cannot compute -- or miscompute -- a number that
reaches the email.

METRIC VOCABULARY (Umami v2.20 semantics, verified against the live schema):
  * `session` rows are DURABLE. `session_id` is a stable hash of
    (website, hostname, ip, user-agent); the row is created on first sight and
    reused forever after. `session.created_at` is therefore FIRST-EVER-SEEN, not
    "a session that started today". One live row was observed spanning
    2026-07-30 -> 2026-08-09 with 24 visits.
  * Visitors      = COUNT(DISTINCT session_id) over the week's events. This is
    the reason the weekly headline is queried over the whole week rather than
    summed from the daily rows: a visitor active on three days is ONE weekly
    visitor and THREE daily ones. Only pageviews and events, being count(*), sum.
  * Visits        = COUNT(DISTINCT visit_id). Umami opens a new visit_id after
    30 minutes of inactivity. This is the "session" in ordinary analytics usage.
    It does not sum across days either, for the same reason plus the midnight
    straddle: one visit spanning midnight carries one visit_id in both days.
  * Pageviews     = event_type 1. Custom events = event_type 2, minus the
    page-load beacons in INSTRUMENTATION_EVENTS: everywhere this report counts
    custom events as visitor ACTIONS it counts interactions only. See that
    constant for why, and the Events triggered section for where the beacons
    are still printed.
  * New visitor   = a visitor whose session.created_at falls inside the week.
    Returning     = first seen before the week. Denominator is the week's
    active visitors, not visits. See NEW_VS_RETURNING_NOTE below for the caveat.
    New visitors is the one distinct count that DOES sum across the daily rows,
    because first-ever-seen lands on exactly one day; compute() checks that
    identity against the weekly query and says so if it ever breaks.

Operator IP exclusion is handled at Umami INGEST level (`IGNORE_IP` in
/opt/umami/.env). These queries inherit it for free and must not re-filter.

Flags:
  --dry-run          Render to stdout; do not send.
  --no-llm           Skip the Anthropic lead paragraph (also the automatic
                     fallback when the API errors or returns nothing).
  --week=YYYY-MM-DD  Report on the week containing this UTC date instead of the
                     most recently completed one. Any date in the week works;
                     it is normalised to that week's Monday.
"""

from __future__ import annotations

import json
import subprocess
import sys
import traceback
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote

# Shared send path. The sys.path insert keeps the no-venv guarantee: opsmail is
# stdlib-only by contract (enforced by test_opsmail.test_module_imports_no_django),
# so a bare python3 can import it straight from the server/ package directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from warships.opsmail import cfg, load_env_file, send_email  # noqa: E402

DEFAULT_ENV_FILE = "/etc/battlestats-ops-email.env"
DEFAULT_UMAMI_ENV_FILE = "/opt/umami/.env"
DEFAULT_SITE_DOMAIN = "battlestats.online"
DEFAULT_PSQL = "psql"

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

# A reported week is Monday 00:00 UTC through the following Monday 00:00 UTC.
WEEK_DAYS = 7

# Weekday labels for the day-by-day table. A literal tuple rather than
# strftime("%a") on purpose: strftime consults LC_TIME, and the label a systemd
# unit renders must not depend on the locale the droplet happens to carry.
WEEKDAY_LABELS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# Custom events that fire unconditionally on every page load rather than in
# response to something a visitor chose to do. `locale-active` is this entire
# class today: LocaleBeacon emits it once per page load, English included,
# because the locale runbook needs English as the denominator. Counted as an
# interaction it is a second pageview tally wearing an event's clothes, and it
# entered the roster on 2026-08-10, so it both wins any ranking by count and
# breaks its own prior-window comparison at its own ship date: the 2026-08-11
# report led on "locale-active at 75 against a prior daily mean of 2.86", which
# describes the deploy, not the period. Everything it measures is already reported,
# against its proper denominator, in the Language section. So it is excluded
# from the headline Custom events row, from the engagement second-event test,
# from the feature roster, and from the figures the model is shown; it is still
# printed once, under Events triggered, so the count is never simply lost.
INSTRUMENTATION_EVENTS = ("locale-active",)

# Derived, not written out, so adding a beacon to the tuple above really is the
# only edit required: a hardcoded name here would quietly go stale.
_BEACON_NAMES = ", ".join(INSTRUMENTATION_EVENTS)

_BEACON_EXCLUSION_NOTE = (
    f"Custom events counts interactions only. The page-load beacons ({_BEACON_NAMES}) fire on "
    "every load rather than on anything the visitor chose, so counting them here would restate "
    "pageviews and would measure a week against a prior week predating them. Their counts are "
    "under Events triggered; what they measure is under Language."
)

NEW_VS_RETURNING_NOTE = (
    "New = this visitor's first-ever appearance in Umami (session.created_at "
    "falls inside the week). Returning = first seen before the week. The "
    "denominator is the week's active visitors, not visits. This share is NOT "
    "comparable to the daily email's: widening the window raises it by "
    "construction, because a first-ever appearance is counted once while an "
    "active visitor is deduplicated across the days they returned on. Compare "
    "week to week, never against a remembered daily figure. Caveat: a visitor is "
    "keyed by a hash of IP + user-agent, so someone whose address rotates "
    "(mobile carriers do this constantly) reads as new. The bs-vid durable-id "
    "correction below is what catches that."
)


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
def read_umami_dsn(umami_env_file: str) -> str:
    """DATABASE_URL out of umami's own env file, and nothing else from it.

    A blanket load_env_file() here would drag APP_SECRET, IGNORE_IP and every
    other umami variable into os.environ; only the one key is wanted.

    No elevated privilege is needed: /opt/umami/.env is battlestats:battlestats
    mode 0640, so the same service user that runs the ops digest can read it.

    An explicit UMAMI_DATABASE_URL in the environment always wins, so a test, a
    staging run, or a future change of that file's ownership never has to touch
    /opt/umami.
    """
    override = cfg("UMAMI_DATABASE_URL")
    if override:
        return override
    try:
        content = Path(umami_env_file).read_text()
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise RuntimeError(
            f"cannot read DATABASE_URL from {umami_env_file} ({type(exc).__name__}); "
            "set UMAMI_DATABASE_URL instead"
        ) from exc
    for raw in content.splitlines():
        line = raw.strip()
        if line.startswith("DATABASE_URL=") and not line.startswith("#"):
            return line.partition("=")[2].strip().strip('"').strip("'")
    raise RuntimeError(f"no DATABASE_URL line in {umami_env_file}")


# --------------------------------------------------------------------------- #
# postgres access (stdlib only: shell out to psql, read back JSON)
# --------------------------------------------------------------------------- #
def _lit(value) -> str:
    """Single-quoted SQL literal. Inputs here are config/computed, never user input."""
    return "'" + str(value).replace("'", "''") + "'"


def run_queries(dsn: str, sqls: list[str], psql_bin: str = DEFAULT_PSQL) -> list[list[dict]]:
    """Run each SQL and return its rows. Every SQL yields exactly one JSON line.

    Each statement is wrapped in an aggregate so `psql -A -t` prints exactly one
    line per query regardless of row count, keeping the line/query
    correspondence exact. It must be `jsonb_agg`, not `json_agg`: `json_agg`
    pretty-prints its array with real newlines between elements, which shatters
    one result into many lines. jsonb's text output is compact and single-line,
    and any newline inside a string value is escaped as the two characters \\n.
    """
    args = [psql_bin, dsn, "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-P", "pager=off"]
    for sql in sqls:
        args += ["-c", f"SELECT coalesce(jsonb_agg(t), '[]'::jsonb)::text FROM ({sql}) t"]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(
            f"psql exited {proc.returncode}: {(proc.stderr or proc.stdout or '').strip()[:800]}"
        )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if len(lines) != len(sqls):
        raise RuntimeError(
            f"psql returned {len(lines)} result lines for {len(sqls)} queries; "
            f"stderr={(proc.stderr or '').strip()[:400]}"
        )
    return [json.loads(ln) for ln in lines]


# --------------------------------------------------------------------------- #
# SQL
# --------------------------------------------------------------------------- #
def resolve_website_sql(domain: str) -> str:
    return (
        "SELECT website_id::text, name FROM website "
        f"WHERE domain = {_lit(domain)} AND deleted_at IS NULL "
        "ORDER BY created_at LIMIT 1"
    )


def build_sqls(website_id: str, week_lo: datetime, week_hi: datetime, prior_lo: datetime) -> dict:
    """The report's queries, keyed by name; insertion order is the run order.

    `week_lo`..`week_hi` is the reported Mon-Sun week; `prior_lo`..`week_lo` is
    the week before it, which supplies every comparison figure the email prints.

    Every boundary is bound as an explicit `timestamptz` literal, and day
    bucketing converts BOTH sides of a comparison to naive UTC
    (`AT TIME ZONE 'UTC'`), so the server's TimeZone setting can never shift a
    day boundary. The live server is GMT today; this does not rely on that.
    """
    w = _lit(website_id)
    lo, hi, plo = _lit(week_lo.isoformat()), _lit(week_hi.isoformat()), _lit(prior_lo.isoformat())
    scope = f"we.website_id = {w}::uuid"
    in_week = f"we.created_at >= {lo}::timestamptz AND we.created_at < {hi}::timestamptz"
    # Applied wherever a custom event is COUNTED as a visitor action. coalesce
    # first: an unnamed event is not a beacon, and `NULL NOT IN (...)` is NULL,
    # which a FILTER reads as false and would silently drop it.
    beacons = ", ".join(_lit(name) for name in INSTRUMENTATION_EVENTS)
    interaction = f"coalesce(we.event_name, '') NOT IN ({beacons})"

    return {
        # --- the two whole-week windows, in one pass -------------------------
        # These headline figures CANNOT be summed from the per-day rows below.
        # `count(DISTINCT session_id)` over Mon-Sun is not the sum of seven
        # daily distinct counts: a visitor active on three days is one weekly
        # visitor and three daily ones, and visit_id has the same property plus
        # the midnight straddle. Pageviews and events are count(*) and would
        # sum, but are taken from here too so that every headline row comes
        # from one window and one query.
        # Aliased `bucket`, not `window`: WINDOW is a reserved word in Postgres.
        "totals": f"""
            SELECT CASE WHEN we.created_at >= {lo}::timestamptz THEN 'current'
                        ELSE 'prior' END AS bucket,
                   count(*) FILTER (WHERE we.event_type = 1) AS pageviews,
                   count(*) FILTER (WHERE we.event_type = 2 AND {interaction}) AS events,
                   count(DISTINCT we.session_id) AS visitors,
                   count(DISTINCT we.visit_id) AS visits
            FROM website_event we
            WHERE {scope}
              AND we.created_at >= {plo}::timestamptz
              AND we.created_at <  {hi}::timestamptz
            GROUP BY 1
        """,
        # --- per-day breakdown of the reported week --------------------------
        "trend": f"""
            WITH ev AS (
              SELECT date_trunc('day', we.created_at AT TIME ZONE 'UTC') AS day,
                     we.session_id, we.visit_id, we.event_type,
                     {interaction} AS is_interaction,
                     (s.created_at AT TIME ZONE 'UTC') AS sess_first
              FROM website_event we JOIN session s USING (session_id)
              WHERE {scope}
                AND we.created_at >= {lo}::timestamptz
                AND we.created_at <  {hi}::timestamptz
            )
            SELECT to_char(day, 'YYYY-MM-DD') AS day,
                   count(*) FILTER (WHERE event_type = 1) AS pageviews,
                   count(*) FILTER (WHERE event_type = 2 AND is_interaction) AS events,
                   count(DISTINCT session_id) AS visitors,
                   count(DISTINCT visit_id) AS visits,
                   count(DISTINCT session_id) FILTER (WHERE sess_first >= day) AS new_visitors
            FROM ev GROUP BY day ORDER BY day
        """,
        # --- depth / duration across the reported week -----------------------
        # `ev` counts interactions only. A beacon fires on every page load, so
        # counting it here would make "single-view visit (no second event)"
        # structurally impossible from 2026-08-10 onward: the measure would read
        # zero forever and look like an engagement win.
        "engagement": f"""
            WITH v AS (
              SELECT we.visit_id,
                     count(*) FILTER (WHERE we.event_type = 1) AS pv,
                     count(*) FILTER (WHERE we.event_type = 2 AND {interaction}) AS ev,
                     extract(epoch FROM max(we.created_at) - min(we.created_at)) AS dur
              FROM website_event we WHERE {scope} AND {in_week} GROUP BY 1
            )
            SELECT count(*) AS visits,
                   round(avg(pv)::numeric, 2) AS avg_pageviews_per_visit,
                   round(avg(dur)::numeric) AS avg_visit_seconds,
                   count(*) FILTER (WHERE pv <= 1 AND ev = 0) AS single_view_visits
            FROM v
        """,
        # --- new vs returning + durable-id (bs-vid) corroboration ------------
        "identity": f"""
            WITH week_sessions AS (
              SELECT DISTINCT we.session_id, s.created_at AS sess_first, s.distinct_id
              FROM website_event we JOIN session s USING (session_id)
              WHERE {scope} AND {in_week}
            )
            SELECT count(*) AS visitors,
                   count(*) FILTER (WHERE sess_first >= {lo}::timestamptz) AS new_visitors,
                   count(*) FILTER (WHERE sess_first <  {lo}::timestamptz) AS returning_visitors,
                   count(distinct_id) AS identified_visitors,
                   count(*) FILTER (
                     WHERE sess_first >= {lo}::timestamptz AND distinct_id IS NOT NULL
                       AND EXISTS (
                         SELECT 1 FROM session older
                         WHERE older.website_id = {w}::uuid
                           AND older.distinct_id = week_sessions.distinct_id
                           AND older.session_id <> week_sessions.session_id
                           AND older.created_at < {lo}::timestamptz)
                   ) AS new_but_known_bs_vid
            FROM week_sessions
        """,
        # --- top pages, RANKED BY VISITORS (never by raw view count) ---------
        "pages": f"""
            SELECT we.url_path,
                   count(DISTINCT we.session_id) AS visitors,
                   count(DISTINCT we.visit_id) AS visits,
                   count(*) AS pageviews
            FROM website_event we
            WHERE {scope} AND {in_week} AND we.event_type = 1
            GROUP BY 1 ORDER BY visitors DESC, pageviews DESC LIMIT 10
        """,
        # --- route families (player pages are high-cardinality by design) ----
        "routes": f"""
            SELECT CASE
                     WHEN we.url_path = '/' THEN '/ (landing)'
                     WHEN we.url_path LIKE '/player/%' THEN '/player/*'
                     WHEN we.url_path LIKE '/clan/%'   THEN '/clan/*'
                     WHEN we.url_path LIKE '/ship/%'   THEN '/ship/*'
                     ELSE we.url_path
                   END AS route,
                   count(DISTINCT we.session_id) AS visitors,
                   count(DISTINCT we.visit_id) AS visits,
                   count(*) AS pageviews
            FROM website_event we
            WHERE {scope} AND {in_week} AND we.event_type = 1
            GROUP BY 1 ORDER BY visitors DESC, pageviews DESC LIMIT 8
        """,
        # --- acquisition. Own-domain referrers are internal nav, not sources.
        "referrers": f"""
            SELECT coalesce(nullif(we.referrer_domain, ''), '(direct / none)') AS source,
                   count(DISTINCT we.session_id) AS visitors,
                   count(DISTINCT we.visit_id) AS visits
            FROM website_event we
            WHERE {scope} AND {in_week} AND we.event_type = 1
              AND coalesce(we.referrer_domain, '') <> coalesce(we.hostname, '')
            GROUP BY 1 ORDER BY visitors DESC LIMIT 8
        """,
        "countries": f"""
            SELECT coalesce(s.country, '??') AS country,
                   count(DISTINCT we.session_id) AS visitors
            FROM website_event we JOIN session s USING (session_id)
            WHERE {scope} AND {in_week}
            GROUP BY 1 ORDER BY visitors DESC LIMIT 8
        """,
        "devices": f"""
            SELECT coalesce(s.device, 'unknown') AS device,
                   count(DISTINCT we.session_id) AS visitors
            FROM website_event we JOIN session s USING (session_id)
            WHERE {scope} AND {in_week}
            GROUP BY 1 ORDER BY visitors DESC
        """,
        # --- UI locale actually in effect, from the locale-active beacon ------
        # Supply side. Only page loads on a build carrying the beacon report at
        # all (shipped v5.2.1, 2026-08-10), so this partitions beacon-reporting
        # visitors, NOT the week's visitors. No LIMIT: three values exist, and a
        # truncated set would give the share below a wrong denominator.
        "locale_active": f"""
            SELECT ed.string_value AS locale,
                   count(DISTINCT we.session_id) AS visitors,
                   count(DISTINCT we.visit_id) AS load_visits
            FROM website_event we
            JOIN event_data ed
              ON ed.website_event_id = we.event_id AND ed.data_key = 'locale'
            WHERE {scope} AND {in_week}
              AND we.event_type = 2 AND we.event_name = 'locale-active'
            GROUP BY 1 ORDER BY visitors DESC, locale
        """,
        # --- browser language: the demand side, captured since long before the
        # locale feature existed. One language per session, so these rows do
        # partition the week's visitors. Folded to the primary subtag (ko-KR and
        # ko are one language). No LIMIT, same denominator reason as above.
        "browser_language": f"""
            SELECT lower(split_part(coalesce(nullif(s.language, ''), '??'), '-', 1)) AS lang,
                   count(DISTINCT we.session_id) AS visitors
            FROM website_event we JOIN session s USING (session_id)
            WHERE {scope} AND {in_week}
            GROUP BY 1 ORDER BY visitors DESC, lang
        """,
        # --- custom events. Ranked by visitors; prior-week count for context.
        # Deliberately unfiltered: the beacon rows are wanted, just not mixed in
        # with the interactions. compute() splits the roster in two.
        "events": f"""
            WITH week_ev AS (
              SELECT we.event_name,
                     count(*) AS events,
                     count(DISTINCT we.session_id) AS visitors,
                     count(DISTINCT we.visit_id) AS visits
              FROM website_event we
              WHERE {scope} AND {in_week} AND we.event_type = 2 AND we.event_name IS NOT NULL
              GROUP BY 1
            ),
            prior AS (
              SELECT we.event_name, count(*) AS prior_week_events
              FROM website_event we
              WHERE {scope} AND we.event_type = 2 AND we.event_name IS NOT NULL
                AND we.created_at >= {plo}::timestamptz
                AND we.created_at <  {lo}::timestamptz
              GROUP BY 1
            )
            SELECT d.event_name, d.events, d.visitors, d.visits,
                   coalesce(p.prior_week_events, 0) AS prior_week_events
            FROM week_ev d LEFT JOIN prior p USING (event_name)
            ORDER BY d.visitors DESC, d.events DESC
        """,
    }


# --------------------------------------------------------------------------- #
# derived arithmetic (all deltas computed here; never by the model)
# --------------------------------------------------------------------------- #
def _num(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            return float(value) if "." in value else int(value)
        except ValueError:
            return None
    return value


def _pct(part, whole) -> float | None:
    part, whole = _num(part), _num(whole)
    if not whole or part is None:
        return None
    return round(100.0 * part / whole, 1)


def _delta(cur, prev) -> dict:
    cur, prev = _num(cur), _num(prev)
    if cur is None or prev is None:
        return {"abs": None, "pct": None}
    diff = round(cur - prev, 2)
    return {"abs": diff, "pct": round(100.0 * diff / prev, 1) if prev else None}


HEADLINE_METRICS = ("visitors", "visits", "pageviews", "events")

# The headline metrics a week's daily rows genuinely add up to. count(*) sums;
# count(DISTINCT ...) does not, because a visitor active on three days is one
# weekly visitor and three daily ones. Named here rather than written out
# because the daily table's legend is derived from it.
SUMMABLE_METRICS = ("pageviews", "events")


def _day_label(row: dict) -> str:
    return f"{row.get('weekday', '')} {row.get('day', '')}".strip()


def _week_days(trend: list[dict], week: date) -> list[dict]:
    """The week's seven rows in calendar order, zero-filled.

    A day with no traffic at all returns no row from the trend query at all.
    Dropping it would silently shorten the table and hide the one thing most
    worth seeing: a dead Sunday must render as a zero, not as an absence.
    """
    by_day = {r.get("day"): r for r in trend}
    rows = []
    for offset in range(WEEK_DAYS):
        d = week + timedelta(days=offset)
        row = by_day.get(d.isoformat()) or dict(
            {m: 0 for m in HEADLINE_METRICS}, day=d.isoformat(), new_visitors=0
        )
        rows.append(dict(row, weekday=WEEKDAY_LABELS[d.weekday()]))
    return rows


def compute(raw: dict, week: date) -> dict:
    """Turn the raw query rows into every number the email prints.

    `week` is the Monday that opens the reported week.
    """
    week_end = week + timedelta(days=WEEK_DAYS - 1)

    # Headline figures come from the whole-week query, NEVER from summing the
    # daily rows: see the `totals` SQL for why that sum would be wrong for
    # visitors and visits.
    buckets = {r.get("bucket"): r for r in (raw.get("totals") or [])}
    current, previous = buckets.get("current") or {}, buckets.get("prior") or {}

    headline = {}
    for metric in HEADLINE_METRICS:
        value = _num(current.get(metric)) or 0
        prior_week = _num(previous.get(metric))
        headline[metric] = {
            "value": value,
            "prior_week": prior_week,
            "vs_prior_week": _delta(value, prior_week),
            "daily_mean": round(value / WEEK_DAYS, 1),
        }

    days = _week_days(raw.get("trend") or [], week)
    ranked_days = sorted(days, key=lambda r: (-(_num(r.get("visitors")) or 0), r["day"]))
    daily = {
        "rows": days,
        "sums": {m: sum((_num(r.get(m)) or 0) for r in days) for m in SUMMABLE_METRICS},
        "new_visitors_sum": sum((_num(r.get("new_visitors")) or 0) for r in days),
        "busiest": _day_label(ranked_days[0]),
        "quietest": _day_label(ranked_days[-1]),
    }

    ident = (raw.get("identity") or [{}])[0]
    visitors = _num(ident.get("visitors")) or 0
    identified = _num(ident.get("identified_visitors")) or 0
    identity = {
        "visitors": visitors,
        "new": _num(ident.get("new_visitors")) or 0,
        "returning": _num(ident.get("returning_visitors")) or 0,
        "new_pct": _pct(ident.get("new_visitors"), visitors),
        "returning_pct": _pct(ident.get("returning_visitors"), visitors),
        "identified_visitors": identified,
        "identified_pct": _pct(identified, visitors),
        "new_but_known_bs_vid": _num(ident.get("new_but_known_bs_vid")) or 0,
    }

    eng = (raw.get("engagement") or [{}])[0]
    engagement = {
        "visits": _num(eng.get("visits")) or 0,
        "avg_pageviews_per_visit": _num(eng.get("avg_pageviews_per_visit")),
        "avg_visit_seconds": _num(eng.get("avg_visit_seconds")),
        "single_view_visits": _num(eng.get("single_view_visits")) or 0,
        "single_view_pct": _pct(eng.get("single_view_visits"), eng.get("visits")),
    }

    # Three columns must add up, and every one of them is computed twice by two
    # independent queries over two differently-shaped windows: pageviews and
    # events because count(*) sums, and new visitors because a first-ever
    # appearance falls on exactly one day, and necessarily a day that visitor
    # was active on. Visitors and visits are absent by design; they genuinely do
    # not sum, which is the whole reason the headline is queried whole-week.
    #
    # A disagreement means a window boundary is wrong somewhere. Recorded, not
    # asserted: a self-check that killed the report would be worse than the
    # discrepancy it caught, and the email prints the mismatch instead.
    daily["discrepancies"] = [
        {"metric": metric, "daily_sum": summed, "week_query": queried}
        for metric, summed, queried in (
            ("New visitors", daily["new_visitors_sum"], identity["new"]),
            ("Pageviews", daily["sums"]["pageviews"], headline["pageviews"]["value"]),
            ("Custom events", daily["sums"]["events"], headline["events"]["value"]),
        )
        if summed != queried
    ]

    events = raw.get("events") or []
    for row in events:
        row["vs_prior_week"] = _delta(row.get("events"), row.get("prior_week_events"))
    # Split, never blend: `rows` is what visitors chose to do and is what every
    # total, ranking and model-visible figure below is drawn from; `beacon_rows`
    # is instrumentation, kept so the number stays visible but held out of the
    # ranking it would otherwise head. See INSTRUMENTATION_EVENTS.
    interactions = [r for r in events if (r.get("event_name") or "") not in INSTRUMENTATION_EVENTS]
    beacon_rows = [r for r in events if (r.get("event_name") or "") in INSTRUMENTATION_EVENTS]
    event_summary = {
        "total_events": sum((_num(r.get("events")) or 0) for r in interactions),
        "distinct_event_names": len(interactions),
        "rows": interactions,
        "families": _event_families(interactions),
        "beacon_rows": beacon_rows,
        "beacon_events": sum((_num(r.get("events")) or 0) for r in beacon_rows),
    }

    pages = [dict(r, label=_pretty_path(r["url_path"])) for r in (raw.get("pages") or [])]
    locale = _locale_summary(raw)

    return {
        "week_start": week.isoformat(),
        "week_end": week_end.isoformat(),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "headline": headline,
        "identity": identity,
        "engagement": engagement,
        "daily": daily,
        "pages": pages,
        "routes": raw.get("routes") or [],
        "referrers": raw.get("referrers") or [],
        "countries": raw.get("countries") or [],
        "devices": raw.get("devices") or [],
        "events": event_summary,
        "locale": locale,
    }


# The two halves answer different questions and must never share a denominator:
# `ui` is which language the interface actually ran in (supply, beacon-reporting
# visitors only), `browser` is which language the visitor's browser asks for
# (demand, every visitor). The gap between them is the discoverability signal
# the locale runbook is about, so both shares are computed HERE rather than left
# for a reader — or for the model — to divide.
LOCALE_TOP_N = 6


def _locale_summary(raw: dict) -> dict:
    ui_rows = raw.get("locale_active") or []
    ui_total = sum((_num(r.get("visitors")) or 0) for r in ui_rows)
    ui_non_en = sum(
        (_num(r.get("visitors")) or 0) for r in ui_rows if (r.get("locale") or "") != "en"
    )

    browser_rows = raw.get("browser_language") or []
    browser_total = sum((_num(r.get("visitors")) or 0) for r in browser_rows)
    browser_non_en = sum(
        (_num(r.get("visitors")) or 0)
        for r in browser_rows
        if (r.get("lang") or "") not in ("en", "??")
    )
    # Korean and Japanese specifically: the only two the UI can currently serve,
    # so this is the reachable ceiling for the share above it.
    browser_cjk = sum(
        (_num(r.get("visitors")) or 0) for r in browser_rows if (r.get("lang") or "") in ("ko", "ja")
    )

    return {
        "ui_rows": ui_rows,
        "ui_visitors": ui_total,
        "ui_non_english": ui_non_en,
        "ui_non_english_pct": _pct(ui_non_en, ui_total),
        "browser_rows": browser_rows[:LOCALE_TOP_N],
        "browser_visitors": browser_total,
        "browser_non_english": browser_non_en,
        "browser_non_english_pct": _pct(browser_non_en, browser_total),
        "browser_ko_ja": browser_cjk,
        "browser_ko_ja_pct": _pct(browser_cjk, browser_total),
        # What fraction of the week's visitors the beacon saw at all. Below 100
        # means the UI share above is drawn from a subset of the week, for any
        # of three reasons: a cached bundle predating the beacon, a visitor who
        # never triggered a full page load, or a week that straddles the
        # beacon's own ship date, where it existed for only part of the window
        # while the browser-language half covered all of it. Without this the
        # two shares look like one population measured two ways.
        "ui_coverage_pct": _pct(ui_total, browser_total),
    }


def _pretty_path(path: str) -> str:
    """Percent-decode for display; player and clan names are URL-encoded on the way in."""
    try:
        return unquote(path)
    except Exception:
        return path


def _event_family(name: str) -> str:
    """Group kebab-case event names by their first two segments when there are
    three or more (`ship-leaderboard-filter` -> `ship-leaderboard`), otherwise by
    the whole name (`search`, `realm-change`)."""
    parts = name.split("-")
    return "-".join(parts[:2]) if len(parts) >= 3 else name


def _event_families(rows: list[dict]) -> list[dict]:
    agg: dict[str, dict] = {}
    for row in rows:
        fam = _event_family(row.get("event_name") or "(unnamed)")
        node = agg.setdefault(fam, {"family": fam, "events": 0, "names": 0})
        node["events"] += _num(row.get("events")) or 0
        node["names"] += 1
    return sorted(agg.values(), key=lambda n: (-n["events"], n["family"]))


# --------------------------------------------------------------------------- #
# rendering (deterministic; the model contributes only `lead`)
# --------------------------------------------------------------------------- #
def _esc(text) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fmt_delta(delta: dict) -> str:
    abs_, pct = delta.get("abs"), delta.get("pct")
    if abs_ is None:
        return "n/a"
    sign = "+" if abs_ > 0 else ""
    body = f"{sign}{abs_:g}"
    return f"{body} ({sign}{pct:g}%)" if pct is not None else body


def _duration(seconds) -> str:
    if seconds is None:
        return "n/a"
    seconds = int(seconds)
    return f"{seconds // 60}m {seconds % 60:02d}s"


TD = "padding:4px 10px 4px 0;border-bottom:1px solid #eee;"
TH = "padding:4px 10px 4px 0;border-bottom:2px solid #ccc;text-align:left;font-weight:600;"


def _table(headers: list[str], rows: list[list], note: str = "") -> str:
    head = "".join(f"<th style='{TH}'>{_esc(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td style='{TD}'>{_esc(c)}</td>" for c in row) + "</tr>" for row in rows
    )
    tail = (
        f"<div style='font-size:11px;color:#777;margin:2px 0 0'>{_esc(note)}</div>" if note else ""
    )
    return (
        "<table style='border-collapse:collapse;font-size:13px;width:100%;margin:4px 0 14px'>"
        f"<tr>{head}</tr>{body}</table>{tail}"
    )


# Below this, the UI share is drawn from too small a slice of the week to be read
# beside the browser figure without saying so. 90 rather than 100 because a few
# visitors on stale bundles are normal and unremarkable.
UI_COVERAGE_CAVEAT_PCT = 90


def _ui_coverage_caveat(loc: dict) -> str:
    pct = loc.get("ui_coverage_pct")
    if pct is None or pct >= UI_COVERAGE_CAVEAT_PCT:
        return ""
    # Zero coverage is the unmeasured case, which both renderers already state
    # plainly. Adding "the UI figure is a subset" on top of it would qualify a
    # figure that was never printed.
    if not loc.get("ui_visitors"):
        return ""
    return (
        f" Note that the beacon reported for {loc['ui_visitors']} of the week's "
        f"{loc['browser_visitors']} visitors ({pct}%), so the interface figure is drawn from a "
        "subset of this week and is not comparable to the browser figure at face value."
    )


def _beacon_summary(ev: dict) -> str:
    """One flat sentence naming the beacons held out of everything above.

    Deliberately a sentence and not a table: a table would rank it, and the whole
    point is that a per-page-load beacon has no business at the head of a
    ranking. No delta is printed either; a beacon's week-over-week movement is
    pageview movement, which the Totals section already reports.
    """
    rows = ev.get("beacon_rows") or []
    if not rows:
        return ""
    named = "; ".join(
        f"{r.get('event_name')} {r.get('events')} events from {r.get('visitors')} visitors"
        for r in rows
    )
    return (
        f"Instrumentation, excluded from every figure above: {named}. These fire on page load "
        "rather than on a visitor action, so they measure delivery, not interest; what the "
        "locale beacon reports is in the Language section."
    )


def _beacon_line_html(ev: dict) -> str:
    text = _beacon_summary(ev)
    if not text:
        return ""
    return f"<div style='font-size:12px;color:#777;margin:-8px 0 14px'>{_esc(text)}</div>"


def _h2(text: str) -> str:
    return f"<h2 style='font:600 15px/1.3 system-ui,sans-serif;margin:20px 0 4px'>{_esc(text)}</h2>"


def _week_label(data: dict) -> str:
    return f"{data['week_start']} to {data['week_end']}"


def render(data: dict, lead: str = "", lead_error: str = "") -> dict:
    h, ident, eng = data["headline"], data["identity"], data["engagement"]
    span = _week_label(data)

    subject = (
        f"[battlestats] traffic week of {data['week_start']}: "
        f"{h['visitors']['value']} visitors, {h['visits']['value']} visits, "
        f"{h['pageviews']['value']} views"
    )

    parts = [
        "<html><body style='font:14px/1.5 system-ui,-apple-system,sans-serif;"
        "color:#222;max-width:760px;margin:0 auto;padding:12px'>",
        "<h1 style='font:600 19px/1.3 system-ui,sans-serif;margin:0 0 2px'>"
        f"battlestats.online traffic: week of {_esc(span)} (UTC)</h1>",
        f"<div style='font-size:11px;color:#777'>generated {_esc(data['generated_at_utc'])}; "
        "a whole Monday-to-Sunday UTC week, compared against the week before it</div>",
    ]

    if lead:
        parts.append(
            "<p style='background:#f6f8fa;border-left:3px solid #567;padding:8px 12px;"
            f"margin:14px 0'>{_esc(lead)}</p>"
        )
    if lead_error:
        parts.append(
            "<p style='font-size:11px;color:#b00;margin:8px 0'>Lead-paragraph synthesis "
            f"skipped: {_esc(lead_error)}. Every number below is computed in Python and is "
            "unaffected.</p>"
        )

    parts.append(_h2("Totals"))
    parts.append(
        _table(
            ["Metric", "This week", "Prior week", "Change", "Daily mean"],
            [
                [
                    label,
                    h[key]["value"],
                    h[key]["prior_week"] if h[key]["prior_week"] is not None else "n/a",
                    _fmt_delta(h[key]["vs_prior_week"]),
                    h[key]["daily_mean"],
                ]
                for key, label in (
                    ("visitors", "Visitors (distinct devices)"),
                    ("visits", "Visits / sessions"),
                    ("pageviews", "Pageviews"),
                    ("events", "Custom events (interactions)"),
                )
            ],
            "Visitors = distinct Umami session_id, a hash of IP + user-agent. Visits = "
            "distinct visit_id; Umami opens a new visit after 30 minutes idle. Both are "
            "counted over the whole week and are deliberately NOT the sum of the day-by-day "
            "rows below: a visitor active on three days is one weekly visitor and three daily "
            "ones, and a visit straddling midnight belongs to both days. Pageviews and events "
            "do sum. " + _BEACON_EXCLUSION_NOTE,
        )
    )

    parts.append(_h2("Engagement"))
    parts.append(
        _table(
            ["Measure", "Value"],
            [
                ["Pageviews per visit", eng["avg_pageviews_per_visit"] or "n/a"],
                ["Average visit duration", _duration(eng["avg_visit_seconds"])],
                [
                    "Single-view visits (no second event)",
                    f"{eng['single_view_visits']} of {eng['visits']}"
                    + (
                        f" ({eng['single_view_pct']}%)"
                        if eng["single_view_pct"] is not None
                        else ""
                    ),
                ],
            ],
            "Duration is last event minus first event within a visit, so a single-event "
            "visit measures zero and pulls the mean down.",
        )
    )

    parts.append(_h2("New vs returning visitors"))
    parts.append(
        _table(
            ["Group", "Visitors", "Share"],
            [
                [
                    "New (first ever seen)",
                    ident["new"],
                    f"{ident['new_pct']}%" if ident["new_pct"] is not None else "n/a",
                ],
                [
                    "Returning (seen before this week)",
                    ident["returning"],
                    f"{ident['returning_pct']}%" if ident["returning_pct"] is not None else "n/a",
                ],
                ["Total active visitors", ident["visitors"], "100%"],
            ],
            NEW_VS_RETURNING_NOTE,
        )
    )
    parts.append(
        "<div style='font-size:12px;color:#555;margin:-8px 0 14px'>"
        f"<b>bs-vid durable-id check:</b> {ident['identified_visitors']} of {ident['visitors']} "
        "visitors carried a durable id"
        + (f" ({ident['identified_pct']}%)" if ident["identified_pct"] is not None else "")
        + f"; {ident['new_but_known_bs_vid']} of the \"new\" visitors were in fact known "
        "browsers arriving on a rotated address. That correction is not folded into the "
        "table above; it is reported alongside it so the primary count stays comparable "
        "week to week.</div>"
    )

    daily = data["daily"]
    parts.append(_h2("Day by day"))
    parts.append(
        _table(
            ["Day", "Visitors", "New", "Visits", "Pageviews", "Events"],
            [
                [
                    _day_label(r),
                    r["visitors"],
                    r["new_visitors"],
                    r["visits"],
                    r["pageviews"],
                    r["events"],
                ]
                for r in daily["rows"]
            ]
            + [
                [
                    "Week",
                    h["visitors"]["value"],
                    ident["new"],
                    h["visits"]["value"],
                    h["pageviews"]["value"],
                    h["events"]["value"],
                ]
            ],
            "The Week row is the whole-week query, not the column above it. New, Pageviews and "
            "Events do add up and are checked against the column sums; Visitors and Visits do "
            "not and will read lower than their columns, because a visitor active on several "
            "days is counted once for the week. Events counts interactions only, on every day, "
            "so the column stays comparable across the arrival of a page-load beacon.",
        )
    )
    for bad in daily["discrepancies"]:
        parts.append(
            "<div style='font-size:12px;color:#b00;margin:-8px 0 14px'><b>Self-check "
            f"failed:</b> {_esc(bad['metric'])} sums to {bad['daily_sum']} across the days "
            f"above but the whole-week query counts {bad['week_query']}. These come from two "
            "independent queries and must agree; a difference means a window boundary is "
            "wrong. Every other figure in this email is still as queried.</div>"
        )

    parts.append(_h2("Top pages"))
    parts.append(
        _table(
            ["Page", "Visitors (sort)", "Visits", "Views"],
            [[r["label"], r["visitors"], r["visits"], r["pageviews"]] for r in data["pages"]]
            or [["(no pageviews)", "", "", ""]],
            "Ranked by distinct visitors, not by view count: one visitor reloading a page "
            "cannot promote it.",
        )
    )
    parts.append(
        _table(
            ["Route family", "Visitors (sort)", "Visits", "Views"],
            [[r["route"], r["visitors"], r["visits"], r["pageviews"]] for r in data["routes"]]
            or [["(none)", "", "", ""]],
        )
    )

    parts.append(_h2("Acquisition"))
    parts.append(
        _table(
            ["Referrer", "Visitors (sort)", "Visits"],
            [[r["source"], r["visitors"], r["visits"]] for r in data["referrers"]]
            or [["(none)", "", ""]],
            "Own-domain referrers are internal navigation and are excluded.",
        )
    )
    parts.append(
        _table(
            ["Country", "Visitors (sort)"],
            [[r["country"], r["visitors"]] for r in data["countries"]] or [["(none)", ""]],
        )
    )
    parts.append(
        _table(
            ["Device", "Visitors (sort)"],
            [[r["device"], r["visitors"]] for r in data["devices"]] or [["(none)", ""]],
        )
    )

    loc = data["locale"]
    parts.append(_h2("Language"))
    parts.append(
        _table(
            ["UI locale in effect", "Visitors (sort)", "Page loads"],
            [[r["locale"], r["visitors"], r["load_visits"]] for r in loc["ui_rows"]]
            or [["(no locale-active events)", "", ""]],
            "From the locale-active beacon, which reports the locale a page load actually ran "
            "in. Its denominator is beacon-reporting visitors, not the week's visitors: a visitor "
            "on a cached pre-v5.2.1 bundle reports nothing.",
        )
    )
    parts.append(
        _table(
            ["Browser language", "Visitors (sort)"],
            [[r["lang"], r["visitors"]] for r in loc["browser_rows"]] or [["(none)", ""]],
            "What the visitor's browser asks for, folded to the primary subtag and captured "
            "since long before the locale feature. One language per visitor, so these do "
            "partition the week.",
        )
    )
    parts.append(
        "<div style='font-size:12px;color:#555;margin:-8px 0 14px'><b>Supply vs demand:</b> "
        + (
            f"the interface ran non-English for {loc['ui_non_english']} of "
            f"{loc['ui_visitors']} beacon-reporting visitors "
            f"({loc['ui_non_english_pct']}%)"
            if loc["ui_non_english_pct"] is not None
            else "no visitor reported a UI locale this week, so the interface side is "
            "unmeasured rather than zero (the beacon shipped 2026-08-10)"
        )
        + f"; {loc['browser_ko_ja']} of {loc['browser_visitors']} visitors arrived with a Korean "
        "or Japanese browser"
        + (f" ({loc['browser_ko_ja_pct']}%)" if loc["browser_ko_ja_pct"] is not None else "")
        + ", which is the reachable ceiling while ko and ja are the only translations. English "
        "remains the default for every new arrival; the gap between the two figures is a "
        "discoverability measure, not a demand measure."
        + _ui_coverage_caveat(loc)
        + "</div>"
    )

    ev = data["events"]
    parts.append(_h2("Events triggered"))
    parts.append(
        f"<div style='font-size:13px;margin:0 0 8px'>{ev['total_events']} interaction events "
        f"across {ev['distinct_event_names']} distinct event names.</div>"
    )
    parts.append(
        _table(
            ["Feature area", "Events", "Event names"],
            [[r["family"], r["events"], r["names"]] for r in ev["families"]]
            or [["(none)", "", ""]],
        )
    )
    parts.append(
        _table(
            ["Event", "Visitors (sort)", "Visits", "Events", "Prior week", "Change"],
            [
                [
                    r["event_name"],
                    r["visitors"],
                    r["visits"],
                    r["events"],
                    r["prior_week_events"],
                    _fmt_delta(r["vs_prior_week"]),
                ]
                for r in ev["rows"]
            ]
            or [["(no custom events)", "", "", "", "", ""]],
            "Ranked by distinct visitors. \"Prior week\" is that event's total count over the "
            "week before this one; an event first emitted since then shows 0.",
        )
    )
    parts.append(_beacon_line_html(ev))

    parts.append(
        "<hr style='border:0;border-top:1px solid #ddd;margin:22px 0 8px'>"
        "<div style='font-size:11px;color:#777'>Source: the Umami analytics Postgres, read "
        "directly. The operator's home and work IPs are excluded at Umami ingest level, so "
        "they never enter these counts. Every figure is computed in SQL and Python; the "
        "model wrote only the lead paragraph.</div></body></html>"
    )

    return {"subject": subject, "html_body": "".join(parts), "text": render_text(data, lead)}


def render_text(data: dict, lead: str = "") -> str:
    h, ident, eng = data["headline"], data["identity"], data["engagement"]
    out = [f"battlestats.online traffic: week of {_week_label(data)} (UTC)", ""]
    if lead:
        out += [lead, ""]
    out.append("TOTALS")
    for key, label in (
        ("visitors", "Visitors"),
        ("visits", "Visits/sessions"),
        ("pageviews", "Pageviews"),
        ("events", "Events (interactions)"),
    ):
        node = h[key]
        out.append(
            f"  {label:<21} {node['value']:>6}   vs prior week "
            f"{_fmt_delta(node['vs_prior_week'])}   daily mean {node['daily_mean']}"
        )
    out += ["", "DAY BY DAY (visitors / new / visits / pageviews / events)"]
    out += [
        f"  {_day_label(r):<14} {r['visitors']:>4} {r['new_visitors']:>4} {r['visits']:>4} "
        f"{r['pageviews']:>5} {r['events']:>5}"
        for r in data["daily"]["rows"]
    ]
    out += [
        f"  SELF-CHECK FAILED: {bad['metric']} sums to {bad['daily_sum']} across the days, "
        f"the whole-week query says {bad['week_query']}"
        for bad in data["daily"]["discrepancies"]
    ]
    out += [
        "",
        "NEW VS RETURNING (denominator: the week's active visitors)",
        f"  new {ident['new']} ({ident['new_pct']}%); returning {ident['returning']} "
        f"({ident['returning_pct']}%); total {ident['visitors']}",
        f"  bs-vid coverage {ident['identified_visitors']}/{ident['visitors']}; "
        f"{ident['new_but_known_bs_vid']} \"new\" visitors were known browsers on a rotated address",
        "",
        "ENGAGEMENT",
        f"  {eng['avg_pageviews_per_visit']} pageviews/visit; average visit "
        f"{_duration(eng['avg_visit_seconds'])}; {eng['single_view_visits']}/{eng['visits']} single-view",
        "",
        "TOP PAGES (ranked by visitors)",
    ]
    out += [f"  {r['visitors']:>3}  {r['label']}" for r in data["pages"]] or ["  (none)"]
    loc = data["locale"]
    out += [
        "",
        "LANGUAGE",
        (
            f"  UI non-English {loc['ui_non_english']}/{loc['ui_visitors']} beacon-reporting "
            f"visitors ({loc['ui_non_english_pct']}%)"
            if loc["ui_non_english_pct"] is not None
            else "  UI locale unmeasured this week: no beacon events "
            "(the beacon shipped 2026-08-10)"
        ),
        f"  Browser ko/ja {loc['browser_ko_ja']}/{loc['browser_visitors']} visitors "
        f"({loc['browser_ko_ja_pct']}%) -- the reachable ceiling; English is still the default",
    ]
    if _ui_coverage_caveat(loc):
        out.append(
            f"  Beacon coverage {loc['ui_visitors']}/{loc['browser_visitors']} of the week's "
            f"visitors ({loc['ui_coverage_pct']}%): the UI figure is a subset of this week"
        )
    out += [f"  {r['visitors']:>3}  browser {r['lang']}" for r in loc["browser_rows"]] or [
        "  (none)"
    ]
    out += ["", "EVENTS TRIGGERED (interactions, ranked by visitors)"]
    out += [
        f"  {r['visitors']:>3}  {r['event_name']} ({r['events']} events)"
        for r in data["events"]["rows"]
    ] or ["  (none)"]
    beacons = _beacon_summary(data["events"])
    if beacons:
        out += ["", f"  {beacons}"]
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# LLM lead paragraph (prose only; it never emits a figure it had to derive)
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = """You write the one-paragraph lead of a weekly web-traffic email \
for battlestats.online, a World of Warships player and clan statistics site run \
by a single operator. You are given a JSON object of already-computed figures for \
one completed Monday-to-Sunday UTC week; the email renders every table itself.

Write 2-4 sentences. Voice: Data from Star Trek -- warm, analytical, precise; no \
hype, no flattery. Use colons and semicolons, never em dashes.

Rules:
- NEVER compute a number. Only restate figures already present in the JSON, and \
only where naming one adds meaning. Never derive a percentage, a difference or a \
ratio that is not already a field.
- NEVER write a construction of the form "X of Y", "X out of Y", or "N% of", and \
never place two figures side by side so that one reads as a share of the other. \
The lists named `top_*_labels` are rank-ordered labels with no counts: say "led \
by" or "mostly", never a quantity.
- Say what matters: whether the week is ordinary or unusual against the prior \
week, where the traffic came from, and which feature the events show people using. \
Traffic here is tens of visitors per day; a swing of a few percent between weeks \
is normally noise. Say "within the usual range" when it is, rather than inventing \
a trend.
- `busiest_day_label` and `quietest_day_label` are labels, not figures. You may \
name a day; you may not attach a count to it or say how much busier it was, \
because those numbers are not in your input.
- `prior_week_events` beside an event is THAT EVENT'S own count last week and \
nothing else. It does not tell you how that event ranked against any other event \
last week, so never write that an ordering, a mix or a leaderboard is unchanged, \
the same as, or different from last week. You may say a single named event rose \
or fell.
- The two `language` percentages have DIFFERENT denominators and are not \
comparable as parts of one whole. `ui_non_english_pct` is the share of \
beacon-reporting visitors whose interface ran in Korean or Japanese; \
`browser_ko_ja_pct` is the share of all visitors whose BROWSER asks for Korean \
or Japanese, which is a ceiling, not usage. English is the default for every \
new arrival, so a low first figure beside a high second one is the expected \
state and not a fault. Mention them only when one has moved; never subtract \
them, and never call the second one usage.
- Do not recommend actions. Do not speculate about causes you cannot see.

Output STRICT JSON only, no markdown fences: {"lead": "..."}"""


def llm_payload(data: dict) -> dict:
    """The narrowed view of the week handed to the model.

    The model is deliberately NOT shown per-route, per-referrer or per-country
    counts. Given them, it juxtaposes a row's count with the period's total and
    writes a share that does not exist: an early live run produced "traffic
    remained mostly direct (36 of 48 visits)" and "40 visits on /player/*" out of
    48, both false, because those columns do not partition the period's visits (one
    visit spans several routes, and a referrer is recorded once per visit while
    the visit's later pageviews carry none). Instructing the model not to derive
    ratios did not stop it; withholding the operands does.

    What remains is either a whole-week total, a pre-computed delta, or a label
    with no number attached, so a cross-denominator ratio has no operands to be
    built from. The per-day series is withheld on exactly the same grounds: seven
    (day, visitors, visits) triples is a standing invitation to write "Tuesday
    was 40 of the week's 120", so the model gets the busiest and quietest day as
    LABELS and nothing else.

    Instrumentation beacons are withheld for the same reason. `top_event_names`
    is drawn from data["events"]["rows"], which compute() has already stripped of
    INSTRUMENTATION_EVENTS, so the lead cannot open on "the event mix is
    dominated by locale-active" -- a true sentence about a beacon that says
    nothing about the week. Telling the model the event is uninteresting would not
    hold; not showing it does.
    """
    return {
        "week_start": data["week_start"],
        "week_end": data["week_end"],
        "headline": data["headline"],
        # Labels, no counts: see the docstring on why the daily series itself
        # never reaches the model.
        "busiest_day_label": data["daily"]["busiest"],
        "quietest_day_label": data["daily"]["quietest"],
        "identity": {
            k: data["identity"][k]
            for k in ("visitors", "new", "returning", "new_pct", "returning_pct")
        },
        "engagement": {
            k: data["engagement"][k]
            for k in ("avg_pageviews_per_visit", "avg_visit_seconds")
        },
        "top_event_names": [
            {
                "event_name": r.get("event_name"),
                "events": r.get("events"),
                "prior_week_events": r.get("prior_week_events"),
            }
            for r in data["events"]["rows"][:3]
        ],
        "total_custom_events": data["events"]["total_events"],
        # Percentages only, both pre-computed, each against its own denominator.
        # The counts behind them are withheld for the reason in this docstring:
        # given both operands the model writes a share across the two.
        "language": {
            "ui_non_english_pct": data["locale"]["ui_non_english_pct"],
            "browser_ko_ja_pct": data["locale"]["browser_ko_ja_pct"],
        },
        # Labels only, in rank order. No counts.
        "top_browser_language_labels": [r.get("lang") for r in data["locale"]["browser_rows"][:3]],
        "top_referrer_labels": [r.get("source") for r in data["referrers"][:3]],
        "top_country_labels": [r.get("country") for r in data["countries"][:3]],
        "top_route_labels": [r.get("route") for r in data["routes"][:3]],
    }


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


def call_anthropic(model: str, api_key: str, data: dict) -> str:
    body = {
        # max_tokens caps thinking AND response text together, which is what
        # produced the earlier empty-response failure. The fix is headroom plus
        # low effort, NOT thinking={"type": "disabled"}: with thinking off,
        # Opus 5 can leak <thinking> tags into the visible response, and this
        # response is parsed as JSON, so a leaked tag breaks the parse.
        "model": model,
        "max_tokens": 4000,
        "output_config": {"effort": "low"},
        "system": SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": "Figures for the week. Write the lead.\n\n"
                + json.dumps(data, separators=(",", ":"), default=str),
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
    # content, so name it before the generic empty-response case below.
    if payload.get("stop_reason") == "refusal":
        raise RuntimeError(
            f"model declined the request (category="
            f"{(payload.get('stop_details') or {}).get('category')})"
        )
    text = "".join(
        blk.get("text", "") for blk in payload.get("content", []) if blk.get("type") == "text"
    ).strip()
    if not text:
        raise RuntimeError(f"empty response (stop_reason={payload.get('stop_reason')})")
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"): text.rfind("}") + 1]
    lead = json.loads(text).get("lead", "").strip()
    if not lead:
        raise RuntimeError("response carried no lead")
    return lead


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def week_start(day: date) -> date:
    """The Monday of the week containing `day`."""
    return day - timedelta(days=day.weekday())


def parse_week(argv: list[str]) -> date:
    """The Monday opening the week to report on.

    The default is the most recent Monday at or before now, minus seven days,
    which is the last COMPLETED week. It is deliberately not "now minus seven
    days": the timer is Persistent=true, so a catch-up run on Wednesday after a
    reboot must report the same Mon-Sun week the Monday run would have, not a
    window slid three days later. Any date inside a week selects that week.
    """
    for arg in argv:
        if arg.startswith("--week="):
            return week_start(date.fromisoformat(arg.split("=", 1)[1]))
    override = cfg("TRAFFIC_EMAIL_WEEK")
    if override:
        return week_start(date.fromisoformat(override))
    return week_start(datetime.now(timezone.utc).date()) - timedelta(days=WEEK_DAYS)


def gather(week: date) -> dict:
    dsn = read_umami_dsn(cfg("UMAMI_ENV_FILE", DEFAULT_UMAMI_ENV_FILE))
    psql_bin = cfg("PSQL_BIN", DEFAULT_PSQL)
    domain = cfg("UMAMI_SITE_DOMAIN", DEFAULT_SITE_DOMAIN)

    site = run_queries(dsn, [resolve_website_sql(domain)], psql_bin)[0]
    if not site:
        raise RuntimeError(f"no Umami website row for domain {domain!r}")
    website_id = site[0]["website_id"]

    week_lo = datetime.combine(week, datetime.min.time(), tzinfo=timezone.utc)
    week_hi = week_lo + timedelta(days=WEEK_DAYS)
    prior_lo = week_lo - timedelta(days=WEEK_DAYS)

    sqls = build_sqls(website_id, week_lo, week_hi, prior_lo)
    results = run_queries(dsn, list(sqls.values()), psql_bin)
    return compute(dict(zip(sqls.keys(), results)), week)


def main() -> int:
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    no_llm = "--no-llm" in args

    load_env_file(cfg("TRAFFIC_EMAIL_ENV_FILE", DEFAULT_ENV_FILE))
    week = parse_week(args)
    data = gather(week)

    lead, lead_error = "", ""
    if not no_llm:
        api_key = cfg("ANTHROPIC_API_KEY")
        if not api_key:
            lead_error = "ANTHROPIC_API_KEY not set"
        else:
            try:
                lead = call_anthropic(
                    cfg("ANTHROPIC_MODEL", "claude-opus-5"), api_key, llm_payload(data)
                )
            except Exception as exc:
                detail = exc
                if isinstance(exc, urllib.error.HTTPError):
                    try:
                        detail = f"{exc} :: {exc.read().decode('utf-8')[:400]}"
                    except Exception:
                        detail = str(exc)
                lead_error = f"{type(exc).__name__}: {detail}"

    email = render(data, lead=lead, lead_error=lead_error)

    if dry_run:
        print("SUBJECT:", email["subject"])
        print("---- TEXT ----")
        print(email["text"])
        print("---- HTML ----")
        print(email["html_body"])
        if lead_error:
            print("---- LEAD ERROR ----\n", lead_error)
        return 0

    send_email(email["subject"], email["html_body"], email["text"])
    print(f"[ok] sent: {email['subject']}")
    return 0


FAILURE_SUBJECT = "[battlestats] weekly traffic email FAILED"


def emit_failure(tb: str, dry_run: bool = False) -> None:
    """Fail loud: mail the traceback so a broken cron run is never silent.

    A dry run is how this script is exercised by hand, so it must send nothing
    at all -- including this. It must also never raise: a failure inside the
    failure handler would replace a useful traceback with a useless one.
    """
    sys.stderr.write(tb + "\n")
    if dry_run:
        sys.stderr.write("[dry-run] failure email suppressed\n")
        return
    try:
        body = (
            "<html><body><h2>battlestats weekly traffic email FAILED</h2><pre>"
            + _esc(tb)
            + "</pre></body></html>"
        )
        send_email(FAILURE_SUBJECT, body, tb)
        print("[warn] sent failure notification email")
    except Exception:
        sys.stderr.write("could not send failure email:\n" + traceback.format_exc() + "\n")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        emit_failure(traceback.format_exc(), dry_run="--dry-run" in sys.argv[1:])
        sys.exit(1)
