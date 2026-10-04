"""Behaviour tests for server/scripts/weekly_traffic_email.py.

The script is a standalone stdlib-only cron entrypoint, not a Django module, so
it is loaded by path. The database read (`run_queries`) and the send path
(`send_email`) are both mocked: these tests assert the arithmetic, the rendered
content, the SQL's ranking/timezone discipline, and the FAILED path.

The report is a completed Monday-to-Sunday UTC week. The single most important
property under test is that the weekly headline is NOT the sum of the daily
rows: `count(DISTINCT session_id)` over seven days is not seven daily distinct
counts added together, and a report that summed them would overstate visitors
and visits by roughly the returning-visitor rate.
"""
import ast
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import tempfile
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from django.test import SimpleTestCase

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "weekly_traffic_email.py"


def _load():
    spec = importlib.util.spec_from_file_location("weekly_traffic_email", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load()


# --------------------------------------------------------------------------- #
# fixtures: the shape run_queries returns, keyed exactly like build_sqls
# --------------------------------------------------------------------------- #
WEEK = date(2026, 8, 17)  # a Monday; the week runs through Sunday 2026-08-23

# Per-day rows. Deliberately uneven, and deliberately summing to LESS than the
# weekly distinct counts in `totals` below would if they were summable: daily
# visitors add to 151 against a weekly 141, which is exactly the discrepancy a
# summed headline would have printed.
DAILY = [
    ("2026-08-17", 60, 40, 20, 30, 8),
    ("2026-08-18", 70, 45, 22, 33, 7),
    ("2026-08-19", 80, 50, 25, 36, 9),
    ("2026-08-20", 65, 42, 21, 31, 6),
    ("2026-08-21", 90, 60, 29, 40, 11),
    ("2026-08-22", 55, 35, 18, 28, 5),
    ("2026-08-23", 50, 30, 16, 25, 4),
]


def _trend():
    return [
        {
            "day": day,
            "pageviews": pv,
            "events": ev,
            "visitors": vis,
            "visits": visits,
            "new_visitors": new,
        }
        for day, pv, ev, vis, visits, new in DAILY
    ]


RAW = {
    # Whole-week windows. pageviews/events match the daily sums (470/302),
    # because count(*) sums; visitors/visits deliberately do not.
    "totals": [
        {"bucket": "current", "pageviews": 470, "events": 302, "visitors": 141, "visits": 190},
        {"bucket": "prior", "pageviews": 400, "events": 280, "visitors": 120, "visits": 170},
    ],
    "trend": _trend(),
    "engagement": [
        {
            "visits": 190,
            "avg_pageviews_per_visit": 1.54,
            "avg_visit_seconds": 248,
            "single_view_visits": 95,
        }
    ],
    "identity": [
        {
            "visitors": 141,
            "new_visitors": 50,  # == the daily New column summed
            "returning_visitors": 91,
            "identified_visitors": 70,
            "new_but_known_bs_vid": 2,
        }
    ],
    "pages": [
        {"url_path": "/", "visitors": 60, "visits": 70, "pageviews": 95},
        {"url_path": "/player/%5BDJMAX%5D", "visitors": 12, "visits": 20, "pageviews": 33},
    ],
    "routes": [{"route": "/player/*", "visitors": 80, "visits": 140, "pageviews": 210}],
    "referrers": [
        {"source": "(direct / none)", "visitors": 70, "visits": 90},
        {"source": "t.co", "visitors": 12, "visits": 14},
    ],
    "countries": [{"country": "US", "visitors": 40}],
    "devices": [{"device": "desktop", "visitors": 90}],
    # 141 beacon-reporting visitors, 21 of them non-English.
    "locale_active": [
        {"locale": "en", "visitors": 120, "load_visits": 180},
        {"locale": "ko", "visitors": 15, "load_visits": 22},
        {"locale": "ja", "visitors": 6, "load_visits": 8},
    ],
    # 141 visitors partitioned by browser language: 60 ko/ja, 68 non-English.
    "browser_language": [
        {"lang": "en", "visitors": 70},
        {"lang": "ko", "visitors": 40},
        {"lang": "ja", "visitors": 20},
        {"lang": "de", "visitors": 8},
        {"lang": "??", "visitors": 3},
    ],
    # Ordered as the SQL returns it: by visitors DESC. The page-load beacon
    # therefore arrives at the HEAD of the roster, which is exactly the position
    # it must not keep once compute() has split it out. The three interaction
    # rows total 302, matching the week's headline event count.
    "events": [
        {
            "event_name": "locale-active",
            "events": 500,
            "visitors": 138,
            "visits": 185,
            "prior_week_events": 450,
        },
        {
            "event_name": "ship-leaderboard-filter",
            "events": 180,
            "visitors": 30,
            "visits": 40,
            "prior_week_events": 150,
        },
        {
            "event_name": "search",
            "events": 100,
            "visitors": 25,
            "visits": 30,
            "prior_week_events": 130,
        },
        {
            "event_name": "theme-change",
            "events": 22,
            "visitors": 6,
            "visits": 6,
            "prior_week_events": 0,
        },
    ],
}


def _computed(raw=None):
    return mod.compute({k: list(v) for k, v in (raw or RAW).items()}, WEEK)


def _bounds():
    """(week_lo, week_hi, prior_lo) as build_sqls takes them."""
    lo = datetime(2026, 8, 17, tzinfo=timezone.utc)
    return lo, lo + timedelta(days=7), lo - timedelta(days=7)


# --------------------------------------------------------------------------- #
# the reported window
# --------------------------------------------------------------------------- #
class WeekWindowTests(SimpleTestCase):
    def test_week_start_snaps_any_date_to_its_monday(self):
        for day in range(17, 24):
            self.assertEqual(mod.week_start(date(2026, 8, day)), WEEK)
        self.assertEqual(mod.week_start(date(2026, 8, 24)), date(2026, 8, 24))

    def test_default_is_the_last_completed_week_not_now_minus_seven_days(self):
        """Run on Monday 2026-08-24, the report covers 08-17 to 08-23."""
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(mod, "datetime", _FrozenDatetime(2026, 8, 24, 10, 30)):
                self.assertEqual(mod.parse_week([]), WEEK)

    def test_a_catch_up_run_later_in_the_week_reports_the_same_week(self):
        """The timer is Persistent=true. A Wednesday run after a reboot must not
        silently slide the window three days; "now minus seven days" would."""
        for day, hour in ((24, 10), (26, 3), (30, 23)):
            with mock.patch.dict(os.environ, {}, clear=True):
                with mock.patch.object(mod, "datetime", _FrozenDatetime(2026, 8, day, hour)):
                    self.assertEqual(mod.parse_week([]), WEEK, f"2026-08-{day}")

    def test_a_run_on_the_following_monday_moves_on_exactly_one_week(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(mod, "datetime", _FrozenDatetime(2026, 8, 31, 10, 30)):
                self.assertEqual(mod.parse_week([]), date(2026, 8, 24))

    def test_week_flag_accepts_any_date_inside_the_week(self):
        self.assertEqual(mod.parse_week(["--week=2026-08-20"]), WEEK)
        self.assertEqual(mod.parse_week(["--week=2026-08-17"]), WEEK)

    def test_week_flag_beats_the_environment_override(self):
        with mock.patch.dict(os.environ, {"TRAFFIC_EMAIL_WEEK": "2026-01-07"}, clear=False):
            self.assertEqual(mod.parse_week(["--week=2026-08-20"]), WEEK)
            self.assertEqual(mod.parse_week([]), date(2026, 1, 5))

    def test_gather_derives_a_seven_day_window_and_the_week_before_it(self):
        captured = {}

        def _build(website_id, week_lo, week_hi, prior_lo):
            captured.update(lo=week_lo, hi=week_hi, plo=prior_lo)
            return {"q": "SELECT 1"}

        with mock.patch.dict(os.environ, {"UMAMI_DATABASE_URL": "postgres://x"}, clear=False), \
             mock.patch.object(mod, "build_sqls", _build), \
             mock.patch.object(mod, "run_queries", side_effect=[[[{"website_id": "w"}]], [[]]]):
            mod.gather(WEEK)
        self.assertEqual(captured["lo"], datetime(2026, 8, 17, tzinfo=timezone.utc))
        self.assertEqual(captured["hi"], datetime(2026, 8, 24, tzinfo=timezone.utc))
        self.assertEqual(captured["plo"], datetime(2026, 8, 10, tzinfo=timezone.utc))


class _FrozenDatetime(datetime):
    """`datetime` with `now()` pinned, so the window derivation is testable."""

    _pinned = None

    def __new__(cls, *args):
        inst = super().__new__(cls, 2000, 1, 1)
        inst._pinned = datetime(*args, tzinfo=timezone.utc)
        return inst

    def now(self, tz=None):  # noqa: D102 - instance is used as the module's `datetime`
        return self._pinned


# --------------------------------------------------------------------------- #
# arithmetic
# --------------------------------------------------------------------------- #
class ComputeTests(SimpleTestCase):
    def test_headline_carries_the_whole_week_figures(self):
        h = _computed()["headline"]
        self.assertEqual(h["visitors"]["value"], 141)
        self.assertEqual(h["visits"]["value"], 190)
        self.assertEqual(h["pageviews"]["value"], 470)
        self.assertEqual(h["events"]["value"], 302)

    def test_visitors_are_never_summed_from_the_daily_rows(self):
        """The load-bearing property of the whole report. A visitor active on
        three days is one weekly visitor and three daily ones; the fixture's
        daily column adds to 151 against a true weekly 141."""
        data = _computed()
        self.assertEqual(sum(r["visitors"] for r in data["daily"]["rows"]), 151)
        self.assertEqual(data["headline"]["visitors"]["value"], 141)

    def test_visits_are_not_summed_either(self):
        data = _computed()
        self.assertEqual(sum(r["visits"] for r in data["daily"]["rows"]), 223)
        self.assertEqual(data["headline"]["visits"]["value"], 190)

    def test_comparison_is_the_prior_week(self):
        h = _computed()["headline"]
        self.assertEqual(h["visitors"]["prior_week"], 120)
        self.assertEqual(h["visitors"]["vs_prior_week"]["abs"], 21)
        self.assertEqual(h["visitors"]["vs_prior_week"]["pct"], 17.5)

    def test_daily_mean_divides_the_week_by_seven(self):
        """A per-day figure the operator can hold against the old daily email."""
        h = _computed()["headline"]
        self.assertEqual(h["visitors"]["daily_mean"], 20.1)
        self.assertEqual(h["pageviews"]["daily_mean"], 67.1)

    def test_a_missing_week_degrades_to_zeros_rather_than_raising(self):
        h = mod.compute(dict(RAW, totals=[]), WEEK)["headline"]
        self.assertEqual(h["visitors"]["value"], 0)
        self.assertIsNone(h["visitors"]["prior_week"])

    def test_delta_against_a_zero_base_reports_absolute_only(self):
        self.assertEqual(mod._delta(5, 0), {"abs": 5, "pct": None})

    def test_delta_against_a_missing_base_is_not_a_zero(self):
        """No prior week must read as 'n/a', never as a 100% jump."""
        self.assertEqual(mod._delta(5, None), {"abs": None, "pct": None})


class DailyBreakdownTests(SimpleTestCase):
    def test_seven_rows_in_calendar_order_with_weekday_labels(self):
        rows = _computed()["daily"]["rows"]
        self.assertEqual([r["day"] for r in rows], [d[0] for d in DAILY])
        self.assertEqual(
            [r["weekday"] for r in rows], ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        )

    def test_a_day_with_no_traffic_renders_as_a_zero_not_as_an_absence(self):
        """The trend query returns no row at all for a dead day. Dropping it
        would shorten the table and hide the one thing worth seeing."""
        raw = dict(RAW, trend=[r for r in _trend() if r["day"] != "2026-08-19"])
        rows = mod.compute(raw, WEEK)["daily"]["rows"]
        self.assertEqual(len(rows), 7)
        wednesday = rows[2]
        self.assertEqual(wednesday["day"], "2026-08-19")
        self.assertEqual(wednesday["weekday"], "Wed")
        self.assertEqual(wednesday["visitors"], 0)
        self.assertEqual(wednesday["new_visitors"], 0)

    def test_weekday_labels_do_not_depend_on_the_hosts_locale(self):
        """strftime('%a') consults LC_TIME; a systemd unit's label must not."""
        self.assertEqual(mod.WEEKDAY_LABELS[0], "Mon")
        self.assertEqual(len(mod.WEEKDAY_LABELS), 7)
        calls = [
            node.func.attr
            for node in ast.walk(ast.parse(SCRIPT.read_text()))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        ]
        self.assertNotIn("strftime", calls)

    def test_busiest_and_quietest_days_are_labels_ranked_by_visitors(self):
        daily = _computed()["daily"]
        self.assertEqual(daily["busiest"], "Fri 2026-08-21")
        self.assertEqual(daily["quietest"], "Sun 2026-08-23")

    def test_only_the_summable_columns_are_summed(self):
        daily = _computed()["daily"]
        self.assertEqual(set(daily["sums"]), {"pageviews", "events"})
        self.assertEqual(daily["sums"]["pageviews"], 470)
        self.assertEqual(daily["sums"]["events"], 302)
        self.assertEqual(mod.SUMMABLE_METRICS, ("pageviews", "events"))
        for absent in ("visitors", "visits"):
            self.assertNotIn(absent, mod.SUMMABLE_METRICS)

    def test_new_visitors_is_the_one_distinct_count_that_does_sum(self):
        """First-ever-seen falls on exactly one day, and necessarily a day that
        visitor was active on, so the two queries must agree."""
        data = _computed()
        self.assertEqual(data["daily"]["new_visitors_sum"], 50)
        self.assertEqual(data["identity"]["new"], 50)
        self.assertEqual(data["daily"]["discrepancies"], [])

    def test_a_boundary_error_is_reported_rather_than_raised(self):
        raw = dict(RAW, identity=[dict(RAW["identity"][0], new_visitors=61)])
        data = mod.compute(raw, WEEK)
        self.assertEqual(
            data["daily"]["discrepancies"],
            [{"metric": "New visitors", "daily_sum": 50, "week_query": 61}],
        )

    def test_the_summable_columns_are_checked_against_the_week_query_too(self):
        raw = dict(
            RAW,
            totals=[dict(RAW["totals"][0], pageviews=999), RAW["totals"][1]],
        )
        data = mod.compute(raw, WEEK)
        self.assertEqual(
            [d["metric"] for d in data["daily"]["discrepancies"]], ["Pageviews"]
        )


class IdentityTests(SimpleTestCase):
    def test_new_plus_returning_equals_the_weeks_active_visitors(self):
        ident = _computed()["identity"]
        self.assertEqual(ident["new"] + ident["returning"], ident["visitors"])

    def test_shares_are_taken_over_visitors_not_visits(self):
        """The denominator is the week's active visitors (141), not visits (190)."""
        ident = _computed()["identity"]
        self.assertEqual(ident["new_pct"], 35.5)
        self.assertEqual(ident["returning_pct"], 64.5)

    def test_durable_id_coverage_is_reported_separately(self):
        ident = _computed()["identity"]
        self.assertEqual(ident["identified_visitors"], 70)
        self.assertEqual(ident["identified_pct"], 49.6)

    def test_bs_vid_correction_is_not_folded_into_the_primary_counts(self):
        """new_but_known_bs_vid is a diagnostic; subtracting it silently would
        make the headline incomparable with earlier weeks."""
        ident = _computed()["identity"]
        self.assertEqual(ident["new_but_known_bs_vid"], 2)
        self.assertEqual(ident["new"], 50)


class EventSummaryTests(SimpleTestCase):
    def test_totals_and_distinct_names(self):
        """Interaction events only: the fixture's 500 beacon events are not in
        the 302, and the beacon is not one of the 3 names."""
        ev = _computed()["events"]
        self.assertEqual(ev["total_events"], 302)
        self.assertEqual(ev["distinct_event_names"], 3)

    def test_the_interaction_roster_reconciles_with_the_headline(self):
        data = _computed()
        self.assertEqual(data["events"]["total_events"], data["headline"]["events"]["value"])

    def test_each_event_gets_a_delta_against_its_own_prior_week(self):
        rows = {r["event_name"]: r for r in _computed()["events"]["rows"]}
        self.assertEqual(rows["ship-leaderboard-filter"]["vs_prior_week"]["abs"], 30)
        self.assertEqual(rows["search"]["vs_prior_week"]["abs"], -30)

    def test_an_event_new_since_the_prior_week_is_not_a_percentage(self):
        rows = {r["event_name"]: r for r in _computed()["events"]["rows"]}
        self.assertEqual(rows["theme-change"]["vs_prior_week"]["abs"], 22)
        self.assertIsNone(rows["theme-change"]["vs_prior_week"]["pct"])

    def test_families_group_by_the_first_two_segments(self):
        fams = {f["family"]: f for f in _computed()["events"]["families"]}
        self.assertIn("ship-leaderboard", fams)
        self.assertEqual(fams["ship-leaderboard"]["events"], 180)

    def test_short_names_stay_whole(self):
        self.assertEqual(mod._event_family("search"), "search")
        self.assertEqual(mod._event_family("theme-change"), "theme-change")
        self.assertEqual(mod._event_family("player-insights-profile"), "player-insights")


class InstrumentationEventTests(SimpleTestCase):
    """A page-load beacon is not an interaction. It outranks every real event by
    construction (one per page load, English included), and it arrived on
    2026-08-10, so left in the roster it heads the ranking and shows a huge rise
    against a prior window that predates it. Both statements are true and
    neither describes the week."""

    def test_the_beacon_is_held_out_of_the_ranked_roster(self):
        ev = _computed()["events"]
        self.assertNotIn("locale-active", [r["event_name"] for r in ev["rows"]])
        self.assertEqual(ev["rows"][0]["event_name"], "ship-leaderboard-filter")

    def test_the_beacon_is_held_out_of_the_feature_roster(self):
        """`locale-active` would otherwise be the largest "feature area" on the
        page, ahead of every feature anyone actually used."""
        self.assertNotIn("locale-active", [f["family"] for f in _computed()["events"]["families"]])

    def test_the_beacon_count_is_kept_rather_than_discarded(self):
        ev = _computed()["events"]
        self.assertEqual(ev["beacon_events"], 500)
        self.assertEqual([r["event_name"] for r in ev["beacon_rows"]], ["locale-active"])

    def test_the_model_never_sees_the_beacon(self):
        """The file's standing doctrine: withholding the operands is what works;
        instructing the model not to dwell on it does not."""
        payload = mod.llm_payload(_computed())
        self.assertNotIn("locale-active", [r["event_name"] for r in payload["top_event_names"]])
        self.assertEqual(payload["total_custom_events"], 302)
        self.assertNotIn("locale-active", json.dumps(payload))

    def test_the_headline_event_count_excludes_beacons_in_both_windows(self):
        """Excluding it from the week but not the prior one would replace one
        discontinuity with a worse one."""
        sqls = mod.build_sqls("w-id", *_bounds())
        self.assertIn(
            "count(*) FILTER (WHERE event_type = 2 AND is_interaction) AS events", sqls["trend"]
        )
        self.assertIn("'locale-active'", sqls["trend"])
        # `totals` computes the reported week and the prior week in one pass, so
        # a single predicate necessarily covers both sides of the comparison.
        self.assertIn("'locale-active'", sqls["totals"])

    def test_single_view_visits_ignores_beacons(self):
        """The beacon fires on every page load, so counting it as the "second
        event" would make a single-view visit impossible and read as engagement
        rising to a perfect score."""
        sql = mod.build_sqls("w-id", *_bounds())["engagement"]
        self.assertIn("event_type = 2 AND coalesce(we.event_name, '') NOT IN", sql)
        self.assertIn("pv <= 1 AND ev = 0", sql)

    def test_an_unnamed_event_is_not_mistaken_for_a_beacon(self):
        """`NULL NOT IN (...)` is NULL, which a FILTER reads as false. The
        coalesce is what keeps an unnamed custom event counted as an
        interaction instead of silently vanishing from the totals."""
        for name in ("trend", "engagement", "totals"):
            self.assertIn("coalesce(we.event_name, '')", mod.build_sqls("w-id", *_bounds())[name])

    def test_the_events_query_itself_stays_unfiltered(self):
        """The split is done in Python so the beacon's own count survives to be
        printed. Filtering in SQL would lose it."""
        self.assertNotIn("locale-active", mod.build_sqls("w-id", *_bounds())["events"])


class LocaleTests(SimpleTestCase):
    """The locale block's two halves answer different questions. Nothing here may
    let them share a denominator: `ui_*` counts only beacon-reporting visitors,
    `browser_*` counts every visitor."""

    def test_ui_share_is_taken_against_beacon_reporting_visitors(self):
        loc = _computed()["locale"]
        self.assertEqual(loc["ui_visitors"], 141)
        self.assertEqual(loc["ui_non_english"], 21)
        self.assertEqual(loc["ui_non_english_pct"], 14.9)

    def test_browser_share_counts_every_visitor_and_folds_unknowns_out_of_non_english(self):
        loc = _computed()["locale"]
        self.assertEqual(loc["browser_visitors"], 141)
        # ko 40 + ja 20; the reachable ceiling while those are the only two locales.
        self.assertEqual(loc["browser_ko_ja"], 60)
        self.assertEqual(loc["browser_ko_ja_pct"], 42.6)
        # de counts as non-English, '??' (unset language) does not.
        self.assertEqual(loc["browser_non_english"], 68)

    def test_a_week_before_the_beacon_shipped_yields_no_share_rather_than_zero(self):
        """Absent instrumentation is not 0% adoption, and the email must not
        print it as such."""
        raw = dict(RAW, locale_active=[])
        loc = _computed(raw)["locale"]
        self.assertEqual(loc["ui_visitors"], 0)
        self.assertIsNone(loc["ui_non_english_pct"])
        # The demand half still reports; it predates the beacon entirely.
        self.assertEqual(loc["browser_ko_ja"], 60)

    def test_browser_rows_are_truncated_for_display_only_never_for_the_denominator(self):
        raw = dict(
            RAW,
            browser_language=[{"lang": f"l{i}", "visitors": 1} for i in range(mod.LOCALE_TOP_N + 4)],
        )
        loc = _computed(raw)["locale"]
        self.assertEqual(len(loc["browser_rows"]), mod.LOCALE_TOP_N)
        self.assertEqual(loc["browser_visitors"], mod.LOCALE_TOP_N + 4)

    def test_full_beacon_coverage_adds_no_caveat(self):
        """141 beacon-reporting visitors against 141 for the week."""
        loc = _computed()["locale"]
        self.assertEqual(loc["ui_coverage_pct"], 100.0)
        self.assertEqual(mod._ui_coverage_caveat(loc), "")

    def test_partial_beacon_coverage_is_declared_rather_than_left_to_the_reader(self):
        """A week straddling the beacon's ship date is the motivating case: it
        covered part of the window while browser language covered all of it, so
        the two shares are not only different populations but different spans."""
        raw = dict(
            RAW,
            locale_active=[
                {"locale": "en", "visitors": 40, "load_visits": 55},
                {"locale": "ko", "visitors": 7, "load_visits": 9},
            ],
        )
        email = mod.render(_computed(raw))
        loc = _computed(raw)["locale"]
        self.assertEqual(loc["ui_coverage_pct"], 33.3)
        self.assertIn("drawn from a subset of this week", email["html_body"])
        self.assertIn("Beacon coverage 47/141", email["text"])

    def test_the_locale_queries_read_the_beacon_and_the_session_language(self):
        sqls = mod.build_sqls("w-id", *_bounds())
        self.assertIn("we.event_name = 'locale-active'", sqls["locale_active"])
        self.assertIn("ed.data_key = 'locale'", sqls["locale_active"])
        # No LIMIT on either: a truncated set would give the shares above a
        # denominator that silently excludes the tail.
        self.assertNotIn("LIMIT", sqls["locale_active"])
        self.assertNotIn("LIMIT", sqls["browser_language"])
        self.assertIn("split_part", sqls["browser_language"])


class PathTests(SimpleTestCase):
    def test_percent_encoded_player_names_are_decoded_for_display(self):
        self.assertEqual(mod._pretty_path("/player/%5BDJMAX%5D"), "/player/[DJMAX]")
        self.assertEqual(mod._pretty_path("/player/DER%20LICTH%20HAMMER"), "/player/DER LICTH HAMMER")


# --------------------------------------------------------------------------- #
# rendered content
# --------------------------------------------------------------------------- #
class RenderTests(SimpleTestCase):
    def setUp(self):
        self.email = mod.render(_computed(), lead="A quiet week.")
        self.html = self.email["html_body"]

    def test_subject_names_the_week_and_the_three_headline_counts(self):
        self.assertEqual(
            self.email["subject"],
            "[battlestats] traffic week of 2026-08-17: 141 visitors, 190 visits, 470 views",
        )

    def test_the_header_states_the_span_and_the_comparison(self):
        self.assertIn("week of 2026-08-17 to 2026-08-23 (UTC)", self.html)
        self.assertIn("compared against the week before it", self.html)

    def test_lead_paragraph_is_included_when_supplied(self):
        self.assertIn("A quiet week.", self.html)

    def test_totals_declare_that_visitors_do_not_sum_from_the_days(self):
        """The number most likely to be misread. The email says why, rather than
        leaving the reader to add the column and find a different answer."""
        self.assertIn("NOT the sum of the day-by-day", self.html)
        self.assertIn("counted once for the week", self.html)

    def test_the_day_by_day_table_carries_every_day_and_a_week_row(self):
        self.assertIn("Day by day", self.html)
        for label in ("Mon 2026-08-17", "Wed 2026-08-19", "Sun 2026-08-23"):
            self.assertIn(label, self.html)
        self.assertIn(">Week</td>", self.html)

    def test_the_week_row_is_the_week_query_not_the_column_sum(self):
        """151 is what the visitors column adds to; printing it would contradict
        the Totals table three sections above."""
        self.assertIn("The Week row is the whole-week query", self.html)
        self.assertNotIn(">151</td>", self.html)
        self.assertIn(">141</td>", self.html)

    def test_a_clean_week_prints_no_self_check_line(self):
        self.assertNotIn("Self-check", self.html)
        self.assertNotIn("SELF-CHECK", self.email["text"])

    def test_a_self_check_failure_is_stated_in_the_email(self):
        raw = dict(RAW, identity=[dict(RAW["identity"][0], new_visitors=61)])
        email = mod.render(mod.compute(raw, WEEK))
        self.assertIn("Self-check", email["html_body"])
        self.assertIn("New visitors sums to 50", email["html_body"])
        self.assertIn("whole-week query counts 61", email["html_body"])
        self.assertIn("SELF-CHECK FAILED: New visitors", email["text"])

    def test_new_vs_returning_definition_is_legible_in_the_email_itself(self):
        """The definition must travel with the number, not live only in code."""
        self.assertIn("first-ever appearance in Umami", self.html)
        self.assertIn("denominator is the week", self.html)
        self.assertIn("35.5%", self.html)

    def test_the_new_share_declares_that_it_is_window_dependent(self):
        """It jumped 44.8% -> 78.5% on the live conversion with no change in the
        audience: the numerator sums across days and the denominator does not.
        Read against a remembered daily figure it reads as churn."""
        self.assertIn("NOT comparable to the daily email", self.html)
        self.assertIn("widening the window raises it by construction", self.html)

    def test_ip_rotation_caveat_is_stated(self):
        self.assertIn("rotates", self.html)
        self.assertIn("bs-vid durable-id check", self.html)

    def test_page_paths_are_shown_decoded(self):
        self.assertIn("/player/[DJMAX]", self.html)
        self.assertNotIn("%5BDJMAX%5D", self.html)

    def test_tables_declare_that_ranking_is_by_visitors(self):
        self.assertIn("Ranked by distinct visitors, not by view count", self.html)

    def test_own_domain_referrer_exclusion_is_declared(self):
        self.assertIn("internal navigation and are excluded", self.html)

    def test_operator_ip_exclusion_is_declared(self):
        self.assertIn("excluded at Umami ingest level", self.html)

    def test_every_custom_event_name_appears(self):
        for name in ("ship-leaderboard-filter", "search", "theme-change"):
            self.assertIn(name, self.html)

    def test_the_event_table_compares_against_the_prior_week(self):
        self.assertIn("Prior week", self.html)
        self.assertIn("total count over the week before this one", self.html)
        self.assertNotIn("7d mean", self.html)

    def test_the_beacon_is_reported_once_as_instrumentation_not_as_a_ranked_row(self):
        """Demoted, not suppressed: the operator still gets the number, in a flat
        sentence that cannot head a ranking, and is told why it is set apart."""
        self.assertIn("Instrumentation, excluded from every figure above", self.html)
        self.assertIn("locale-active 500 events from 138 visitors", self.html)
        # Never a row in the ranked table: cells render as <td ...>name</td>.
        self.assertNotIn(">locale-active</td>", self.html)
        # No delta beside it: a beacon's movement is pageview movement, which
        # Totals already reports.
        self.assertNotIn("450", self.html)

    def test_the_headline_row_says_what_it_counts(self):
        self.assertIn("Custom events (interactions)", self.html)
        self.assertIn("fire on every load rather than on anything the visitor chose", self.html)

    def test_the_legends_name_the_beacons_from_the_constant(self):
        """The runbook promises that adding a beacon needs no other edit. A
        hardcoded name in a legend would quietly break that promise."""
        self.assertIn("(locale-active)", mod._BEACON_EXCLUSION_NOTE)
        for name in mod.INSTRUMENTATION_EVENTS:
            self.assertIn(name, mod._BEACON_EXCLUSION_NOTE)

    def test_text_alternative_carries_the_same_demotion(self):
        text = self.email["text"]
        self.assertIn("EVENTS TRIGGERED (interactions, ranked by visitors)", text)
        self.assertIn("Instrumentation, excluded from every figure above", text)
        self.assertEqual(text.count("locale-active"), 1)

    def test_text_alternative_carries_the_daily_breakdown(self):
        text = self.email["text"]
        self.assertIn("DAY BY DAY", text)
        self.assertIn("Mon 2026-08-17", text)
        self.assertIn("Sun 2026-08-23", text)

    def test_a_week_with_no_beacon_prints_no_instrumentation_line(self):
        raw = dict(RAW, events=[r for r in RAW["events"] if r["event_name"] != "locale-active"])
        email = mod.render(_computed(raw))
        self.assertNotIn("Instrumentation, excluded", email["html_body"])
        self.assertNotIn("Instrumentation, excluded", email["text"])

    def test_html_is_escaped_not_injected(self):
        raw = dict(
            RAW,
            pages=[{"url_path": "/<script>x</script>", "visitors": 1, "visits": 1, "pageviews": 1}],
        )
        html = mod.render(mod.compute(raw, WEEK))["html_body"]
        self.assertNotIn("<script>x</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_lead_error_is_surfaced_without_blocking_the_numbers(self):
        html = mod.render(_computed(), lead_error="HTTPError: 529")["html_body"]
        self.assertIn("HTTPError: 529", html)
        self.assertIn("computed in Python and is unaffected", html)

    def test_text_alternative_carries_the_headline_and_the_split(self):
        text = self.email["text"]
        self.assertIn("Visitors                 141", text)
        self.assertIn("vs prior week +21 (+17.5%)", text)
        self.assertIn("new 50 (35.5%); returning 91 (64.5%)", text)

    def test_language_section_states_both_denominators_in_the_email_itself(self):
        """A reader seeing 14.9% beside 42.6% must be told they are not the same
        population, or the pair reads as a 28-point shortfall in adoption."""
        self.assertIn("Language", self.html)
        self.assertIn("UI locale in effect", self.html)
        self.assertIn("Browser language", self.html)
        self.assertIn("denominator is beacon-reporting visitors, not the week", self.html)
        self.assertIn("reachable ceiling", self.html)
        self.assertIn("English remains the default", self.html)

    def test_a_week_with_no_beacon_events_reads_as_unmeasured_not_as_zero(self):
        """Every week before 2026-08-10 has no beacon data. Printing "0 (None%)"
        would read as nobody using a non-English UI, which is a different claim."""
        email = mod.render(_computed(dict(RAW, locale_active=[])))
        self.assertIn("(no locale-active events)", email["html_body"])
        self.assertIn("unmeasured rather than zero", email["html_body"])
        self.assertIn("Browser language", email["html_body"])
        self.assertIn("UI locale unmeasured this week", email["text"])
        self.assertNotIn("None%", email["html_body"])
        self.assertNotIn("None%", email["text"])
        # No coverage caveat on top: it would qualify a figure never printed.
        self.assertNotIn("Beacon coverage", email["text"])
        self.assertNotIn("drawn from a subset of this week", email["html_body"])

    def test_text_alternative_carries_the_language_split(self):
        text = self.email["text"]
        self.assertIn("UI non-English 21/141 beacon-reporting visitors (14.9%)", text)
        self.assertIn("Browser ko/ja 60/141 visitors (42.6%)", text)

    def test_duration_is_rendered_as_minutes_and_seconds(self):
        self.assertEqual(mod._duration(248), "4m 08s")
        self.assertEqual(mod._duration(None), "n/a")

    def test_empty_week_renders_without_raising(self):
        empty = {k: [] for k in RAW}
        email = mod.render(mod.compute(empty, WEEK))
        self.assertIn("no custom events", email["html_body"])
        # Seven zero rows, and no self-check alarm on a genuinely empty week.
        self.assertIn("Sun 2026-08-23", email["html_body"])
        self.assertNotIn("Self-check", email["html_body"])


# --------------------------------------------------------------------------- #
# SQL discipline (regression guards for bugs already paid for once)
# --------------------------------------------------------------------------- #
class SqlTests(SimpleTestCase):
    def setUp(self):
        self.sqls = mod.build_sqls("w-id", *_bounds())

    def test_pages_are_ranked_by_visitors_never_by_view_count(self):
        """Raw event counts have produced misleading readouts here before."""
        self.assertIn("ORDER BY visitors DESC", self.sqls["pages"])
        self.assertIn("ORDER BY visitors DESC", self.sqls["routes"])
        self.assertIn("ORDER BY visitors DESC", self.sqls["referrers"])

    def test_events_are_ranked_by_visitors_first(self):
        self.assertIn("ORDER BY d.visitors DESC", self.sqls["events"])

    def test_pageviews_and_custom_events_use_the_right_event_types(self):
        self.assertIn("we.event_type = 1", self.sqls["pages"])
        self.assertIn("we.event_type = 2", self.sqls["events"])

    def test_no_query_casts_a_timestamp_to_date(self):
        """`(ts AT TIME ZONE 'UTC')::date` compared against a timestamptz forces an
        implicit cast through the server's TimeZone setting, which can shift a day
        boundary. Nothing casts to date; bounds are timestamptz literals."""
        for name, sql in self.sqls.items():
            self.assertNotIn("::date", sql, name)
        self.assertIn("::timestamptz", self.sqls["identity"])

    def test_every_window_bound_is_one_of_the_three_computed_literals(self):
        """A stray `now()` or `interval '7 days'` would make the report's window
        depend on when it ran rather than on which week it names."""
        for name, sql in self.sqls.items():
            self.assertNotIn("now()", sql, name)
            self.assertNotIn("current_date", sql.lower(), name)
            self.assertNotIn("interval", sql.lower(), name)

    def test_the_two_week_windows_are_adjacent_and_seven_days_each(self):
        totals = self.sqls["totals"]
        self.assertIn("'2026-08-10T00:00:00+00:00'::timestamptz", totals)  # prior_lo
        self.assertIn("'2026-08-17T00:00:00+00:00'::timestamptz", totals)  # week_lo
        self.assertIn("'2026-08-24T00:00:00+00:00'::timestamptz", totals)  # week_hi

    def test_the_totals_bucket_alias_avoids_the_reserved_word(self):
        """WINDOW is reserved in Postgres; an unquoted alias would be a syntax
        error found only on the live database."""
        self.assertIn("END AS bucket", self.sqls["totals"])
        self.assertNotIn("AS window", self.sqls["totals"])

    def test_the_daily_breakdown_covers_the_reported_week_only(self):
        """Fourteen rows would silently double the table."""
        trend = self.sqls["trend"]
        self.assertIn("we.created_at >= '2026-08-17T00:00:00+00:00'::timestamptz", trend)
        self.assertNotIn("2026-08-10", trend)

    def test_trend_derives_both_sides_of_its_comparison_in_naive_utc(self):
        """The real hazard is a naive/aware comparison, not a ::date cast. Both
        `day` and `sess_first` must be produced by an explicit AT TIME ZONE 'UTC',
        so neither side of `sess_first >= day` is a bare timestamptz."""
        trend = self.sqls["trend"]
        self.assertIn("date_trunc('day', we.created_at AT TIME ZONE 'UTC') AS day", trend)
        self.assertIn("(s.created_at AT TIME ZONE 'UTC') AS sess_first", trend)
        self.assertIn("sess_first >= day", trend)
        # No bare column may appear on either side of that comparison.
        self.assertNotIn("s.created_at >= day", trend)
        self.assertNotIn("sess_first >= we.created_at", trend)

    def test_referrers_exclude_own_domain_internal_navigation(self):
        self.assertIn("we.referrer_domain, '') <> coalesce(we.hostname", self.sqls["referrers"])

    def test_no_analytics_side_ip_filter_is_attempted(self):
        """Operator IPs are excluded at Umami INGEST level (IGNORE_IP), and Umami
        stores no raw IP column at all, so any filter here would be a fiction that
        silently dropped real visitors."""
        for name, sql in self.sqls.items():
            self.assertIsNone(re.search(r"\bip\b|ip_address|IGNORE_IP", sql, re.I), name)

    def test_website_scope_is_bound_on_every_query(self):
        for name, sql in self.sqls.items():
            self.assertIn("we.website_id = 'w-id'::uuid", sql, name)

    def test_sql_literals_escape_quotes(self):
        self.assertEqual(mod._lit("o'brien"), "'o''brien'")


# --------------------------------------------------------------------------- #
# the psql transport
# --------------------------------------------------------------------------- #
class RunQueriesTests(SimpleTestCase):
    def _proc(self, stdout="", stderr="", code=0):
        return subprocess.CompletedProcess(args=[], returncode=code, stdout=stdout, stderr=stderr)

    def test_parses_one_json_line_per_query(self):
        out = '[{"a": 1}]\n[]\n'
        with mock.patch.object(mod.subprocess, "run", return_value=self._proc(stdout=out)):
            rows = mod.run_queries("dsn", ["SELECT 1", "SELECT 2"])
        self.assertEqual(rows, [[{"a": 1}], []])

    def test_uses_jsonb_agg_not_json_agg(self):
        """json_agg pretty-prints with real newlines, shattering one result across
        many lines and desynchronising the line/query mapping."""
        with mock.patch.object(mod.subprocess, "run", return_value=self._proc(stdout="[]\n")) as run:
            mod.run_queries("dsn", ["SELECT 1"])
        sql = run.call_args[0][0][-1]
        self.assertIn("jsonb_agg", sql)
        self.assertNotIn("json_agg(t), '[]'::json)", sql)

    def test_nonzero_exit_raises_with_the_psql_error(self):
        proc = self._proc(stderr="ERROR:  relation does not exist", code=1)
        with mock.patch.object(mod.subprocess, "run", return_value=proc):
            with self.assertRaises(RuntimeError) as ctx:
                mod.run_queries("dsn", ["SELECT 1"])
        self.assertIn("relation does not exist", str(ctx.exception))

    def test_line_count_mismatch_raises_rather_than_misaligning_results(self):
        with mock.patch.object(mod.subprocess, "run", return_value=self._proc(stdout="[]\n[]\n")):
            with self.assertRaises(RuntimeError) as ctx:
                mod.run_queries("dsn", ["SELECT 1"])
        self.assertIn("2 result lines for 1 queries", str(ctx.exception))

    def test_stops_on_first_sql_error(self):
        with mock.patch.object(mod.subprocess, "run", return_value=self._proc(stdout="[]\n")) as run:
            mod.run_queries("dsn", ["SELECT 1"])
        self.assertIn("ON_ERROR_STOP=1", run.call_args[0][0])


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
class ConfigTests(SimpleTestCase):
    def test_explicit_env_dsn_wins_and_umami_env_file_is_never_read(self):
        with mock.patch.dict(os.environ, {"UMAMI_DATABASE_URL": "postgres://x"}, clear=False):
            self.assertEqual(mod.read_umami_dsn("/nonexistent"), "postgres://x")

    def test_reads_only_database_url_from_umamis_env_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as fh:
            fh.write('APP_SECRET=shh\nDATABASE_URL="postgres://u:p@h/db"\nIGNORE_IP=1.2.3.4\n')
            path = fh.name
        try:
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(mod.read_umami_dsn(path), "postgres://u:p@h/db")
                # The other keys must not leak into the process environment.
                self.assertNotIn("APP_SECRET", os.environ)
                self.assertNotIn("IGNORE_IP", os.environ)
        finally:
            os.unlink(path)

    def test_unreadable_env_file_raises_a_named_error(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError) as ctx:
                mod.read_umami_dsn("/nonexistent/umami.env")
        self.assertIn("UMAMI_DATABASE_URL", str(ctx.exception))

    def test_no_secret_is_hard_coded_in_the_script(self):
        """The file lives in a public repo: every credential comes from an env file."""
        src = SCRIPT.read_text()
        for marker in ("postgres://", "postgresql://", "sk-ant-", "SMTP_PASS="):
            self.assertNotIn(marker, src)


# --------------------------------------------------------------------------- #
# fail-loud
# --------------------------------------------------------------------------- #
class FailureTests(SimpleTestCase):
    def test_failure_sends_a_tagged_email_carrying_the_traceback(self):
        send = mock.MagicMock()
        with mock.patch.object(mod, "send_email", send):
            mod.emit_failure("Traceback (most recent call last):\nRuntimeError: psql exited 2")
        send.assert_called_once()
        subject, html, text = send.call_args[0]
        self.assertEqual(subject, mod.FAILURE_SUBJECT)
        self.assertIn("FAILED", subject)
        self.assertIn("RuntimeError: psql exited 2", html)
        self.assertIn("RuntimeError: psql exited 2", text)

    def test_dry_run_sends_nothing_even_on_failure(self):
        send = mock.MagicMock()
        with mock.patch.object(mod, "send_email", send):
            mod.emit_failure("boom", dry_run=True)
        send.assert_not_called()

    def test_a_broken_send_path_does_not_mask_the_original_traceback(self):
        send = mock.MagicMock(side_effect=OSError("smtp down"))
        with mock.patch.object(mod, "send_email", send):
            mod.emit_failure("original boom")  # must not raise


class MainTests(SimpleTestCase):
    ENV = {"ANTHROPIC_API_KEY": "", "TRAFFIC_EMAIL_ENV_FILE": "/nonexistent.env"}

    def _run(self, argv, send):
        with mock.patch.dict(os.environ, self.ENV, clear=False), \
             mock.patch.object(mod, "load_env_file"), \
             mock.patch.object(mod, "gather", return_value=_computed()), \
             mock.patch.object(mod, "send_email", send), \
             mock.patch.object(mod.sys, "argv", ["weekly_traffic_email.py"] + argv):
            return mod.main()

    def test_dry_run_prints_and_sends_nothing(self):
        send = mock.MagicMock()
        self.assertEqual(self._run(["--dry-run", "--no-llm"], send), 0)
        send.assert_not_called()

    def test_normal_run_sends_exactly_one_email(self):
        send = mock.MagicMock()
        self.assertEqual(self._run(["--no-llm"], send), 0)
        send.assert_called_once()
        subject = send.call_args[0][0]
        self.assertIn("traffic week of 2026-08-17", subject)

    def test_the_week_flag_reaches_gather(self):
        send, seen = mock.MagicMock(), {}

        def _gather(week):
            seen["week"] = week
            return _computed()

        with mock.patch.dict(os.environ, self.ENV, clear=False), \
             mock.patch.object(mod, "load_env_file"), \
             mock.patch.object(mod, "gather", _gather), \
             mock.patch.object(mod, "send_email", send), \
             mock.patch.object(mod.sys, "argv", ["weekly_traffic_email.py", "--week=2026-08-20"]):
            mod.main()
        self.assertEqual(seen["week"], WEEK)

    def test_missing_api_key_still_sends_the_deterministic_report(self):
        send = mock.MagicMock()
        self._run([], send)
        send.assert_called_once()
        self.assertIn("ANTHROPIC_API_KEY not set", send.call_args[0][1])

    def test_llm_failure_never_blocks_the_email(self):
        send = mock.MagicMock()
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}, clear=False), \
             mock.patch.object(mod, "load_env_file"), \
             mock.patch.object(mod, "gather", return_value=_computed()), \
             mock.patch.object(mod, "call_anthropic", side_effect=RuntimeError("529")), \
             mock.patch.object(mod, "send_email", send), \
             mock.patch.object(mod.sys, "argv", ["weekly_traffic_email.py"]):
            mod.main()
        send.assert_called_once()
        self.assertIn("RuntimeError: 529", send.call_args[0][1])


class ContractTests(SimpleTestCase):
    def test_script_imports_no_django_so_it_runs_without_the_venv(self):
        """The timer runs this under bare python3: no venv, no pip installs."""
        tree = ast.parse(SCRIPT.read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        # Everything imported must be stdlib, plus `warships.opsmail`: the one
        # sanctioned local import, itself stdlib-only by contract
        # (test_opsmail.test_module_imports_no_django).
        allowed = {
            "__future__", "json", "subprocess", "sys", "traceback",
            "urllib", "datetime", "pathlib", "warships",
        }
        self.assertEqual(imported - allowed, set())
        self.assertNotIn("django", imported)

    def test_thinking_is_bounded_by_low_effort_not_disabled(self):
        """The budget problem is solved with effort + headroom, not thinking-off.

        max_tokens caps thinking AND response text together, which is what
        produced the original empty-response failure. Disabling thinking fixes
        that but buys a worse bug: with thinking off, Opus 5 can leak <thinking>
        tags into the visible response, and this response is parsed as JSON, so
        a leaked tag breaks the parse outright.
        """
        source = SCRIPT.read_text().replace("'", '"')
        self.assertIn('"output_config": {"effort": "low"}', source)
        self.assertNotIn('"thinking": {"type": "disabled"}', source)

    def test_a_refusal_is_named_rather_than_surfacing_as_a_parse_error(self):
        """A classifier decline is HTTP 200 with no content, not an exception."""
        source = SCRIPT.read_text()
        self.assertIn('payload.get("stop_reason") == "refusal"', source)
        self.assertIn("model declined the request", source)

    def test_the_model_is_told_never_to_compute_a_number(self):
        self.assertIn("NEVER compute a number", mod.SYSTEM_PROMPT)
        self.assertIn('NEVER write a construction of the form "X of Y"', mod.SYSTEM_PROMPT)

    def test_the_model_may_name_a_day_but_not_quantify_it(self):
        self.assertIn("busiest_day_label", mod.SYSTEM_PROMPT)
        self.assertIn("labels, not figures", mod.SYSTEM_PROMPT)

    def test_the_model_is_told_a_prior_week_count_is_not_a_prior_ranking(self):
        """The first live weekly lead wrote "the same ordering as last week",
        which the payload cannot support: it carries each top event's own prior
        count, never last week's roster."""
        self.assertIn("prior_week_events", mod.SYSTEM_PROMPT)
        self.assertIn("never write that an ordering", mod.SYSTEM_PROMPT)


class LlmPayloadTests(SimpleTestCase):
    """The model gets a narrowed view. A live run given the full dict wrote
    "traffic remained mostly direct (36 of 48 visits)" and "40 visits on
    /player/*" out of 48: both false, because neither column partitions the
    period's visits. Withholding the operands is the guard; the prompt alone was
    not."""

    def setUp(self):
        self.payload = mod.llm_payload(_computed())
        self.blob = json.dumps(self.payload)

    def test_per_route_counts_are_withheld(self):
        self.assertNotIn("routes", self.payload)
        self.assertEqual(self.payload["top_route_labels"], ["/player/*"])

    def test_payload_keys_are_an_explicit_allowlist(self):
        """Adding a field here must be a deliberate act: every count the model can
        see is a candidate operand for a fabricated share."""
        self.assertEqual(
            set(self.payload),
            {
                "week_start", "week_end", "headline", "identity", "engagement",
                "top_event_names", "total_custom_events", "top_referrer_labels",
                "top_country_labels", "top_route_labels", "language",
                "top_browser_language_labels", "busiest_day_label", "quietest_day_label",
            },
        )

    def test_the_daily_series_never_reaches_the_model(self):
        """Seven (day, visitors, visits) triples is a standing invitation to
        write "Tuesday was 40 of the week's 141"."""
        self.assertNotIn("daily", self.payload)
        self.assertNotIn("2026-08-19", self.blob)
        for day in ("Mon", "Tue", "Wed", "Thu", "Sat"):
            self.assertNotIn(day, self.blob)

    def test_the_peak_and_trough_days_are_labels_without_counts(self):
        self.assertEqual(self.payload["busiest_day_label"], "Fri 2026-08-21")
        self.assertEqual(self.payload["quietest_day_label"], "Sun 2026-08-23")
        for key in ("busiest_day_label", "quietest_day_label"):
            self.assertIsInstance(self.payload[key], str)

    def test_language_is_two_precomputed_percentages_with_no_operands(self):
        """The two shares have different denominators. Handed the counts, the
        model would divide one by the other and call the browser ceiling usage."""
        self.assertEqual(
            set(self.payload["language"]), {"ui_non_english_pct", "browser_ko_ja_pct"}
        )
        self.assertEqual(self.payload["language"]["ui_non_english_pct"], 14.9)
        self.assertEqual(self.payload["language"]["browser_ko_ja_pct"], 42.6)
        # The exact key set above already excludes the count fields; these check
        # they did not reach the model by some other route. Only names that are
        # not substrings of the two surviving keys can be asserted this way.
        for withheld in ("ui_visitors", "browser_visitors", "browser_rows", "ui_rows"):
            self.assertNotIn(withheld, self.blob)
        self.assertTrue(all(isinstance(v, float) for v in self.payload["language"].values()))

    def test_referrer_and_country_counts_are_withheld(self):
        self.assertNotIn("referrers", self.payload)
        self.assertNotIn("countries", self.payload)
        self.assertEqual(self.payload["top_referrer_labels"], ["(direct / none)", "t.co"])
        self.assertEqual(self.payload["top_country_labels"], ["US"])

    def test_label_lists_carry_no_numbers_at_all(self):
        for key in (
            "top_referrer_labels",
            "top_country_labels",
            "top_route_labels",
            "top_browser_language_labels",
        ):
            for item in self.payload[key]:
                self.assertIsInstance(item, str)

    def test_per_page_rows_are_withheld(self):
        self.assertNotIn("pages", self.payload)

    def test_whole_week_totals_and_precomputed_deltas_are_kept(self):
        self.assertEqual(self.payload["headline"]["visitors"]["value"], 141)
        self.assertEqual(self.payload["headline"]["visitors"]["vs_prior_week"]["abs"], 21)
        self.assertEqual(self.payload["identity"]["new_pct"], 35.5)

    def test_top_events_keep_their_own_prior_week_for_context(self):
        top = self.payload["top_event_names"]
        self.assertEqual(len(top), 3)
        self.assertEqual(top[0]["event_name"], "ship-leaderboard-filter")
        self.assertEqual(top[0]["prior_week_events"], 150)

    def test_main_hands_the_narrowed_payload_to_the_model_not_the_full_dict(self):
        called = {}

        def _capture(model, key, payload):
            called["payload"] = payload
            return "lead"

        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}, clear=False), \
             mock.patch.object(mod, "load_env_file"), \
             mock.patch.object(mod, "gather", return_value=_computed()), \
             mock.patch.object(mod, "call_anthropic", _capture), \
             mock.patch.object(mod, "send_email", mock.MagicMock()), \
             mock.patch.object(mod.sys, "argv", ["weekly_traffic_email.py"]):
            mod.main()
        self.assertNotIn("routes", called["payload"])
        self.assertNotIn("referrers", called["payload"])
        self.assertIn("top_route_labels", called["payload"])
        self.assertNotIn("daily", called["payload"])


