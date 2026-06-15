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
  ``capture_hot_player_observations_task``. This is the queue's sole purpose: a
  ≥24h battle-history pull per hot player. (The old per-12-min freshness sweep —
  Tier 3 of the player-refresh-latency runbook, which kept ``battles_updated_at``
  inside the visit window for sub-second loads — was retired 2026-06-15.)
* ``backfill_hot_players`` — one-time seed of the most-active players (see fn).

Env knobs (read inline via ``os.getenv`` to match the FLOOR / SNAPSHOT / ENRICH
families — no sibling domain knob is a settings.py constant). See the runbook:
``agents/runbooks/runbook-hot-players-engagement-queue-2026-06-10.md``.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import timedelta

from django.db.models import Count, Exists, F, Max, OuterRef, Sum
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


def _capture_max_pulls() -> int:
    """Max WG fetches per capture run — the anti-starvation work budget.

    Capping WG calls (not members) keeps one run safely under the task's 540s
    soft limit even at the worst observed ~7s/pull (65 * 7 = 455s), regardless of
    how many of the hot set are floor-missed. Members past the budget rotate in on
    the next run (ordering by ``last_observed_at`` advances them). See
    [[project_hot_queue_stale_seed_starves]] for why an unbudgeted all-pull sweep
    starved the tail.
    """
    return int(os.getenv("HOT_CAPTURE_MAX_PULLS", "65"))


def _backfill_active_days() -> int:
    return int(os.getenv("HOT_BACKFILL_ACTIVE_DAYS", "7"))


# Every backfill seed's hot_score is held below this ceiling so it always ranks
# under the engagement floor (a surviving engagement member has active_days >=
# HOT_EVICT_MIN_ACTIVE_DAYS=2 → score >= 2_000_000). Seeds are still ordered
# among themselves by battle volume (most active trimmed last).
_BACKFILL_SCORE_CEIL = 900_000


