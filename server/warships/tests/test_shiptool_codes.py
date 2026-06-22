"""Tests for populate_shiptool_codes: the GameParams-index -> Ship Tool
short-code transform, and the Vortex-backed populate command."""

from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from warships.management.commands.populate_shiptool_codes import (
    derive_shiptool_code,
)
from warships.models import Ship


class DeriveShiptoolCodeTests(TestCase):
    def test_known_ships(self):
        # Mirrors Ship Tool's own createShortIndex: P<N>S<T><digits> -> N+T+digits.
        cases = {
            "PRSC110_Pr_66_Moskva": "RC110",   # the user's reference example
            "PJSB510_Shikishima": "JB510",
            "PBSC111_Edgar": "BC111",          # T11 supership
            "PASD505_Hill": "AD505",           # premium DD
            "PISC897_Azur_Gorizia": "IC897",   # collab/skinned cruiser
        }
        for name, expected in cases.items():
            self.assertEqual(derive_shiptool_code(name), expected, name)

    def test_leading_zeros_stripped(self):
        self.assertEqual(derive_shiptool_code("PRSC012_Foo"), "RC12")

    def test_non_conforming_returns_empty(self):
        for name in ("", "Aux_Something", "PRS110_Bad", "Snowflake"):
            self.assertEqual(derive_shiptool_code(name), "")


class PopulateShiptoolCodesCommandTests(TestCase):
    VORTEX = {
        "data": {
            "4179539408": {"name": "PRSC110_Pr_66_Moskva", "level": 10},
            "4293768176": {"name": "PJSB018_Yamato", "level": 10},
            # id IS in the catalog, but our DB row is the bracketed clone.
            "3340678608": {"name": "PRSC910_Pr_66_Moskva", "level": 10},
            # A vehicle with a non-conforming index -> no code derivable.
            "999": {"name": "Aux_Target", "level": 1},
        }
    }

    def setUp(self):
        Ship.objects.create(ship_id=4179539408, name="Moskva",
                            nation="ussr", ship_type="Cruiser", tier=10)
        Ship.objects.create(ship_id=4293768176, name="Yamato",
                            nation="japan", ship_type="Battleship", tier=10)
        # In our DB but absent from the Vortex catalog -> stays blank.
        Ship.objects.create(ship_id=111111, name="GhostShip",
                            nation="usa", ship_type="Cruiser", tier=8)
        # Bracketed (removed/test clone) variant -> code suppressed even
        # though its id IS in the Vortex catalog.
        Ship.objects.create(ship_id=3340678608, name="[Moskva]",
                            nation="ussr", ship_type="Cruiser", tier=10)

    def _run(self, **kwargs):
        with mock.patch(
            "warships.management.commands.populate_shiptool_codes.requests.get"
        ) as get:
            get.return_value = mock.Mock(
                json=mock.Mock(return_value=self.VORTEX),
                raise_for_status=mock.Mock(),
            )
            call_command("populate_shiptool_codes", **kwargs)

    def test_populates_matching_ships(self):
        self._run()
        self.assertEqual(Ship.objects.get(ship_id=4179539408).shiptool_code, "RC110")
        # Leading zeros are stripped (Ship Tool's createShortIndex uses 0*),
        # so PJSB018 (Yamato) -> JB18, verified to load on shiptool.st.
        self.assertEqual(Ship.objects.get(ship_id=4293768176).shiptool_code, "JB18")
        # Ship absent from Vortex keeps an empty code (link hidden).
        self.assertEqual(Ship.objects.get(ship_id=111111).shiptool_code, "")
        # Bracketed clone is suppressed despite its id being in the catalog.
        self.assertEqual(Ship.objects.get(ship_id=3340678608).shiptool_code, "")

    def test_idempotent(self):
        self._run()
        self._run()
        self.assertEqual(Ship.objects.get(ship_id=4179539408).shiptool_code, "RC110")

    def test_dry_run_writes_nothing(self):
        self._run(dry_run=True)
        self.assertEqual(Ship.objects.get(ship_id=4179539408).shiptool_code, "")
