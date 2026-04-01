from typing import Dict, List, Optional
import logging

from warships.api.client import DEFAULT_REALM, make_api_request

logging.basicConfig(level=logging.INFO)


def _fetch_clan_data(clan_id: str, realm: str = DEFAULT_REALM) -> Dict:
    """Fetch clan info for a given player_id."""
    params = {
        "clan_id": clan_id,
        "fields": "members_count,tag,name,clan_id,description,leader_id,leader_name"
    }
    logging.info(f' ---> Remote fetching clan info for clan_id: {clan_id}')
    data = _make_api_request("clans/info/", params, realm=realm)
    return data.get(str(clan_id), {}) if data else {}


def _fetch_clan_member_ids(clan_id: str, realm: str = DEFAULT_REALM) -> List[str]:
    """Fetch all members of a given clan."""
    params = {
        "clan_id": clan_id,
        "fields": "members_ids"
    }
    logging.info(f' ---> Remote fetching clan members for clan_id: {clan_id}')
    data = _make_api_request("clans/info/", params, realm=realm)
    return data.get(str(clan_id), {}).get('members_ids', []) if data else []


def _fetch_clan_battle_seasons_info(realm: str = DEFAULT_REALM) -> Dict:
    """Fetch clan battle season metadata."""
    params = {}
    logging.info(' ---> Remote fetching clan battle seasons metadata')
    data = _make_api_request("clans/season/", params, realm=realm)
    return data if data else {}


def _fetch_clan_battle_season_stats(account_id: int, realm: str = DEFAULT_REALM) -> Dict:
    """Fetch clan battle season stats for a single player account."""
    params = {
        "account_id": account_id,
    }
    logging.info(
        f' ---> Remote fetching clan battle season stats for account_id: {account_id}')
    data = _make_api_request("clans/seasonstats/", params, realm=realm)
    return data.get(str(account_id), {}) if data else {}


def _fetch_player_data_from_list(players: List[int], realm: str = DEFAULT_REALM) -> Dict:
    """Fetch all player data for a given list of player ids."""
    member_list = ','.join(map(str, players))
    params = {
        "account_id": member_list
    }
    logging.info(
        f' ---> Remote fetching player data for members: {member_list}')
    data = _make_api_request("account/info/", params, realm=realm)
    return data if data else {}


def _fetch_clan_membership_for_player(player_id: int, realm: str = DEFAULT_REALM) -> Dict:
    """Fetch clan membership data for a given player account id."""
    params = {
        "account_id": player_id,
        "extra": "clan",
        "fields": "account_id,account_name,clan_id,clan"
    }
    logging.info(
        f' ---> Remote fetching clan membership for player_id: {player_id}')
    data = _make_api_request("clans/accountinfo/", params, realm=realm)
    return data.get(str(player_id), {}) if data else {}


def _make_api_request(endpoint: str, params: Dict, realm: str = DEFAULT_REALM) -> Optional[Dict]:
    """Helper function to make API requests and handle responses."""
    data = make_api_request(endpoint, params, realm=realm)
    return data if isinstance(data, dict) or isinstance(data, list) else None
