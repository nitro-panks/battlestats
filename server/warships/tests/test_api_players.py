"""Unit tests for the WG player-API fallback boundary (`warships/api/players.py`).

These functions are the live Wargaming-integration seam the bulk observation
floor relies on; the key contract is distinguishing a transient upstream error
(abort, don't cache zeros) from a poison-batch / genuinely-empty result
(fall back per-player). Network-free — the request layer is mocked.
"""

from unittest import TestCase
from unittest.mock import patch

from warships.api.players import (
    _bulk_fetch_account_info,
    _per_player_account_fallback,
)


class BulkFetchAccountInfoTests(TestCase):
    @patch('warships.api.players.make_api_request_typed')
    def test_returns_payload_and_no_error_on_success(self, mock_typed):
        payload = {'1': {'nickname': 'A'}, '2': {'nickname': 'B'}}
        mock_typed.return_value = (payload, None)

        data, err = _bulk_fetch_account_info([1, 2], realm='na')

        self.assertEqual(data, payload)
        self.assertIsNone(err)

    @patch('warships.api.players.make_api_request_typed')
    def test_surfaces_request_limit_error_so_caller_can_abort(self, mock_typed):
        # REQUEST_LIMIT_EXCEEDED must reach the caller (abort), not look empty.
        mock_typed.return_value = (None, 'REQUEST_LIMIT_EXCEEDED')

        data, err = _bulk_fetch_account_info([1, 2], realm='na')

        self.assertEqual(data, {})
        self.assertEqual(err, 'REQUEST_LIMIT_EXCEEDED')

    @patch('warships.api.players.make_api_request_typed')
    def test_surfaces_invalid_account_error_for_per_player_fallback(self, mock_typed):
        # INVALID_ACCOUNT_ID (poison batch) is distinct from a rate limit; the
        # caller uses this to drop to the per-player isolation path.
        mock_typed.return_value = (None, 'INVALID_ACCOUNT_ID')

        data, err = _bulk_fetch_account_info([1, 2], realm='na')

        self.assertEqual(data, {})
        self.assertEqual(err, 'INVALID_ACCOUNT_ID')

    @patch('warships.api.players.make_api_request_typed')
    def test_normalizes_non_dict_payload_to_empty(self, mock_typed):
        mock_typed.return_value = ([], None)

        data, err = _bulk_fetch_account_info([1], realm='na')

        self.assertEqual(data, {})
        self.assertIsNone(err)

    @patch('warships.api.players.make_api_request_typed')
    def test_sends_comma_joined_account_ids(self, mock_typed):
        mock_typed.return_value = ({}, None)

        _bulk_fetch_account_info([11, 22, 33], realm='eu')

        called_params = mock_typed.call_args.args[1]
        self.assertEqual(called_params['account_id'], '11,22,33')


class PerPlayerAccountFallbackTests(TestCase):
    @patch('warships.api.players._fetch_player_personal_data')
    def test_maps_present_and_missing_players(self, mock_fetch):
        # Present player keeps its dict; an empty {} result normalises to None so
        # the caller's "None -> skip" slice handling fires.
        mock_fetch.side_effect = [{'nickname': 'A'}, {}]

        out = _per_player_account_fallback([1, 2], realm='na')

        self.assertEqual(out, {'1': {'nickname': 'A'}, '2': None})

    @patch('warships.api.players._fetch_player_personal_data')
    def test_exception_for_one_player_becomes_none_not_a_crash(self, mock_fetch):
        # One poison ID must not abort the whole isolation sweep.
        mock_fetch.side_effect = [RuntimeError('boom'), {'nickname': 'B'}]

        out = _per_player_account_fallback([1, 2], realm='na')

        self.assertEqual(out, {'1': None, '2': {'nickname': 'B'}})
