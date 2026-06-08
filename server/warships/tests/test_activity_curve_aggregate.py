"""Tests for `aggregate_player_activity_curve_task` (F3 activity curve).

Rebuilds the per-realm hour-of-day histogram (PlayerActivityHourly) from
BattleObservation.last_battle_time. Companion runbook:
agents/runbooks/analysis-feed-schedule-optimization-2026-06-08.md
"""

import os
from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone as django_timezone

from warships.models import BattleObservation, Player, PlayerActivityHourly
from warships.tasks import aggregate_player_activity_curve_task


class ActivityCurveAggregateTests(TestCase):
    def setUp(self):
        self.now = django_timezone.now()

    def _player(self, name, realm="na"):
        return Player.objects.create(
            name=name,
            player_id=abs(hash(name)) % (10 ** 9),
            realm=realm,
            last_battle_date=self.now.date(),
        )

    def _obs(self, player, hour, *, days_ago=1):
        """Create an observation whose last_battle_time lands on `hour` UTC."""
        ts = (self.now - timedelta(days=days_ago)).replace(
            hour=hour, minute=0, second=0, microsecond=0)
        BattleObservation.objects.create(
            player=player, pvp_battles=0, last_battle_time=ts)

    def _run(self, **env):
        with mock.patch.dict(
            os.environ, {"ACTIVITY_CURVE_ENABLED": "1", **env}, clear=False
        ):
            return aggregate_player_activity_curve_task.apply().get()

    def test_no_op_when_disabled(self):
        self._obs(self._player("A"), hour=2)
        with mock.patch.dict(
            os.environ, {"ACTIVITY_CURVE_ENABLED": "0"}, clear=False
        ):
            result = aggregate_player_activity_curve_task.apply().get()
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(PlayerActivityHourly.objects.count(), 0)

    def test_buckets_distinct_players_by_hour(self):
        # Two NA players battled at hour 2, one at hour 14; one EU player at 20.
        self._obs(self._player("na2a"), hour=2)
        self._obs(self._player("na2b"), hour=2)
        self._obs(self._player("na14"), hour=14)
        self._obs(self._player("eu20", realm="eu"), hour=20)

        result = self._run()
        self.assertEqual(result["status"], "completed")

        na = {r.hour: r.player_count
              for r in PlayerActivityHourly.objects.filter(realm="na")}
        self.assertEqual(na, {2: 2, 14: 1})
        eu = {r.hour: r.player_count
              for r in PlayerActivityHourly.objects.filter(realm="eu")}
        self.assertEqual(eu, {20: 1})

    def test_counts_players_not_observations(self):
        # One player observed twice in the same hour counts once (distinct).
        p = self._player("repeat")
        self._obs(p, hour=5)
        self._obs(p, hour=5)
        self._run()
        row = PlayerActivityHourly.objects.get(realm="na", hour=5)
        self.assertEqual(row.player_count, 1)

    def test_excludes_observations_outside_window(self):
        self._obs(self._player("recent"), hour=3, days_ago=1)
        self._obs(self._player("stale"), hour=8, days_ago=30)
        self._run(ACTIVITY_CURVE_WINDOW_DAYS="7")
        hours = set(
            PlayerActivityHourly.objects.filter(realm="na")
            .values_list("hour", flat=True))
        self.assertEqual(hours, {3})

    def test_rebuild_is_idempotent(self):
        self._obs(self._player("p1"), hour=1)
        self._run()
        self._run()
        self.assertEqual(
            PlayerActivityHourly.objects.filter(realm="na", hour=1).count(), 1)
