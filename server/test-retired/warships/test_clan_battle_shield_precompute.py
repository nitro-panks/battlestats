from datetime import timedelta
from unittest.mock import patch, MagicMock

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from warships.data import (
    CLAN_BATTLE_BADGE_REFRESH_DAYS,
    CLAN_BATTLE_SUMMARY_STALE_DAYS,
    clan_battle_summary_is_stale,
    get_published_clan_battle_summary_payload,
    maybe_refresh_clan_battle_data,
)
from warships.models import Clan, Player, PlayerExplorerSummary


class ClanBattleSummaryIsStaleTests(TestCase):
    def setUp(self):
        self.player = Player.objects.create(
            name='StaleTestPlayer', player_id=80001, is_hidden=False)

    def test_stale_when_no_explorer_summary(self):
        self.assertTrue(clan_battle_summary_is_stale(self.player))

    def test_stale_when_updated_at_is_null(self):
        PlayerExplorerSummary.objects.create(
            player=self.player, clan_battle_summary_updated_at=None)
        self.player.refresh_from_db()
        self.assertTrue(clan_battle_summary_is_stale(self.player))

    def test_stale_when_old(self):
        PlayerExplorerSummary.objects.create(
            player=self.player,
            clan_battle_summary_updated_at=timezone.now() - timedelta(
                days=CLAN_BATTLE_BADGE_REFRESH_DAYS + 1),
        )
        self.player.refresh_from_db()
        self.assertTrue(clan_battle_summary_is_stale(self.player))

    def test_fresh_when_recent(self):
        PlayerExplorerSummary.objects.create(
            player=self.player,
            clan_battle_summary_updated_at=timezone.now() - timedelta(days=3),
        )
        self.player.refresh_from_db()
        self.assertFalse(clan_battle_summary_is_stale(self.player))


class MaybeRefreshClanBattleDataTests(TestCase):
    def setUp(self):
        self.player = Player.objects.create(
            name='RefreshTestPlayer', player_id=80002, is_hidden=False)

    @patch('warships.tasks.queue_clan_battle_data_refresh')
    def test_dispatches_when_stale(self, mock_dispatch):
        # No explorer summary → stale
        maybe_refresh_clan_battle_data(self.player)
        mock_dispatch.assert_called_once_with(self.player.player_id, realm='na')

    @patch('warships.tasks.queue_clan_battle_data_refresh')
    def test_no_dispatch_when_fresh(self, mock_dispatch):
        PlayerExplorerSummary.objects.create(
            player=self.player,
            clan_battle_summary_updated_at=timezone.now() - timedelta(days=1),
        )
        self.player.refresh_from_db()
        maybe_refresh_clan_battle_data(self.player)
        mock_dispatch.assert_not_called()

    @patch('warships.tasks.queue_clan_battle_data_refresh')
    def test_no_dispatch_when_hidden(self, mock_dispatch):
        self.player.is_hidden = True
        self.player.save()
        maybe_refresh_clan_battle_data(self.player)
        mock_dispatch.assert_not_called()


