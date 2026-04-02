from django.test import TestCase

from warships.models import Player


class PlayerModelTests(TestCase):
    def test_player_str_without_clan(self):
        player = Player.objects.create(name="SoloPlayer", player_id=1001)

        self.assertEqual(str(player), "SoloPlayer (1001) No Clan")
