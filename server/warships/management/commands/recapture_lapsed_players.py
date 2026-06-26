"""Cheap bulk re-discovery of returning ("lapsed") players.

Players who go quiet for more than `BATTLE_OBSERVATION_FLOOR_DAYS` (7) fall out
of the observation floor's `active_7d` scope. Their stored `last_battle_date` is
then frozen and only ages — nothing passively re-checks them, so a returning
player stays invisible to battle capture until an *event* (a profile view, or a
clan crawl reaching their clan) forces a direct WG refresh. The clanless,
unviewed returner can play for days fully uncaptured.

The cheap fix: WG `account/info` is bulk (100 accounts per call) and returns
`last_battle_time`, so we can scan a whole lapsed band for a few hundred WG calls
and detect who has actually come back — *detection* is cheap; the expensive
ships/stats harvest is only ever paid for real returners.

For movers whose new battle lands them back inside `active_7d`, writing the fresh
`last_battle_date` drops them back into floor scope and the **existing floor
harvests them on its next cycle** — no new harvest path (the "let the floor catch
it" design). Like `refresh_clan_member_idle_task`, the promote step writes ONLY
`last_battle_date` + `days_since_last_battle`, NEVER `last_fetch` (bumping it
would suppress the real per-player full refresh that builds `battles_json`).

LRU rotation (the production knob): a single recency-first pass would re-check the
just-lapsed end forever and never reach the deep >90d tail — exactly the "gone
100+ days, new battles waiting" case. So `--apply` stamps `Player.last_idle_check_at`
on every checked row and the candidate query orders by it NULLS FIRST. Each run
takes the least-recently-checked `--limit` dormant rows, so over a few cycles the
cursor walks the whole pool and then maintains it. The Beat task
(`recapture_lapsed_players_task`, gated by `RECAPTURE_LAPSED_ENABLED`) sizes
`--limit` so a realm's band rotates fully in ~a week.

Modes:
  * `--apply` OFF (default) = DETECT-ONLY: hits WG to measure yield but writes
    NOTHING (no promotes, no cursor stamp, so no rotation). Use this for a
    one-shot yield measurement on the droplet (shared WG limiter) before trusting
    writes. NOT `--dry-run` — it does make WG calls.
  * `--apply` ON = production: promote returners + stamp the rotation cursor.

Yield is bucketed into the two groups that matter for the design:
  - reactivated INTO active_7d  -> promote-only harvests them for free.
  - advanced but STILL lapsed   -> promote keeps their displayed idle accurate
                                   but the floor won't harvest them (out of scope).
and each is split clanned vs clanless. Clanless-into-7d is the marginal value:
returners nothing else recovers.
"""
import logging
import os
import time
from datetime import datetime, timedelta, timezone as dt_timezone

from django.core.management.base import BaseCommand
from django.db.models import F
from django.utils import timezone

from warships.models import Clan, DEFAULT_REALM, DeletedAccount, Player, VALID_REALMS

