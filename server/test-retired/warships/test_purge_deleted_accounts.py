"""Tests for the deleted account purge pipeline: blocklist, player_records gate, views gate, management command."""
import csv
import io
import json
import os
import tempfile
import zipfile
from unittest.mock import patch, MagicMock

import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "battlestats.settings")

from django.test import TestCase, override_settings
from warships.models import Clan, DeletedAccount, EntityVisitDaily, EntityVisitEvent, Player, PlayerAchievementStat, PlayerExplorerSummary, Snapshot
from warships.player_records import BlockedAccountError, get_or_create_canonical_player
from warships.blocklist import get_blocked_ids, invalidate_blocklist_cache, is_account_blocked
from warships.management.commands.purge_deleted_accounts import _parse_account_ids


def _make_zip(account_ids: list[int], tmp_dir: str) -> str:
    """Create a zip with accounts.csv for testing."""
    zip_path = os.path.join(tmp_dir, "deleted_accounts.zip")
    csv_buf = io.StringIO()
    writer = csv.writer(csv_buf)
    writer.writerow(["account_id"])
    for aid in account_ids:
        writer.writerow([aid])
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("accounts.csv", csv_buf.getvalue())
    return zip_path


class ParseAccountIdsTests(TestCase):
    def test_parse_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _make_zip([111, 222, 333], tmp)
            ids = _parse_account_ids(path)
            self.assertEqual(ids, [111, 222, 333])

    def test_parse_csv(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("account_id\n100\n200\n")
            f.flush()
            ids = _parse_account_ids(f.name)
            self.assertEqual(ids, [100, 200])
            os.unlink(f.name)


class BlocklistTests(TestCase):
    def setUp(self):
        invalidate_blocklist_cache()

    def test_empty_blocklist(self):
        self.assertFalse(is_account_blocked(999))

    def test_blocked_account(self):
        DeletedAccount.objects.create(account_id=12345)
        invalidate_blocklist_cache()
        self.assertTrue(is_account_blocked(12345))
        self.assertFalse(is_account_blocked(99999))

    def test_get_blocked_ids_returns_set(self):
        DeletedAccount.objects.create(account_id=1)
        DeletedAccount.objects.create(account_id=2)
        invalidate_blocklist_cache()
        ids = get_blocked_ids()
        self.assertIsInstance(ids, set)
        self.assertEqual(ids, {1, 2})


class PlayerRecordsBlocklistTests(TestCase):
    def setUp(self):
        invalidate_blocklist_cache()

    def test_blocked_account_raises(self):
        DeletedAccount.objects.create(account_id=55555)
        invalidate_blocklist_cache()
        with self.assertRaises(BlockedAccountError) as ctx:
            get_or_create_canonical_player(55555)
        self.assertEqual(ctx.exception.player_id, 55555)

    def test_unblocked_account_creates(self):
        player, created = get_or_create_canonical_player(77777)
        self.assertTrue(created)
        self.assertEqual(player.player_id, 77777)

    def test_existing_player_not_blocked(self):
        Player.objects.create(name="test", player_id=88888)
        player, created = get_or_create_canonical_player(88888)
        self.assertFalse(created)
        self.assertEqual(player.player_id, 88888)


class PurgeCommandTests(TestCase):
    def setUp(self):
        invalidate_blocklist_cache()
        self.clan = Clan.objects.create(clan_id=1, name="TestClan", tag="TC")
        self.player = Player.objects.create(
            name="PurgeTarget", player_id=11111, clan=self.clan,
        )
        Snapshot.objects.create(
            player=self.player, date="2026-01-01", battles=100, wins=50,
        )
        PlayerExplorerSummary.objects.create(player=self.player)

    def test_dry_run_does_not_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = _make_zip([11111], tmp)
            from django.core.management import call_command
            out = io.StringIO()
            call_command("purge_deleted_accounts", zip_path, "--dry-run", stdout=out)
            # Player should still exist
            self.assertTrue(Player.objects.filter(player_id=11111).exists())
            # No blocklist entry in dry run
            self.assertFalse(DeletedAccount.objects.filter(account_id=11111).exists())

    def test_purge_deletes_player_and_blocklists(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = _make_zip([11111], tmp)
            transcript_path = os.path.join(tmp, "transcript.jsonl")
            from django.core.management import call_command
            out = io.StringIO()
            call_command(
                "purge_deleted_accounts", zip_path,
                f"--transcript={transcript_path}",
                stdout=out,
            )
            # Player deleted
            self.assertFalse(Player.objects.filter(player_id=11111).exists())
            # Snapshots cascaded
            self.assertEqual(Snapshot.objects.filter(player=self.player.pk).count(), 0)
            # Blocklisted
            self.assertTrue(DeletedAccount.objects.filter(account_id=11111).exists())
            # Transcript exists
            self.assertTrue(os.path.exists(transcript_path))
            with open(transcript_path) as f:
                lines = [json.loads(line) for line in f]
            # Should have 1 detail line + 1 summary line
            self.assertEqual(len(lines), 2)
            detail = lines[0]
            self.assertEqual(detail["account_id"], 11111)
            self.assertTrue(detail["found"])
            self.assertEqual(detail["player_name"], "PurgeTarget")
            self.assertEqual(detail["rows_deleted"]["player"], 1)
            self.assertEqual(detail["rows_deleted"]["snapshots"], 1)
            summary = lines[1]
            self.assertTrue(summary["summary"])
            self.assertEqual(summary["found_in_db"], 1)

    def test_purge_not_found_still_blocklists(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = _make_zip([99999], tmp)
            transcript_path = os.path.join(tmp, "transcript.jsonl")
            from django.core.management import call_command
            out = io.StringIO()
            call_command(
                "purge_deleted_accounts", zip_path,
                f"--transcript={transcript_path}",
                stdout=out,
            )
            self.assertTrue(DeletedAccount.objects.filter(account_id=99999).exists())
            with open(transcript_path) as f:
                lines = [json.loads(line) for line in f]
            detail = lines[0]
            self.assertFalse(detail["found"])
            self.assertTrue(detail["blocklisted"])

    def test_purge_nulls_clan_leader(self):
        self.clan.leader_id = 11111
        self.clan.leader_name = "PurgeTarget"
        self.clan.save()
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = _make_zip([11111], tmp)
            transcript_path = os.path.join(tmp, "transcript.jsonl")
            from django.core.management import call_command
            out = io.StringIO()
            call_command(
                "purge_deleted_accounts", zip_path,
                f"--transcript={transcript_path}",
                stdout=out,
            )
            self.clan.refresh_from_db()
            self.assertIsNone(self.clan.leader_id)
            self.assertIsNone(self.clan.leader_name)

    def test_re_entry_blocked_after_purge(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = _make_zip([11111], tmp)
            from django.core.management import call_command
            call_command("purge_deleted_accounts", zip_path, stdout=io.StringIO())
        with self.assertRaises(BlockedAccountError):
            get_or_create_canonical_player(11111)
