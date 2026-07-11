"""Regression: update_player_data must not clobber concurrently-written fields.

On a cold (>23h) visit the cache-miss fan-out dispatches update_player_data,
update_battle_data and update_ranked_data concurrently. update_player_data does
an account/info refresh then historically a bare player.save() — writing back
EVERY field on its stale snapshot, including battles_updated_at / ranked_json /
the derived chart columns. If a concurrent update_battle_data (now()-stamp) or
update_ranked_data (scoped ranked_json write) landed during the account/info
fetch, the bare save reverted them: the "Updating…" pill re-armed and fresh
ranked data flickered stale (runbook-player-refresh-pill-clobber-2026-06-21,
cold >23h variant).

The fix scopes the non-hidden save to the account/info fields it owns and drops
the WG stats_updated_at -> battles_updated_at write entirely. These tests pin it.
"""

from datetime import datetime
from unittest import mock

from django.test import TestCase

from warships.data import update_player_data
from warships.models import Player

PID = 1054131305
OLD = datetime(2026, 6, 20, 10, 0, 0)
FRESH = datetime(2026, 6, 21, 20, 37, 18)
NEW_RANKED = [{"season_id": 99, "battles": 7}]

ACCT_PAYLOAD = {
    "nickname": "HMSHOOD06_NEW",
    "account_id": PID,
    "clan_id": None,
    # created_at / last_battle_time left None: update_player_data derives
    # creation_date via datetime.fromtimestamp(..., tz=utc) (tz-aware), which the
    # sqlite test backend rejects under USE_TZ=False (Postgres accepts it). Nulling
    # them keeps the test backend-agnostic without affecting the save-scoping path.
    "created_at": None,
    "last_battle_time": None,
    "hidden_profile": False,
    # present in the real payload but must NO LONGER be written to battles_updated_at:
    "stats_updated_at": 1600000000,
    "statistics": {"battles": 500, "pvp": {
        "battles": 480, "wins": 250, "losses": 220, "frags": 600,
        "survived_battles": 300, "survived_wins": 200,
    }},
}


def _patches():
    return [
        mock.patch("warships.data._fetch_clan_membership_for_player", return_value={}),
        mock.patch("warships.data.update_player_efficiency_data", return_value=[]),
        mock.patch("warships.data.refresh_player_explorer_summary"),
        mock.patch("warships.data.invalidate_player_detail_cache"),
    ]


class UpdatePlayerDataScopedSaveTests(TestCase):
    def setUp(self):
        # last_fetch old enough that update_player_data does NOT early-return.
        self.player = Player.objects.create(
            name="OLD", player_id=PID, realm="na", battles_updated_at=OLD,
            ranked_json=[{"season_id": 1}],
            last_fetch=datetime(2026, 6, 18, 0, 0, 0),
        )
        for p in _patches():
            p.start()
            self.addCleanup(p.stop)

    def test_concurrent_battle_and_ranked_writes_survive(self):
        # The concurrent update_battle_data (battles_updated_at=now()) + update_ranked_data
        # (ranked_json=NEW) land DURING the account/info fetch.
        def _fetch(*_a, **_k):
            Player.objects.filter(player_id=PID, realm="na").update(
                battles_updated_at=FRESH, ranked_json=NEW_RANKED)
            return ACCT_PAYLOAD

        with mock.patch("warships.data._fetch_player_personal_data", side_effect=_fetch):
            update_player_data(self.player, realm="na")

        row = Player.objects.get(player_id=PID, realm="na")
        # The concurrent writes must NOT be reverted by update_player_data's save.
        self.assertEqual(row.battles_updated_at, FRESH)
        self.assertEqual(row.ranked_json, NEW_RANKED)
        # ...while the account/info fields THIS refresh owns still persisted.
        self.assertEqual(row.name, "HMSHOOD06_NEW")
        self.assertEqual(row.pvp_battles, 480)

    def test_account_info_no_longer_writes_battles_updated_at(self):
        # No concurrent writer; battles_updated_at starts None. account/info must
        # NOT set it from WG stats_updated_at (that was the backwards-clobber).
        self.player.battles_updated_at = None
        self.player.save(update_fields=["battles_updated_at"])

        with mock.patch("warships.data._fetch_player_personal_data", return_value=ACCT_PAYLOAD):
            update_player_data(self.player, realm="na")

        row = Player.objects.get(player_id=PID, realm="na")
        self.assertIsNone(row.battles_updated_at)   # owned by update_battle_data, not this path
        self.assertEqual(row.name, "HMSHOOD06_NEW")  # account/info still persisted
