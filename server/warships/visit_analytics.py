from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import timedelta, timezone as dt_timezone
from urllib.parse import parse_qs, urlparse

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q, Sum
from django.utils import timezone

from warships.models import Clan, DEFAULT_REALM, EntityVisitDaily, EntityVisitEvent, Player, VALID_REALMS


VISIT_DEDUPE_WINDOW = timedelta(minutes=30)
SUPPORTED_REPORTING_PERIODS = {
    '1d': 1,
    '7d': 7,
    '30d': 30,
}

_BOT_USER_AGENT_RE = re.compile(
    r'(bot|crawler|spider|slurp|bingpreview|facebookexternalhit|headless)',
    re.IGNORECASE,
)


def _normalize_datetime(value):
    if timezone.is_aware(value):
        return value.astimezone(dt_timezone.utc).replace(tzinfo=None)
    return value


def hash_identifier(value: str) -> str:
    seeded_value = f"{settings.SECRET_KEY}:{value}"
    return hashlib.sha256(seeded_value.encode('utf-8')).hexdigest()


def is_bot_user_agent(user_agent: str) -> bool:
    if not user_agent:
        return False
    return bool(_BOT_USER_AGENT_RE.search(user_agent))


def _realm_from_route_path(route_path: str) -> str:
    try:
        realm = parse_qs(urlparse(route_path or '').query).get(
            'realm', [DEFAULT_REALM])[0]
    except Exception:
        return DEFAULT_REALM

    normalized_realm = (realm or DEFAULT_REALM).strip().lower()
    return normalized_realm if normalized_realm in VALID_REALMS else DEFAULT_REALM


def _sync_recent_player_surface(entity_type: str, entity_id: int, occurred_at, route_path: str) -> None:
    if entity_type != EntityVisitEvent.ENTITY_TYPE_PLAYER:
        return

    realm = _realm_from_route_path(route_path)
    updated = Player.objects.filter(
        player_id=entity_id, realm=realm).update(last_lookup=occurred_at)
    if not updated:
        return

    from warships.data import push_recently_viewed_player

    push_recently_viewed_player(entity_id, realm=realm)


