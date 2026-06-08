"""Output-shape tests for the public data-product serializers.

`test_data_product_contracts.py` already guards the serializer *field names*
against the published ODCS contract. This complements it on the axis that
test can't see: the emitted *types*, and that nullable fields actually accept
None (a dropped ``allow_null=True`` would pass the name check but crash on the
many sparse/hidden players in production).
"""

from django.test import SimpleTestCase

from warships.serializers import PlayerSummarySerializer


def _full_summary_input() -> dict:
    return {
        'kill_ratio': 1.2, 'player_score': 1450.0, 'player_id': 42,
        'name': 'Tester', 'is_hidden': False, 'days_since_last_battle': 3,
        'last_battle_date': '2026-06-01', 'account_age_days': 900,
        'pvp_ratio': 54.3, 'pvp_battles': 5000, 'pvp_survival_rate': 49.1,
        'battles_last_29_days': 120, 'wins_last_29_days': 70,
        'active_days_last_29_days': 18, 'recent_win_rate': 58.3,
        'activity_trend_direction': 'up', 'ships_played_total': 80,
        'ship_type_spread': 4, 'tier_spread': 6,
        'ranked_seasons_participated': 5, 'latest_ranked_battles': 200,
        'highest_ranked_league_recent': 'Gold',
    }


class PlayerSummarySerializerShapeTests(SimpleTestCase):
    def test_full_payload_emits_declared_python_types(self):
        data = PlayerSummarySerializer(_full_summary_input()).data

        # Exact key set — no field silently added or dropped.
        self.assertEqual(set(data.keys()),
                         set(PlayerSummarySerializer().get_fields().keys()))
        # Representative type checks across the field kinds.
        self.assertIsInstance(data['player_id'], int)
        self.assertIsInstance(data['name'], str)
        self.assertIsInstance(data['is_hidden'], bool)
        self.assertIsInstance(data['pvp_ratio'], float)
        self.assertIsInstance(data['pvp_battles'], int)

    def test_nullable_fields_pass_through_none(self):
        # A sparse/hidden player: only the three non-null fields are populated.
        sparse = {k: None for k in PlayerSummarySerializer().get_fields()}
        sparse.update(player_id=7, name='Hidden', is_hidden=True)

        data = PlayerSummarySerializer(sparse).data

        self.assertIsNone(data['kill_ratio'])
        self.assertIsNone(data['pvp_battles'])
        self.assertIsNone(data['last_battle_date'])
        self.assertEqual(data['player_id'], 7)
        self.assertEqual(data['name'], 'Hidden')
