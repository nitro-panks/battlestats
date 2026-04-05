from unittest import TestCase
from unittest.mock import patch

from warships.api.clans import _fetch_clan_member_ids


class ClanApiHelperTests(TestCase):
    @patch('warships.api.clans._make_api_request')
    def test_fetch_clan_member_ids_returns_empty_list_when_upstream_clan_entry_is_none(self, mock_make_api_request):
        mock_make_api_request.return_value = {'7603': None}

        member_ids = _fetch_clan_member_ids('7603', realm='na')

        self.assertEqual(member_ids, [])