class _FakeResponse:
    """Stand-in for the urlopen context manager: yields one canned JSON body."""

    def __init__(self, payload: dict):
        self._raw = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._raw


def _api_response(**overrides):
    payload = {
        "model": "claude-opus-5",
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": json.dumps({"lead": "An ordinary week."})}],
        "usage": {"input_tokens": 1290, "output_tokens": 412,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
    }
    payload.update(overrides)
    return payload


class AnthropicRequestTests(SimpleTestCase):
    """The real call_anthropic, with only the network replaced.

    Every other test patches call_anthropic whole, so nothing else pins what is
    actually put on the wire or what is recorded about the response.
    """

    SECRET = "sk-test-not-a-real-key"

    def call(self, response=None):
        import contextlib
        import io
        sent = {}
        payload = mod.llm_payload(_computed())

        def _urlopen(req, timeout=None):
            sent["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeResponse(response if response is not None else _api_response())

        out = io.StringIO()
        with mock.patch.object(mod.urllib.request, "urlopen", _urlopen), \
             contextlib.redirect_stdout(out):
            lead = mod.call_anthropic("claude-opus-5", self.SECRET, payload)
        return lead, payload, sent["body"], out.getvalue()

    def test_payload_is_sent_as_compact_json(self):
        """Indentation is billed input that carries no information."""
        _lead, payload, body, _out = self.call()
        content = body["messages"][0]["content"]
        instruction, _, blob = content.partition("\n\n")
        self.assertEqual(instruction, "Figures for the week. Write the lead.")
        self.assertEqual(blob, json.dumps(payload, separators=(",", ":"), default=str))
        self.assertNotIn("\n", blob)
        self.assertEqual(json.loads(blob), json.loads(json.dumps(payload, default=str)))

    def test_request_shape_is_otherwise_unchanged(self):
        _lead, _payload, body, _out = self.call()
        self.assertEqual(body["model"], "claude-opus-5")
        self.assertEqual(body["max_tokens"], 4000)
        self.assertEqual(body["output_config"], {"effort": "low"})
        self.assertEqual(body["system"], mod.SYSTEM_PROMPT)
        self.assertNotIn("cache_control", json.dumps(body))

    def test_usage_is_logged_and_the_key_is_not(self):
        lead, _payload, _body, out = self.call()
        self.assertEqual(lead, "An ordinary week.")
        self.assertIn(
            "[llm] model=claude-opus-5 stop_reason=end_turn input_tokens=1290 "
            "output_tokens=412 cache_creation_input_tokens=0 cache_read_input_tokens=0",
            out,
        )
        self.assertNotIn(self.SECRET, out)

    def test_a_response_without_usage_still_parses(self):
        missing = _api_response()
        del missing["usage"]
        for response in (missing, _api_response(usage=None)):
            with self.subTest(usage=response.get("usage", "absent")):
                lead, _payload, _body, out = self.call(response=response)
                self.assertEqual(lead, "An ordinary week.")
                self.assertIn("input_tokens=0 output_tokens=0", out)
