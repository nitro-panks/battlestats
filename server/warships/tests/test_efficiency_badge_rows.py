from django.test import SimpleTestCase

from warships.data import join_efficiency_battle_stats


class JoinEfficiencyBattleStatsTests(SimpleTestCase):
    """`join_efficiency_battle_stats` attaches each badged ship's career random
    battles + win ratio (from battles_json, by ship_id) so the Efficiency table
    can show Battles and WR% next to the award grade. Read-time join: the stored
    efficiency_json is never mutated."""

    efficiency_rows = [
        {'ship_id': 100, 'top_grade_class': 1, 'ship_name': 'Des Moines'},
        {'ship_id': 200, 'top_grade_class': 2, 'ship_name': 'Bismarck'},
    ]
    battles_json = [
        {'ship_id': 100, 'pvp_battles': 1200, 'win_ratio': 0.58},
        {'ship_id': 200, 'pvp_battles': 300, 'win_ratio': 0.52},
    ]

    def test_joins_battles_and_win_ratio_by_ship_id(self):
        rows = join_efficiency_battle_stats(self.efficiency_rows, self.battles_json)
        by_id = {row['ship_id']: row for row in rows}

        self.assertEqual(by_id[100]['pvp_battles'], 1200)
        self.assertAlmostEqual(by_id[100]['win_ratio'], 0.58)
        self.assertEqual(by_id[200]['pvp_battles'], 300)
        self.assertAlmostEqual(by_id[200]['win_ratio'], 0.52)
        # Original badge fields ride through untouched.
        self.assertEqual(by_id[100]['top_grade_class'], 1)
        self.assertEqual(by_id[100]['ship_name'], 'Des Moines')

    def test_missing_battles_leaves_stats_null(self):
        # No battles_json at all, and a ship absent from a present battles_json,
        # both yield null stats — the client renders a dash.
        rows = join_efficiency_battle_stats(self.efficiency_rows, None)
        self.assertIsNone(rows[0]['pvp_battles'])
        self.assertIsNone(rows[0]['win_ratio'])

        rows = join_efficiency_battle_stats(
            [{'ship_id': 100, 'top_grade_class': 1}],
            [{'ship_id': 999, 'pvp_battles': 5, 'win_ratio': 0.5}],
        )
        self.assertIsNone(rows[0]['pvp_battles'])
        self.assertIsNone(rows[0]['win_ratio'])

    def test_does_not_mutate_stored_rows(self):
        rows = [{'ship_id': 100, 'top_grade_class': 1}]
        join_efficiency_battle_stats(rows, self.battles_json)
        self.assertNotIn('pvp_battles', rows[0])

    def test_non_list_efficiency_rows_yield_empty(self):
        self.assertEqual(join_efficiency_battle_stats(None, self.battles_json), [])
