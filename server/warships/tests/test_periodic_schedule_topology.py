"""Periodic schedule topology tests.

Pins the contract for `register_periodic_schedules` in `signals.py`:

1. All expected schedule names exist after post_migrate.
2. Per-realm interval-style families are crontab schedules, not
   IntervalSchedules — they're striped per realm via
   `REALM_INTERVAL_OFFSETS` so at most one realm fires at a time.
3. Realm offsets are pairwise distinct.
4. Rolling observation floor fires 4× per day per realm.
5. Retired schedule names get deleted on each registration.

This file addresses the open follow-up from
`agents/runbooks/runbook-periodic-task-topology-2026-04-11.md` (#3) and
catches the most common regressions: forgetting to clear `interval`
when transitioning a row from interval → crontab, and forgetting to
add an old name to `_RETIRED_SCHEDULE_NAMES` after a rename.
"""
from __future__ import annotations

import os
from unittest import mock

from django.apps import apps
from django.test import TestCase
from django_celery_beat.models import PeriodicTask

from warships.models import VALID_REALMS
from warships.signals import (
    REALM_INTERVAL_OFFSETS,
    _realm_crontab_for_cycle,
    register_periodic_schedules,
)


# Schedule families that get a per-realm row with a striped crontab.
# Tuples are (name_prefix, cycle_env_default_minutes).
STRIPED_PER_REALM_FAMILIES = [
    ("landing-page-warmer", 120),
    ("player-distribution-warmer", 360),
    ("player-correlation-warmer", 360),
    ("hot-entity-cache-warmer", 30),
    ("recently-viewed-player-warmer", 10),
    ("incremental-player-refresh", 180),
    ("incremental-ranked-refresh", 120),
    ("hot-players-capture", 1440),
]


class ExpectedScheduleNamesTests(TestCase):
    """Test 1: full expected schedule set exists after post_migrate."""

    def test_all_per_realm_schedules_registered(self):
        # post_migrate runs during the per-test DB build, so the rows
        # exist at the start of the test. We just need to verify they
        # all show up.
        for prefix, _cycle in STRIPED_PER_REALM_FAMILIES:
            for realm in VALID_REALMS:
                name = f"{prefix}-{realm}"
                self.assertTrue(
                    PeriodicTask.objects.filter(name=name).exists(),
                    f"Missing PeriodicTask row: {name}",
                )

    def test_observation_floor_per_realm(self):
        for realm in VALID_REALMS:
            self.assertTrue(
                PeriodicTask.objects.filter(
                    name=f"observation-floor-{realm}").exists(),
                f"Missing observation-floor-{realm}",
            )

    def test_singleton_schedules_present(self):
        for name in (
            "player-enrichment-kickstart",
            "battle-history-daily-rollup",
            "poll-tracked-player-battles",
        ):
            self.assertTrue(
                PeriodicTask.objects.filter(name=name).exists(),
                f"Missing singleton PeriodicTask row: {name}",
            )


class StripedSchedulesAreCrontabTests(TestCase):
    """Test 2: striped families use crontab, not interval."""

    def test_striped_families_use_crontab(self):
        for prefix, _cycle in STRIPED_PER_REALM_FAMILIES:
            for realm in VALID_REALMS:
                name = f"{prefix}-{realm}"
                row = PeriodicTask.objects.get(name=name)
                self.assertIsNotNone(
                    row.crontab,
                    f"{name} should be on a CrontabSchedule",
                )
                self.assertIsNone(
                    row.interval,
                    f"{name} should not also have an IntervalSchedule attached",
                )


class RealmOffsetsDistinctTests(TestCase):
    """Test 3: per-realm crontab offsets are pairwise distinct."""

    def _crontab_signature(self, name):
        row = PeriodicTask.objects.get(name=name)
        return (row.crontab.minute, row.crontab.hour)

    def test_player_refresh_offsets_distinct(self):
        sigs = {
            realm: self._crontab_signature(f"incremental-player-refresh-{realm}")
            for realm in VALID_REALMS
        }
        self.assertEqual(
            len(set(sigs.values())), len(VALID_REALMS),
            f"Player refresh schedules collide: {sigs}",
        )

    def test_observation_floor_offsets_distinct(self):
        sigs = {
            realm: self._crontab_signature(f"observation-floor-{realm}")
            for realm in VALID_REALMS
        }
        self.assertEqual(
            len(set(sigs.values())), len(VALID_REALMS),
            f"Observation floor schedules collide: {sigs}",
        )


