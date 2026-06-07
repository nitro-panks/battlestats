import logging
from typing import Dict, Optional

from django.db.models.functions import Lower

from warships.models import Player
from warships.api.client import DEFAULT_REALM, make_api_request, make_api_request_typed

logging.basicConfig(level=logging.INFO)



def _fetch_player_personal_data(player_id: int, realm: str = DEFAULT_REALM) -> Dict:
    """Fetch JSON data for a given player_id."""
    params = {
        "account_id": player_id
    }
    logging.info(
        f' ---> Remote fetching player personal (account) data for player_id: {player_id}')
    data = _make_api_request("account/info/", params, realm=realm)
    return data.get(str(player_id), {}) if data else {}


def _bulk_fetch_account_info(player_ids: list[int], realm: str) -> tuple[dict, str | None]:
    """Bulk-fetch account/info for up to 100 players. Returns (data, error_code).

    Added for runbook-bulk-battle-observation-capture-2026-06-06.md (D1). Sends
    no `fields` filter, so each per-key value `data[str(pid)]` has the same shape
    as `_fetch_player_personal_data`'s return — the bulk observation floor relies
    on that parity. Unlike `clan_crawl.fetch_players_bulk`, the typed client
    surfaces the WG error_code so callers can distinguish INVALID_ACCOUNT_ID
    (poison-batch -> per-player fallback) from REQUEST_LIMIT_EXCEEDED (abort).
    """
    params = {"account_id": ",".join(str(pid) for pid in player_ids)}
    logging.info("Bulk fetching account/info for %d players [%s]", len(player_ids), realm.upper())
    data, err = make_api_request_typed("account/info/", params, realm=realm)
    return (data if isinstance(data, dict) else {}), err


def _per_player_account_fallback(player_ids: list[int], realm: str) -> dict:
    """Fallback: fetch account/info individually to isolate poison IDs (D5).

    Mirrors `_per_player_ship_fallback`. Returns {str(pid): <acct dict> | None}.
    `_fetch_player_personal_data` returns {} for a missing key; we normalise that
    falsy result to None so the caller's `None -> skip` slice handling (D4) fires.
    """
    out: dict = {}
    for pid in player_ids:
        try:
            r = _fetch_player_personal_data(pid, realm=realm)
            out[str(pid)] = r or None
        except Exception:
            logging.warning("Per-player account fallback failed for %s [%s]", pid, realm)
            out[str(pid)] = None
    return out


def _fetch_ranked_account_info(player_id: int, realm: str = DEFAULT_REALM) -> Dict:
    """Fetch ranked battles account info (rank_info) for a player."""
    params = {
        "account_id": player_id,
        "fields": "rank_info"
    }
    logging.info(
        f' ---> Remote fetching ranked account info for player_id: {player_id}')
    data = _make_api_request("seasons/accountinfo/", params, realm=realm)
    return data.get(str(player_id), {}) if data else {}


def _fetch_ranked_seasons_info(realm: str = DEFAULT_REALM) -> Dict:
    """Fetch all ranked season metadata (names, dates)."""
    params = {
        "fields": "season_id,season_name,start_at,close_at"
    }
    logging.info(' ---> Remote fetching ranked seasons metadata')
    data = _make_api_request("seasons/info/", params, realm=realm)
    return data if data else {}


def _fetch_player_achievements(player_id: int, realm: str = DEFAULT_REALM) -> Optional[Dict]:
    """Fetch the raw achievements payload for a single player account."""
    params = {
        "account_id": player_id,
        "fields": "battle,progress",
    }
    logging.info(
        f' ---> Remote fetching achievements data for player_id: {player_id}')
    data = _make_api_request("account/achievements/", params, realm=realm)
    if not isinstance(data, dict):
        return None
    payload = data.get(str(player_id))
    return payload if isinstance(payload, dict) else None


def _fetch_player_id_by_name(player_name: str, realm: str = DEFAULT_REALM) -> Optional[str]:
    """Return a player_id from local cache first, then WoWS API exact lookup."""
    normalized_name = player_name.strip()
    if not normalized_name or len(normalized_name) > 64:
        return None

    local_player = Player.objects.alias(name_lower=Lower("name")).filter(
        name_lower=normalized_name.casefold(),
        realm=realm,
    ).first()
    if local_player:
        return str(local_player.player_id)

    params = {
        "search": normalized_name,
        "type": "exact",
        "limit": 1,
        "fields": "account_id,nickname"
    }
    logging.info(f' ---> Remote fetching player info for: {normalized_name}')
    data = _make_api_request("account/list/", params, realm=realm)

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


def _make_api_request(endpoint: str, params: Dict, realm: str = DEFAULT_REALM) -> Optional[Dict]:
    """Helper function to make API requests and handle responses."""
    data = make_api_request(endpoint, params, realm=realm)
    return data if isinstance(data, dict) or isinstance(data, list) else None