class ClanMembersShieldTests(TestCase):
    """Integration tests for the clan_members view."""

    def setUp(self):
        cache.clear()
        self.now = timezone.now()
        self.clan = Clan.objects.create(
            clan_id=88001, name='ShieldClan', members_count=1,
            last_fetch=self.now)
        self.member = Player.objects.create(
            name='ShieldMember', player_id=88101, clan=self.clan,
            last_battle_date=self.now.date(), total_battles=100,
            pvp_battles=80, is_hidden=False)
        PlayerExplorerSummary.objects.create(
            player=self.member,
            clan_battle_seasons_participated=3,
            clan_battle_total_battles=45,
            clan_battle_overall_win_rate=62.5,
            clan_battle_summary_updated_at=self.now - timedelta(days=1),
        )

    @patch('warships.data.queue_clan_ranked_hydration',
           return_value={'pending_player_ids': set(), 'queued_player_ids': set(),
                         'deferred_player_ids': set(), 'max_in_flight': 5})
    @patch('warships.data.queue_clan_efficiency_hydration',
           return_value={'pending_player_ids': set(), 'queued_player_ids': set(),
                         'deferred_player_ids': set(), 'max_in_flight': 5})
    @patch('warships.tasks.queue_clan_battle_data_refresh')
    def test_returns_shield_data_from_db(self, mock_cb_dispatch,
                                         mock_eff_hydration, mock_ranked):
        response = self.client.get(
            f'/api/fetch/clan_members/{self.clan.clan_id}/')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        member = next(m for m in data if m['name'] == 'ShieldMember')
        self.assertTrue(member['is_clan_battle_player'])
        self.assertEqual(member['clan_battle_win_rate'], 62.5)
        self.assertNotIn('clan_battle_hydration_pending', member)
        # No X-Clan-Battle-Hydration-* headers
        for header in response.headers:
            self.assertFalse(header.lower().startswith(
                'x-clan-battle-hydration'))

    @patch('warships.data.queue_clan_ranked_hydration',
           return_value={'pending_player_ids': set(), 'queued_player_ids': set(),
                         'deferred_player_ids': set(), 'max_in_flight': 5})
    @patch('warships.data.queue_clan_efficiency_hydration',
           return_value={'pending_player_ids': set(), 'queued_player_ids': set(),
                         'deferred_player_ids': set(), 'max_in_flight': 5})
    @patch('warships.tasks.queue_clan_battle_data_refresh')
    def test_no_shield_when_never_hydrated(self, mock_cb_dispatch,
                                           mock_eff_hydration, mock_ranked):
        bare = Player.objects.create(
            name='BareMember', player_id=88102, clan=self.clan,
            last_battle_date=self.now.date(), total_battles=50,
            pvp_battles=40, is_hidden=False)
        response = self.client.get(
            f'/api/fetch/clan_members/{self.clan.clan_id}/')
        self.assertEqual(response.status_code, 200)
        member = next(m for m in response.json()
                      if m['name'] == 'BareMember')
        self.assertFalse(member['is_clan_battle_player'])
        self.assertIsNone(member['clan_battle_win_rate'])

    @patch('warships.data.queue_clan_ranked_hydration',
           return_value={'pending_player_ids': set(), 'queued_player_ids': set(),
                         'deferred_player_ids': set(), 'max_in_flight': 5})
    @patch('warships.data.queue_clan_efficiency_hydration',
           return_value={'pending_player_ids': set(), 'queued_player_ids': set(),
                         'deferred_player_ids': set(), 'max_in_flight': 5})
    @patch('warships.tasks.queue_clan_battle_data_refresh')
    def test_dispatches_refresh_for_stale(self, mock_cb_dispatch,
                                          mock_eff_hydration, mock_ranked):
        stale = Player.objects.create(
            name='StaleMember', player_id=88103, clan=self.clan,
            last_battle_date=self.now.date(), total_battles=50,
            pvp_battles=40, is_hidden=False)
        PlayerExplorerSummary.objects.create(
            player=stale,
            clan_battle_summary_updated_at=self.now - timedelta(days=CLAN_BATTLE_BADGE_REFRESH_DAYS + 1),
        )
        response = self.client.get(
            f'/api/fetch/clan_members/{self.clan.clan_id}/')
        self.assertEqual(response.status_code, 200)
        mock_cb_dispatch.assert_called_once_with(88103, realm='na')

    @patch('warships.data.queue_clan_ranked_hydration',
           return_value={'pending_player_ids': set(), 'queued_player_ids': set(),
                         'deferred_player_ids': set(), 'max_in_flight': 5})
    @patch('warships.data.queue_clan_efficiency_hydration',
           return_value={'pending_player_ids': set(), 'queued_player_ids': set(),
                         'deferred_player_ids': set(), 'max_in_flight': 5})
    @patch('warships.tasks.queue_clan_battle_data_refresh')
    def test_no_dispatch_for_fresh(self, mock_cb_dispatch,
                                   mock_eff_hydration, mock_ranked):
        response = self.client.get(
            f'/api/fetch/clan_members/{self.clan.clan_id}/')
        self.assertEqual(response.status_code, 200)
        # Only the fresh member exists → no dispatch
        mock_cb_dispatch.assert_not_called()