class ObservationFloorRunsFourTimesADayTests(TestCase):
    """Test 4: rolling floor fires the cycle's count per day per realm.

    NOTE: tests register schedules with the *code default* cadence
    (BATTLE_OBSERVATION_FLOOR_CYCLE_MINUTES unset → 360 = 6h → 4×/day). Prod
    runs 180 (3h → 8×/day). The 180min ASIA-wrap regression is therefore NOT
    exercised here — `RealmCrontabHelperTests.test_180min_floor_asia_wraps_to_8`
    guards that directly against the helper.
    """

    def test_observation_floor_fires_four_times_per_day(self):
        for realm in VALID_REALMS:
            row = PeriodicTask.objects.get(name=f"observation-floor-{realm}")
            hour_segments = row.crontab.hour.split(",")
            self.assertEqual(
                len(hour_segments), 4,
                f"observation-floor-{realm} should fire 4×/day at the 360min "
                f"default, got hour='{row.crontab.hour}'",
            )


class ShipSnapshotFiresTwiceADayTests(TestCase):
    """The rolling T10 top-ship-player snapshot recomputes every 12h, i.e.
    twice per day per realm at striped, pairwise-distinct hours."""

    def test_ship_snapshot_fires_twice_per_day(self):
        for realm in VALID_REALMS:
            row = PeriodicTask.objects.get(name=f"ship-top-player-snapshot-{realm}")
            hour_segments = row.crontab.hour.split(",")
            self.assertEqual(
                len(hour_segments), 2,
                f"ship-top-player-snapshot-{realm} should fire 2×/day (every "
                f"12h), got hour='{row.crontab.hour}'",
            )
            # The two firings are exactly 12h apart.
            hours = sorted(int(h) for h in hour_segments)
            self.assertEqual(
                (hours[1] - hours[0]) % 24, 12,
                f"ship-top-player-snapshot-{realm} firings should be 12h apart, "
                f"got hour='{row.crontab.hour}'",
            )

    def test_ship_snapshot_firings_dont_collide_across_realms(self):
        # Compare the *set* of firing hours, not the raw "h1,h2" string: under
        # a 12h period eu (offset 0) and asia (offset 12) would fire at the same
        # two wall-clock hours while their hour strings ("2,14" vs "14,2") differ.
        # A no-two-realms-share-a-firing-hour check is what actually guards the
        # "three ~12s aggregations off each other" property.
        all_hours = []
        for realm in VALID_REALMS:
            hour = PeriodicTask.objects.get(
                name=f"ship-top-player-snapshot-{realm}").crontab.hour
            all_hours.extend(int(h) for h in hour.split(","))
        self.assertEqual(
            len(all_hours), len(set(all_hours)),
            f"Ship snapshot firings collide across realms (hours={sorted(all_hours)})",
        )


class RetirementListPrunesOldRowsTests(TestCase):
    """Test 5: re-running registration deletes retired names."""

    def test_daily_observation_floor_legacy_name_is_pruned(self):
        # Pre-create the legacy row that the 2026-05-09 promotion retired.
        # `register_periodic_schedules` deletes any name in
        # `_RETIRED_SCHEDULE_NAMES` at the top of its run.
        from django_celery_beat.models import IntervalSchedule
        legacy_schedule, _ = IntervalSchedule.objects.get_or_create(
            every=1440, period=IntervalSchedule.MINUTES,
        )
        PeriodicTask.objects.create(
            name="daily-observation-floor-na",
            task="warships.tasks.ensure_daily_battle_observations_task",
            interval=legacy_schedule,
        )
        self.assertTrue(
            PeriodicTask.objects.filter(
                name="daily-observation-floor-na").exists(),
        )

        register_periodic_schedules(sender=apps.get_app_config("warships"))

        self.assertFalse(
            PeriodicTask.objects.filter(
                name="daily-observation-floor-na").exists(),
            "Retired name daily-observation-floor-na should have been deleted",
        )


