"""Tests for the BattleObservation row-retention tier (DB audit F5).

The table had zero lifetime deletes: ~89% of rows are JSON-stripped skeletons
(no reader at any age) and ~19% are fully-empty polls. This tier deletes
stripped skeletons past the retention window and empty polls past a short
window, always preserving (a) every JSON-carrying row (the keep-latest-3
compaction owns those) and (b) each player's latest observation (the floor's
freshness anchor). Backend-agnostic like the archive tests.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from warships.incremental_battles import prune_battle_observation_rows
from warships.models import BattleObservation, Player


def _obs(player, *, days_ago, json_payload=None, lbt=True, battles=10):
    o = BattleObservation.objects.create(
        player=player, pvp_battles=battles,
        last_battle_time=(datetime.utcnow() - timedelta(days=days_ago)
                          if lbt else None),
        ships_stats_json=json_payload)
    BattleObservation.objects.filter(pk=o.pk).update(
        observed_at=datetime.utcnow() - timedelta(days=days_ago))
    return o.pk


class PruneBattleObservationRowsTests(TestCase):
    def setUp(self):
        self.player = Player.objects.create(
            name="Obs", player_id=2001, realm="na")

    def test_old_stripped_skeleton_deleted_when_newer_exists(self):
        old = _obs(self.player, days_ago=40)
        newer = _obs(self.player, days_ago=1)
        result = prune_battle_observation_rows(retention_days=32)
        self.assertEqual(result["deleted"], 1)
        self.assertFalse(BattleObservation.objects.filter(pk=old).exists())
        self.assertTrue(BattleObservation.objects.filter(pk=newer).exists())

    def test_latest_observation_kept_even_when_old_and_stripped(self):
        only = _obs(self.player, days_ago=80)
        result = prune_battle_observation_rows(retention_days=32)
        self.assertEqual(result["deleted"], 0)
        self.assertTrue(BattleObservation.objects.filter(pk=only).exists())

    def test_json_carrying_rows_never_deleted(self):
        keeper = _obs(self.player, days_ago=80,
                      json_payload={"data": {"1": []}})
        _obs(self.player, days_ago=1)
        prune_battle_observation_rows(retention_days=32)
        self.assertTrue(BattleObservation.objects.filter(pk=keeper).exists())

    def test_ranked_json_also_protects_row(self):
        o = _obs(self.player, days_ago=80)
        BattleObservation.objects.filter(pk=o).update(
            ranked_ships_stats_json={"data": []})
        _obs(self.player, days_ago=1)
        prune_battle_observation_rows(retention_days=32)
        self.assertTrue(BattleObservation.objects.filter(pk=o).exists())

    def test_in_window_skeleton_kept(self):
        young = _obs(self.player, days_ago=10)
        _obs(self.player, days_ago=1)
        result = prune_battle_observation_rows(retention_days=32)
        self.assertEqual(result["deleted"], 0)
        self.assertTrue(BattleObservation.objects.filter(pk=young).exists())

    def test_empty_poll_deleted_past_short_window(self):
        # Fully-empty poll (no last_battle_time, no JSON) aged 10d: inside the
        # 32d skeleton window but past the 7d empty window.
        empty = _obs(self.player, days_ago=10, lbt=False, battles=0)
        _obs(self.player, days_ago=1)
        result = prune_battle_observation_rows(
            retention_days=32, empty_retention_days=7)
        self.assertEqual(result["deleted"], 1)
        self.assertFalse(BattleObservation.objects.filter(pk=empty).exists())

    def test_young_empty_poll_kept(self):
        empty = _obs(self.player, days_ago=2, lbt=False, battles=0)
        _obs(self.player, days_ago=1)
        result = prune_battle_observation_rows(
            retention_days=32, empty_retention_days=7)
        self.assertEqual(result["deleted"], 0)
        self.assertTrue(BattleObservation.objects.filter(pk=empty).exists())

    def test_dry_run_counts_without_deleting(self):
        _obs(self.player, days_ago=40)
        _obs(self.player, days_ago=1)
        result = prune_battle_observation_rows(retention_days=32, dry_run=True)
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["candidates"], 1)
        self.assertEqual(result["deleted"], 0)
        self.assertEqual(BattleObservation.objects.count(), 2)

    def test_max_rows_caps_a_run(self):
        for d in (40, 45, 50):
            _obs(self.player, days_ago=d)
        _obs(self.player, days_ago=1)
        result = prune_battle_observation_rows(retention_days=32, max_rows=2)
        self.assertEqual(result["deleted"], 2)
        self.assertEqual(BattleObservation.objects.count(), 2)


class ArchiveCommandObservationTierTests(TestCase):
    def setUp(self):
        import shutil
        import tempfile
        self.archive_dir = tempfile.mkdtemp(prefix="obs_prune_cmd_test_")
        self.addCleanup(shutil.rmtree, self.archive_dir, ignore_errors=True)
        self.player = Player.objects.create(
            name="Obs2", player_id=2002, realm="na")

    def _call(self, *extra, env):
        out = StringIO()
        with patch.dict(os.environ, env):
            call_command("archive_battle_history",
                         "--archive-dir", self.archive_dir, *extra,
                         stdout=out)
        return out.getvalue()

    def _seed_candidate(self):
        pk = _obs(self.player, days_ago=40)
        _obs(self.player, days_ago=1)
        return pk

    def test_gate_off_leaves_rows(self):
        pk = self._seed_candidate()
        self._call("--force",
                   env={"BATTLE_OBSERVATION_ROW_RETENTION_ENABLED": "0"})
        self.assertTrue(BattleObservation.objects.filter(pk=pk).exists())

    def test_gate_on_prunes_via_command(self):
        pk = self._seed_candidate()
        out = self._call(
            "--force", env={"BATTLE_OBSERVATION_ROW_RETENTION_ENABLED": "1"})
        self.assertFalse(BattleObservation.objects.filter(pk=pk).exists())
        self.assertIn("battleobservation", out)

    def test_dry_run_reports_observation_tier(self):
        self._seed_candidate()
        out = self._call(
            "--dry-run", env={"BATTLE_OBSERVATION_ROW_RETENTION_ENABLED": "1"})
        self.assertIn("battleobservation", out)
        self.assertEqual(BattleObservation.objects.count(), 2)
