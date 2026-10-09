"""Tests for the exception-only ops alert email (`server/scripts/daily_ops_email.py`).

The contract under test, in one sentence: Python decides, the LLM only writes up.

So these tests assert the *decision*, not the prose. Healthy input must send
nothing and must not even reach the Anthropic API; every individual tripped
condition must send; missing, stale, unreadable and mis-shaped snapshots must
send; and the fail-loud path must still mail on an exception, because that path
is the one thing exception-only mode must never quiet.

The script is not a package module (it is deliberately runnable by a bare
python3 with no venv), so it is loaded by path the same way cron does.
"""
import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from unittest.mock import patch

from django.test import SimpleTestCase

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "daily_ops_email.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("daily_ops_email_under_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


doe = _load_script()


# --------------------------------------------------------------------------- #
# healthy fixture: numbers taken from the middle of the observed 2026 regime
# --------------------------------------------------------------------------- #
def _obs_realm(active_7d, active_1d, productive, bulk, poll, fresh, stale):
    return {
        "active_1d": active_1d,
        "active_7d": active_7d,
        "distinct_productive": productive,
        "coverage_ratio_vs_7d": round(productive / active_7d, 4),
        "productive_rate": 0.92,
        "fresh_within_24h": fresh,
        "fresh_frac": round(fresh / active_7d, 4),
        "stale_over_24h": stale,
        "obs_bulk_floor": bulk,
        "obs_poll": poll,
        "never_observed": 0,
    }


def write_healthy_tree(root: Path, now: datetime) -> None:
    """A snapshot tree that must produce ZERO conditions."""
    obs_dir = root / "observation-floor"
    obs_dir.mkdir(parents=True, exist_ok=True)
    realms = {
        "na": _obs_realm(53078, 27293, 18626, 27351, 3833, 19977, 33101),
        "eu": _obs_realm(88278, 32182, 30377, 42491, 5037, 31449, 56829),
        "asia": _obs_realm(65352, 33805, 21242, 26038, 3666, 22205, 42880),
    }
    totals = _obs_realm(206904, 96035, 70929, 96955, 12544, 74113, 132507)
    # observation snapshot lands at 04:30; the timer runs at 11:31 -> ~7h old
    obs_ts = now - timedelta(hours=7)
    (obs_dir / f"{obs_ts:%Y-%m-%d_%H%M}Z.json").write_text(json.dumps({
        "captured_at": obs_ts.isoformat(),
        "window_hours": 24,
        "config": {"BATTLE_OBSERVATION_FLOOR_LIMIT": "12000"},
        "realms": realms,
        "totals": totals,
    }))

    # crawl passes take days; ~40h old is well inside normal
    cy_dir = root / "crawl-yield"
    cy_dir.mkdir(parents=True, exist_ok=True)
    for realm, classified, disc, react, dorm, refreshed in (
        ("na", 275600, 383, 1886, 3, 54220),
        ("eu", 473814, 1225, 10668, 9, 93011),
        ("asia", 260700, 686, 5335, 6, 64740),
    ):
        still = classified - disc - react - dorm - refreshed
        ts = now - timedelta(hours=40)
        (cy_dir / f"{ts:%Y-%m-%d_%H%M}Z_{realm}.json").write_text(json.dumps({
            "captured_at": ts.isoformat(),
            "realm": realm,
            "pass_started_at": (ts - timedelta(days=3)).isoformat(),
            "active_window_days": 7,
            "players_classified": classified,
            "buckets": {
                "discovered_active": disc, "discovered_dormant": dorm,
                "reactivated": react, "refreshed_active": refreshed,
                "still_dormant": still,
            },
            "yield_total": disc + react,
            "overlap_total": refreshed,
            "yield_frac": round((disc + react) / classified, 4),
            "overlap_frac": round(refreshed / classified, 4),
        }))

    # recapture runs daily ~10:10-10:50; the timer runs at 11:31 -> ~1h old
    rc_dir = root / "recapture-lapsed"
    rc_dir.mkdir(parents=True, exist_ok=True)
    for realm, advanced, clanless in (("na", 740, 96), ("eu", 1708, 189), ("asia", 527, 93)):
        ts = now - timedelta(hours=1)
        (rc_dir / f"{ts:%Y-%m-%d_%H%M}Z_{realm}.json").write_text(json.dumps(
            healthy_recapture(realm, ts, advanced, clanless)))

    # service health: written by a root-owned timer shortly before the digest.
    sh_dir = root / "service-health"
    sh_dir.mkdir(parents=True, exist_ok=True)
    sh_ts = now - timedelta(hours=1)
    (sh_dir / f"{sh_ts:%Y-%m-%d_%H%M}Z.json").write_text(json.dumps(
        healthy_service_health(sh_ts)))


def healthy_service_health(ts, **overrides):
    """A clean service-health snapshot: nothing failing, journal readable."""
    d = {
        "captured_at": ts.isoformat(),
        "window_hours": 24,
        "since": (ts - timedelta(hours=24)).isoformat(),
        "journal_readable": True,
        "celery_task_failures": [],
        "celery_failure_total": 0,
        "gunicorn_worker_timeouts": 0,
        "gunicorn_error_paths": [],
        "gunicorn_error_total": 0,
        "status": "ok",
    }
    d.update(overrides)
    return d


def healthy_recapture(realm, ts, advanced=740, clanless=96):
    return {
        "captured_at": ts.isoformat(),
        "realm": realm,
        "mode": "apply",
        "band_days": [8, 365],
        "active_days": 7,
        "limit": 30000,
        "partial": False,
        "candidates": 30000,
        "scanned": 30000,
        "wg_calls": 300,
        "chunk_errors": 0,
        "no_data": 9,
        "hidden": 1,
        "still_dormant": 30000 - advanced - 1 - 9,
        "advanced": advanced,
        "yield_frac": round(advanced / 30000, 4),
        "into7d": advanced,
        "into7d_clanned": advanced - clanless,
        "into7d_clanless": clanless,
        "still_lapsed": 0,
        "still_lapsed_clanless": 0,
        "cursor_stamped": 30000,
    }


