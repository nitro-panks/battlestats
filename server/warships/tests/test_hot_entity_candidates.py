"""Tests for hot-entity candidate selection cost fixes (DB audit F9.1).

`_get_hot_clan_ids` ranked clans by live-aggregating every member's
pvp_wins/pvp_battles on each 30-min warm cycle (30.7 s mean on prod — the
single biggest cumulative DB consumer). The Clan row already carries the
denormalized `cached_clan_wr` / `cached_total_battles` columns for exactly
this ranking; staleness is immaterial for choosing which clans to warm.
"""
from __future__ import annotations

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from warships.data import _get_hot_clan_ids
from warships.models import Clan


def _clan(clan_id, name, *, wr=None, battles=None):
    return Clan.objects.create(
        clan_id=clan_id, realm="na", name=name, tag=name[:5].upper(),
        members_count=30, cached_clan_wr=wr, cached_total_battles=battles)


class HotClanCandidateTests(TestCase):
    def test_ranked_by_cached_columns_without_member_aggregation(self):
        # No Player rows exist at all: the live SUM() ranking would return
        # nothing, while the cached-column ranking must still surface the
        # big high-WR clans in cached-WR order.
        _clan(9101, "Alpha", wr=56.0, battles=400_000)
        _clan(9102, "Bravo", wr=61.5, battles=250_000)
        _clan(9103, "TooSmall", wr=70.0, battles=5_000)   # under battle floor
        _clan(9104, "NoCache", wr=None, battles=None)

        with CaptureQueriesContext(connection) as ctx:
            ids = _get_hot_clan_ids(limit=10, realm="na")

        self.assertIn(9102, ids)
        self.assertIn(9101, ids)
        self.assertLess(ids.index(9102), ids.index(9101))  # higher cached WR first
        self.assertNotIn(9103, ids)
        self.assertNotIn(9104, ids)
        clan_queries = [q["sql"] for q in ctx.captured_queries
                        if "warships_clan" in q["sql"].lower()]
        self.assertTrue(clan_queries)
        for sql in clan_queries:
            self.assertNotIn('SUM(', sql.upper())
            self.assertNotIn('warships_player', sql.lower())
