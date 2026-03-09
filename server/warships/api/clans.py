from typing import Dict, List, Optional
import logging

from warships.api.client import make_api_request

logging.basicConfig(level=logging.INFO)


def _fetch_clan_data(clan_id: str) -> Dict:
    """Fetch clan info for a given player_id."""
    params = {
        "clan_id": clan_id,
        "fields": "members_count,tag,name,clan_id,description,leader_id,leader_name"
    }
    logging.info(f' ---> Remote fetching clan info for clan_id: {clan_id}')
    data = _make_api_request("clans/info/", params)
    return data.get(str(clan_id), {}) if data else {}


def _fetch_clan_member_ids(clan_id: str) -> List[str]:
    """Fetch all members of a given clan."""
    params = {
        "clan_id": clan_id,
        "fields": "members_ids"
    }
    logging.info(f' ---> Remote fetching clan members for clan_id: {clan_id}')
    data = _make_api_request("clans/info/", params)
    return data.get(str(clan_id), {}).get('members_ids', []) if data else []


def _fetch_clan_battle_seasons_info() -> Dict:
    """Fetch clan battle season metadata."""
    params = {}
    logging.info(' ---> Remote fetching clan battle seasons metadata')
    data = _make_api_request("clans/season/", params)
    return data if data else {}


def _fetch_clan_battle_season_stats(account_id: int) -> Dict:
    """Fetch clan battle season stats for a single player account."""
    params = {
        "account_id": account_id,
    }
    logging.info(
        f' ---> Remote fetching clan battle season stats for account_id: {account_id}')
    data = _make_api_request("clans/seasonstats/", params)
    return data.get(str(account_id), {}) if data else {}


def _fetch_player_data_from_list(players: List[int]) -> Dict:
    """Fetch all player data for a given list of player ids."""
    member_list = ','.join(map(str, players))
    params = {
        "account_id": member_list
    }
    logging.info(
        f' ---> Remote fetching player data for members: {member_list}')
    data = _make_api_request("account/info/", params)
    return data if data else {}


def _fetch_clan_membership_for_player(player_id: int) -> Dict:
    """Fetch clan membership data for a given player account id."""
    params = {
        "account_id": player_id,
        "extra": "clan",
        "fields": "account_id,account_name,clan_id,clan"
    }
    logging.info(
        f' ---> Remote fetching clan membership for player_id: {player_id}')
    data = _make_api_request("clans/accountinfo/", params)
    return data.get(str(player_id), {}) if data else {}


def _make_api_request(endpoint: str, params: Dict) -> Optional[Dict]:
    """Helper function to make API requests and handle responses."""
    data = make_api_request(endpoint, params)
    return data if isinstance(data, dict) or isinstance(data, list) else None