BASE_ENV = {
    # a path that does not exist, so load_env_file is a no-op in tests
    "OPS_EMAIL_ENV_FILE": "/nonexistent/ops-email.env",
    # heartbeat off by default so the exception-only assertions are not
    # accidentally satisfied by "today happens to be Monday"
    "OPS_EMAIL_HEARTBEAT_DOW": "",
    "OPS_EMAIL_ALWAYS_SEND": "0",
    "ANTHROPIC_API_KEY": "test-key",
}


class OpsAlertTestCase(SimpleTestCase):
    """Base: a healthy tree on disk plus helpers to perturb one thing at a time."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.bench = Path(self._tmp.name)
        self.now = doe.utcnow()
        write_healthy_tree(self.bench, self.now)

    # -- helpers ---------------------------------------------------------
    def gather(self):
        now = doe.utcnow()
        return {
            "observation": doe.gather_observation(str(self.bench), now),
            "crawl_yield": doe.gather_crawl_yield(str(self.bench), now),
            "recapture": doe.gather_recapture(str(self.bench), now),
            "service_health": doe.gather_service_health(str(self.bench), now),
        }

    def rewrite_service_health(self, **overrides):
        d = self.bench / "service-health"
        for f in d.glob("*.json"):
            obj = json.loads(f.read_text())
            obj.update(overrides)
            f.write_text(json.dumps(obj))

    def codes(self):
        return [c["code"] for c in doe.evaluate(self.gather())]

    def rewrite_recapture(self, realm, **overrides):
        d = self.bench / "recapture-lapsed"
        for f in d.glob(f"*_{realm}.json"):
            obj = json.loads(f.read_text())
            for k, v in overrides.items():
                if v is doe_DELETE:
                    obj.pop(k, None)
                else:
                    obj[k] = v
            f.write_text(json.dumps(obj))

    def rewrite_observation(self, scope, **overrides):
        d = self.bench / "observation-floor"
        f = sorted(d.glob("*.json"))[-1]
        obj = json.loads(f.read_text())
        node = obj["totals"] if scope == "totals" else obj["realms"][scope]
        node.update(overrides)
        f.write_text(json.dumps(obj))

    def age_file(self, sub, hours, realm=None):
        """Rewrite a family's newest file(s) to look `hours` old."""
        d = self.bench / sub
        pattern = f"*_{realm}.json" if realm else "*.json"
        ts = doe.utcnow() - timedelta(hours=hours)
        for f in sorted(d.glob(pattern)):
            obj = json.loads(f.read_text())
            obj["captured_at"] = ts.isoformat()
            f.write_text(json.dumps(obj))

    def run_main(self, argv=(), env=None):
        """Run main() with send_email + the Anthropic call mocked.

        Returns (exit_code, send_mock, llm_mock).
        """
        environ = dict(BASE_ENV, BENCH_DIR=str(self.bench))
        environ.update(env or {})
        with mock.patch.dict(os.environ, environ, clear=True), \
             mock.patch.object(doe, "send_email") as send, \
             mock.patch.object(doe, "call_anthropic") as llm, \
             mock.patch.object(sys, "argv", ["daily_ops_email.py", *argv]):
            llm.return_value = {"subject": "[battlestats] ops ALERT stub",
                                "html_body": "<html><body>stub</body></html>"}
            rc = doe.main()
        return rc, send, llm


doe_DELETE = object()


# --------------------------------------------------------------------------- #
# 1. healthy input sends nothing (the whole point)
# --------------------------------------------------------------------------- #
class HealthyInputTests(OpsAlertTestCase):
    def test_healthy_tree_trips_no_conditions(self):
        self.assertEqual(self.codes(), [])

    def test_healthy_input_does_not_send(self):
        rc, send, _llm = self.run_main()
        self.assertEqual(rc, 0)
        send.assert_not_called()

    def test_healthy_input_does_not_even_call_the_llm(self):
        """Verdict-first control flow: an all-clear run must cost nothing.

        Asserting only 'send was not called' would still pass if the script
        synthesized a digest and then threw it away.
        """
        _rc, _send, llm = self.run_main()
        llm.assert_not_called()


