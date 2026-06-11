from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any, Dict, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


logger = logging.getLogger(__name__)

# Contract notes for relied-on upstream endpoints live under
# agents/contracts/upstream/.

REALM_BASE_URLS = {
    'na': 'https://api.worldofwarships.com/wows/',
    'eu': 'https://api.worldofwarships.eu/wows/',
    'asia': 'https://api.worldofwarships.asia/wows/',
}
DEFAULT_REALM = 'na'

BASE_URL = os.getenv(
    "WG_API_BASE_URL", REALM_BASE_URLS[DEFAULT_REALM])
APP_ID = os.getenv("WG_APP_ID")
REQUEST_TIMEOUT_SECONDS = int(os.getenv("WG_REQUEST_TIMEOUT_SECONDS", "20"))
# Synchronous WG calls still exist on the gunicorn request thread (the cold
# player-lookup path: account/list/ + update_player_data, views.py get_object).
# A slow/unreachable WG there hangs the worker into a 502. Bound the per-attempt
# HTTP timeout much tighter on the request thread so the worker can never block
# long. NOTE: the shared session mounts Retry(total=2), so this timeout applies
# *per attempt* — worst-case request-thread block is ~timeout*3 + backoff
# (4s -> ~13.5s), which still lands comfortably under the gunicorn 25s timeout.
# Background tasks (nowhere to be) keep the longer budget. Tunable.
REQUEST_THREAD_TIMEOUT_SECONDS = int(
    os.getenv("WG_REQUEST_THREAD_TIMEOUT_SECONDS", "4"))
RETRY_TOTAL = int(os.getenv("WG_API_RETRY_TOTAL", "2"))


def _request_timeout_seconds() -> int:
    """Pick the per-attempt HTTP timeout for the current caller context.

    Mirrors the rate limiter's request-thread detection
    (``api/rate_limiter._in_request_thread``): a gunicorn request thread gets a
    tight bound so a stalled WG call fails fast (Tier-1 client retry then handles
    the transient); a celery background task keeps the longer budget.
    """
    try:
        from warships.api.rate_limiter import _in_request_thread
        if _in_request_thread():
            return REQUEST_THREAD_TIMEOUT_SECONDS
    except Exception:
        pass
    return REQUEST_TIMEOUT_SECONDS


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


def get_base_url(realm: str = DEFAULT_REALM) -> str:
    return REALM_BASE_URLS.get(realm, REALM_BASE_URLS[DEFAULT_REALM])


def _request_api_payload(endpoint: str, params: Dict[str, Any], realm: str = DEFAULT_REALM) -> Optional[Dict[str, Any]]:
    if not APP_ID:
        logger.error("WG_APP_ID environment variable is not set")
        return None

    clean_endpoint = endpoint.lstrip("/")
    clean_params = {key: value for key,
                    value in params.items() if value is not None}
    clean_params.setdefault("application_id", APP_ID)

    base_url = get_base_url(realm)

    # Global WG rate limit (shared across all worker processes + request
    # threads via Redis). No-op when disabled / no Redis / on error.
    from warships.api.rate_limiter import acquire as _wg_rate_limit_acquire
    _wg_rate_limit_acquire()

    try:
        response = _get_session().get(
            base_url + clean_endpoint,
            params=clean_params,
            timeout=_request_timeout_seconds(),
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as error:
        logger.error("HTTP request failed for endpoint '%s': %s",
                     clean_endpoint, error)
        return None
    except ValueError as error:
        logger.error("Invalid JSON from endpoint '%s': %s",
                     clean_endpoint, error)
        return None

    if not isinstance(payload, dict):
        logger.error(
            "Unexpected non-dict API response for endpoint '%s'", clean_endpoint)
        return None

    if payload.get("status") != "ok":
        logger.error("Error in response for endpoint '%s': %s",
                     clean_endpoint, payload)
        return None

    data = payload.get("data")
    if data is None:
        logger.error("Missing data payload for endpoint '%s'", clean_endpoint)
        return None

    return payload


def make_api_request(endpoint: str, params: Dict[str, Any], realm: str = DEFAULT_REALM) -> Optional[Any]:
    payload = _request_api_payload(endpoint, params, realm=realm)
    if payload is None:
        return None
    return payload.get("data")


def make_api_request_with_meta(endpoint: str, params: Dict[str, Any], realm: str = DEFAULT_REALM) -> Optional[Dict[str, Any]]:
    payload = _request_api_payload(endpoint, params, realm=realm)
    if payload is None:
        return None

    return {
        "data": payload.get("data"),
        "meta": payload.get("meta") or {},
    }


def make_api_request_typed(endpoint: str, params: Dict[str, Any], realm: str = DEFAULT_REALM):
    """Like make_api_request but returns (data, error_code).

    error_code is:
      - None on success
      - The WG error message string (e.g. "INVALID_ACCOUNT_ID") on API-level error
      - "TRANSPORT_ERROR" on HTTP/network/JSON failure
    Callers can use the error_code to distinguish poison-batch failures from
    transient failures and respond accordingly.
    """
    if not APP_ID:
        logger.error("WG_APP_ID environment variable is not set")
        return None, "TRANSPORT_ERROR"

    clean_endpoint = endpoint.lstrip("/")
    clean_params = {key: value for key, value in params.items() if value is not None}
    clean_params.setdefault("application_id", APP_ID)
    base_url = get_base_url(realm)

    # Global WG rate limit (shared across all worker processes + request
    # threads via Redis). No-op when disabled / no Redis / on error.
    from warships.api.rate_limiter import acquire as _wg_rate_limit_acquire
    _wg_rate_limit_acquire()

    try:
        response = _get_session().get(
            base_url + clean_endpoint,
            params=clean_params,
            timeout=_request_timeout_seconds(),
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as error:
        logger.error("HTTP request failed for endpoint '%s': %s", clean_endpoint, error)
        return None, "TRANSPORT_ERROR"
    except ValueError as error:
        logger.error("Invalid JSON from endpoint '%s': %s", clean_endpoint, error)
        return None, "TRANSPORT_ERROR"

    if not isinstance(payload, dict):
        logger.error("Unexpected non-dict API response for endpoint '%s'", clean_endpoint)
        return None, "TRANSPORT_ERROR"

    if payload.get("status") != "ok":
        err = (payload.get("error") or {}).get("message") or "UNKNOWN_ERROR"
        logger.error("Error in response for endpoint '%s': %s", clean_endpoint, payload)
        return None, err

    return payload.get("data"), None
