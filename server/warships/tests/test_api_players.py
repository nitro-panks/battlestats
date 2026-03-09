from unittest.mock import patch

from django.test import TestCase

from warships.api.players import _fetch_player_id_by_name
from warships.models import Player


class PlayerApiLookupTests(TestCase):
    def test_fetch_player_id_by_name_uses_local_match_first(self):
        Player.objects.create(name="ExactLocal", player_id=12345)

        with patch("warships.api.players._make_api_request") as mock_request:
            result = _fetch_player_id_by_name("ExactLocal")

        self.assertEqual(result, "12345")
        mock_request.assert_not_called()

    @patch("warships.api.players._make_api_request")
    def test_fetch_player_id_by_name_rejects_non_exact_upstream_match(self, mock_request):
        mock_request.return_value = [
            {"account_id": 99, "nickname": "ExactCaptain123"}
        ]

        result = _fetch_player_id_by_name("ExactCaptain")

        self.assertIsNone(result)

    @patch("warships.api.players._make_api_request")
    def test_fetch_player_id_by_name_returns_none_for_malformed_payload(self, mock_request):
        mock_request.return_value = {"unexpected": "shape"}

        result = _fetch_player_id_by_name("MalformedCaptain")

        self.assertIsNone(result)

    def test_fetch_player_id_by_name_rejects_overlong_input(self):
        result = _fetch_player_id_by_name("X" * 65)

        self.assertIsNone(result)