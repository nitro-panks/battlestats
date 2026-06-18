"""Resolve the originating client IP behind the nginx reverse proxy.

Production sits behind nginx, so ``REMOTE_ADDR`` is always ``127.0.0.1``; the
real client address is the first hop in ``X-Forwarded-For``. Use this anywhere
the genuine visitor IP matters (abuse attribution, operator-traffic exclusion).
"""

from __future__ import annotations


def get_client_ip(request) -> str | None:
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded:
        # left-most entry is the original client; the rest are proxy hops
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')