# --------------------------------------------------------------------------- #
# 2. each individual condition sends
# --------------------------------------------------------------------------- #
class TrippedConditionTests(OpsAlertTestCase):
    def assert_quiet(self):
        self.assertEqual(self.codes(), [])

    def assert_fires(self, expected_code_prefix):
        codes = self.codes()
        self.assertTrue(
            any(c.startswith(expected_code_prefix) for c in codes),
            f"expected a {expected_code_prefix!r} condition, got {codes}",
        )
        rc, send, _llm = self.run_main()
        self.assertEqual(rc, 0)
        send.assert_called_once()
        subject = send.call_args[0][0]
        self.assertTrue(subject.startswith("[battlestats] ops ALERT"), subject)
        return codes

    # -- shape: the recapture truncation lesson --------------------------
    def test_recapture_partial_true_sends(self):
        """A truncated pass is numerically identical to a healthy one."""
        self.rewrite_recapture("eu", partial=True, scanned=11200)
        self.assert_fires("recapture_partial:eu")

    def test_recapture_partial_field_absent_sends(self):
        """`is not False`, not `is True`: a missing shape field is shape drift.

        Post-fix snapshots all carry an explicit partial=False, so a latest file
        without it means the writer changed or an old code path came back, and
        truncation would once again be undetectable.
        """
        self.rewrite_recapture("asia", partial=doe_DELETE)
        self.assert_fires("recapture_partial_field_absent:asia")

    def test_generic_status_field_sends(self):
        """Generalized: any status/partial/failed_buckets style field is checked."""
        self.rewrite_recapture("na", status="partial")
        self.assert_fires("snapshot_status:recapture-lapsed:na")

    def test_generic_failed_buckets_field_sends(self):
        self.rewrite_recapture("na", failed_buckets=["drift"])
        self.assert_fires("snapshot_failed_buckets:recapture-lapsed:na")

    def test_recapture_detect_mode_sends(self):
        self.rewrite_recapture("na", mode="detect")
        self.assert_fires("recapture_mode:na")

    # -- upstream abort collapses the cluster ----------------------------
    def test_recapture_aborted_collapses_to_one_condition(self):
        """The 2026-08-12 asia shape: one outage reported four times.

        chunk_errors is real; cursor_stamped=0 and the zero component sum are
        CORRECT BY DESIGN (a failed chunk skips the rotation stamp), and
        advanced=0 follows from both. The abort branch must report the cause once
        and suppress all four.
        """
        self.rewrite_recapture(
            "asia", aborted=True, abort_reason="10 consecutive unproductive WG chunks",
            chunk_errors=10, scanned=1000, cursor_stamped=0, advanced=0,
            still_dormant=0, hidden=0, no_data=1000, into7d=0)
        codes = self.assert_fires("recapture_aborted:asia")
        for suppressed in ("recapture_chunk_errors:asia",
                           "recapture_high_no_data:asia",
                           "recapture_cursor_stalled:asia",
                           "recapture_component_mismatch:asia",
                           "recapture_no_returners:asia",
                           "recapture_scanned_zero:asia",
                           "recapture_shape:asia"):
            self.assertNotIn(suppressed, codes,
                             f"{suppressed} is a consequence of the abort, not a "
                             f"separate fault")

    def test_recapture_aborted_still_reports_staleness(self):
        """Liveness outranks the abort: a sweep that aborted AND stopped running
        must not hide behind a permanent 'aborted'."""
        self.rewrite_recapture("asia", aborted=True, abort_reason="x",
                               captured_at="2026-01-01T00:00:00")
        codes = self.codes()
        self.assertIn("snapshot_stale:recapture-lapsed:asia", codes)
        self.assertIn("recapture_aborted:asia", codes)

    def test_recapture_aborted_false_is_quiet(self):
        """The healthy path: an explicit aborted=False changes nothing."""
        self.rewrite_recapture("na", aborted=False, abort_reason=None)
        self.assertEqual(self.codes(), [])

    def test_recapture_chunk_errors_send(self):
        self.rewrite_recapture("eu", chunk_errors=4)
        self.assert_fires("recapture_chunk_errors:eu")

    def test_recapture_high_no_data_sends(self):
        self.rewrite_recapture("eu", no_data=900, still_dormant=30000 - 1708 - 1 - 900)
        self.assert_fires("recapture_high_no_data:eu")

    def test_recapture_zero_returners_sends(self):
        self.rewrite_recapture("asia", advanced=0, into7d=0, into7d_clanned=0,
                               into7d_clanless=0, still_dormant=30000 - 1 - 9)
        self.assert_fires("recapture_no_returners:asia")

    def test_recapture_component_mismatch_sends(self):
        """Counts that do not add up mean the snapshot is not describing itself."""
        self.rewrite_recapture("na", still_dormant=12)
        self.assert_fires("recapture_component_mismatch:na")

    def test_recapture_cursor_not_advancing_sends(self):
        self.rewrite_recapture("eu", cursor_stamped=0)
        self.assert_fires("recapture_cursor_stalled:eu")

    # -- staleness / absence ---------------------------------------------
    def test_recapture_missing_realm_sends(self):
        for f in (self.bench / "recapture-lapsed").glob("*_asia.json"):
            f.unlink()
        self.assert_fires("realm_snapshot_missing:recapture-lapsed:asia")

    def test_recapture_stale_beyond_26h_sends(self):
        """The 2026-08-06 incident signature: EU/ASIA silently stopped writing."""
        self.age_file("recapture-lapsed", 26.5, realm="eu")
        self.assert_fires("snapshot_stale:recapture-lapsed:eu")

    def test_recapture_single_missed_run_is_quiet_by_design(self):
        """Widened 24 -> 26h on 2026-10-08: a late-but-healthy sweep (started
        after ~11:15 UTC) must not false-fire. A single missed run (25.4h) is
        accepted as silent until the next day's fresh run."""
        self.age_file("recapture-lapsed", 25.4, realm="eu")
        self.assertEqual(self.codes(), [])

    def test_recapture_one_day_old_but_inside_the_window_is_quiet(self):
        """23h is still same-cadence; only a genuinely missed run should fire."""
        self.age_file("recapture-lapsed", 23.0, realm="eu")
        self.assertEqual(self.codes(), [])

    def test_observation_snapshot_missing_sends(self):
        for f in (self.bench / "observation-floor").glob("*.json"):
            f.unlink()
        self.assert_fires("snapshots_missing:observation-floor")

    def test_observation_snapshot_stale_sends(self):
        self.age_file("observation-floor", 31.0)
        self.assert_fires("snapshot_stale:observation-floor")

    def test_observation_seven_hours_old_is_quiet(self):
        """The healthy age at run time; must never fire."""
        self.age_file("observation-floor", 7.0)
        self.assertEqual(self.codes(), [])

    def test_crawl_yield_stale_sends(self):
        self.age_file("crawl-yield", 200.0, realm="asia")
        self.assert_fires("snapshot_stale:crawl-yield:asia")

    def test_crawl_yield_five_days_old_is_quiet(self):
        """Passes legitimately run days apart; 131.6h was the worst healthy age."""
        self.age_file("crawl-yield", 130.0)
        self.assertEqual(self.codes(), [])

    def test_crawl_yield_missing_realm_sends(self):
        for f in (self.bench / "crawl-yield").glob("*_na.json"):
            f.unlink()
        self.assert_fires("realm_snapshot_missing:crawl-yield:na")

    def test_crawl_yield_bucket_mismatch_sends(self):
        d = self.bench / "crawl-yield"
        f = sorted(d.glob("*_eu.json"))[-1]
        obj = json.loads(f.read_text())
        obj["buckets"]["still_dormant"] += 5000
        f.write_text(json.dumps(obj))
        self.assert_fires("crawl_bucket_mismatch:eu")

    # -- per-realm classified floor -------------------------------------
    # Realms differ in size by 1.8x (asia ~260k classified vs eu ~473k), so one
    # global floor is necessarily loose for the largest realm. The old global
    # 150,000 tolerated a 68% coverage loss on eu and silently absorbed two
    # genuinely partial passes: na 2026-08-10 (93,353, the WG outage) and eu
    # 2026-07-17 (336,000). Floors are now per realm, at ~91% of each realm's
    # observed steady-state minimum.

    def set_classified(self, realm, classified):
        """Rewrite a realm's newest pass to `classified`, keeping buckets summed
        so crawl_bucket_mismatch can't fire and confound the assertion."""
        d = self.bench / "crawl-yield"
        f = sorted(d.glob(f"*_{realm}.json"))[-1]
        obj = json.loads(f.read_text())
        obj["players_classified"] = classified
        b = obj["buckets"]
        b["still_dormant"] = classified - sum(
            v for k, v in b.items() if k != "still_dormant")
        f.write_text(json.dumps(obj))

    def test_eu_partial_pass_the_old_global_floor_missed_now_fires(self):
        # The real 2026-07-17 eu pass: 336,000 of a ~473k realm — 71% coverage,
        # comfortably above the old 150,000 global, so it never alerted.
        self.set_classified("eu", 336000)
        self.assert_fires("crawl_low_classified:eu")

    def test_a_healthy_asia_sized_pass_is_partial_for_eu(self):
        # 260,000 is a normal asia pass and a 45%-loss eu pass. One global floor
        # cannot tell those apart; per-realm floors must.
        self.set_classified("asia", 260000)
        self.assert_quiet()
        self.set_classified("eu", 260000)
        self.assert_fires("crawl_low_classified:eu")

    def test_each_realm_is_quiet_just_above_and_fires_just_below(self):
        for realm in ("na", "eu", "asia"):
            floor = doe.thr_realm("crawl_classified_min", realm)
            with self.subTest(realm=realm, floor=floor):
                write_healthy_tree(self.bench, self.now)
                self.set_classified(realm, int(floor) + 1)
                self.assert_quiet()
                self.set_classified(realm, int(floor) - 1)
                self.assert_fires(f"crawl_low_classified:{realm}")

    def test_floors_are_ordered_by_realm_size(self):
        # Guards the calibration itself: eu is the largest realm and asia the
        # smallest, so a floor set from the wrong realm's band is caught here.
        asia = doe.thr_realm("crawl_classified_min", "asia")
        na = doe.thr_realm("crawl_classified_min", "na")
        eu = doe.thr_realm("crawl_classified_min", "eu")
        self.assertLess(asia, na)
        self.assertLess(na, eu)

    def test_per_realm_env_override_wins(self):
        with patch.dict(os.environ,
                        {"OPS_ALERT_CRAWL_CLASSIFIED_MIN_EU": "100000"}):
            self.set_classified("eu", 336000)
            self.assert_quiet()

    def test_unknown_realm_falls_back_to_the_global_floor(self):
        self.assertEqual(doe.thr_realm("crawl_classified_min", "zz"),
                         doe.thr("crawl_classified_min"))

    def test_crawl_yield_collapse_sends(self):
        d = self.bench / "crawl-yield"
        f = sorted(d.glob("*_na.json"))[-1]
        obj = json.loads(f.read_text())
        obj["buckets"]["discovered_active"] = 10
        obj["buckets"]["reactivated"] = 20
        obj["yield_total"] = 30
        obj["buckets"]["still_dormant"] = (
            obj["players_classified"] - 10 - 20
            - obj["buckets"]["discovered_dormant"] - obj["buckets"]["refreshed_active"]
        )
        f.write_text(json.dumps(obj))
        self.assert_fires("crawl_no_yield:na")

    # -- unreadable ------------------------------------------------------
    def test_unreadable_snapshot_sends(self):
        """A corrupt newest file used to vanish silently and read as 'fine'."""
        (self.bench / "observation-floor" / "9999-01-01_0430Z.json").write_text("{ not json")
        self.assert_fires("snapshot_unreadable:observation-floor")

    # -- observation numeric backstops -----------------------------------
    def test_observation_coverage_collapse_sends(self):
        self.rewrite_observation("totals", coverage_ratio_vs_7d=0.05, distinct_productive=10000)
        self.assert_fires("obs_low_coverage")

    def test_observation_per_realm_coverage_collapse_sends(self):
        self.rewrite_observation("asia", coverage_ratio_vs_7d=0.02, distinct_productive=1200)
        self.assert_fires("obs_low_coverage:asia")

    def test_observation_bulk_floor_collapse_sends(self):
        self.rewrite_observation("totals", obs_bulk_floor=900)
        self.assert_fires("obs_low_bulk_floor")

    def test_observation_never_observed_spike_sends(self):
        self.rewrite_observation("totals", never_observed=47601)
        self.assert_fires("obs_high_never_observed")

    def test_stale_wall_alone_is_not_an_alert(self):
        """stale_over_24h is the by-design change-gate non-mover wall.

        Alerting on it alone would cry regression on a healthy day, which is
        precisely what the /observation skill forbids.
        """
        self.rewrite_observation("totals", stale_over_24h=182000)
        self.assertEqual(self.codes(), [])

    def test_stale_wall_with_capture_drop_is_an_alert(self):
        self.rewrite_observation("totals", stale_over_24h=182000, distinct_productive=41000)
        codes = self.assert_fires("obs_stale_wall_with_capture_drop")
        self.assertIn("obs_stale_wall_with_capture_drop", codes)


