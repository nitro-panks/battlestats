"""Tests for the clan-crawl yield-by-source instrumentation.

The crawl is the only mechanism that can (a) discover net-new account IDs and
(b) re-detect dormant->active players, since the observation floor is gated on
players that are *already* active. This instrumentation classifies every saved
player so we can measure that floor-impossible yield per pass vs. overlap with
the floor. See warships/clan_crawl.py (`_classify_player_yield`,
`crawl_clan_members`, `emit_crawl_yield_snapshot`).
"""

import json
import os
import tempfile
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from warships import clan_crawl
from warships.clan_crawl import (
    crawl_clan_members,
    emit_crawl_yield_snapshot,
    save_player,
)
from warships.models import Clan, Player


def _pdata(account_id, *, last_battle_days_ago=None, nickname="p", hidden=False):
    """Build a minimal account/info payload as the crawl receives it."""
    data = {"account_id": account_id, "nickname": nickname}
    if last_battle_days_ago is not None:
        ts = timezone.now() - timedelta(days=last_battle_days_ago)
        data["last_battle_time"] = int(ts.timestamp())
    if hidden:
        data["hidden_profile"] = True
    else:
        data["statistics"] = {"battles": 100, "pvp": {
            "battles": 100, "wins": 50, "losses": 50,
            "frags": 60, "survived_battles": 40}}
    return data


class ClassifyPlayerYieldTests(TestCase):
    """Unit-level: the bucket returned by save_player for each transition.

    cutoff = today - BATTLE_OBSERVATION_FLOOR_DAYS (default 7), mirroring the
    observation floor's active window. core_only=True keeps save_player to the
    DB write path (no enrichment / explorer-summary side calls).
    """

    def setUp(self):
        self.cutoff = clan_crawl._crawl_active_cutoff()
        self.clan = Clan.objects.create(clan_id=7000, realm="na", tag="T", name="T")

    def _save(self, pdata):
        return save_player(pdata, self.clan, realm="na",
                           core_only=True, cutoff=self.cutoff)

    def test_new_active_player_is_discovered_active(self):
        bucket = self._save(_pdata(1001, last_battle_days_ago=1))
        self.assertEqual(bucket, "discovered_active")

    def test_new_dormant_player_is_discovered_dormant(self):
        bucket = self._save(_pdata(1002, last_battle_days_ago=30))
        self.assertEqual(bucket, "discovered_dormant")

    def test_known_dormant_to_active_is_reactivated(self):
        Player.objects.create(
            player_id=1003, realm="na",
            last_battle_date=(timezone.now() - timedelta(days=30)).date())
        bucket = self._save(_pdata(1003, last_battle_days_ago=1))
        self.assertEqual(bucket, "reactivated")

    def test_known_already_active_is_refreshed_active(self):
        Player.objects.create(
            player_id=1004, realm="na",
            last_battle_date=timezone.now().date())
        bucket = self._save(_pdata(1004, last_battle_days_ago=1))
        self.assertEqual(bucket, "refreshed_active")

    def test_known_stays_dormant_is_still_dormant(self):
        Player.objects.create(
            player_id=1005, realm="na",
            last_battle_date=(timezone.now() - timedelta(days=40)).date())
        bucket = self._save(_pdata(1005, last_battle_days_ago=30))
        self.assertEqual(bucket, "still_dormant")

    def test_hidden_player_still_classified(self):
        # A hidden profile still carries last_battle_time, so the crawl can
        # still surface it as a (re)activation — classification is independent
        # of the hidden branch.
        bucket = self._save(_pdata(1006, last_battle_days_ago=1, hidden=True))
        self.assertEqual(bucket, "discovered_active")

    def test_no_cutoff_returns_none(self):
        # Instrumentation disabled (cutoff=None) -> no classification overhead.
        bucket = save_player(_pdata(1007, last_battle_days_ago=1), self.clan,
                             realm="na", core_only=True, cutoff=None)
        self.assertIsNone(bucket)


