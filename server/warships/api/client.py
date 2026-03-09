from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any, Dict, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


logger = logging.getLogger(__name__)

BASE_URL = os.getenv("WG_API_BASE_URL", "https://api.worldofwarships.com/wows/")
APP_ID = os.getenv("WG_APP_ID")
REQUEST_TIMEOUT_SECONDS = int(os.getenv("WG_REQUEST_TIMEOUT_SECONDS", "20"))
RETRY_TOTAL = int(os.getenv("WG_API_RETRY_TOTAL", "2"))


@lru_cache(maxsize=1)
def _get_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=RETRY_TOTAL,
        connect=RETRY_TOTAL,
        read=RETRY_TOTAL,
        status=RETRY_TOTAL,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({
        "User-Agent": "battlestats/1.0",
        "Accept": "application/json",
    })
    return session


def make_api_request(endpoint: str, params: Dict[str, Any]) -> Optional[Any]:
    if not APP_ID:
        logger.error("WG_APP_ID environment variable is not set")
        return None

    clean_endpoint = endpoint.lstrip("/")
    clean_params = {key: value for key, value in params.items() if value is not None}
    clean_params.setdefault("application_id", APP_ID)

    try:
        response = _get_session().get(
            BASE_URL + clean_endpoint,
            params=clean_params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as error:
        logger.error("HTTP request failed for endpoint '%s': %s", clean_endpoint, error)
        return None
    except ValueError as error:
        logger.error("Invalid JSON from endpoint '%s': %s", clean_endpoint, error)
        return None

    if not isinstance(payload, dict):
        logger.error("Unexpected non-dict API response for endpoint '%s'", clean_endpoint)
        return None

    if payload.get("status") != "ok":
        logger.error("Error in response for endpoint '%s': %s", clean_endpoint, payload)
        return None

    data = payload.get("data")
    if data is None:
        logger.error("Missing data payload for endpoint '%s'", clean_endpoint)
        return None

    return data