def record_entity_visit(payload: dict, user_agent: str = '') -> dict:
    if is_bot_user_agent(user_agent):
        return {
            'accepted': False,
            'counted_in_deduped_views': False,
            'reason': 'bot',
        }

    occurred_at = _normalize_datetime(payload['occurred_at'])
    event_date = occurred_at.date()
    entity_type = payload['entity_type']
    entity_id = payload['entity_id']
    realm = _realm_from_route_path(payload.get('route_path') or '')
    source = payload.get('source') or EntityVisitEvent.SOURCE_WEB_FIRST_PARTY
    visitor_key_hash = hash_identifier(payload['visitor_key'])
    session_key_hash = hash_identifier(payload['session_key'])

    with transaction.atomic():
        cooldown_floor = occurred_at - VISIT_DEDUPE_WINDOW
        prior_counted_visit = (
            EntityVisitEvent.objects.select_for_update()
            .filter(
                entity_type=entity_type,
                entity_id=entity_id,
                visitor_key_hash=visitor_key_hash,
                occurred_at__gte=cooldown_floor,
                counted_in_deduped_views=True,
            )
            .order_by('-occurred_at')
            .first()
        )

        counted_in_deduped_views = prior_counted_visit is None
        dedupe_bucket_started_at = occurred_at
        if prior_counted_visit is not None:
            dedupe_bucket_started_at = (
                prior_counted_visit.dedupe_bucket_started_at or prior_counted_visit.occurred_at
            )

        try:
            EntityVisitEvent.objects.create(
                event_uuid=payload['event_uuid'],
                occurred_at=occurred_at,
                event_date=event_date,
                entity_type=entity_type,
                entity_id=entity_id,
                realm=realm,
                entity_name_snapshot=payload['entity_name'],
                entity_slug_snapshot=payload.get('entity_slug') or '',
                route_path=payload['route_path'],
                referrer_path=payload.get('referrer_path') or '',
                source=source,
                visitor_key_hash=visitor_key_hash,
                session_key_hash=session_key_hash,
                dedupe_bucket_started_at=dedupe_bucket_started_at,
                counted_in_deduped_views=counted_in_deduped_views,
            )
        except IntegrityError:
            return {
                'accepted': False,
                'counted_in_deduped_views': False,
                'reason': 'duplicate_event_uuid',
            }

        daily_row, _ = EntityVisitDaily.objects.select_for_update().get_or_create(
            date=event_date,
            entity_type=entity_type,
            entity_id=entity_id,
            realm=realm,
            defaults={
                'entity_name_snapshot': payload['entity_name'],
                'last_view_at': occurred_at,
            },
        )

        daily_row.entity_name_snapshot = payload['entity_name']
        daily_row.views_raw += 1
        if counted_in_deduped_views:
            daily_row.views_deduped += 1
        if source == EntityVisitEvent.SOURCE_WEB_FIRST_PARTY:
            daily_row.source_first_party_views += 1
        elif source == EntityVisitEvent.SOURCE_GA4:
            daily_row.source_ga4_views += 1
        if daily_row.last_view_at is None or occurred_at > daily_row.last_view_at:
            daily_row.last_view_at = occurred_at

        day_events = EntityVisitEvent.objects.filter(
            event_date=event_date,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        daily_row.unique_visitors = day_events.values(
            'visitor_key_hash').distinct().count()
        daily_row.unique_sessions = day_events.values(
            'session_key_hash').distinct().count()
        daily_row.save()

    _sync_recent_player_surface(
        entity_type,
        entity_id,
        occurred_at,
        payload.get('route_path') or '',
    )

    return {
        'accepted': True,
        'counted_in_deduped_views': counted_in_deduped_views,
        'reason': 'recorded',
    }


def _resolve_entity_names(entity_type: str, entity_ids: list[int]) -> dict[int, str]:
    if not entity_ids:
        return {}

    if entity_type == EntityVisitEvent.ENTITY_TYPE_PLAYER:
        return dict(Player.objects.filter(player_id__in=entity_ids).values_list('player_id', 'name'))
    if entity_type == EntityVisitEvent.ENTITY_TYPE_CLAN:
        return dict(Clan.objects.filter(clan_id__in=entity_ids).values_list('clan_id', 'name'))
    return {}


def get_top_entities(entity_type: str, period: str, metric: str, limit: int) -> list[dict]:
    days = SUPPORTED_REPORTING_PERIODS[period]
    today = timezone.now().date()
    start_date = today - timedelta(days=days - 1)

    if metric in {'views_raw', 'views_deduped'}:
        rows = list(
            EntityVisitDaily.objects.filter(
                entity_type=entity_type,
                date__gte=start_date,
                date__lte=today,
            )
            .values('entity_type', 'entity_id')
            .annotate(
                views_raw=Sum('views_raw'),
                views_deduped=Sum('views_deduped'),
                last_view_at=Max('last_view_at'),
            )
            .order_by(f'-{metric}', '-last_view_at', 'entity_id')[:limit]
        )

        exact_uniques = {
            row['entity_id']: row
            for row in EntityVisitEvent.objects.filter(
                entity_type=entity_type,
                event_date__gte=start_date,
                event_date__lte=today,
                entity_id__in=[item['entity_id'] for item in rows],
            )
            .values('entity_id')
            .annotate(
                unique_visitors=Count('visitor_key_hash', distinct=True),
                unique_sessions=Count('session_key_hash', distinct=True),
            )
        }
        for row in rows:
            unique_row = exact_uniques.get(row['entity_id'], {})
            row['unique_visitors'] = unique_row.get('unique_visitors', 0)
            row['unique_sessions'] = unique_row.get('unique_sessions', 0)
    else:
        rows = list(
            EntityVisitEvent.objects.filter(
                entity_type=entity_type,
                event_date__gte=start_date,
                event_date__lte=today,
            )
            .values('entity_type', 'entity_id')
            .annotate(
                views_raw=Count('id'),
                views_deduped=Count('id', filter=Q(
                    counted_in_deduped_views=True)),
                unique_visitors=Count('visitor_key_hash', distinct=True),
                unique_sessions=Count('session_key_hash', distinct=True),
                last_view_at=Max('occurred_at'),
            )
            .order_by(f'-{metric}', '-last_view_at', 'entity_id')[:limit]
        )

    names_by_id = _resolve_entity_names(
        entity_type, [row['entity_id'] for row in rows])
    daily_names = {
        row['entity_id']: row['entity_name_snapshot']
        for row in EntityVisitDaily.objects.filter(
            entity_type=entity_type,
            entity_id__in=[item['entity_id'] for item in rows],
        )
        .order_by('entity_id', '-date')
        .values('entity_id', 'entity_name_snapshot')
    }

    for row in rows:
        row['entity_name'] = names_by_id.get(
            row['entity_id']) or daily_names.get(row['entity_id']) or ''

    return rows


def rebuild_entity_visit_daily(*, start_date=None, end_date=None, entity_type: str | None = None, dry_run: bool = False) -> dict:
    queryset = EntityVisitEvent.objects.all()
    if start_date is not None:
        queryset = queryset.filter(event_date__gte=start_date)
    if end_date is not None:
        queryset = queryset.filter(event_date__lte=end_date)
    if entity_type is not None:
        queryset = queryset.filter(entity_type=entity_type)

    event_rows = list(
        queryset.order_by('event_date', 'entity_type', 'entity_id', '-occurred_at').values(
            'event_date',
            'entity_type',
            'entity_id',
            'realm',
            'entity_name_snapshot',
            'occurred_at',
            'visitor_key_hash',
            'session_key_hash',
            'counted_in_deduped_views',
            'source',
        )
    )

    grouped = defaultdict(lambda: {
        'entity_name_snapshot': '',
        'views_raw': 0,
        'views_deduped': 0,
        'visitor_keys': set(),
        'session_keys': set(),
        'last_view_at': None,
        'source_first_party_views': 0,
        'source_ga4_views': 0,
    })

    for row in event_rows:
        key = (row['event_date'], row['entity_type'], row['entity_id'], row.get('realm', DEFAULT_REALM))
        bucket = grouped[key]
        if not bucket['entity_name_snapshot']:
            bucket['entity_name_snapshot'] = row['entity_name_snapshot']
        bucket['views_raw'] += 1
        if row['counted_in_deduped_views']:
            bucket['views_deduped'] += 1
        bucket['visitor_keys'].add(row['visitor_key_hash'])
        bucket['session_keys'].add(row['session_key_hash'])
        if bucket['last_view_at'] is None or row['occurred_at'] > bucket['last_view_at']:
            bucket['last_view_at'] = row['occurred_at']
        if row['source'] == EntityVisitEvent.SOURCE_WEB_FIRST_PARTY:
            bucket['source_first_party_views'] += 1
        elif row['source'] == EntityVisitEvent.SOURCE_GA4:
            bucket['source_ga4_views'] += 1

    rebuilt_rows = []
    for (event_date, resolved_entity_type, entity_id, realm), bucket in grouped.items():
        rebuilt_rows.append(EntityVisitDaily(
            date=event_date,
            entity_type=resolved_entity_type,
            entity_id=entity_id,
            realm=realm,
            entity_name_snapshot=bucket['entity_name_snapshot'],
            views_raw=bucket['views_raw'],
            views_deduped=bucket['views_deduped'],
            unique_visitors=len(bucket['visitor_keys']),
            unique_sessions=len(bucket['session_keys']),
            last_view_at=bucket['last_view_at'],
            source_first_party_views=bucket['source_first_party_views'],
            source_ga4_views=bucket['source_ga4_views'],
        ))

    daily_queryset = EntityVisitDaily.objects.all()
    if start_date is not None:
        daily_queryset = daily_queryset.filter(date__gte=start_date)
    if end_date is not None:
        daily_queryset = daily_queryset.filter(date__lte=end_date)
    if entity_type is not None:
        daily_queryset = daily_queryset.filter(entity_type=entity_type)

    existing_rows = daily_queryset.count()
    if not dry_run:
        with transaction.atomic():
            daily_queryset.delete()
            if rebuilt_rows:
                EntityVisitDaily.objects.bulk_create(rebuilt_rows)

    return {
        'status': 'dry-run' if dry_run else 'completed',
        'start_date': start_date.isoformat() if start_date is not None else None,
        'end_date': end_date.isoformat() if end_date is not None else None,
        'entity_type': entity_type,
        'source_events': len(event_rows),
        'existing_daily_rows': existing_rows,
        'rebuilt_daily_rows': len(rebuilt_rows),
    }


def cleanup_entity_visit_events(*, older_than_days: int, dry_run: bool = False) -> dict:
    cutoff_date = timezone.now().date() - timedelta(days=older_than_days)
    queryset = EntityVisitEvent.objects.filter(event_date__lt=cutoff_date)
    matching_rows = queryset.count()

    deleted_rows = 0
    if not dry_run and matching_rows:
        deleted_rows, _ = queryset.delete()

    return {
        'status': 'dry-run' if dry_run else 'completed',
        'older_than_days': older_than_days,
        'cutoff_date': cutoff_date.isoformat(),
        'matching_rows': matching_rows,
        'deleted_rows': deleted_rows,
    }
