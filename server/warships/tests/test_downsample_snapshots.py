"""Tests for the Snapshot 90d-downsample retention job.

Backend-agnostic: the keeper selection uses ORM ExtractIsoYear/ExtractWeek
annotations, exercised here on SQLite and under the Postgres release gate.
"""
from __future__ import annotations

from datetime import timedelta

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from warships.models import Player, Snapshot
from warships.snapshot_retention import downsample_snapshots


class DownsampleSnapshotsTests(TestCase):
    def setUp(self):
        self.player = Player.objects.create(
            name="Tester", player_id=2002, realm="na")
        self.today = timezone.now().date()

    def _snap(self, *, days_ago, battles):
        return Snapshot.objects.create(
            player=self.player,
            date=self.today - timedelta(days=days_ago),
            battles=battles,
            wins=battles // 2,
        )

    def test_recent_rows_within_window_are_untouched(self):
        # Seven consecutive recent days (well inside the 90d window).
        for d in range(7):
            self._snap(days_ago=d, battles=100 + d)
        result = downsample_snapshots(retention_days=90)
        self.assertEqual(result["candidates"], 0)
        self.assertEqual(result["deleted"], 0)
        self.assertEqual(Snapshot.objects.count(), 7)

    def test_old_week_collapses_to_latest_keeper(self):
        # Seven consecutive days all older than the cutoff, inside ONE ISO week.
        base = 200  # days ago — far past the 90d cutoff
        # Use a contiguous Mon-Sun span so they share an ISO week.
        # Find a Monday >200 days ago for determinism.
        anchor = self.today - timedelta(days=base)
        monday = anchor - timedelta(days=anchor.weekday())
        keeper_date = None
        for i in range(7):
            d = monday + timedelta(days=i)
            Snapshot.objects.create(
                player=self.player, date=d, battles=300 + i, wins=i)
            keeper_date = d  # last iteration = Sunday = max date in week
        self.assertEqual(Snapshot.objects.count(), 7)

        result = downsample_snapshots(retention_days=90)
        self.assertEqual(result["candidates"], 7)
        self.assertEqual(result["keepers"], 1)
        self.assertEqual(result["deleted"], 6)

        remaining = list(Snapshot.objects.all())
        self.assertEqual(len(remaining), 1)
        # The kept row is the latest date in the week (cumulative trajectory).
        self.assertEqual(remaining[0].date, keeper_date)
        self.assertEqual(remaining[0].battles, 306)

    def test_dry_run_writes_nothing(self):
        anchor = self.today - timedelta(days=200)
        monday = anchor - timedelta(days=anchor.weekday())
        for i in range(5):
            Snapshot.objects.create(
                player=self.player, date=monday + timedelta(days=i),
                battles=400 + i)
        result = downsample_snapshots(retention_days=90, dry_run=True)
        self.assertEqual(result["deletable"], 4)
        self.assertEqual(result["deleted"], 0)
        self.assertEqual(Snapshot.objects.count(), 5)

    def test_separate_weeks_each_keep_one(self):
        # Two distinct old ISO weeks -> two keepers.
        anchor = self.today - timedelta(days=200)
        monday = anchor - timedelta(days=anchor.weekday())
        for i in range(3):
            Snapshot.objects.create(
                player=self.player, date=monday + timedelta(days=i),
                battles=500 + i)
        next_monday = monday + timedelta(days=7)
        for i in range(3):
            Snapshot.objects.create(
                player=self.player, date=next_monday + timedelta(days=i),
                battles=600 + i)
        result = downsample_snapshots(retention_days=90)
        self.assertEqual(result["keepers"], 2)
        self.assertEqual(result["deleted"], 4)
        self.assertEqual(Snapshot.objects.count(), 2)

    def test_command_killswitch_no_op_without_env(self):
        anchor = self.today - timedelta(days=200)
        monday = anchor - timedelta(days=anchor.weekday())
        for i in range(4):
            Snapshot.objects.create(
                player=self.player, date=monday + timedelta(days=i),
                battles=700 + i)
        # No SNAPSHOT_DOWNSAMPLE_ENABLED in env -> live run is a no-op.
        call_command("downsample_snapshots")
        self.assertEqual(Snapshot.objects.count(), 4)