# --------------------------------------------------------------------------- #
# 3. the LLM never gates, and never silences
# --------------------------------------------------------------------------- #
class LLMBoundaryTests(OpsAlertTestCase):
    def test_alert_path_uses_the_alert_system_prompt(self):
        self.rewrite_recapture("eu", partial=True, scanned=11200)
        _rc, send, llm = self.run_main()
        send.assert_called_once()
        llm.assert_called_once()
        kwargs = llm.call_args.kwargs
        self.assertEqual(kwargs.get("system"), doe.ALERT_SYSTEM_PROMPT)
        payload = llm.call_args.args[2]
        self.assertTrue(payload["tripped_conditions"])

    def test_llm_failure_still_sends_with_a_condition_naming_subject(self):
        """An Anthropic outage must not downgrade an alert into a 'digest'."""
        self.rewrite_recapture("eu", partial=True, scanned=11200)
        environ = dict(BASE_ENV, BENCH_DIR=str(self.bench))
        with mock.patch.dict(os.environ, environ, clear=True), \
             mock.patch.object(doe, "send_email") as send, \
             mock.patch.object(doe, "call_anthropic", side_effect=RuntimeError("api down")), \
             mock.patch.object(sys, "argv", ["daily_ops_email.py"]):
            rc = doe.main()
        self.assertEqual(rc, 0)
        send.assert_called_once()
        subject = send.call_args[0][0]
        self.assertTrue(subject.startswith("[battlestats] ops ALERT"), subject)
        self.assertIn("recapture_partial", subject)
        self.assertIn("TRUNCATED", send.call_args[0][1])

    def test_no_llm_flag_still_sends_the_alert(self):
        self.rewrite_recapture("eu", partial=True, scanned=11200)
        rc, send, llm = self.run_main(argv=["--no-llm"])
        self.assertEqual(rc, 0)
        llm.assert_not_called()
        send.assert_called_once()
        self.assertTrue(send.call_args[0][0].startswith("[battlestats] ops ALERT"))

    def test_subject_is_rewritten_if_the_model_ignores_the_instruction(self):
        self.rewrite_recapture("eu", partial=True, scanned=11200)
        environ = dict(BASE_ENV, BENCH_DIR=str(self.bench))
        with mock.patch.dict(os.environ, environ, clear=True), \
             mock.patch.object(doe, "send_email") as send, \
             mock.patch.object(doe, "call_anthropic") as llm, \
             mock.patch.object(sys, "argv", ["daily_ops_email.py"]):
            llm.return_value = {"subject": "[battlestats] daily ops digest",
                                "html_body": "<html><body>x</body></html>"}
            doe.main()
        self.assertTrue(send.call_args[0][0].startswith("[battlestats] ops ALERT"))


