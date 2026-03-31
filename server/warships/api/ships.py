import logging
import re
from typing import Optional, Dict, Any

from django.core.cache import cache

from warships.api.client import DEFAULT_REALM, make_api_request, make_api_request_with_meta
from warships.models import Ship


logging.basicConfig(level=logging.INFO)


CHART_NAME_MAX_LENGTH = 15
SHIP_NAME_CONNECTORS = {
    "and", "de", "der", "du", "la", "le", "of", "the", "van", "von",
}
SHIP_NAME_REPLACEMENTS = {
    "admiral": "Adm.",
    "alexander": "Alex.",
    "general": "Gen.",
    "imperator": "Imp.",
    "knyaz": "Kn.",
    "mount": "Mt.",
    "prince": "Pr.",
    "prinz": "Prz.",
    "saint": "St.",
    "sovetskaya": "Sov.",
    "sovetsky": "Sov.",
    "velikaya": "Vel.",
    "velikiy": "Vel.",
    "veliky": "Vel.",
}
ROMAN_NUMERAL_PATTERN = re.compile(r"^[IVXLCDM]+$", re.IGNORECASE)


def _normalize_ship_name(name: str) -> str:
    return " ".join((name or "").split())


def _is_roman_numeral(token: str) -> bool:
    return bool(ROMAN_NUMERAL_PATTERN.fullmatch(token))


def build_ship_chart_name(name: str, max_length: int = CHART_NAME_MAX_LENGTH) -> str:
    clean_name = _normalize_ship_name(name)
    if len(clean_name) <= max_length:
        return clean_name

    words = [SHIP_NAME_REPLACEMENTS.get(
        word.lower(), word) for word in clean_name.split()]
    candidate = " ".join(words)
    if len(candidate) <= max_length:
        return candidate

    filtered_words = [
        word for index, word in enumerate(words)
        if index in (0, len(words) - 1) or word.lower().rstrip(".") not in SHIP_NAME_CONNECTORS
    ]
    candidate = " ".join(filtered_words)
    if len(candidate) <= max_length:
        return candidate

    if len(filtered_words) > 1:
        abbreviated_words = []
        for index, word in enumerate(filtered_words):
            if index == len(filtered_words) - 1 or _is_roman_numeral(word) or word.endswith('.'):
                abbreviated_words.append(word)
            else:
                abbreviated_words.append(f"{word[0]}.")
        candidate = " ".join(abbreviated_words)
        if len(candidate) <= max_length:
            return candidate

        first_word = abbreviated_words[0]
        last_word = filtered_words[-1]
        candidate = f"{first_word} {last_word}".strip()
        if len(candidate) <= max_length:
            return candidate

    return f"{clean_name[:max_length - 1].rstrip()}."


def _ship_cache_is_complete(ship: Ship) -> bool:
    return bool(ship.name and ship.ship_type and ship.tier is not None and ship.chart_name)


def _upsert_ship_from_api_payload(ship: Ship, ship_data: Dict[str, Any]) -> Ship:
    ship.name = ship_data.get('name') or ''
    ship.chart_name = build_ship_chart_name(ship.name)
    ship.nation = ship_data.get('nation') or ''
    ship.is_premium = bool(ship_data.get('is_premium'))
    ship.ship_type = ship_data.get('type') or ''
    ship.tier = ship_data.get('tier')
    ship.save()
    cache.set(f'ship:{ship.ship_id}', ship, 86400)
    return ship