class CrawlYieldAggregationTests(TestCase):
    """Integration-level: crawl_clan_members accrues counts into the per-pass
    Redis aggregate and the summary; emit_crawl_yield_snapshot derives the
    yield/overlap split and writes a durable snapshot."""

    def setUp(self):
        cache.clear()
        self.fresh_after = timezone.now()
        # one already-active member (overlap), one net-new active (yield)
        Player.objects.create(
            player_id=9002, realm="na",
            last_battle_date=timezone.now().date())

    @patch("warships.clan_crawl.fetch_players_bulk")
    @patch("warships.clan_crawl.fetch_member_ids")
    @patch("warships.clan_crawl.fetch_clan_info")
    def _run_crawl(self, mock_info, mock_members, mock_bulk):
        mock_info.return_value = {
            "clan_id": 8001, "name": "C", "tag": "C", "members_count": 2,
            "description": "", "leader_id": 9001, "leader_name": "L"}
        mock_members.return_value = [9001, 9002]
        mock_bulk.return_value = {
            "9001": _pdata(9001, last_battle_days_ago=1),   # net-new + active
            "9002": _pdata(9002, last_battle_days_ago=1),   # known + active
        }
        return crawl_clan_members(
            [{"clan_id": 8001}], resume=False, realm="na",
            core_only=True, request_delay=0, fresh_after=self.fresh_after)

    def test_summary_and_redis_accrue_yield_counts(self):
        with patch.dict(os.environ, {"CRAWL_YIELD_INSTRUMENT_ENABLED": "1"}):
            summary = self._run_crawl()

        self.assertEqual(summary["yield"]["discovered_active"], 1)
        self.assertEqual(summary["yield"]["refreshed_active"], 1)

        pass_id = clan_crawl._crawl_yield_pass_id(self.fresh_after)
        agg = cache.get(clan_crawl._crawl_yield_key("na", pass_id))
        self.assertEqual(agg.get("discovered_active"), 1)
        self.assertEqual(agg.get("refreshed_active"), 1)

    def test_emit_snapshot_derives_split_and_clears_key(self):
        with patch.dict(os.environ, {"CRAWL_YIELD_INSTRUMENT_ENABLED": "1"}):
            self._run_crawl()
            with tempfile.TemporaryDirectory() as tmp:
                with patch.object(clan_crawl, "CRAWL_YIELD_BENCHMARK_DIR", tmp):
                    snap = emit_crawl_yield_snapshot("na", self.fresh_after)
                # read the durable snapshot while the temp dir still exists
                files = os.listdir(tmp)
                self.assertEqual(len(files), 1)
                with open(os.path.join(tmp, files[0])) as handle:
                    written = json.load(handle)

        self.assertEqual(snap["yield_total"], 1)     # discovered_active
        self.assertEqual(snap["overlap_total"], 1)   # refreshed_active
        self.assertEqual(snap["players_classified"], 2)
        self.assertEqual(snap["yield_frac"], 0.5)
        self.assertEqual(snap["realm"], "na")
        self.assertEqual(written["yield_total"], 1)
        # aggregate cleared after emit
        pass_id = clan_crawl._crawl_yield_pass_id(self.fresh_after)
        self.assertIsNone(cache.get(clan_crawl._crawl_yield_key("na", pass_id)))

    def test_disabled_flag_skips_instrumentation(self):
        with patch.dict(os.environ, {"CRAWL_YIELD_INSTRUMENT_ENABLED": "0"}):
            summary = self._run_crawl()
        # buckets stay zero; no Redis aggregate created
        self.assertEqual(sum(summary["yield"].values()), 0)
        pass_id = clan_crawl._crawl_yield_pass_id(self.fresh_after)
        self.assertIsNone(cache.get(clan_crawl._crawl_yield_key("na", pass_id)))
