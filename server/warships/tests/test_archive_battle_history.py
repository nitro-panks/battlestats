"""Tests for the battle-history cold-archive + prune job.

Backend-agnostic: on the SQLite test DB this exercises the csv-writer export
fallback + count/verify/delete/manifest logic; under the Postgres release gate
the same assertions run against the server-side COPY + VACUUM path
(connection.vendor == 'postgresql').
"""
from __future__ import annotations

import csv
import gzip
import json
import os
import shutil
import tempfile
from datetime import datetime, timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from warships.incremental_battles import (
    _sha256_file,
    archive_and_prune_battle_history,
)
from warships.models import (
    BattleEvent,
    BattleObservation,
    Player,
    PlayerDailyShipStats,
)


class ArchiveBattleHistoryTests(TestCase):
    def setUp(self):
        self.archive_dir = tempfile.mkdtemp(prefix="bh_archive_test_")
        self.addCleanup(shutil.rmtree, self.archive_dir, ignore_errors=True)
        self.player = Player.objects.create(
            name="Tester", player_id=1001, realm="na")
        self.now = datetime.utcnow()
        self.retention_days = 32

    # -- seeding helpers ---------------------------------------------------

    def _battle_event(self, *, detected_at, ship_id=1):
        obs_a = BattleObservation.objects.create(
            player=self.player, pvp_battles=1)
        obs_b = BattleObservation.objects.create(
            player=self.player, pvp_battles=2)
        ev = BattleEvent.objects.create(
            player=self.player, ship_id=ship_id, ship_name="Yamato",
            mode="random", battles_delta=1, wins_delta=0, losses_delta=0,
            frags_delta=0, from_observation=obs_a, to_observation=obs_b)
        BattleEvent.objects.filter(pk=ev.pk).update(detected_at=detected_at)
        return ev.pk

    def _pdss(self, *, on_date, ship_id=1):
        return PlayerDailyShipStats.objects.create(
            player=self.player, date=on_date, ship_id=ship_id,
            mode="random", battles=1).pk

    def _seed(self):
        """Two old (>32d) + two new (<32d) rows per table. Returns id sets."""
        old_dt = self.now - timedelta(days=40)
        new_dt = self.now - timedelta(days=5)
        old_date = (self.now - timedelta(days=40)).date()
        new_date = (self.now - timedelta(days=5)).date()
        old_be = {self._battle_event(detected_at=old_dt, ship_id=1),
                  self._battle_event(detected_at=old_dt, ship_id=2)}
        new_be = {self._battle_event(detected_at=new_dt, ship_id=3),
                  self._battle_event(detected_at=new_dt, ship_id=4)}
        old_pd = {self._pdss(on_date=old_date, ship_id=1),
                  self._pdss(on_date=old_date, ship_id=2)}
        new_pd = {self._pdss(on_date=new_date, ship_id=3),
                  self._pdss(on_date=new_date, ship_id=4)}
        return old_be, new_be, old_pd, new_pd

    def _run(self, **kwargs):
        opts = dict(
            retention_days=self.retention_days, archive_dir=self.archive_dir,
            batch_size=1, sleep_between_batches=0.0)
        opts.update(kwargs)
        return archive_and_prune_battle_history(**opts)

    # -- tests -------------------------------------------------------------

    def test_dry_run_writes_and_deletes_nothing(self):
        old_be, new_be, old_pd, new_pd = self._seed()
        result = self._run(dry_run=True)

        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["dry_run"])
        # candidates reported, but no rows removed and no files written.
        self.assertEqual(BattleEvent.objects.count(), 4)
        self.assertEqual(PlayerDailyShipStats.objects.count(), 4)
        run_dir = result["run_dir"]
        self.assertFalse(
            os.path.exists(os.path.join(
                run_dir, "warships_battleevent.csv.gz")))
        by_table = {t["table"]: t for t in result["tables"]}
        self.assertEqual(by_table["warships_battleevent"]["candidates"], 2)
        self.assertEqual(
            by_table["warships_playerdailyshipstats"]["candidates"], 2)

    def test_live_archives_and_deletes_only_old_rows(self):
        old_be, new_be, old_pd, new_pd = self._seed()
        result = self._run()

        self.assertEqual(result["status"], "completed")
        # Only the >32d rows are gone; the <32d rows survive.
        self.assertEqual(
            set(BattleEvent.objects.values_list("pk", flat=True)), new_be)
        self.assertEqual(
            set(PlayerDailyShipStats.objects.values_list("pk", flat=True)),
            new_pd)
        # BattleObservation is out of scope — untouched.
        self.assertEqual(BattleObservation.objects.count(), 8)

        by_table = {t["table"]: t for t in result["tables"]}
        for table, old_ids in (
            ("warships_battleevent", old_be),
            ("warships_playerdailyshipstats", old_pd),
        ):
            entry = by_table[table]
            self.assertEqual(entry["status"], "completed")
            self.assertEqual(entry["exported"], len(old_ids))
            self.assertEqual(entry["deleted"], len(old_ids))

            gz = os.path.join(result["run_dir"], f"{table}.csv.gz")
            self.assertTrue(os.path.exists(gz))
            # manifest sha256 matches the archive on disk
            with open(os.path.join(
                    result["run_dir"], f"{table}.manifest.json")) as fh:
                manifest = json.load(fh)
            self.assertEqual(manifest["sha256"], _sha256_file(gz))
            self.assertEqual(manifest["exported"], len(old_ids))
            # archive contains exactly the old ids (column 0)
            with gzip.open(gz, "rt", newline="") as fh:
                reader = csv.reader(fh)
                header = next(reader)
                self.assertEqual(header[0], "id")
                archived_ids = {int(r[0]) for r in reader if r}
            self.assertEqual(archived_ids, old_ids)

    def test_max_rows_caps_per_table(self):
        old_be, _, old_pd, _ = self._seed()
        result = self._run(max_rows=1)
        by_table = {t["table"]: t for t in result["tables"]}
        # Exactly one (lowest id) old row archived+deleted per table.
        self.assertEqual(by_table["warships_battleevent"]["deleted"], 1)
        self.assertEqual(
            by_table["warships_playerdailyshipstats"]["deleted"], 1)
        self.assertEqual(BattleEvent.objects.count(), 3)
        self.assertEqual(PlayerDailyShipStats.objects.count(), 3)

    def test_tables_subset(self):
        old_be, new_be, old_pd, new_pd = self._seed()
        self._run(tables=["playerdailyshipstats"])
        # BattleEvent untouched, only PDSS pruned.
        self.assertEqual(BattleEvent.objects.count(), 4)
        self.assertEqual(
            set(PlayerDailyShipStats.objects.values_list("pk", flat=True)),
            new_pd)

    def test_command_disabled_without_force_is_noop(self):
        self._seed()
        out = StringIO()
        # BATTLE_HISTORY_ARCHIVE_ENABLED unset -> live no-op.
        call_command("archive_battle_history",
                     archive_dir=self.archive_dir, stdout=out)
        self.assertEqual(BattleEvent.objects.count(), 4)
        self.assertEqual(PlayerDailyShipStats.objects.count(), 4)
        self.assertIn("disabled", out.getvalue().lower())

    def test_command_force_runs_live(self):
        old_be, new_be, old_pd, new_pd = self._seed()
        out = StringIO()
        call_command("archive_battle_history", "--force",
                     archive_dir=self.archive_dir,
                     retention_days=self.retention_days, batch_size=1,
                     stdout=out)
        self.assertEqual(
            set(BattleEvent.objects.values_list("pk", flat=True)), new_be)
        self.assertEqual(
            set(PlayerDailyShipStats.objects.values_list("pk", flat=True)),
            new_pd)
