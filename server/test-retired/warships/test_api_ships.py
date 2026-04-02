from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from warships.api.ships import _fetch_ship_info, build_ship_chart_name
from warships.models import Ship


LOCMEM_CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'TIMEOUT': 60,
    }
}


@override_settings(CACHES=LOCMEM_CACHES)
class ShipInfoApiTests(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    @patch("warships.api.ships._make_api_request")
    def test_fetch_ship_info_refreshes_incomplete_existing_ship(self, mock_make_api_request):
        Ship.objects.create(
            ship_id=123456789,
            name="",
            nation="",
            ship_type="",
            tier=None,
            is_premium=False,
        )

        mock_make_api_request.return_value = {
            "123456789": {
                "name": "Khabarovsk",
                "nation": "ussr",
                "is_premium": False,
                "type": "Destroyer",
                "tier": 10,
            }
        }

        ship = _fetch_ship_info("123456789")

        self.assertIsNotNone(ship)
        ship.refresh_from_db()
        self.assertEqual(ship.name, "Khabarovsk")
        self.assertEqual(ship.ship_type, "Destroyer")
        self.assertEqual(ship.tier, 10)
        self.assertEqual(ship.nation, "ussr")
        self.assertEqual(ship.chart_name, "Khabarovsk")
        mock_make_api_request.assert_called_once()

    def test_build_ship_chart_name_abbreviates_long_names(self):
        self.assertEqual(build_ship_chart_name(
            "Admiral Graf Spee"), "Adm. Graf Spee")
