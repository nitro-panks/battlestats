import inspect
import re
from pathlib import Path

import yaml
from django.test import SimpleTestCase

from warships.api import clans as clans_api
from warships.api import players as players_api


REPO_ROOT = Path(__file__).resolve().parents[3]
UPSTREAM_CONTRACTS_DIR = REPO_ROOT / "agents" / "contracts" / "upstream"
SCHEMA_GROUP_KEYS = {
    "base_fields",
    "extra_fields",
    "commonly_used_statistics",
    "optional_behavior",
    "fields",
}


def _load_contract(filename: str) -> dict:
    with open(UPSTREAM_CONTRACTS_DIR / filename, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _flatten_schema_paths(value: object, prefix: str = "") -> set[str]:
    paths: set[str] = set()
    if not isinstance(value, dict):
        return paths

    for key, nested in value.items():
        if key in SCHEMA_GROUP_KEYS:
            paths.update(_flatten_schema_paths(nested, prefix))
            continue

        current = f"{prefix}.{key}" if prefix else key
        paths.add(current)
        if isinstance(nested, dict):
            paths.update(_flatten_schema_paths(nested, current))

    return paths


def _documented_response_paths(contract: dict) -> set[str]:
    response = contract.get("response", {})
    paths: set[str] = set()

    for section_name in ("expected_data_shape", "expected_item_shape"):
        section = response.get(section_name)
        if isinstance(section, dict):
            paths.update(_flatten_schema_paths(section))

    return paths


def _requested_field_paths(func: object) -> set[str]:
    source = inspect.getsource(func)
    match = re.search(r'["\']fields["\']\s*:\s*["\']([^"\']+)["\']', source)
    if not match:
        return set()

    return {
        field.strip()
        for field in match.group(1).split(",")
        if field.strip()
    }


class UpstreamContractAlignmentTests(SimpleTestCase):
    def test_account_list_requested_fields_exist_in_contract(self):
        contract = _load_contract("wows-account-list.yaml")
        documented_paths = _documented_response_paths(contract)
        requested_paths = _requested_field_paths(
            players_api._fetch_player_id_by_name)

        self.assertTrue(requested_paths)
        self.assertTrue(requested_paths.issubset(documented_paths))

    def test_account_statsbydate_requested_fields_exist_in_contract(self):
        contract = _load_contract("wows-account-statsbydate.yaml")
        documented_paths = _documented_response_paths(contract)
        requested_paths = _requested_field_paths(
            players_api._fetch_snapshot_data)

        self.assertTrue(requested_paths)
        self.assertTrue(requested_paths.issubset(documented_paths))

    def test_account_achievements_requested_fields_exist_in_contract(self):
        contract = _load_contract("wows-account-achievements.yaml")
        documented_paths = _documented_response_paths(contract)
        requested_paths = _requested_field_paths(
            players_api._fetch_player_achievements)

        self.assertTrue(requested_paths)
        self.assertTrue(requested_paths.issubset(documented_paths))

    def test_clans_accountinfo_requested_fields_exist_in_contract(self):
        contract = _load_contract("wows-clans-accountinfo.yaml")
        documented_paths = _documented_response_paths(contract)
        requested_paths = _requested_field_paths(
            clans_api._fetch_clan_membership_for_player)

        self.assertTrue(requested_paths)
        self.assertTrue(requested_paths.issubset(documented_paths))

    def test_account_info_core_hydration_fields_exist_in_contract(self):
        contract = _load_contract("wows-account-info.yaml")
        documented_paths = _documented_response_paths(contract)
        relied_on_paths = {
            "account_id",
            "nickname",
            "created_at",
            "last_battle_time",
            "hidden_profile",
            "stats_updated_at",
            "statistics",
            "statistics.battles",
            "statistics.pvp",
            "statistics.pvp.battles",
            "statistics.pvp.wins",
            "statistics.pvp.losses",
            "statistics.pvp.survived_battles",
            "statistics.pvp.survived_wins",
        }

        self.assertTrue(relied_on_paths.issubset(documented_paths))