# --------------------------------------------------------------------------- #
# 4. liveness: the kill switch and the heartbeat
# --------------------------------------------------------------------------- #
class LivenessTests(OpsAlertTestCase):
    def test_always_send_kill_switch_restores_the_daily_digest(self):
        rc, send, llm = self.run_main(env={"OPS_EMAIL_ALWAYS_SEND": "1"})
        self.assertEqual(rc, 0)
        send.assert_called_once()
        # digest prompt, not the alert prompt: nothing tripped
        self.assertIsNone(llm.call_args.kwargs.get("system"))

    def test_force_flag_sends_on_a_clear_day(self):
        _rc, send, _llm = self.run_main(argv=["--force"])
        send.assert_called_once()

    def test_heartbeat_sends_on_its_configured_day(self):
        today = doe.DOW_NAMES[doe.utcnow().weekday()]
        _rc, send, _llm = self.run_main(env={"OPS_EMAIL_HEARTBEAT_DOW": today})
        send.assert_called_once()
        self.assertIn("heartbeat", send.call_args[0][0].lower() + send.call_args[0][1].lower())

    def test_heartbeat_only_send_does_not_call_the_model(self):
        """The transport proof must not depend on, or pay for, the Anthropic API."""
        today = doe.DOW_NAMES[doe.utcnow().weekday()]
        rc, send, llm = self.run_main(env={"OPS_EMAIL_HEARTBEAT_DOW": today})
        self.assertEqual(rc, 0)
        llm.assert_not_called()
        send.assert_called_once()
        subject, html = send.call_args[0][0], send.call_args[0][1]
        self.assertEqual(subject, "[battlestats] ops heartbeat: all clear")
        self.assertIn("deterministic table", html)
        self.assertIn("weekly heartbeat", html)
        # By design, not a failure: the fallback banner must not appear.
        self.assertNotIn("LLM synthesis failed", html)

    def test_heartbeat_day_dry_run_does_not_call_the_model_either(self):
        """A dry run shows what would be sent, so it follows the same rule."""
        today = doe.DOW_NAMES[doe.utcnow().weekday()]
        _rc, send, llm = self.run_main(argv=["--dry-run"],
                                       env={"OPS_EMAIL_HEARTBEAT_DOW": today})
        llm.assert_not_called()
        send.assert_not_called()

    def test_an_alert_on_the_heartbeat_day_still_calls_the_model(self):
        """The heartbeat never downgrades an alert: the alert write-up runs."""
        self.rewrite_recapture("eu", partial=True, scanned=11200)
        today = doe.DOW_NAMES[doe.utcnow().weekday()]
        _rc, send, llm = self.run_main(env={"OPS_EMAIL_HEARTBEAT_DOW": today})
        llm.assert_called_once()
        self.assertEqual(llm.call_args.kwargs.get("system"), doe.ALERT_SYSTEM_PROMPT)
        self.assertTrue(send.call_args[0][0].startswith("[battlestats] ops ALERT"))

    def test_an_alert_send_calls_the_model(self):
        self.rewrite_recapture("eu", partial=True, scanned=11200)
        _rc, send, llm = self.run_main()
        llm.assert_called_once()
        self.assertEqual(llm.call_args.kwargs.get("system"), doe.ALERT_SYSTEM_PROMPT)
        send.assert_called_once()

    def test_a_forced_send_on_the_heartbeat_day_still_writes_the_digest(self):
        """--force and OPS_EMAIL_ALWAYS_SEND ask for the digest; the day does not change that."""
        today = doe.DOW_NAMES[doe.utcnow().weekday()]
        for argv, env in ((["--force"], {}), ([], {"OPS_EMAIL_ALWAYS_SEND": "1"})):
            _rc, send, llm = self.run_main(
                argv=argv, env=dict(env, OPS_EMAIL_HEARTBEAT_DOW=today))
            llm.assert_called_once()
            self.assertIsNone(llm.call_args.kwargs.get("system"))
            self.assertNotEqual(send.call_args[0][0], "[battlestats] ops heartbeat: all clear")

    def test_heartbeat_is_quiet_on_other_days(self):
        other = doe.DOW_NAMES[(doe.utcnow().weekday() + 3) % 7]
        _rc, send, _llm = self.run_main(env={"OPS_EMAIL_HEARTBEAT_DOW": other})
        send.assert_not_called()

    def test_dry_run_never_sends(self):
        self.rewrite_recapture("eu", partial=True, scanned=11200)
        rc, send, _llm = self.run_main(argv=["--dry-run"])
        self.assertEqual(rc, 0)
        send.assert_not_called()