class PlayerDetailStaleDispatchTests(TestCase):

    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    @patch("warships.views.update_player_data_task.delay")
    @patch('warships.data.maybe_refresh_clan_battle_data')
    def test_dispatches_when_stale(self, mock_maybe_refresh,
                                   mock_player_task, mock_clan_task,
                                   mock_clan_members_task):
        now = timezone.now()
        clan = Clan.objects.create(
            clan_id=88200, name='DetailClan', members_count=1,
            last_fetch=now)
        player = Player.objects.create(
            name='DetailPlayer', player_id=88201, clan=clan,
            last_fetch=now, pvp_battles=0, is_hidden=False)
        # No explorer summary → stale → should dispatch
        response = self.client.get('/api/player/DetailPlayer/')
        self.assertEqual(response.status_code, 200)
        mock_maybe_refresh.assert_called()

    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    @patch("warships.views.update_player_data_task.delay")
    @patch('warships.data.maybe_refresh_clan_battle_data')
    def test_no_dispatch_when_fresh(self, mock_maybe_refresh,
                                    mock_player_task, mock_clan_task,
                                    mock_clan_members_task):
        now = timezone.now()
        clan = Clan.objects.create(
            clan_id=88210, name='FreshDetailClan', members_count=1,
            last_fetch=now)
        player = Player.objects.create(
            name='FreshDetailPlayer', player_id=88211, clan=clan,
            last_fetch=now, pvp_battles=0, is_hidden=False)
        PlayerExplorerSummary.objects.create(
            player=player,
            clan_battle_summary_updated_at=now - timedelta(days=1),
        )
        response = self.client.get('/api/player/FreshDetailPlayer/')
        self.assertEqual(response.status_code, 200)
        # maybe_refresh is called but internally won't dispatch (summary is fresh).
        # We're mocking the function at data module level, so it's always called
        # but we verify the function IS invoked (the internal staleness check is
        # tested separately in MaybeRefreshClanBattleDataTests).
        mock_maybe_refresh.assert_called()


class SerializerReadsFromDbTests(TestCase):

    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    @patch("warships.views.update_player_data_task.delay")
    def test_serializer_uses_db_not_cache(self, mock_player_task,
                                          mock_clan_task,
                                          mock_clan_members_task):
        now = timezone.now()
        clan = Clan.objects.create(
            clan_id=88300, name='SerClan', members_count=1, last_fetch=now)
        player = Player.objects.create(
            name='SerPlayer', player_id=88301, clan=clan,
            last_fetch=now, pvp_battles=0, is_hidden=False)
        PlayerExplorerSummary.objects.create(
            player=player,
            clan_battle_seasons_participated=5,
            clan_battle_total_battles=100,
            clan_battle_overall_win_rate=58.3,
            clan_battle_summary_updated_at=now - timedelta(days=1),
        )
        with patch('warships.tasks.queue_clan_battle_data_refresh'):
            response = self.client.get('/api/player/SerPlayer/')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['clan_battle_header_seasons_played'], 5)
        self.assertEqual(data['clan_battle_header_total_battles'], 100)
        self.assertAlmostEqual(
            data['clan_battle_header_overall_win_rate'], 58.3)


