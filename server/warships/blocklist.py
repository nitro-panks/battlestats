"""Cached blocklist of deleted Wargaming account IDs.

The set is loaded once and refreshed every 5 minutes via Django's cache
framework.  All Player creation gates call `is_account_blocked()` to
prevent re-ingestion of purged accounts.
"""
from __future__ import annotations

import logging

from django.core.cache import cache

log = logging.getLogger(__name__)

BLOCKLIST_CACHE_KEY = "deleted_accounts:blocked_ids"
BLOCKLIST_CACHE_TTL = 300  # 5 minutes


def _load_blocked_ids() -> set[int]:
    from warships.models import DeletedAccount
    ids = set(DeletedAccount.objects.values_list("account_id", flat=True))
    cache.set(BLOCKLIST_CACHE_KEY, ids, BLOCKLIST_CACHE_TTL)
    return ids


def get_blocked_ids() -> set[int]:
    ids = cache.get(BLOCKLIST_CACHE_KEY)
    if ids is None:
        ids = _load_blocked_ids()
    return ids


def is_account_blocked(account_id: int) -> bool:
    return account_id in get_blocked_ids()


def invalidate_blocklist_cache() -> None:
    cache.delete(BLOCKLIST_CACHE_KEY)