class RealmCrontabHelperTests(TestCase):
    """Direct unit coverage of the striping helper."""

    def test_180min_cycle_3_realms(self):
        # NA hour=0,3,6,…; EU hour=1,4,7,…; ASIA hour=2,5,8,…
        na_min, na_hr = _realm_crontab_for_cycle("na", 180)
        eu_min, eu_hr = _realm_crontab_for_cycle("eu", 180)
        as_min, as_hr = _realm_crontab_for_cycle("asia", 180)

        self.assertEqual(na_min, "0")
        self.assertEqual(na_hr, "0,3,6,9,12,15,18,21")
        self.assertEqual(eu_min, "0")
        self.assertEqual(eu_hr, "1,4,7,10,13,16,19,22")
        self.assertEqual(as_min, "0")
        self.assertEqual(as_hr, "2,5,8,11,14,17,20,23")

    def test_30min_cycle_uses_minute_list_with_wildcard_hour(self):
        # NA fires every 30min at :00 :30; EU at :10 :40; ASIA at :20 :50
        na_min, na_hr = _realm_crontab_for_cycle("na", 30)
        eu_min, eu_hr = _realm_crontab_for_cycle("eu", 30)
        as_min, as_hr = _realm_crontab_for_cycle("asia", 30)

        self.assertEqual(na_min, "0,30")
        self.assertEqual(eu_min, "10,40")
        self.assertEqual(as_min, "20,50")
        for hr in (na_hr, eu_hr, as_hr):
            self.assertEqual(hr, "*")

    def test_120min_cycle_3_realms(self):
        # stride = 40min: NA min=0 hr even, EU min=40 hr even, ASIA min=20 hr odd
        na_min, na_hr = _realm_crontab_for_cycle("na", 120)
        eu_min, eu_hr = _realm_crontab_for_cycle("eu", 120)
        as_min, as_hr = _realm_crontab_for_cycle("asia", 120)

        self.assertEqual(na_min, "0")
        self.assertEqual(na_hr, "0,2,4,6,8,10,12,14,16,18,20,22")
        self.assertEqual(eu_min, "40")
        self.assertEqual(eu_hr, "0,2,4,6,8,10,12,14,16,18,20,22")
        self.assertEqual(as_min, "20")
        self.assertEqual(as_hr, "1,3,5,7,9,11,13,15,17,19,21,23")

    def test_offsets_dictionary_covers_all_realms(self):
        # Catches forgetting to register a new realm in the offset map.
        for realm in VALID_REALMS:
            self.assertIn(realm, REALM_INTERVAL_OFFSETS,
                          f"REALM_INTERVAL_OFFSETS missing realm {realm}")

    def test_180min_floor_asia_wraps_to_8(self):
        # Regression guard for the prod (180min) floor cadence: before the
        # mod-1440 wrap fix, the ASIA-offset start (base_minute=75 + 120 = 195)
        # truncated at `while t < 1440`, dropping the 8th fire and leaving a 6h
        # hole (21:15→03:15). All three realms must now fire 8 evenly-spaced
        # times. See agents/runbooks/analysis-feed-schedule-optimization-2026-06-08.md (F2).
        expected = {
            "na": ("15", "1,4,7,10,13,16,19,22"),
            "eu": ("15", "2,5,8,11,14,17,20,23"),
            "asia": ("15", "0,3,6,9,12,15,18,21"),
        }
        for realm, (exp_min, exp_hr) in expected.items():
            minute_str, hour_str = _realm_crontab_for_cycle(
                realm, 180, base_minute=75)
            self.assertEqual(minute_str, exp_min, f"{realm} minute")
            self.assertEqual(hour_str, exp_hr, f"{realm} hour")
            self.assertEqual(
                len(hour_str.split(",")), 8,
                f"{realm} floor should fire 8×/day at 180min, got '{hour_str}'")