# --------------------------------------------------------------------------- #
# 5. thresholds are named, deterministic and overridable
# --------------------------------------------------------------------------- #
class ThresholdTests(SimpleTestCase):
    def test_env_override_wins(self):
        with mock.patch.dict(os.environ, {"OPS_ALERT_OBS_MAX_AGE_HOURS": "48"}, clear=True):
            self.assertEqual(doe.thr("obs_max_age_hours"), 48.0)

    def test_unparseable_env_falls_back_to_the_default(self):
        with mock.patch.dict(os.environ, {"OPS_ALERT_OBS_MAX_AGE_HOURS": "soon"}, clear=True):
            self.assertEqual(doe.thr("obs_max_age_hours"),
                             doe.DEFAULT_THRESHOLDS["obs_max_age_hours"])

    def test_every_threshold_referenced_by_evaluate_has_a_default(self):
        """Guards against a typo'd threshold name reaching production as a KeyError."""
        for name in doe.DEFAULT_THRESHOLDS:
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertIsInstance(doe.thr(name), float)

    def test_evaluate_is_pure_and_deterministic(self):
        # One condition per snapshot family, all four of them: an empty tree must
        # report every family as missing rather than silently covering three.
        data = {"observation": {"available": 0, "unreadable": []},
                "crawl_yield": {"available": 0, "unreadable": []},
                "recapture": {"available": 0, "unreadable": []},
                "service_health": {"available": 0, "unreadable": []}}
        first = doe.evaluate(data)
        second = doe.evaluate(data)
        self.assertEqual([c["code"] for c in first], [c["code"] for c in second])
        self.assertEqual(len(first), 4)
        self.assertIn("snapshots_missing:service-health", [c["code"] for c in first])

    def test_alert_subject_fits_the_header_limit(self):
        conds = [{"code": f"some_quite_long_condition_code:{i}", "detail": "x"} for i in range(12)]
        self.assertLessEqual(len(doe.alert_subject(conds)), 78)
        self.assertTrue(doe.alert_subject(conds).startswith("[battlestats] ops ALERT"))


# --------------------------------------------------------------------------- #
# 6. fail-loud is untouched: exception-only applies to the DIGEST, not to errors
# --------------------------------------------------------------------------- #
class FailLoudTests(SimpleTestCase):
    def test_exception_still_mails_the_failure_notice(self):
        """The one path exception-only mode must never quiet."""
        with mock.patch.dict(os.environ, dict(BASE_ENV), clear=True), \
             mock.patch.object(doe, "send_email") as send, \
             mock.patch.object(doe, "main", side_effect=ZeroDivisionError("boom")), \
             mock.patch.object(sys, "argv", ["daily_ops_email.py"]):
            # mirror the module's __main__ guard
            try:
                raise_rc = doe.main()
            except Exception:
                import traceback
                tb = traceback.format_exc()
                doe.send_email("[battlestats] daily ops email FAILED",
                               "<html><body><pre>" + doe._esc(tb) + "</pre></body></html>", tb)
                raise_rc = 1
        self.assertEqual(raise_rc, 1)
        send.assert_called_once()
        self.assertEqual(send.call_args[0][0], "[battlestats] daily ops email FAILED")
        self.assertIn("ZeroDivisionError", send.call_args[0][1])

    def test_main_guard_source_is_unconditional(self):
        """Structural check on the real __main__ block, not a re-implementation.

        The test above exercises the behaviour with a stub; this one asserts the
        shipped guard still sends without consulting any verdict or kill switch,
        so a future refactor cannot quietly fold the failure mail into the
        exception-only branch.
        """
        src = _SCRIPT.read_text()
        tail = src.split('if __name__ == "__main__":')[1]
        self.assertIn("daily ops email FAILED", tail)
        self.assertIn("send_email(", tail)
        self.assertNotIn("OPS_EMAIL_ALWAYS_SEND", tail)
        self.assertNotIn("evaluate(", tail)


class AnthropicCallShapeTests(SimpleTestCase):
    """The alert write-up call is configured for a short deterministic render."""

    def test_thinking_is_bounded_by_low_effort_not_disabled(self):
        """The budget problem is solved with effort + headroom, not thinking-off.

        max_tokens caps thinking AND response text together, which is what
        produced the original empty-text/stop_reason=max_tokens failure.
        Disabling thinking fixes that but buys a worse bug: with thinking off,
        Opus 5 can leak <thinking> tags into the visible response, and this
        response is parsed as JSON, so a leaked tag breaks the parse outright.
        """
        source = _SCRIPT.read_text().replace("'", '"')
        self.assertIn('"output_config": {"effort": "low"}', source)
        self.assertNotIn('"thinking": {"type": "disabled"}', source)

    def test_a_refusal_is_named_rather_than_surfacing_as_a_parse_error(self):
        """A classifier decline is HTTP 200 with no content, not an exception."""
        source = _SCRIPT.read_text()
        self.assertIn('payload.get("stop_reason") == "refusal"', source)
        self.assertIn("model declined the request", source)


