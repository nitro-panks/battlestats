"""Hot-Players Engagement Capture Queue — promotion/eviction brain + capture hands.

Shared logic for the two-task engagement-capture loop:

* ``evaluate_realm_engagement`` — the active-days ``GROUP BY`` over
  ``EntityVisitDaily`` (recurrence across distinct days, NOT summed views) that
  separates a one-time spike from sustained return interest.
* ``maintain_hot_players`` — promote/evict/re-score/cap-trim the ``HotPlayer``
  set for one realm (DB-only; no WG calls). Backs ``maintain_hot_players`` cmd +
  ``maintain_hot_players_task``.
* ``capture_hot_players`` — sweep the hot set guaranteeing a ``BattleObservation``
  (skip-if-fresh) and a gap-free daily ``Snapshot`` per member. Backs
  ``capture_hot_player_observations_task``.

Env knobs (read inline via ``os.getenv`` to match the FLOOR / SNAPSHOT / ENRICH
families — no sibling domain knob is a settings.py constant). See the runbook:
``agents/runbooks/runbook-hot-players-engagement-queue-2026-06-10.md``.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import timedelta

from django.db.models import Count, Max, Sum
from django.utils import timezone

from warships.models import (
    BattleObservation,
    EntityVisitDaily,
    EntityVisitEvent,
    HotPlayer,
    Player,
    Snapshot,
)

logger = logging.getLogger(__name__)


# --- Env-knob accessors (inline reads, sibling-family convention) ----------

def _window_days() -> int:
    return int(os.getenv("HOT_PLAYERS_WINDOW_DAYS", "14"))


def _promote_min_active_days() -> int:
    return int(os.getenv("HOT_PROMOTE_MIN_ACTIVE_DAYS", "3"))


def _promote_max_recency_days() -> int:
    return int(os.getenv("HOT_PROMOTE_MAX_RECENCY_DAYS", "3"))


def _promote_min_sessions() -> int:
    return int(os.getenv("HOT_PROMOTE_MIN_SESSIONS", "2"))


def _evict_inactivity_days() -> int:
    return int(os.getenv("HOT_EVICT_INACTIVITY_DAYS", "14"))


def _evict_min_active_days() -> int:
    return int(os.getenv("HOT_EVICT_MIN_ACTIVE_DAYS", "2"))


def _hot_players_max() -> int:
    return int(os.getenv("HOT_PLAYERS_MAX", "500"))


def _observe_floor_hours() -> int:
    return int(os.getenv("HOT_OBSERVE_FLOOR_HOURS", "20"))


def _capture_delay() -> float:
    return float(os.getenv("HOT_PLAYERS_CAPTURE_DELAY", "0.5"))


def _enabled() -> bool:
    return os.getenv("HOT_PLAYERS_ENABLED", "1") == "1"


def compute_hot_score(active_days: int, sessions: int, views: int) -> float:
    """Deterministic ranking value: active_days primary, then sessions, then views.

    Encoded as a single sortable float so the ``HOT_PLAYERS_MAX`` cap-trim and
    the status ranking are stable. The tiebreak terms are bounded fractions so
    a higher tier can never be overtaken by a lower one:
      active_days * 1e6  +  sessions * 1e3  +  views
    (sessions/views are realistically well under their 1e3/1e6 ceilings; even at
    the ceiling the ordering only degrades gracefully, never inverts the primary.)
    """
    return float(active_days) * 1_000_000.0 + float(sessions) * 1_000.0 + float(views)


def evaluate_realm_engagement(realm: str, *, window_days: int | None = None,
                              today=None) -> dict[int, dict]:
    """Active-days engagement aggregate per player_id for ``realm`` over W days.

    Returns ``{player_id: {active_days, sessions, views, recency_days,
    last_engaged_at}}`` where:

      active_days  = COUNT(DISTINCT date WHERE views_deduped >= 1)   over W
      sessions     = SUM(unique_sessions)                            over W
      views        = SUM(views_deduped)                              over W
      recency_days = today - MAX(date with a view)
      last_engaged_at = MAX(last_view_at)

    Recurrence (distinct active days), NOT summed views, is the discriminator —
    a single viral spike and a player visited a little on many days can sum to
    the same view total but only the latter is sustained. Realm-scoped, players
    only.
    """
    window_days = window_days or _window_days()
    today = today or timezone.now().date()
    cutoff = today - timedelta(days=window_days)

    rows = (
        EntityVisitDaily.objects
        .filter(
            entity_type=EntityVisitEvent.ENTITY_TYPE_PLAYER,
            realm=realm,
            date__gte=cutoff,
            date__lte=today,
            views_deduped__gte=1,
        )
        .values('entity_id')
        .annotate(
            active_days=Count('date', distinct=True),
            sessions=Sum('unique_sessions'),
            views=Sum('views_deduped'),
            last_date=Max('date'),
            last_engaged_at=Max('last_view_at'),
        )
    )

    out: dict[int, dict] = {}
    for row in rows:
        last_date = row['last_date']
        out[int(row['entity_id'])] = {
            'active_days': int(row['active_days'] or 0),
            'sessions': int(row['sessions'] or 0),
            'views': int(row['views'] or 0),
            'recency_days': (today - last_date).days if last_date else None,
            'last_engaged_at': row['last_engaged_at'],
        }
    return out


def maintain_hot_players(realm: str, *, dry_run: bool = False,
                         logger=logger) -> dict:
    """Promote / evict / re-score / cap-trim the HotPlayer set for one realm.

    Pure DB. Promotion gates on recurrence + recency + sessions (NO visitor
    breadth — a single devoted fan must qualify). Eviction uses hysteresis:
    promote at >= HOT_PROMOTE_MIN_ACTIVE_DAYS, evict only below
    HOT_EVICT_MIN_ACTIVE_DAYS (or after HOT_EVICT_INACTIVITY_DAYS of no views),
    so a player hovering at 2-3 active-days/W stays put instead of flapping.
    ``source='pinned'`` rows are never auto-evicted or trimmed.
    """
    today = timezone.now().date()
    engagement = evaluate_realm_engagement(realm, today=today)

    promote_min = _promote_min_active_days()
    promote_recency = _promote_max_recency_days()
    promote_sessions = _promote_min_sessions()
    evict_inactivity = _evict_inactivity_days()
    evict_min_active = _evict_min_active_days()
    cap = _hot_players_max()

    # Key incumbents by the WG account_id (Player.player_id), NOT the FK pk —
    # that is what EntityVisitDaily.entity_id is keyed on.
    existing = {
        hp.player.player_id: hp
        for hp in HotPlayer.objects.filter(realm=realm).select_related('player')
    }

    promoted = evicted = updated = 0

    # Resolve player_ids that have a local Player row (entity_id == player_id).
    candidate_ids = set(engagement) | set(existing)
    known_player_ids = set(
        Player.objects
        .filter(realm=realm, player_id__in=candidate_ids)
        .values_list('player_id', flat=True)
    )

    # --- Promotions + survivor re-score -----------------------------------
    for player_id, eng in engagement.items():
        active_days = eng['active_days']
        recency = eng['recency_days']
        sessions = eng['sessions']
        views = eng['views']
        score = compute_hot_score(active_days, sessions, views)

        hp = existing.get(player_id)
        if hp is None:
            # Promotion rule (enter the queue).
            qualifies = (
                active_days >= promote_min
                and recency is not None and recency <= promote_recency
                and sessions >= promote_sessions
            )
            if not qualifies:
                continue
            if player_id not in known_player_ids:
                # Engagement on an account we don't have a Player row for —
                # can't capture it; skip without churn.
                continue
            logger.info(
                "hot_players[%s] PROMOTE player_id=%s active_days=%s recency=%s "
                "sessions=%s views=%s score=%s",
                realm, player_id, active_days, recency, sessions, views, score)
            promoted += 1
            if not dry_run:
                try:
                    player = Player.objects.get(player_id=player_id, realm=realm)
                except Player.DoesNotExist:
                    continue
                HotPlayer.objects.update_or_create(
                    player=player, realm=realm,
                    defaults={
                        'source': HotPlayer.SOURCE_ENGAGEMENT,
                        'last_engaged_at': eng['last_engaged_at'],
                        'active_days_window': active_days,
                        'unique_sessions_window': sessions,
                        'views_deduped_window': views,
                        'hot_score': score,
                    },
                )
        else:
            # Existing member — refresh audit fields + score (hysteresis means
            # we do NOT re-apply the promote threshold here).
            updated += 1
            if not dry_run:
                hp.last_engaged_at = eng['last_engaged_at'] or hp.last_engaged_at
                hp.active_days_window = active_days
                hp.unique_sessions_window = sessions
                hp.views_deduped_window = views
                hp.hot_score = score
                hp.save(update_fields=[
                    'last_engaged_at', 'active_days_window',
                    'unique_sessions_window', 'views_deduped_window', 'hot_score'])

    # --- Evictions (hysteresis) -------------------------------------------
    for player_id, hp in existing.items():
        if hp.source == HotPlayer.SOURCE_PINNED:
            continue
        eng = engagement.get(player_id)
        active_days = eng['active_days'] if eng else 0
        recency = eng['recency_days'] if eng else None
        # No views at all in the window => recency unbounded => inactivity evict.
        inactive_too_long = recency is None or recency > evict_inactivity
        too_few_active = active_days < evict_min_active
        if inactive_too_long or too_few_active:
            logger.info(
                "hot_players[%s] EVICT player_id=%s active_days=%s recency=%s "
                "reason=%s",
                realm, player_id, active_days, recency,
                'inactivity' if inactive_too_long else 'low-active-days')
            evicted += 1
            if not dry_run:
                hp.delete()

    # --- Cap / trim by hot_score ------------------------------------------
    trimmed = 0
    if not dry_run:
        engagement_members = HotPlayer.objects.filter(
            realm=realm, source=HotPlayer.SOURCE_ENGAGEMENT).order_by('-hot_score')
        member_count = engagement_members.count()
        if member_count > cap:
            trim_ids = list(
                engagement_members.values_list('id', flat=True)[cap:])
            trimmed = len(trim_ids)
            logger.info(
                "hot_players[%s] TRIM %s engagement members over cap=%s "
                "(qualified=%s)",
                realm, trimmed, cap, member_count)
            HotPlayer.objects.filter(id__in=trim_ids).delete()
    else:
        # Dry-run cap sizing: project the post-maintain engagement set size and
        # report how many would be trimmed over the cap (audit only, no writes).
        def _would_promote(pid: int, e: dict) -> bool:
            return (
                pid in known_player_ids
                and e['active_days'] >= promote_min
                and e['recency_days'] is not None
                and e['recency_days'] <= promote_recency
                and e['sessions'] >= promote_sessions
            )

        def _would_evict(pid: int) -> bool:
            e = engagement.get(pid)
            recency = e['recency_days'] if e else None
            active_days = e['active_days'] if e else 0
            return (recency is None or recency > evict_inactivity
                    or active_days < evict_min_active)

        new_promotions = {
            pid for pid, e in engagement.items()
            if pid not in existing and _would_promote(pid, e)
        }
        survivors = {
            pid for pid, hp in existing.items()
            if hp.source == HotPlayer.SOURCE_PINNED or not _would_evict(pid)
        }
        engagement_survivors = {
            pid for pid in survivors
            if existing[pid].source == HotPlayer.SOURCE_ENGAGEMENT
        }
        projected = len(new_promotions | engagement_survivors)
        trimmed = max(0, projected - cap)

    result = {
        'realm': realm,
        'promoted': promoted,
        'evicted': evicted,
        'updated': updated,
        'trimmed': trimmed,
        'hot_set_size': HotPlayer.objects.filter(realm=realm).count(),
        'dry_run': dry_run,
    }
    logger.info("hot_players[%s] maintain: %s", realm, result)
    return result


def capture_hot_players(realm: str, *, logger=logger) -> dict:
    """Sweep the HotPlayer set: guarantee an observation + a daily snapshot.

    For each member, skip-if-fresh against the latest ``BattleObservation`` (the
    floor already covers active hot players within HOT_OBSERVE_FLOOR_HOURS) else
    ``record_observation_and_diff``; and write a gap-free daily ``Snapshot`` via
    ``update_snapshot_data(refresh_player=False)`` when today's row is missing.
    Bounded by HOT_PLAYERS_MAX, paced by HOT_PLAYERS_CAPTURE_DELAY. Hidden
    accounts return nothing from WG and are recorded as skipped (no retry storm).
    """
    from warships.data import update_snapshot_data
    from warships.incremental_battles import record_observation_and_diff

    now = timezone.now()
    today = now.date()
    floor_hours = _observe_floor_hours()
    stale_before = now - timedelta(hours=floor_hours)
    delay = _capture_delay()
    cap = _hot_players_max()

    members = list(
        HotPlayer.objects
        .filter(realm=realm)
        .select_related('player')
        .order_by('-hot_score')[:cap]
    )

    observed = obs_skipped_fresh = obs_skipped_hidden = 0
    snapshotted = snap_skipped_present = errors = 0

    for hp in members:
        player = hp.player
        # --- Observation path (skip-if-fresh) ---
        latest = (
            BattleObservation.objects
            .filter(player=player)
            .order_by('-observed_at')
            .values_list('observed_at', flat=True)
            .first()
        )
        if latest is not None and latest >= stale_before:
            obs_skipped_fresh += 1
        else:
            try:
                res = record_observation_and_diff(player.player_id, realm)
                if res.get('status') == 'skipped':
                    obs_skipped_hidden += 1
                else:
                    observed += 1
                    hp.last_observed_at = timezone.now()
                    hp.save(update_fields=['last_observed_at'])
            except Exception:
                errors += 1
                logger.exception(
                    "hot_players[%s] capture observation failed for player_id=%s",
                    realm, player.player_id)

        # --- Snapshot path (gap-free daily summary) ---
        if Snapshot.objects.filter(player=player, date=today).exists():
            snap_skipped_present += 1
        else:
            try:
                update_snapshot_data(player.player_id, realm, refresh_player=False)
                snapshotted += 1
                hp.last_snapshotted_at = timezone.now()
                hp.save(update_fields=['last_snapshotted_at'])
            except Player.DoesNotExist:
                errors += 1
            except Exception:
                errors += 1
                logger.exception(
                    "hot_players[%s] capture snapshot failed for player_id=%s",
                    realm, player.player_id)

        if delay:
            time.sleep(delay)

    result = {
        'realm': realm,
        'hot_set_size': len(members),
        'observed': observed,
        'obs_skipped_fresh': obs_skipped_fresh,
        'obs_skipped_hidden': obs_skipped_hidden,
        'snapshotted': snapshotted,
        'snap_skipped_present': snap_skipped_present,
        'errors': errors,
    }
    logger.info("hot_players[%s] capture: %s", realm, result)
    return result