class MinuteLaneDePileTests(TestCase):
    """The hour-multiple striped families must each occupy a distinct NA
    minute-of-hour lane so they don't stack onto the 1-vCPU DB at minute 0.

    See agents/runbooks/analysis-feed-schedule-optimization-2026-06-08.md (F1).
    """

    # name prefix → registered NA crontab.minute lane (single-valued because
    # these are hour-multiple cycles).
    NA_LANE_FAMILIES = [
        "incremental-player-refresh",
        "incremental-ranked-refresh",
        "observation-floor",
        "player-distribution-warmer",
        "player-correlation-warmer",
        "landing-page-warmer",
        "hot-players-capture",
    ]

    def test_na_minute_lanes_are_distinct(self):
        lanes = {}
        for prefix in self.NA_LANE_FAMILIES:
            row = PeriodicTask.objects.get(name=f"{prefix}-na")
            lanes[prefix] = row.crontab.minute
        # No family should sit on the minute-0 boundary, and no two may collide.
        self.assertNotIn(
            "0", lanes.values(), f"a family still anchors NA minute 0: {lanes}")
        self.assertEqual(
            len(set(lanes.values())), len(lanes),
            f"NA minute lanes collide: {lanes}")


class HotPlayersScheduleTopologyTests(TestCase):
    """Pins the two hot-players engagement-queue families
    (`runbook-hot-players-engagement-queue-2026-06-10.md`):

    - `hot-players-maintain-{realm}` — DB-only daily brain, striped at fixed
      times in the 08:00-09:00 UTC maintenance band (na 08:30 / eu 08:50 /
      asia 09:10). Always-enabled (respects HOT_PLAYERS_ENABLED).
    - `hot-players-capture-{realm}` — background daily sweep, striped via
      `_realm_crontab_for_cycle` / `REALM_INTERVAL_OFFSETS` (covered for
      existence + crontab + NA-lane de-pile by the families lists above).
    """

    def test_maintain_present_crontab_and_realm_kwarg(self):
        for realm in VALID_REALMS:
            row = PeriodicTask.objects.get(name=f"hot-players-maintain-{realm}")
            self.assertIsNotNone(
                row.crontab, f"hot-players-maintain-{realm} should be on a crontab")
            self.assertIsNone(
                row.interval,
                f"hot-players-maintain-{realm} should not also have an interval")
            self.assertIn(f'"realm": "{realm}"', row.kwargs)

    def test_maintain_fixed_times_are_distinct_per_realm(self):
        # The maintenance band is striped by fixed (minute, hour) per realm so
        # at most one realm runs the analytical GROUP BY at a time.
        sigs = {
            realm: (
                PeriodicTask.objects.get(
                    name=f"hot-players-maintain-{realm}").crontab.minute,
                PeriodicTask.objects.get(
                    name=f"hot-players-maintain-{realm}").crontab.hour,
            )
            for realm in VALID_REALMS
        }
        self.assertEqual(
            len(set(sigs.values())), len(VALID_REALMS),
            f"hot-players-maintain schedules collide: {sigs}")

    def test_capture_offsets_distinct_per_realm(self):
        sigs = {
            realm: (
                PeriodicTask.objects.get(
                    name=f"hot-players-capture-{realm}").crontab.minute,
                PeriodicTask.objects.get(
                    name=f"hot-players-capture-{realm}").crontab.hour,
            )
            for realm in VALID_REALMS
        }
        self.assertEqual(
            len(set(sigs.values())), len(VALID_REALMS),
            f"hot-players-capture schedules collide: {sigs}")


class TrackedPlayerPollGateTests(TestCase):
    """The every-60s PoC poll dispatcher must only be ENABLED when
    BATTLE_TRACKING_PLAYER_NAMES is set. On prod (unset) it is a no-op that
    was being dispatched 1440x/day and piling up in the background queue.
    See agents/runbooks/runbook-db-cpu-saturation-2026-05-24.md.
    """

    def _registered_poll_task(self):
        register_periodic_schedules(sender=apps.get_app_config("warships"))
        return PeriodicTask.objects.get(name="poll-tracked-player-battles")

    def test_poll_dispatcher_disabled_when_no_tracked_players(self):
        with mock.patch.dict(os.environ, {"BATTLE_TRACKING_PLAYER_NAMES": ""}, clear=False):
            self.assertFalse(self._registered_poll_task().enabled)

    def test_poll_dispatcher_enabled_when_tracking_configured(self):
        with mock.patch.dict(os.environ, {"BATTLE_TRACKING_PLAYER_NAMES": "lil_boots"}, clear=False):
            self.assertTrue(self._registered_poll_task().enabled)
