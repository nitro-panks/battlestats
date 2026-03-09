import logging
from typing import Dict, Optional

from warships.models import Player
from warships.api.client import make_api_request

logging.basicConfig(level=logging.INFO)


def _fetch_snapshot_data(player_id: int, dates: str = '') -> Dict:
    """Fetch JSON data containing recent battle stats for a given player_id."""
    params = {
        "account_id": player_id,
        "dates": dates,
        "fields": "pvp.account_id,pvp.battles,pvp.wins,pvp.survived_battles,pvp.battle_type,pvp.date"
    }
    logging.info(f' ---> Remote fetching snapshot for player_id: {player_id}')
    data = _make_api_request("account/statsbydate/", params)

    return data.get(str(player_id), {}).get('pvp', {}) if data else {}


def _fetch_player_personal_data(player_id: int) -> Dict:
    """Fetch JSON data for a given player_id."""
    params = {
        "account_id": player_id
    }
    logging.info(
        f' ---> Remote fetching player personal (account) data for player_id: {player_id}')
    data = _make_api_request("account/info/", params)
    return data.get(str(player_id), {}) if data else {}


def _fetch_ranked_account_info(player_id: int) -> Dict:
    """Fetch ranked battles account info (rank_info) for a player."""
    params = {
        "account_id": player_id,
        "fields": "rank_info"
    }
    logging.info(
        f' ---> Remote fetching ranked account info for player_id: {player_id}')
    data = _make_api_request("seasons/accountinfo/", params)
    return data.get(str(player_id), {}) if data else {}


def _fetch_ranked_seasons_info() -> Dict:
    """Fetch all ranked season metadata (names, dates)."""
    params = {
        "fields": "season_id,season_name,start_at,close_at"
    }
    logging.info(' ---> Remote fetching ranked seasons metadata')
    data = _make_api_request("seasons/info/", params)
    return data if data else {}


def _fetch_player_id_by_name(player_name: str) -> Optional[str]:
    """Return a player_id from local cache first, then WoWS API exact lookup."""
    normalized_name = player_name.strip()
    if not normalized_name or len(normalized_name) > 64:
        return None

    local_player = Player.objects.filter(name__iexact=normalized_name).first()
    if local_player:
        return str(local_player.player_id)

    params = {
        "search": normalized_name,
        "type": "exact",
        "limit": 1,
        "fields": "account_id,nickname"
    }
    logging.info(f' ---> Remote fetching player info for: {normalized_name}')
    data = _make_api_request("account/list/", params)

    if not data:
        return None

    try:
        first_match = data[0]
        nickname = str(first_match["nickname"])
        if nickname.casefold() != normalized_name.casefold():
            logging.warning(
                "Skipping non-exact upstream player match for '%s': '%s'",
                normalized_name,
                nickname,
            )
            return None
        return str(first_match['account_id'])
    except (KeyError, IndexError, TypeError):
        logging.error(
            f"ERROR: Accessing player data by name: {normalized_name}")
        return None


def _make_api_request(endpoint: str, params: Dict) -> Optional[Dict]:
    """Helper function to make API requests and handle responses."""
    data = make_api_request(endpoint, params)
    return data if isinstance(data, dict) or isinstance(data, list) else None
