"""Tests for hot-player candidate selection cost (DB CPU alerts, 2026-11-23).

`_get_hot_player_ids` ran two table-wide rankings on every 30-minute warm
cycle. The score ranking alone was 32 s mean and ~2.2 GB read on prod, the
largest single statement in pg_stat_statements, and its result was discarded
whenever the earlier sources had already filled the limit. The sources are now
consulted lazily, and the score list is cached for a day.
Runbook: agents/runbooks/runbook-db-cpu-alerts-steal-and-scan-bursts-2026-11-23.md
"""
from __future__ import annotations

from unittest import mock

from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from warships import data
from warships.data import _get_hot_player_ids, _hot_player_score_ids_cache_key
from warships.models import Player, PlayerExplorerSummary


def _player(player_id, name, *, realm="na", score=None, ratio=50.0,
            looked_up=False, hidden=False):
    player = Player.objects.create(
        player_id=player_id, realm=realm, name=name, pvp_ratio=ratio,
        is_hidden=hidden,
        last_lookup=timezone.now() if looked_up else None)
    if score is not None:
        PlayerExplorerSummary.objects.create(
            player=player, realm=realm, player_score=score)
    return player


def _score_queries(ctx):
    return [q["sql"] for q in ctx.captured_queries
            if "player_score" in q["sql"].lower()]


class HotPlayerCandidateTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_score_ranking_skipped_when_earlier_sources_fill_the_limit(self):
        _player(101, "Visited1")
        _player(102, "Visited2")
        _player(201, "TopScore", score=99.0)

        with mock.patch.object(data, "_top_visited_entity_ids",
                               return_value=[101, 102]), \
                CaptureQueriesContext(connection) as ctx:
            ids = _get_hot_player_ids(limit=2, realm="na")

        self.assertEqual(ids, [101, 102])
        self.assertEqual(_score_queries(ctx), [])
        self.assertEqual(
            [q for q in ctx.captured_queries
             if "last_lookup" in q["sql"].lower() and "ORDER BY" in q["sql"].upper()],
            [])

    def test_sources_fill_in_priority_order(self):
        _player(101, "Visited")
        _player(301, "Looked", looked_up=True)
        _player(201, "HighScore", score=90.0)
        _player(202, "LowScore", score=10.0)
        _player(203, "Hidden", score=99.0, hidden=True)

        with mock.patch.object(data, "_top_visited_entity_ids",
                               return_value=[101]):
            ids = _get_hot_player_ids(limit=4, realm="na")

        self.assertEqual(ids, [101, 301, 201, 202])

    def test_score_ranking_is_computed_once_and_reused(self):
        _player(201, "HighScore", score=90.0)
        _player(202, "LowScore", score=10.0)

        with mock.patch.object(data, "_top_visited_entity_ids", return_value=[]):
            with CaptureQueriesContext(connection) as first:
                first_ids = _get_hot_player_ids(limit=5, realm="na")
            # A new top scorer appears; the day's list must not be recomputed.
            _player(204, "Newcomer", score=95.0)
            with CaptureQueriesContext(connection) as second:
                second_ids = _get_hot_player_ids(limit=5, realm="na")

        self.assertEqual(first_ids, [201, 202])
        self.assertEqual(second_ids, [201, 202])
        self.assertEqual(len(_score_queries(first)), 1)
        self.assertEqual(_score_queries(second), [])

    def test_empty_score_ranking_is_cached(self):
        with mock.patch.object(data, "_top_visited_entity_ids", return_value=[]):
            self.assertEqual(_get_hot_player_ids(limit=5, realm="na"), [])
            with CaptureQueriesContext(connection) as ctx:
                self.assertEqual(_get_hot_player_ids(limit=5, realm="na"), [])

        self.assertEqual(_score_queries(ctx), [])

    def test_score_ranking_cache_is_per_realm(self):
        _player(201, "NaScore", realm="na", score=90.0)
        _player(501, "EuScore", realm="eu", score=80.0)

        with mock.patch.object(data, "_top_visited_entity_ids", return_value=[]):
            self.assertEqual(_get_hot_player_ids(limit=5, realm="na"), [201])
            self.assertEqual(_get_hot_player_ids(limit=5, realm="eu"), [501])

        self.assertEqual(cache.get(_hot_player_score_ids_cache_key("na", 5)), [201])
        self.assertEqual(cache.get(_hot_player_score_ids_cache_key("eu", 5)), [501])