BULK_ACCOUNT_INFO_SIZE = 100  # WG account/info max account_ids per call
CURSOR_STAMP_CHUNK = 2000     # ids per cursor UPDATE statement

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = ("Detect returning lapsed players via bulk account/info and (with "
            "--apply) promote them back into the active_7d floor scope, stamping "
            "the LRU rotation cursor so the whole dormant pool rotates over time.")

    def add_arguments(self, parser):
        parser.add_argument('--realm', default=DEFAULT_REALM, choices=sorted(VALID_REALMS))
        parser.add_argument('--min-days', type=int, default=8,
                            help='Lower edge of the lapsed band: last battle at least '
                                 'this many days ago (default 8 = just past active_7d).')
        parser.add_argument('--max-days', type=int, default=365,
                            help='Upper edge of the lapsed band: last battle no more than '
                                 'this many days ago (default 365; the deeper tail is huge '
                                 'and low-yield).')
        parser.add_argument('--active-days', type=int,
                            default=int(os.getenv('BATTLE_OBSERVATION_FLOOR_DAYS', '7')),
                            help='Floor window: a returner whose new last battle is within '
                                 'this many days re-enters the floor scope (default 7).')
        parser.add_argument('--batch-size', type=int, default=BULK_ACCOUNT_INFO_SIZE,
                            help='account/info ids per WG call (max 100).')
        parser.add_argument('--delay', type=float, default=0.2,
                            help='Seconds to pause between bulk batches (default 0.2).')
        parser.add_argument('--limit', type=int, default=0,
                            help='Max players to scan per run (0 = whole band). Production '
                                 'always sets this; 0 is for one-shot band-wide measurement.')
        parser.add_argument('--sample', type=int, default=15,
                            help='Print up to this many example reactivations (default 15).')
        parser.add_argument('--apply', action='store_true',
                            help='Persist promotions (last_battle_date + days_since) AND stamp '
                                 'the rotation cursor. OFF by default = detect-only, no writes.')

    def handle(self, *args, **opts):
        from warships.api.players import (
            _bulk_fetch_account_info,
            _per_player_account_fallback,
        )

        realm = opts['realm']
        min_days, max_days = opts['min_days'], opts['max_days']
        active_days = opts['active_days']
        batch = min(opts['batch_size'], BULK_ACCOUNT_INFO_SIZE)
        delay = opts['delay']
        limit = opts['limit']
        sample_n = opts['sample']
        apply = opts['apply']
        out = self.stdout.write

        now_dt = timezone.now()
        today = now_dt.date()
        # last_battle_date in [today-max_days, today-min_days]  ==  the lapsed band.
        newest = today - timedelta(days=min_days)
        oldest = today - timedelta(days=max_days)

        candidates = (
            Player.objects
            .filter(realm=realm, is_hidden=False,
                    last_battle_date__isnull=False,
                    last_battle_date__gte=oldest,
                    last_battle_date__lte=newest)
            .exclude(name='')
            .exclude(player_id__in=DeletedAccount.objects.values('account_id'))
            # LRU rotation: least-recently-checked first (never-checked = NULL
            # sorts first), recency as the tiebreak. The cursor stamp (apply mode)
            # is what advances this across runs.
            .order_by(F('last_idle_check_at').asc(nulls_first=True), '-last_battle_date')
        )
        if limit:
            candidates = candidates[:limit]

        # Pull ONLY the columns we need — never the full model, whose battles_json
        # blob OOMs the box at band scale (43k rows -> 6GB+ RSS).
        rows = list(candidates.values_list(
            'id', 'player_id', 'name', 'last_battle_date', 'clan_id'))
        by_id = {pid: (row_id, name, stored, clan_id)
                 for (row_id, pid, name, stored, clan_id) in rows}
        ids = list(by_id)

        # Counters
        wg_calls = chunk_errors = no_data = hidden = still_dormant = 0
        into7d_clanned = into7d_clanless = 0
        lapsed_clanned = lapsed_clanless = 0
        promote = []
        checked_ids = []   # rows we got a definitive answer for -> advance cursor
        samples = []

        for start in range(0, len(ids), batch):
            chunk = ids[start:start + batch]
            data, err = _bulk_fetch_account_info(chunk, realm)
            wg_calls += 1
            if err == 'INVALID_ACCOUNT_ID':
                data = _per_player_account_fallback(chunk, realm)
            elif err:
                # Transient batch failure: leave the cursor untouched so these
                # rows are retried next run rather than rotated past unchecked.
                chunk_errors += 1
                self.stderr.write(
                    f"recapture_lapsed_players: batch failed realm={realm} err={err}")
                continue

            for pid in chunk:
                info = data.get(str(pid)) if data else None
                if not info:
                    no_data += 1
                    checked_ids.append(by_id[pid][0])
                    continue
                # We got a real answer for this row -> it counts toward rotation.
                checked_ids.append(by_id[pid][0])
                if info.get('hidden_profile'):
                    hidden += 1
                    continue
                lbt = info.get('last_battle_time')
                new_date = (
                    datetime.fromtimestamp(lbt, tz=dt_timezone.utc).date()
                    if lbt else None
                )
                row_id, name, stored, clan_id = by_id[pid]
                if not new_date or (stored and new_date <= stored):
                    still_dormant += 1
                    continue

                # Advanced: real new activity since our stored value.
                into_7d = new_date >= (today - timedelta(days=active_days))
                clanless = clan_id is None
                if into_7d and clanless:
                    into7d_clanless += 1
                elif into_7d:
                    into7d_clanned += 1
                elif clanless:
                    lapsed_clanless += 1
                else:
                    lapsed_clanned += 1

                if len(samples) < sample_n:
                    samples.append((
                        name, stored, new_date,
                        (new_date - stored).days if stored else None,
                        clan_id,
                        'into-7d' if into_7d else 'still-lapsed',
                    ))

                # bulk_update only touches the listed fields, keyed by pk (id).
                promote.append(Player(
                    id=row_id, last_battle_date=new_date,
                    days_since_last_battle=(today - new_date).days))
            if delay:
                time.sleep(delay)

        if apply:
            if promote:
                Player.objects.bulk_update(
                    promote, ['last_battle_date', 'days_since_last_battle'])
            # Advance the rotation cursor for every row we actually checked
            # (never last_fetch — that would suppress the floor's real refresh).
            for i in range(0, len(checked_ids), CURSOR_STAMP_CHUNK):
                Player.objects.filter(
                    id__in=checked_ids[i:i + CURSOR_STAMP_CHUNK]
                ).update(last_idle_check_at=now_dt)

        into7d = into7d_clanned + into7d_clanless
        still_lapsed = lapsed_clanned + lapsed_clanless
        advanced = len(promote)
        scanned = len(ids)
        mode = "APPLY (promoted + cursor stamped)" if apply else "DETECT-ONLY (no writes)"
        rate = (advanced / scanned * 100) if scanned else 0.0

        # One structured line for the /recapture readout skill to grep out of the
        # worker journal (the multi-line stdout block below is for humans).
        logger.info(
            "recapture-summary realm=%s mode=%s band=%d-%d scanned=%d wg_calls=%d "
            "advanced=%d into7d=%d into7d_clanless=%d still_lapsed=%d "
            "still_dormant=%d hidden=%d no_data=%d errors=%d",
            realm, ("apply" if apply else "detect"), min_days, max_days, scanned,
            wg_calls, advanced, into7d, into7d_clanless, still_lapsed,
            still_dormant, hidden, no_data, chunk_errors,
        )

        out(f"=== recapture_lapsed_players  realm={realm}  band={min_days}-{max_days}d  {mode} ===")
        out(f"  scanned={scanned}  WG_calls={wg_calls}  chunk_errors={chunk_errors}  "
            f"no_data={no_data}  hidden={hidden}")
        out(f"  still_dormant={still_dormant}  advanced(returned)={advanced}  "
            f"yield={rate:.2f}% of scanned")
        out(f"  -> reactivated INTO active_7d (floor harvests free): {into7d}  "
            f"[clanned={into7d_clanned}  CLANLESS={into7d_clanless}]")
        out(f"  -> advanced but STILL lapsed (out of floor scope): {still_lapsed}  "
            f"[clanned={lapsed_clanned}  CLANLESS={lapsed_clanless}]")
        if apply:
            out(f"  cursor stamped on {len(checked_ids)} checked rows (LRU rotation advanced)")
        else:
            out(f"  (detect-only: {wg_calls} WG calls made, {advanced} reactivations detected, 0 writes)")
        if samples:
            clan_names = dict(
                Clan.objects.filter(
                    id__in={cid for *_, cid, _ in samples if cid is not None})
                .values_list('id', 'name'))
            out("  sample reactivations (name | stored -> new | +days | clan | bucket):")
            for name, stored, new_date, adv, clan_id, bucket in samples:
                clan = 'clanless' if clan_id is None else (clan_names.get(clan_id) or '?')
                out(f"    {name[:24]:24}  {str(stored):10} -> {str(new_date):10}  "
                    f"+{adv}d  {clan[:18]:18}  {bucket}")
