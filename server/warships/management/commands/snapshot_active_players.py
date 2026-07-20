"""Daily snapshot engine for active players.

Writes a per-player daily ``Snapshot`` row (cumulative PvP battles/wins +
day-over-day interval) for every recently-active player, so day-over-day
progress is tracked for the whole active base — not just players who happen to
get visited or caught by the capped detail-refresh.

Design (deliberately light + bulk-efficient):
* Selects active, visible players (``last_battle_date`` within ``--active-days``)
  that do NOT already have *today's* snapshot — so it is idempotent and
  self-completing across runs.
* Refreshes cumulative stats via **bulk** ``account/info`` (100 accounts per WG
  call) through ``fetch_players_bulk`` → ``save_player(core_only=True)``, then
  writes the daily ``Snapshot`` via ``update_snapshot_data(refresh_player=False)``
  (pure-DB: no extra WG call). ~1 WG call per 100 active players.
* Does NOT rebuild ``battles_json`` (the heavy per-ship detail) — that stays on
  the incremental-refresh / on-demand path. This job is the *snapshot* engine.

It is meant to run frequently on a Beat schedule (coexisting with clan crawls)
so the active base is fully snapshotted each UTC day. Runbook:
``agents/runbooks/runbook-daily-active-snapshots-2026-06-09.md``.
"""
import os
import time
from datetime import timedelta

from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.utils import timezone

from warships.models import DEFAULT_REALM, Player, VALID_REALMS

BULK_ACCOUNT_INFO_SIZE = 100  # WG account/info max account_ids per call


class Command(BaseCommand):
    help = "Write daily Snapshot rows for active players (bulk account/info, idempotent per UTC day)."

    def add_arguments(self, parser):
        parser.add_argument('--realm', default=DEFAULT_REALM, choices=sorted(VALID_REALMS))
        parser.add_argument('--active-days', type=int,
                            default=int(os.getenv('SNAPSHOT_ACTIVE_DAYS', '7')),
                            help='Snapshot players who battled within this many days (default 7).')
        parser.add_argument('--limit', type=int,
                            default=int(os.getenv('SNAPSHOT_ACTIVE_LIMIT', '3000')),
                            help='Max players to snapshot this run (default 3000).')
        parser.add_argument('--min-battles', type=int,
                            default=int(os.getenv('SNAPSHOT_ACTIVE_MIN_BATTLES', '0')),
                            help='Skip players with fewer than this many PvP battles (default 0).')
        parser.add_argument('--delay', type=float,
                            default=float(os.getenv('SNAPSHOT_ACTIVE_DELAY', '0.2')),
                            help='Seconds to pause between bulk batches (default 0.2).')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report how many players would be snapshotted, fetch nothing.')

    def handle(self, *args, **opts):
        from warships.clan_crawl import fetch_players_bulk, save_player
        from warships.data import update_snapshot_data

        realm = opts['realm']
        active_days = opts['active_days']
        limit = opts['limit']
        min_battles = opts['min_battles']
        delay = opts['delay']
        dry_run = opts['dry_run']

        today = timezone.now().date()
        cutoff = today - timedelta(days=active_days)

        candidates = (
            Player.objects
            .filter(realm=realm, is_hidden=False,
                    last_battle_date__isnull=False, last_battle_date__gte=cutoff,
                    pvp_battles__gte=min_battles)
            .exclude(snapshot__date=today)
            .order_by('-last_battle_date')
        )

        # Delta-gated writes leave unchanged players without a today-row, so
        # the has-today-row exclusion alone would re-select the same recency-
        # ordered top of the pool every run. A per-day checked set (cache,
        # realm+date-keyed) keeps the 30-min runs converging across the whole
        # pool; on cache loss the engine gracefully degrades to re-polling.
        # Errored players are deliberately NOT marked checked (retry next run).
        checked_key = f"snapshot_checked:{realm}:{today.isoformat()}"
        checked = cache.get(checked_key) or set()

        out = self.stdout.write
        if dry_run:
            pending = candidates.count()
            out(f"=== snapshot_active_players DRY RUN realm={realm} ===")
            out(f"Active (<= {active_days}d), visible, not yet snapshot today: {pending} "
                f"(checked-today (unchanged): {len(checked)}; "
                f"would process up to {limit})")
            return

        # At most len(checked) ids of this recency-ordered prefix can already
        # be checked, so it always yields `limit` fresh targets when they exist.
        candidate_ids = list(
            candidates.values_list('player_id', flat=True)[:limit + len(checked)])
        target_ids = [pid for pid in candidate_ids if pid not in checked][:limit]
        rank = {pid: i for i, pid in enumerate(target_ids)}
        batch_players = sorted(
            Player.objects
            .filter(realm=realm, player_id__in=target_ids)
            .select_related('clan'),
            key=lambda p: rank[p.player_id])
        clan_by_id = {p.player_id: p.clan for p in batch_players}
        all_ids = [p.player_id for p in batch_players]

        snapshotted = skipped_unchanged = skipped_hidden = errors = 0
        for start in range(0, len(all_ids), BULK_ACCOUNT_INFO_SIZE):
            ids = all_ids[start:start + BULK_ACCOUNT_INFO_SIZE]
            data = fetch_players_bulk(ids, realm=realm, request_delay=delay)
            for pid_str, pdata in (data or {}).items():
                try:
                    pid = int(pid_str)
                except (TypeError, ValueError):
                    continue
                try:
                    # Light summary refresh (preserve clan; no efficiency/achievements).
                    save_player(pdata, clan=clan_by_id.get(pid), realm=realm, core_only=True)
                    if pdata and pdata.get('hidden_profile'):
                        skipped_hidden += 1
                        continue
                    status = update_snapshot_data(
                        pid, realm=realm, refresh_player=False)
                    if status == 'skipped-unchanged':
                        skipped_unchanged += 1
                        checked.add(pid)
                    else:
                        snapshotted += 1
                except Exception:
                    errors += 1
                    self.stderr.write(f"snapshot_active_players: error on player {pid_str}")
            if delay:
                time.sleep(delay)

        if checked:
            cache.set(checked_key, checked, timeout=26 * 3600)

        out(f"=== snapshot_active_players realm={realm} ===")
        out(f"Queued: {len(all_ids)}  Snapshotted: {snapshotted}  "
            f"Unchanged-skipped: {skipped_unchanged}  "
            f"Hidden-skipped: {skipped_hidden}  Errors: {errors}")
