from unittest import TestCase
from unittest.mock import patch

from warships.api.clans import (
    _fetch_clan_battle_season_stats,
    _fetch_clan_member_ids,
)


class ClanApiHelperTests(TestCase):
    @patch('warships.api.clans._make_api_request')
    def test_fetch_clan_member_ids_returns_empty_list_when_upstream_clan_entry_is_none(self, mock_make_api_request):
        mock_make_api_request.return_value = {'7603': None}

        member_ids = _fetch_clan_member_ids('7603', realm='na')

        self.assertEqual(member_ids, [])


class ClanBattleSeasonStatsFetchTests(TestCase):
    """`_fetch_clan_battle_season_stats` must distinguish an upstream failure
    (None) from a player who genuinely has no clan-battle history ({}), so the
    caller doesn't cache a transient REQUEST_LIMIT_EXCEEDED as real zeros."""

    @patch('warships.api.clans._make_api_request')
    def test_returns_none_when_upstream_request_fails(self, mock_make_api_request):
        mock_make_api_request.return_value = None

        self.assertIsNone(_fetch_clan_battle_season_stats(123, realm='na'))

    @patch('warships.api.clans._make_api_request')
    def test_returns_empty_dict_when_player_has_no_clan_battle_history(self, mock_make_api_request):
        mock_make_api_request.return_value = {'123': None}

        self.assertEqual(_fetch_clan_battle_season_stats(123, realm='na'), {})

    @patch('warships.api.clans._make_api_request')
    def test_returns_account_payload_on_success(self, mock_make_api_request):
        payload = {'seasons': [{'season_id': 22, 'battles': 10}]}
        mock_make_api_request.return_value = {'123': payload}

        self.assertEqual(
            _fetch_clan_battle_season_stats(123, realm='na'), payload)