class ServiceHealthConditions(OpsAlertTestCase):
    """F4, 2026-08-26: the digest used to be blind to the whole services axis.

    It evaluated only observation-floor, crawl-yield and recapture-lapsed, all of
    which are benchmark JSON. So on 2026-08-25 it reported "all clear" while
    `roll_up_player_daily_ship_stats_task` had failed five consecutive nights and
    `/api/landing/player-suggestions` was returning 500s. Nothing was
    mis-thresholded; the signal simply had no way in. These tests pin the way in.
    """

    def test_healthy_tree_still_trips_nothing(self):
        """The new family must not make a healthy tree noisy."""
        self.assertEqual(self.codes(), [])

    def test_a_task_that_never_succeeds_trips(self):
        """One failure a night, zero successes: the rollup's exact signature."""
        self.rewrite_service_health(
            celery_task_failures=[{
                "unit": "battlestats-celery-background",
                "task": "warships.tasks.roll_up_player_daily_ship_stats_task",
                "exception": "SoftTimeLimitExceeded",
                "count": 1,
                "succeeded": 0,
            }],
            celery_failure_total=1,
        )
        self.assertIn(
            "celery_task_failing:warships.tasks.roll_up_player_daily_ship_stats_task",
            self.codes(),
        )

    def test_a_flaky_but_completing_task_does_not_trip(self):
        """The calibration that keeps this digest worth reading.

        Measured on a real 24h window, alerting on "failed at least once" trips 8
        conditions, 7 of them cache warmers that fail a fraction of their runs
        and fall back to the durable :published copy by design. A digest that
        fires every morning stops being read, and then the morning that matters
        looks like all the others. Failing *some* runs is not the same as failing
        *every* run, and only the latter is a broken task.
        """
        self.rewrite_service_health(
            celery_task_failures=[{
                "unit": "battlestats-celery-background",
                "task": "warships.tasks.warm_player_ranked_wr_battles_correlation_task",
                "exception": "SoftTimeLimitExceeded",
                "count": 10,
                "succeeded": 42,
            }],
            celery_failure_total=10,
        )
        self.assertEqual(self.codes(), [])

    def test_a_missing_success_count_still_trips(self):
        """An older writer omits `succeeded`; unknown must not read as healthy."""
        self.rewrite_service_health(
            celery_task_failures=[{
                "unit": "battlestats-celery-background",
                "task": "warships.tasks.roll_up_player_daily_ship_stats_task",
                "exception": "SoftTimeLimitExceeded",
                "count": 1,
            }],
            celery_failure_total=1,
        )
        self.assertIn(
            "celery_task_failing:warships.tasks.roll_up_player_daily_ship_stats_task",
            self.codes(),
        )

    def test_the_alert_names_the_task_and_the_exception(self):
        """A bare count sends the reader back to a journal they cannot read."""
        self.rewrite_service_health(
            celery_task_failures=[{
                "unit": "battlestats-celery-background",
                "task": "warships.tasks.roll_up_player_daily_ship_stats_task",
                "exception": "SoftTimeLimitExceeded",
                "count": 5,
                "succeeded": 0,
            }],
            celery_failure_total=5,
        )
        detail = " ".join(c["detail"] for c in doe.evaluate(self.gather()))
        self.assertIn("roll_up_player_daily_ship_stats_task", detail)
        self.assertIn("SoftTimeLimitExceeded", detail)
        self.assertIn("battlestats-celery-background", detail)

    def test_a_long_cycle_task_with_zero_successes_does_not_trip(self):
        """The clan crawl's unit of work is larger than the alert's window.

        crawl_all_clans_task's own comment puts a full pass at ~12-18h against a
        20700s (5h45m) per-dispatch soft limit, so truncation is the designed
        steady state and a pass completes every 2-4 dispatches. Measured on the
        droplet journal for the 7 days to 2026-08-28: three completions, so on 4
        of those 7 days the window held zero successes and at least one
        SoftTimeLimitExceeded -- the exact shape this rule alerts on. Firing four
        mornings in seven is how a digest stops being read.
        """
        self.rewrite_service_health(
            celery_task_failures=[{
                "unit": "battlestats-celery-crawls",
                "task": "warships.tasks.crawl_all_clans_task",
                "exception": "SoftTimeLimitExceeded",
                "count": 4,
                "succeeded": 0,
            }],
            celery_failure_total=4,
        )
        self.assertEqual(self.codes(), [])

    def test_the_exemption_does_not_leak_to_other_tasks(self):
        """Narrow by name, not by exception or by unit.

        SoftTimeLimitExceeded on the crawls unit is not itself the exemption;
        only the named long-cycle task is.
        """
        self.rewrite_service_health(
            celery_task_failures=[{
                "unit": "battlestats-celery-crawls",
                "task": "warships.tasks.crawl_clan_batch_task",
                "exception": "SoftTimeLimitExceeded",
                "count": 4,
                "succeeded": 0,
            }],
            celery_failure_total=4,
        )
        self.assertIn(
            "celery_task_failing:warships.tasks.crawl_clan_batch_task",
            self.codes(),
        )

    def test_the_long_cycle_list_names_the_crawl(self):
        """Pins the membership itself.

        An exemption is a silence. If a future edit widens this set, the widening
        should have to change a test that says so out loud.
        """
        self.assertEqual(
            doe.LONG_CYCLE_TASKS,
            frozenset({"warships.tasks.crawl_all_clans_task"}),
        )

    def test_gunicorn_worker_timeouts_trip(self):
        self.rewrite_service_health(gunicorn_worker_timeouts=5)
        self.assertIn("gunicorn_worker_timeouts", self.codes())

    def test_gunicorn_error_paths_are_named(self):
        self.rewrite_service_health(
            gunicorn_worker_timeouts=5,
            gunicorn_error_paths=[
                {"path": "/api/landing/player-suggestions", "count": 5}],
            gunicorn_error_total=5,
        )
        detail = " ".join(c["detail"] for c in doe.evaluate(self.gather()))
        self.assertIn("/api/landing/player-suggestions", detail)

    def test_missing_family_is_itself_a_condition(self):
        """Absent evidence must never read as good news."""
        for f in (self.bench / "service-health").glob("*.json"):
            f.unlink()
        self.assertIn("snapshots_missing:service-health", self.codes())

    def test_stale_snapshot_trips(self):
        stale = self.now - timedelta(hours=40)
        for f in (self.bench / "service-health").glob("*.json"):
            f.unlink()
        (self.bench / "service-health" / f"{stale:%Y-%m-%d_%H%M}Z.json").write_text(
            json.dumps(healthy_service_health(stale)))
        self.assertIn("snapshot_stale:service-health", self.codes())

    def test_unreadable_snapshot_trips(self):
        (self.bench / "service-health" / "9999-01-01_0000Z.json").write_text("{ not json")
        self.assertIn("snapshot_unreadable:service-health", self.codes())

    def test_an_unreadable_journal_trips_instead_of_reading_as_healthy(self):
        """Zero failures from a journal we could not open is not zero failures.

        This is the exact trap F4 exists to close. The writer runs as root, but
        if it ever loses journal access every count arrives as 0, which would
        otherwise render as a clean bill of health.
        """
        self.rewrite_service_health(journal_readable=False)
        self.assertIn("journal_unreadable:service-health", self.codes())


