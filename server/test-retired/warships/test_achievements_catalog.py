from django.test import SimpleTestCase

from warships.achievements_catalog import ACHIEVEMENT_CATALOG, PLAYER_SURFACE_ACHIEVEMENT_CODES


class AchievementCatalogTests(SimpleTestCase):
    def test_required_mvp_combat_codes_are_present(self):
        required_codes = {
            'PCH001_DoubleKill',
            'PCH003_MainCaliber',
            'PCH004_Dreadnought',
            'PCH005_Support',
            'PCH006_Withering',
            'PCH011_InstantKill',
            'PCH012_Arsonist',
            'PCH013_Liquidator',
            'PCH014_Headbutt',
            'PCH016_FirstBlood',
            'PCH017_Fireproof',
            'PCH018_Unsinkable',
            'PCH019_Detonated',
            'PCH020_ATBACaliber',
            'PCH023_Warrior',
        }

        self.assertTrue(required_codes.issubset(ACHIEVEMENT_CATALOG.keys()))
        self.assertTrue(required_codes.issubset(
            PLAYER_SURFACE_ACHIEVEMENT_CODES))

    def test_stable_labels_match_player_surface_expectations(self):
        self.assertEqual(
            ACHIEVEMENT_CATALOG['PCH016_FirstBlood']['label'], 'First Blood')
        self.assertEqual(
            ACHIEVEMENT_CATALOG['PCH003_MainCaliber']['label'], 'Main Caliber')
        self.assertEqual(
            ACHIEVEMENT_CATALOG['PCH023_Warrior']['label'], 'Kraken Unleashed')

    def test_event_and_campaign_codes_are_not_player_enabled(self):
        self.assertFalse(
            ACHIEVEMENT_CATALOG['PCH070_Campaign1Completed']['enabled_for_player_surface'])
        self.assertFalse(
            ACHIEVEMENT_CATALOG['PCH087_FillAlbum']['enabled_for_player_surface'])
        self.assertFalse(
            ACHIEVEMENT_CATALOG['PCH097_PVE_HON_WIN_ALL_DONE']['enabled_for_player_surface'])
