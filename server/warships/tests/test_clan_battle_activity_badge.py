from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from warships.data import summarize_clan_battle_activity_badge


class ClanBattleActivityBadgeTests(TestCase):
    """Covers the clan-battle activity badge helper.

    This helper survived the 3.0 Best/Popular landing-board decommission; its
    only former caller (the landing best-clans builder) was removed, so this is
    now its dedicated coverage.
    """

    def test_clan_battle_activity_badge_requires_recent_sustained_participation(self):
        today = timezone.now().date()

        def season_date(offset_days: int) -> str:
            return (today - timedelta(days=offset_days)).strftime('%Y-%m-%d')

        season_meta = {
            season_id: {
                'name': f'Season {season_id}',
                'label': f'S{season_id}',
                'start_date': season_date((12 - season_id) * 90 + 21),
                'end_date': season_date((12 - season_id) * 90),
            }
            for season_id in range(1, 13)
        }

        sustained_rows = [
            {'season_id': season_id, 'participants': 8, 'roster_battles': 28}
            for season_id in (12, 11, 10)
        ]
        low_share_rows = [
            {'season_id': season_id, 'participants': 2, 'roster_battles': 28}
            for season_id in (12, 11, 10)
        ]
        one_season_spike = [
            {'season_id': 12, 'participants': 12, 'roster_battles': 40}
        ]

        with patch('warships.data._get_clan_battle_seasons_metadata', return_value=season_meta):
            sustained = summarize_clan_battle_activity_badge(
                sustained_rows,
                total_members=40,
                reference_date=today,
            )
            low_share = summarize_clan_battle_activity_badge(
                low_share_rows,
                total_members=40,
                reference_date=today,
            )
            spike = summarize_clan_battle_activity_badge(
                one_season_spike,
                total_members=40,
                reference_date=today,
            )

        self.assertTrue(sustained['is_clan_battle_active'])
        self.assertFalse(low_share['is_clan_battle_active'])
        self.assertFalse(spike['is_clan_battle_active'])