class CeleryPerRealmConditions(OpsAlertTestCase):
    """Per-realm striped tasks: one realm failing every run must not hide.

    The existing Celery axis keys on the TASK NAME and alerts only on zero
    successes, so a task striped across three realms that fails `eu` every
    single run reads as 2-of-3 healthy and says nothing. Measured on the live
    snapshot 2026-08-26_1100Z: `warm_player_correlations_task` sat at 1 failure
    / 2 successes -- 66.7% -- which is exactly that signature.

    A failure-RATE threshold cannot fix it: any rate low enough to stay quiet on
    genuinely flaky warmers is also quiet on 66.7%. Realm is the right axis, and
    the writer supplies it as per-realm SUCCESS counts, because attributing
    failures would mean parsing exception paths (where the 2026-08-26 F2 trap
    lived) while a success is a plain success-path log line.

    Runbook: agents/runbooks/runbook-correlation-warm-budget-and-per-realm-alerting-2026-08-26.md
    """

    TASK = "warships.tasks.warm_player_correlations_task"

    def test_a_realm_that_never_succeeds_trips(self):
        self.rewrite_service_health(
            celery_realm_successes=[
                {"task": self.TASK, "realm": "na", "count": 2},
                {"task": self.TASK, "realm": "asia", "count": 2},
                {"task": self.TASK, "realm": "eu", "count": 0},
            ],
        )
        self.assertIn(f"celery_task_realm_failing:{self.TASK}:eu", self.codes())

    def test_a_realm_absent_from_the_rows_trips(self):
        """Never dispatched and always failed both read as zero successes.

        That conflation is deliberate: a striped task that silently stopped
        being dispatched for one realm is just as broken as one that fails.
        """
        self.rewrite_service_health(
            celery_realm_successes=[
                {"task": self.TASK, "realm": "na", "count": 2},
                {"task": self.TASK, "realm": "asia", "count": 2},
            ],
        )
        self.assertIn(f"celery_task_realm_failing:{self.TASK}:eu", self.codes())

    def test_all_realms_succeeding_stays_quiet(self):
        self.rewrite_service_health(
            celery_realm_successes=[
                {"task": self.TASK, "realm": r, "count": 1}
                for r in ("na", "eu", "asia")
            ],
        )
        self.assertEqual(self.codes(), [])

    def test_a_task_with_no_successes_anywhere_does_not_double_report(self):
        """The zero-success rule already owns that case; this must not pile on.

        Requiring at least one succeeding realm is what keeps a fully-broken
        task to ONE condition instead of one per realm.
        """
        self.rewrite_service_health(
            celery_realm_successes=[
                {"task": self.TASK, "realm": r, "count": 0}
                for r in ("na", "eu", "asia")
            ],
        )
        self.assertEqual(
            [c for c in self.codes() if c.startswith("celery_task_realm_failing")],
            [])

    def test_an_older_writer_without_the_field_stays_quiet(self):
        """A writer predating this field must not manufacture alerts."""
        self.rewrite_service_health(celery_realm_successes=None)
        self.assertEqual(
            [c for c in self.codes() if c.startswith("celery_task_realm_failing")],
            [])

    def test_rows_for_the_same_realm_on_two_units_are_summed(self):
        """The writer tallies per (unit, task, realm); the evaluator keys on
        (task, realm). Assigning instead of accumulating makes the last row win,
        so a zero from one unit erases a healthy count from another and invents
        an alert. Each task lands on one unit today, which is exactly why this
        needs a fixture rather than trust.
        """
        self.rewrite_service_health(
            celery_realm_successes=[
                {"task": self.TASK, "realm": "na", "count": 1},
                {"task": self.TASK, "realm": "eu", "count": 2},
                # same (task, realm) seen again on a second unit, zero there
                {"task": self.TASK, "realm": "eu", "count": 0},
                {"task": self.TASK, "realm": "asia", "count": 1},
            ],
        )
        self.assertEqual(
            [c for c in self.codes() if c.startswith("celery_task_realm_failing")],
            [], "eu succeeded twice on one unit; a zero row must not erase it")


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
        "content": [{"type": "text", "text": json.dumps(
            {"subject": "[battlestats] ops ALERT x", "html_body": "<html><body>x</body></html>"})}],
        "usage": {"input_tokens": 4012, "output_tokens": 1877,
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
    PACKAGE = {"observation": {"latest": {"totals": {"active_7d": 206904}}},
               "tripped_conditions": [{"code": "c", "detail": "d"}]}

    def call(self, response=None, **kwargs):
        import contextlib
        import io
        sent = {}

        def _urlopen(req, timeout=None):
            sent["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeResponse(response if response is not None else _api_response())

        out = io.StringIO()
        with mock.patch.object(doe.urllib.request, "urlopen", _urlopen), \
             contextlib.redirect_stdout(out):
            result = doe.call_anthropic("claude-opus-5", self.SECRET, self.PACKAGE, **kwargs)
        return result, sent["body"], out.getvalue()

    def test_data_package_is_sent_as_compact_json(self):
        """Indentation is billed input that carries no information."""
        _result, body, _out = self.call()
        content = body["messages"][0]["content"]
        instruction, _, blob = content.partition("\n\n")
        self.assertTrue(instruction)
        self.assertEqual(blob, json.dumps(self.PACKAGE, separators=(",", ":")))
        self.assertNotIn("\n", blob)
        self.assertEqual(json.loads(blob), self.PACKAGE)

    def test_request_shape_is_otherwise_unchanged(self):
        _result, body, _out = self.call(system="S", instruction="I")
        self.assertEqual(body["model"], "claude-opus-5")
        self.assertEqual(body["max_tokens"], 8000)
        self.assertEqual(body["output_config"], {"effort": "low"})
        self.assertEqual(body["system"], "S")
        self.assertTrue(body["messages"][0]["content"].startswith("I\n\n"))
        self.assertNotIn("cache_control", json.dumps(body))

    def test_usage_is_logged_and_the_key_is_not(self):
        result, _body, out = self.call()
        self.assertEqual(result["subject"], "[battlestats] ops ALERT x")
        self.assertIn(
            "[llm] model=claude-opus-5 stop_reason=end_turn input_tokens=4012 "
            "output_tokens=1877 cache_creation_input_tokens=0 cache_read_input_tokens=0",
            out,
        )
        self.assertNotIn(self.SECRET, out)

    def test_a_response_without_usage_still_parses(self):
        for usage in (doe_DELETE, None):
            response = _api_response()
            if usage is doe_DELETE:
                del response["usage"]
            else:
                response["usage"] = None
            result, _body, out = self.call(response=response)
            self.assertEqual(result["html_body"], "<html><body>x</body></html>")
            self.assertIn("input_tokens=0 output_tokens=0", out)

    def test_usage_is_logged_before_a_refusal_raises(self):
        """A declined request is still a billed, accountable call."""
        import contextlib
        import io
        response = _api_response(stop_reason="refusal", content=[])
        out = io.StringIO()
        with mock.patch.object(doe.urllib.request, "urlopen",
                               lambda req, timeout=None: _FakeResponse(response)), \
             contextlib.redirect_stdout(out), \
             self.assertRaises(RuntimeError):
            doe.call_anthropic("claude-opus-5", self.SECRET, self.PACKAGE)
        self.assertIn("[llm] model=claude-opus-5 stop_reason=refusal", out.getvalue())