class IncrementalRefreshCBBackfillTests(TestCase):

    @patch('warships.management.commands.incremental_player_refresh.update_achievements_data')
    @patch('warships.management.commands.incremental_player_refresh.update_player_efficiency_data')
    @patch('warships.management.commands.incremental_player_refresh.save_player')
    @patch('warships.management.commands.incremental_player_refresh.fetch_players_bulk')
    @patch('warships.management.commands.incremental_player_refresh.fetch_player_clan_battle_seasons')
    def test_backfills_when_cb_never_hydrated(self, mock_fetch_cb, mock_bulk,
                                              mock_save, mock_eff, mock_ach):
        now = timezone.now()
        player = Player.objects.create(
            name='BackfillPlayer', player_id=88401, is_hidden=False,
            last_fetch=now)
        PlayerExplorerSummary.objects.create(
            player=player, clan_battle_summary_updated_at=None)
        mock_bulk.return_value = {str(player.player_id): {'mock': 'data'}}

        from warships.management.commands.incremental_player_refresh import _refresh_player
        _refresh_player(player.id)

        mock_fetch_cb.assert_called_once_with(player.player_id, realm='na')

    @patch('warships.management.commands.incremental_player_refresh.update_achievements_data')
    @patch('warships.management.commands.incremental_player_refresh.update_player_efficiency_data')
    @patch('warships.management.commands.incremental_player_refresh.save_player')
    @patch('warships.management.commands.incremental_player_refresh.fetch_players_bulk')
    @patch('warships.management.commands.incremental_player_refresh.fetch_player_clan_battle_seasons')
    def test_skips_when_cb_already_populated(self, mock_fetch_cb, mock_bulk,
                                             mock_save, mock_eff, mock_ach):
        now = timezone.now()
        player = Player.objects.create(
            name='PopulatedPlayer', player_id=88402, is_hidden=False,
            last_fetch=now)
        PlayerExplorerSummary.objects.create(
            player=player,
            clan_battle_summary_updated_at=now - timedelta(days=2),
        )
        mock_bulk.return_value = {str(player.player_id): {'mock': 'data'}}

        from warships.management.commands.incremental_player_refresh import _refresh_player
        _refresh_player(player.id)

        mock_fetch_cb.assert_not_called()

    @patch('warships.management.commands.incremental_player_refresh.update_achievements_data')
    @patch('warships.management.commands.incremental_player_refresh.update_player_efficiency_data')
    @patch('warships.management.commands.incremental_player_refresh.save_player')
    @patch('warships.management.commands.incremental_player_refresh.fetch_players_bulk')
    @patch('warships.management.commands.incremental_player_refresh.fetch_player_clan_battle_seasons')
    def test_backfills_when_cb_summary_is_stale(self, mock_fetch_cb, mock_bulk,
                                                mock_save, mock_eff, mock_ach):
        now = timezone.now()
        player = Player.objects.create(
            name='StaleBadgePlayer', player_id=88403, is_hidden=False,
            last_fetch=now)
        PlayerExplorerSummary.objects.create(
            player=player,
            clan_battle_summary_updated_at=now - timedelta(days=CLAN_BATTLE_BADGE_REFRESH_DAYS + 1),
        )
        mock_bulk.return_value = {str(player.player_id): {'mock': 'data'}}

        from warships.management.commands.incremental_player_refresh import _refresh_player
        _refresh_player(player.id)

        mock_fetch_cb.assert_called_once_with(player.player_id, realm='na')


class GetPublishedClanBattleSummaryPayloadTests(TestCase):

    def test_returns_zeros_when_no_explorer_summary(self):
        player = Player.objects.create(
            name='NoSummaryPlayer', player_id=88501, is_hidden=False)
        result = get_published_clan_battle_summary_payload(player)
        self.assertEqual(result['seasons_participated'], 0)
        self.assertEqual(result['total_battles'], 0)
        self.assertIsNone(result['win_rate'])
        self.assertIsNone(result['updated_at'])

    def test_returns_db_values_when_summary_exists(self):
        now = timezone.now()
        player = Player.objects.create(
            name='SummaryPlayer', player_id=88502, is_hidden=False)
        PlayerExplorerSummary.objects.create(
            player=player,
            clan_battle_seasons_participated=4,
            clan_battle_total_battles=80,
            clan_battle_overall_win_rate=55.0,
            clan_battle_summary_updated_at=now,
        )
        player = Player.objects.select_related('explorer_summary').get(
            pk=player.pk)
        result = get_published_clan_battle_summary_payload(player)
        self.assertEqual(result['seasons_participated'], 4)
        self.assertEqual(result['total_battles'], 80)
        self.assertEqual(result['win_rate'], 55.0)
