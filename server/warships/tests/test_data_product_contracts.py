from pathlib import Path

import yaml
from django.test import SimpleTestCase

from warships.serializers import PlayerSummarySerializer


REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_PRODUCT_CONTRACTS_DIR = REPO_ROOT / \
    "agents" / "contracts" / "data-products"


def _load_contract(filename: str) -> dict:
    with open(DATA_PRODUCT_CONTRACTS_DIR / filename, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _property_names(contract: dict) -> set[str]:
    schema = contract.get("schema", [])
    if not schema:
        return set()

    properties = schema[0].get("properties", [])
    return {
        property_def.get("name")
        for property_def in properties
        if isinstance(property_def, dict) and property_def.get("name")
    }


def _serializer_field_names(serializer_class: type) -> set[str]:
    return set(serializer_class().get_fields().keys())


class DataProductContractAlignmentTests(SimpleTestCase):
    def test_player_summary_contract_matches_serializer_fields(self):
        contract = _load_contract("player-summary.odcs.yaml")
        self.assertEqual(
            _property_names(contract),
            _serializer_field_names(PlayerSummarySerializer),
        )