def sync_ship_catalog(page_size: int = 100) -> dict[str, int]:
    page_no = 1
    created_count = 0
    updated_count = 0
    processed_count = 0

    while True:
        payload = make_api_request_with_meta(
            "encyclopedia/ships/",
            {
                "fields": "ship_id,name,nation,is_premium,type,tier",
                "limit": page_size,
                "page_no": page_no,
            },
        )

        if payload is None:
            if page_no == 1:
                raise RuntimeError("Unable to load ship catalog from WG API.")
            break

        data = payload.get("data")
        meta = payload.get("meta") or {}
        if not isinstance(data, dict) or not data:
            break

        ship_rows = [row for row in data.values() if isinstance(
            row, dict) and row.get("ship_id")]
        ship_ids = [int(row["ship_id"]) for row in ship_rows]
        existing_by_id = Ship.objects.in_bulk(ship_ids, field_name="ship_id")

        to_create: list[Ship] = []
        to_update: list[Ship] = []
        for row in ship_rows:
            ship_id = int(row["ship_id"])
            chart_name = build_ship_chart_name(str(row.get("name") or ""))
            existing = existing_by_id.get(ship_id)
            if existing is None:
                to_create.append(Ship(
                    ship_id=ship_id,
                    name=str(row.get("name") or ""),
                    chart_name=chart_name,
                    nation=str(row.get("nation") or ""),
                    ship_type=str(row.get("type") or ""),
                    tier=row.get("tier"),
                    is_premium=bool(row.get("is_premium")),
                ))
                continue

            changed = False
            for field_name, value in (
                ("name", str(row.get("name") or "")),
                ("chart_name", chart_name),
                ("nation", str(row.get("nation") or "")),
                ("ship_type", str(row.get("type") or "")),
                ("tier", row.get("tier")),
                ("is_premium", bool(row.get("is_premium"))),
            ):
                if getattr(existing, field_name) != value:
                    setattr(existing, field_name, value)
                    changed = True
            if changed:
                to_update.append(existing)

        if to_create:
            Ship.objects.bulk_create(to_create)
            created_count += len(to_create)
        if to_update:
            Ship.objects.bulk_update(
                to_update,
                ["name", "chart_name", "nation", "ship_type", "tier", "is_premium"],
            )
            updated_count += len(to_update)

        for ship_id in ship_ids:
            cache.delete(f"ship:{ship_id}")

        processed_count += len(ship_rows)
        page_total = meta.get("page_total") or meta.get(
            "pages_total") or meta.get("page_count")
        if not page_total or page_no >= int(page_total):
            break
        page_no += 1

    return {
        "processed": processed_count,
        "created": created_count,
        "updated": updated_count,
    }


def _fetch_ranked_ship_stats_for_player(player_id: int, season_ids: Optional[list[int]] = None, realm: str = DEFAULT_REALM) -> list[dict[str, Any]]:
    """Fetch ranked ship stats for a player, optionally scoped to one or more seasons."""
    params = {
        "account_id": player_id,
    }
    if season_ids:
        params["season_id"] = ",".join(str(season_id)
                                       for season_id in season_ids)

    logging.info(
        f' ---> Remote fetching ranked ship stats for player_id: {player_id}')
    data = _make_api_request("seasons/shipstats/", params, realm=realm)

    try:
        rows = data[str(player_id)]
    except (KeyError, TypeError):
        rows = []

    return rows if isinstance(rows, list) else []


def _fetch_efficiency_badges_for_player(player_id: int, realm: str = DEFAULT_REALM) -> list[dict[str, Any]]:
    """Fetch per-ship efficiency badge classes for a player."""
    params = {
        "account_id": player_id,
    }
    logging.info(
        ' ---> Remote fetching efficiency badges for player_id: %s',
        player_id,
    )
    data = _make_api_request("ships/badges/", params, realm=realm)

    try:
        rows = data[str(player_id)]
    except (KeyError, TypeError):
        rows = []

    return rows if isinstance(rows, list) else []


def _fetch_ship_stats_for_player(player_id: str, realm: str = DEFAULT_REALM) -> Dict:
    """Fetch all competitive data for all ships for a given player_id."""
    params = {
        "account_id": player_id
    }
    logging.info(
        f' ---> EXPENSIVE: Remote fetching all battle stats for player_id: {player_id}')
    data = _make_api_request("ships/stats/", params, realm=realm)

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
    if cached is not None and _ship_cache_is_complete(cached):
        return cached
    if cached is not None:
        cache.delete(cache_key)

    ship, created = Ship.objects.get_or_create(ship_id=clean_ship_id)
    needs_refresh = created or not ship.name or not ship.ship_type or ship.tier is None
    if not needs_refresh:
        if not ship.chart_name:
            ship.chart_name = build_ship_chart_name(ship.name)
            ship.save(update_fields=['chart_name'])
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
            _upsert_ship_from_api_payload(ship, ship_data)
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


def _make_api_request(endpoint: str, params: Dict, realm: str = DEFAULT_REALM) -> Optional[Dict]:
    """Helper function to make API requests and handle responses."""
    data = make_api_request(endpoint, params, realm=realm)
    return data if isinstance(data, dict) or isinstance(data, list) else None
