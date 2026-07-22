from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from warships.models import Player, PlayerExplorerSummary
from warships.data import (
    _build_player_clan_battle_wr_battles_population_correlation_payload,
    warm_player_clan_battle_wr_battles_population_correlation,
    fetch_player_clan_battle_wr_battles_correlation,
)


def _make_player(name, pid, battles, win_rate, is_hidden=False):
    now = timezone.now()
    player = Player.objects.create(
        name=name, player_id=pid, realm='na', last_fetch=now,
        is_hidden=is_hidden, pvp_battles=0,
    )
    PlayerExplorerSummary.objects.create(
        player=player,
        realm='na',
        clan_battle_total_battles=battles,
        clan_battle_overall_win_rate=win_rate,
        clan_battle_summary_updated_at=now,
    )
    return player


class ClanBattleWrBattlesCorrelationTests(TestCase):
    """The clan-battle population correlation mirrors the ranked one but sources
    per-player battles + WR straight from PlayerExplorerSummary CB fields."""

    def setUp(self):
        # Population: three visible players over the 50-battle floor.
        _make_player('CbPop1', 7001, 60, 45.0)
        _make_player('CbPop2', 7002, 120, 50.0)
        _make_player('CbPop3', 7003, 300, 55.0)
        # Excluded: below the floor, and hidden.
        _make_player('CbLow', 7004, 30, 60.0)
        _make_player('CbHidden', 7005, 200, 52.0, is_hidden=True)

    def test_builder_counts_only_eligible_rows(self):
        payload = _build_player_clan_battle_wr_battles_population_correlation_payload(realm='na')

        self.assertEqual(payload['metric'], 'clan_battle_wr_battles')
        self.assertEqual(payload['label'], 'Clan Battles vs Win Rate')
        # Only the three visible, >=50-battle players count.
        self.assertEqual(payload['tracked_population'], 3)
        self.assertTrue(payload['tiles'])
        self.assertEqual(payload['y_domain'], {'min': 30.0, 'max': 70.0, 'bin_width': 0.75})

    def test_fetch_overlays_player_point_from_pes_fields(self):
        target = _make_player('CbTarget', 7010, 100, 52.3)
        warm_player_clan_battle_wr_battles_population_correlation(realm='na')

        result = fetch_player_clan_battle_wr_battles_correlation(str(target.player_id), realm='na')
        point = result['player_point']
        self.assertIsNotNone(point)
        # Same PES fields the population scan uses — not the season sum.
        self.assertEqual(point['x'], 100.0)
        self.assertAlmostEqual(point['y'], 52.3)
        self.assertEqual(point['label'], 'CbTarget')

    def test_fetch_returns_null_point_without_pes_cb_summary(self):
        now = timezone.now()
        bare = Player.objects.create(
            name='CbNoSummary', player_id=7011, realm='na', last_fetch=now,
            is_hidden=False, pvp_battles=0,
        )
        warm_player_clan_battle_wr_battles_population_correlation(realm='na')

        result = fetch_player_clan_battle_wr_battles_correlation(str(bare.player_id), realm='na')
        self.assertIsNone(result['player_point'])

    def test_endpoint_returns_payload(self):
        target = _make_player('CbEndpoint', 7020, 150, 51.0)
        warm_player_clan_battle_wr_battles_population_correlation(realm='na')

        with patch('warships.tasks.queue_player_clan_battle_wr_battles_correlation_refresh'):
            response = self.client.get(
                f'/api/fetch/player_correlation/clan_battle_wr_battles/{target.player_id}/')

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['metric'], 'clan_battle_wr_battles')
        self.assertIsNotNone(payload['player_point'])
        self.assertEqual(payload['player_point']['x'], 150.0)