def _backfill_score(pvp_battles) -> float:
    return float(min(int(pvp_battles or 0), _BACKFILL_SCORE_CEIL))


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
            # we do NOT re-apply the promote threshold here). A backfill seed that
            # now meets the promote rule GRADUATES to 'engagement' so it lives by
            # the normal rules (and becomes inactivity-evictable) from here on.
            updated += 1
            graduate = (
                hp.source == HotPlayer.SOURCE_BACKFILL
                and active_days >= promote_min
                and recency is not None and recency <= promote_recency
                and sessions >= promote_sessions
            )
            if not dry_run:
                if graduate:
                    hp.source = HotPlayer.SOURCE_ENGAGEMENT
                hp.last_engaged_at = eng['last_engaged_at'] or hp.last_engaged_at
                hp.active_days_window = active_days
                hp.unique_sessions_window = sessions
                hp.views_deduped_window = views
                hp.hot_score = score
                fields = ['last_engaged_at', 'active_days_window',
                          'unique_sessions_window', 'views_deduped_window', 'hot_score']
                if graduate:
                    fields.append('source')
                hp.save(update_fields=fields)

    # --- Evictions (hysteresis) -------------------------------------------
    for player_id, hp in existing.items():
        # Pinned overrides and backfill seeds are exempt from inactivity-eviction
        # (backfill rows have no engagement by design — they leave only via the
        # cap-trim below when engaged players need their slots).
        if hp.source in (HotPlayer.SOURCE_PINNED, HotPlayer.SOURCE_BACKFILL):
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
    # Cap applies to engagement + backfill combined (pinned exempt). Trimming the
    # lowest-score tail over the cap removes backfill seeds FIRST (their score is
    # always below the engagement floor), then the weakest engagement members only
    # if engagement alone exceeds the cap.
    trimmed = 0
    if not dry_run:
        trimmable = HotPlayer.objects.filter(
            realm=realm,
            source__in=[HotPlayer.SOURCE_ENGAGEMENT, HotPlayer.SOURCE_BACKFILL],
        ).order_by('-hot_score')
        member_count = trimmable.count()
        if member_count > cap:
            trim_ids = list(trimmable.values_list('id', flat=True)[cap:])
            trimmed = len(trim_ids)
            logger.info(
                "hot_players[%s] TRIM %s members over cap=%s (qualified=%s, "
                "backfill-first)",
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

        # Pinned and backfill are exempt from inactivity-eviction.
        protected = (HotPlayer.SOURCE_PINNED, HotPlayer.SOURCE_BACKFILL)
        new_promotions = {
            pid for pid, e in engagement.items()
            if pid not in existing and _would_promote(pid, e)
        }
        survivors = {
            pid for pid, hp in existing.items()
            if hp.source in protected or not _would_evict(pid)
        }
        # Cap counts engagement + backfill survivors (pinned exempt); the trim
        # falls on backfill first.
        capped_survivors = {
            pid for pid in survivors
            if existing[pid].source != HotPlayer.SOURCE_PINNED
        }
        projected = len(new_promotions | capped_survivors)
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


def capture_hot_players(realm: str, *, logger=logger, max_pulls=None) -> dict:
    """Sweep the HotPlayer set: guarantee an observation + a daily snapshot.

    For each member, skip-if-fresh against the latest ``BattleObservation`` (the
    floor already covers active hot players within HOT_OBSERVE_FLOOR_HOURS) else
    ``record_observation_and_diff``; and write a gap-free daily ``Snapshot`` via
    ``update_snapshot_data(refresh_player=False)`` when today's row is missing.
    Hidden accounts return nothing from WG and are recorded as skipped.

    **Work-budgeted + rotating (anti-starvation).** A floor-missed backfill seed is
    all expensive pulls, so an unbudgeted ``-hot_score`` sweep blows the task's
    540s soft limit at ~90 members and re-pulls the same static head every run
    while the tail starves ([[project_hot_queue_stale_seed_starves]]). Instead:

    * Order = engagement/pinned first (by ``-hot_score`` — few, high-value, keeps
      their daily contract), then backfill by ``last_observed_at`` ASC NULLS FIRST
      so the floor-missed set drains **round-robin** by coverage age.
    * Stop after ``HOT_CAPTURE_MAX_PULLS`` actual WG fetches (success *or*
      hidden-skip — both spend a call); members past the budget rotate in next run.
    * Stamp ``last_observed_at`` whenever a WG call is spent on a member (pull or
      hidden), advancing them to the back of the rotation. A cheap *fresh-skip*
      (no WG call) is NOT stamped — those stay at the head for a free re-check.
    """
    from warships.data import update_snapshot_data
    from warships.incremental_battles import record_observation_and_diff

    now = timezone.now()
    today = now.date()
    floor_hours = _observe_floor_hours()
    stale_before = now - timedelta(hours=floor_hours)
    delay = _capture_delay()
    cap = _hot_players_max()
    if max_pulls is None:
        max_pulls = _capture_max_pulls()

    # Priority members (engagement/pinned) every run; backfill rotates by coverage
    # age so the limited per-run WG budget walks the whole floor-missed set.
    priority = list(
        HotPlayer.objects
        .filter(realm=realm)
        .exclude(source=HotPlayer.SOURCE_BACKFILL)
        .select_related('player')
        .order_by('-hot_score')
    )
    backfill = list(
        HotPlayer.objects
        .filter(realm=realm, source=HotPlayer.SOURCE_BACKFILL)
        .select_related('player')
        .order_by(F('last_observed_at').asc(nulls_first=True), '-hot_score')
    )
    members = (priority + backfill)[:cap]

    observed = obs_skipped_fresh = obs_skipped_hidden = 0
    snapshotted = snap_skipped_present = errors = 0
    wg_calls = 0
    processed = 0
    stopped_early = False

    for hp in members:
        if wg_calls >= max_pulls:
            stopped_early = True
            break
        processed += 1
        player = hp.player
        # --- Observation path (skip-if-fresh, else a budgeted WG fetch) ---
        latest = (
            BattleObservation.objects
            .filter(player=player)
            .order_by('-observed_at')
            .values_list('observed_at', flat=True)
            .first()
        )
        if latest is not None and latest >= stale_before:
            obs_skipped_fresh += 1   # floor covers them; free, no stamp, no budget
        else:
            wg_calls += 1
            try:
                res = record_observation_and_diff(player.player_id, realm)
                if res.get('status') == 'skipped':
                    obs_skipped_hidden += 1
                else:
                    observed += 1
                # Stamp on any spent WG call (pull or hidden) so the member
                # rotates to the back instead of re-burning budget every run.
                hp.last_observed_at = timezone.now()
                hp.save(update_fields=['last_observed_at'])
            except Exception:
                errors += 1
                logger.exception(
                    "hot_players[%s] capture observation failed for player_id=%s",
                    realm, player.player_id)
            if delay:
                time.sleep(delay)

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

    result = {
        'realm': realm,
        'hot_set_size': len(members),
        'processed': processed,
        'wg_calls': wg_calls,
        'max_pulls': max_pulls,
        'stopped_early': stopped_early,
        'remaining': max(0, len(members) - processed),
        'observed': observed,
        'obs_skipped_fresh': obs_skipped_fresh,
        'obs_skipped_hidden': obs_skipped_hidden,
        'snapshotted': snapshotted,
        'snap_skipped_present': snap_skipped_present,
        'errors': errors,
    }
    logger.info("hot_players[%s] capture: %s", realm, result)
    return result


def backfill_hot_players(realm: str, *, dry_run: bool = False,
                         logger=logger) -> dict:
    """One-time seed: fill a realm's hot queue to the cap with the most-active
    players the observation floor is NOT already keeping fresh.

    Selects active (``last_battle_date`` within ``HOT_BACKFILL_ACTIVE_DAYS``=7),
    non-hidden players ordered by ``pvp_battles`` desc (recent + high volume),
    skips anyone already in the queue, **and excludes players who already have a
    ``BattleObservation`` within ``HOT_OBSERVE_FLOOR_HOURS``** — i.e. the ones the
    capture sweep would skip-if-fresh. Mirroring that skip predicate means each
    seeded slot is a player the sweep will actually *pull* (coverage the floor is
    missing), not a wasted skip. It inserts ``source='backfill'`` rows up to the
    remaining ``HOT_PLAYERS_MAX`` headroom. Each seed scores BELOW the engagement
    floor (``_backfill_score``) so it ranks under every engaged member and is the
    first cap-trimmed; ``maintain_hot_players`` protects it from
    inactivity-eviction and graduates it to 'engagement' if it later earns
    view-recurrence. Idempotent — re-running tops the queue back up to the cap.
    No WG calls (pure DB); the nightly capture sweep does the observation work.

    Because the seed is all floor-missed (expensive) players, the capture sweep is
    work-budgeted and rotates by coverage age — see ``capture_hot_players`` and
    [[project_hot_queue_stale_seed_starves]]; an unbudgeted all-pull sweep starves.
    """
    cap = _hot_players_max()
    active_days = _backfill_active_days()
    today = timezone.now().date()
    cutoff = today - timedelta(days=active_days)
    stale_before = timezone.now() - timedelta(hours=_observe_floor_hours())

    current_ids = set(
        HotPlayer.objects.filter(realm=realm)
        .values_list('player__player_id', flat=True)
    )
    slots = cap - len(current_ids)
    if slots <= 0:
        result = {'realm': realm, 'cap': cap, 'current': len(current_ids),
                  'slots': 0, 'added': 0, 'dry_run': dry_run}
        logger.info("hot_players[%s] backfill (full): %s", realm, result)
        return result

    # Exclude players the floor already covers (fresh obs within the capture skip
    # window) so the seed holds only players the sweep will actually pull.
    fresh_obs = BattleObservation.objects.filter(
        player_id=OuterRef('id'), observed_at__gte=stale_before)
    candidates = list(
        Player.objects
        .filter(realm=realm, is_hidden=False, last_battle_date__gte=cutoff)
        .exclude(player_id__in=current_ids)
        .annotate(_has_fresh_obs=Exists(fresh_obs))
        .filter(_has_fresh_obs=False)
        .order_by('-pvp_battles', '-last_battle_date')
        .values_list('id', 'pvp_battles')[:slots]
    )
    added = len(candidates)
    if not dry_run and candidates:
        rows = [
            HotPlayer(player_id=pk, realm=realm,
                      source=HotPlayer.SOURCE_BACKFILL,
                      hot_score=_backfill_score(pvp))
            for pk, pvp in candidates
        ]
        # ignore_conflicts guards the unique(player, realm) constraint against a
        # concurrent promotion racing the seed.
        HotPlayer.objects.bulk_create(rows, ignore_conflicts=True, batch_size=500)

    result = {'realm': realm, 'cap': cap, 'current': len(current_ids),
              'slots': slots, 'added': added, 'dry_run': dry_run}
    logger.info("hot_players[%s] backfill: %s", realm, result)
    return result
