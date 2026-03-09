import logging
from typing import Optional, Dict, Any

from django.core.cache import cache

from warships.api.client import make_api_request
from warships.models import Ship


logging.basicConfig(level=logging.INFO)


def _fetch_ranked_ship_stats_for_player(player_id: int, season_ids: Optional[list[int]] = None) -> list[dict[str, Any]]:
    """Fetch ranked ship stats for a player, optionally scoped to one or more seasons."""
    params = {
        "account_id": player_id,
    }
    if season_ids:
        params["season_id"] = ",".join(str(season_id) for season_id in season_ids)

    logging.info(
        f' ---> Remote fetching ranked ship stats for player_id: {player_id}')
    data = _make_api_request("seasons/shipstats/", params)

    try:
        rows = data[str(player_id)]
    except (KeyError, TypeError):
        rows = []

    return rows if isinstance(rows, list) else []

def _fetch_ship_stats_for_player(player_id: str) -> Dict:
    """Fetch all competitive data for all ships for a given player_id."""
    params = {
        "account_id": player_id
    }
    logging.info(
        f' ---> EXPENSIVE: Remote fetching all battle stats for player_id: {player_id}')
    data = _make_api_request("ships/stats/", params)

    data_dict = {}
    try:
        data_dict = data[str(player_id)]
    except (KeyError, TypeError):
        keys_to_print = list(data.keys())[
            :10] if isinstance(data, dict) else []
        logging.error(
            f'Unexpected response while loading ship data: {keys_to_print}')

    return data_dict


def _fetch_ship_info(ship_id: str) -> Optional[Ship]:
    """Get or create a specific ship model and populate with non-competitive data."""
    try:
        clean_ship_id = int(ship_id)
        if clean_ship_id < 1:
            return None
    except ValueError:
        logging.error(f"ERROR: Invalid ship_id: {ship_id}")
        return None

    cache_key = f'ship:{clean_ship_id}'
    cached = cache.get(cache_key)
    if cached is not None and cached.name and cached.ship_type and cached.tier is not None:
        return cached
    if cached is not None:
        cache.delete(cache_key)

    ship, created = Ship.objects.get_or_create(ship_id=clean_ship_id)
    needs_refresh = created or not ship.name or not ship.ship_type or ship.tier is None
    if not needs_refresh:
        # Cache the fully-populated ship for future lookups
        cache.set(cache_key, ship, 86400)
        return ship
    if needs_refresh:
        params = {
            "ship_id": ship_id
        }
        logging.info(f' ---> Remote fetching ship info for id: {ship_id}')
        data = _make_api_request("encyclopedia/ships/", params)

        if data and data.get(str(ship_id)):
            ship_data = data[str(ship_id)]
            ship.name = ship_data.get('name')
            ship.nation = ship_data.get('nation')
            ship.is_premium = ship_data.get('is_premium')
            ship.ship_type = ship_data.get('type')
            ship.tier = ship_data.get('tier')
            ship.save()
            cache.set(cache_key, ship, 86400)
            if created:
                logging.info(f'Created ship {ship.name}')
            else:
                logging.info(f'Refreshed ship metadata for {ship.name}')
        else:
            logging.error(
                f"ERROR: Null or invalid response data for ship_id: {ship_id}")
            logging.error(f"Response data: {data}")
            return None

    return ship


def _make_api_request(endpoint: str, params: Dict) -> Optional[Dict]:
    """Helper function to make API requests and handle responses."""
    data = make_api_request(endpoint, params)
    return data if isinstance(data, dict) or isinstance(data, list) else None
