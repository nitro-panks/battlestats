from datetime import datetime, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from warships.data import update_snapshot_data, fetch_activity_data, fetch_randoms_data, update_player_data, update_clan_data
from warships.models import Player, Snapshot, Clan


class SnapshotDataTests(TestCase):
    @patch("warships.data.update_player_data")
    def test_update_snapshot_data_creates_snapshot_and_intervals(self, mock_update_player_data):
        player = Player.objects.create(
            name="ActivityUser", player_id=222,
            pvp_battles=103, pvp_wins=52,
        )

        def hydrate_player(p, force_refresh=False):
            p.pvp_battles = 103
            p.pvp_wins = 52
            p.save()

        mock_update_player_data.side_effect = hydrate_player

        update_snapshot_data(player.player_id)

        today = datetime.now().date()
        snapshot = Snapshot.objects.get(player=player, date=today)
        self.assertEqual(snapshot.battles, 103)
        self.assertEqual(snapshot.wins, 52)


class ActivityDataRefreshTests(TestCase):
    @patch("warships.data.update_activity_data")
    @patch("warships.data.update_snapshot_data")
    def test_fetch_activity_data_refreshes_cumulative_spike_cache(
        self,
        mock_update_snapshot_data,
        mock_update_activity_data,
    ):
        player = Player.objects.create(
            name="SpikeUser",
            player_id=333,
            activity_json=[
                {"date": "2026-03-01", "battles": 0, "wins": 0},
                {"date": "2026-03-02", "battles": 6500, "wins": 3000},
            ],
            activity_updated_at=timezone.now(),
        )

        fetch_activity_data(player.player_id)

        mock_update_snapshot_data.assert_called_once_with(player.player_id)
        mock_update_activity_data.assert_called_once_with(player.player_id)


class RandomsDataRefreshTests(TestCase):
    @patch("warships.data.update_randoms_data")
    @patch("warships.data.update_battle_data")
    def test_fetch_randoms_data_refreshes_stale_cache_synchronously(
        self,
        mock_update_battle_data,
        mock_update_randoms_data,
    ):
        player = Player.objects.create(
            name="RandomsUser",
            player_id=444,
            battles_json=[
                {
                    "ship_name": "Old Ship",
                    "ship_type": "Destroyer",
                    "ship_tier": 8,
                    "pvp_battles": 10,
                    "win_ratio": 0.5,
                    "wins": 5,
                }
            ],
            randoms_json=[
                {
                    "ship_name": "Old Ship",
                    "ship_type": "Destroyer",
                    "ship_tier": 8,
                    "pvp_battles": 10,
                    "win_ratio": 0.5,
                    "wins": 5,
                }
            ],
            battles_updated_at=timezone.now() - timedelta(hours=1),
            randoms_updated_at=timezone.now() - timedelta(days=2),
        )

        def write_fresh_rows(player_id):
            p = Player.objects.get(player_id=player_id)
            p.randoms_json = [
                {
                    "ship_name": "Fresh Ship",
                    "ship_type": "Cruiser",
                    "ship_tier": 10,
                    "pvp_battles": 99,
                    "win_ratio": 0.6,
                    "wins": 59,
                }
            ]
            p.randoms_updated_at = timezone.now()
            p.save(update_fields=["randoms_json", "randoms_updated_at"])

        mock_update_randoms_data.side_effect = write_fresh_rows

        rows = fetch_randoms_data(player.player_id)

        self.assertEqual(rows[0]["ship_name"], "Fresh Ship")
        mock_update_battle_data.assert_called_once_with(player.player_id)
        mock_update_randoms_data.assert_called_once_with(player.player_id)


class PlayerDataHardeningTests(TestCase):
    @patch("warships.data._fetch_clan_membership_for_player")
    @patch("warships.data._fetch_player_personal_data")
    def test_update_player_data_hidden_profile_clears_cached_views(
        self,
        mock_fetch_player_personal_data,
        mock_fetch_clan_membership,
    ):
        player = Player.objects.create(
            name="VisibleCaptain",
            player_id=8080,
            is_hidden=False,
            total_battles=100,
            pvp_battles=90,
            pvp_wins=50,
            pvp_losses=40,
            pvp_ratio=55.5,
            battles_json=[{"ship_name": "Old Ship"}],
            randoms_json=[{"ship_name": "Old Ship"}],
            ranked_json=[{"season_id": 1}],
        )
        mock_fetch_player_personal_data.return_value = {
            "account_id": 8080,
            "nickname": "VisibleCaptain",
            "hidden_profile": True,
        }
        mock_fetch_clan_membership.return_value = {}

        update_player_data(player, force_refresh=True)

        player.refresh_from_db()
        self.assertTrue(player.is_hidden)
        self.assertEqual(player.total_battles, 0)
        self.assertIsNone(player.battles_json)
        self.assertIsNone(player.randoms_json)
        self.assertIsNone(player.ranked_json)

    @patch("warships.data._fetch_player_personal_data")
    def test_update_player_data_does_not_overwrite_on_empty_upstream_response(self, mock_fetch_player_personal_data):
        player = Player.objects.create(
            name="StableCaptain",
            player_id=9090,
            total_battles=77,
            last_fetch=timezone.now() - timedelta(days=2),
        )
        mock_fetch_player_personal_data.return_value = {}

        update_player_data(player, force_refresh=True)

        player.refresh_from_db()
        self.assertEqual(player.name, "StableCaptain")
        self.assertEqual(player.total_battles, 77)

    @patch("warships.data._fetch_clan_data")
    def test_update_clan_data_does_not_blank_existing_clan_on_empty_upstream_response(self, mock_fetch_clan_data):
        clan = Clan.objects.create(
            clan_id=555,
            name="ExistingClan",
            tag="EC",
            members_count=33,
        )
        mock_fetch_clan_data.return_value = {}

        update_clan_data(clan.clan_id)

        clan.refresh_from_db()
        self.assertEqual(clan.name, "ExistingClan")
        self.assertEqual(clan.tag, "EC")
        self.assertEqual(clan.members_count, 33)
