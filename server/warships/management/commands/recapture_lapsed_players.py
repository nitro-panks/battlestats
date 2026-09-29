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
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone as dt_timezone

from celery.exceptions import SoftTimeLimitExceeded
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.models import F
from django.utils import timezone

from warships.models import Clan, DEFAULT_REALM, DeletedAccount, Player, VALID_REALMS

BULK_ACCOUNT_INFO_SIZE = 100  # WG account/info max account_ids per call
CURSOR_STAMP_CHUNK = 2000     # ids per cursor UPDATE statement

# Durable per-run yield snapshots (sibling of the crawl-yield / observation-floor
# benchmarks). The /recapture skill reads these rather than the worker journal,
# because the background worker suppresses module-logger INFO (so a logged summary
# line never lands). Latest file per realm = "the last run".
RECAPTURE_BENCHMARK_DIR = os.getenv(
    "RECAPTURE_BENCHMARK_DIR",
    "/opt/battlestats-server/shared/benchmarks/recapture-lapsed",
)

logger = logging.getLogger(__name__)


def _max_consecutive_chunk_failures() -> int:
    """Consecutive unproductive WG chunks that abort the pass; 0 disables.

    Mirrors `clan_crawl._max_consecutive_clan_failures`. 10 rather than the
    crawl's 25 because the units differ: a chunk is 100 players to a crawl
    failure's 1 clan. `chunk_errors` was 0 on all 113 observed runs, so there is
    no transient-blip noise floor to clear and a tight bound is safe.
    """
    try:
        return int(os.getenv("RECAPTURE_MAX_CONSECUTIVE_CHUNK_FAILURES", "10"))
    except ValueError:
        return 10


def _chunk_failure_backoff_base() -> float:
    """Seconds slept after the first unproductive chunk; doubles per consecutive
    failure up to CHUNK_FAILURE_BACKOFF_CAP_S. 0 disables.

    Without it the guard measured calls, not time: on 2026-09-28 WG answered ten
    NA chunks with an instant 504 SOURCE_NOT_AVAILABLE inside 0.13s, and the
    whole 30,000-row sweep aborted on a blip shorter than one healthy chunk. At
    base 1s the default threshold of 10 now needs ~2.5 min of sustained failure
    (1+2+4+8+16+30x4 = 151s), while an isolated blip costs a second or two.
    """
    try:
        return max(0.0, float(os.getenv("RECAPTURE_CHUNK_FAILURE_BACKOFF_S", "1")))
    except ValueError:
        return 1.0


CHUNK_FAILURE_BACKOFF_CAP_S = 30.0


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
        promote = []       # flush buffer: returners awaiting bulk_update
        checked_ids = []   # flush buffer: rows we got a definitive answer for
        advanced = 0       # cumulative across flushes
        cursor_stamped = 0  # cumulative across flushes
        examined = 0       # ids actually iterated (< len(ids) when truncated)
        samples = []

        def flush():
            """Persist one incremental slice: promotes FIRST, then the cursor.

            Order matters. Stamping `last_idle_check_at` on a row whose promote
            has not landed rotates that returner past unpromoted, and the LRU
            cursor then hides them for a whole pool cycle (~a week) — a silent
            loss strictly worse than the truncation this guards against.
            """
            nonlocal advanced, cursor_stamped
            # Tally AFTER the write it describes, and clear in the same breath.
            # SoftTimeLimitExceeded arrives as a signal and lands on an arbitrary
            # bytecode boundary: counting first left the buffer full when it
            # landed mid-flush, and the finalizing flush() below then counted the
            # same rows twice. Re-running an interrupted write is safe (both
            # bulk_update and the cursor stamp are idempotent), so
            # write -> count -> clear is correct under every interleaving.
            if apply and promote:
                Player.objects.bulk_update(
                    promote, ['last_battle_date', 'days_since_last_battle'])
            advanced += len(promote)
            promote.clear()
            if apply:
                # Advance the rotation cursor for every row we actually checked
                # (never last_fetch — that would suppress the floor's real refresh).
                for i in range(0, len(checked_ids), CURSOR_STAMP_CHUNK):
                    Player.objects.filter(
                        id__in=checked_ids[i:i + CURSOR_STAMP_CHUNK]
                    ).update(last_idle_check_at=now_dt)
                cursor_stamped += len(checked_ids)
            checked_ids.clear()

        # Writes flush incrementally so a truncated run keeps everything it
        # earned. Before this, promotes and the cursor stamp both sat past the
        # end of the scan: EU/ASIA blew the worker's soft time limit every day
        # (ASIA last completed 2026-07-20) and lost the whole pass — WG calls
        # spent, no promotes, no cursor advance, no snapshot.
        # Upstream-failure guard. One dead chunk is noise; a run of them says the
        # upstream is gone and every further WG call is waste. The streak resets on
        # a chunk yielding >=1 usable `info`, NOT on "the chunk avoided `elif err:`":
        # INVALID_ACCOUNT_ID routes to `_per_player_account_fallback`, which under a
        # total outage returns a TRUTHY dict of Nones, so every row would take the
        # `no_data` path, reset a naive streak, and get cursor-stamped on an answer
        # nobody gave. See runbook-recapture-upstream-failure-guard-2026-08-12.md.
        aborted = False
        abort_reason = None
        consecutive_chunk_failures = 0
        max_consecutive_chunk_failures = _max_consecutive_chunk_failures()
        backoff_base = _chunk_failure_backoff_base()

        def note_chunk_failure(reason):
            """Count an unproductive chunk; True when the pass must abort."""
            nonlocal consecutive_chunk_failures, aborted, abort_reason
            consecutive_chunk_failures += 1
            if (max_consecutive_chunk_failures
                    and consecutive_chunk_failures >= max_consecutive_chunk_failures):
                aborted = True
                abort_reason = (
                    f"{consecutive_chunk_failures} consecutive unproductive WG "
                    f"chunks ({reason})")
                logger.error(
                    "recapture_lapsed_players: aborting realm=%s after %d "
                    "consecutive unproductive chunks (%s) — treating this as an "
                    "upstream outage. Rows in the failed chunks keep a NULL "
                    "cursor and are retried on the next run.",
                    realm, consecutive_chunk_failures, reason)
                return True
            # Space the next attempt out so the streak measures an outage's
            # duration rather than how fast WG can return an error.
            if backoff_base:
                time.sleep(min(CHUNK_FAILURE_BACKOFF_CAP_S,
                               backoff_base * 2 ** (consecutive_chunk_failures - 1)))
            return False

        truncated = False
        try:
            for start in range(0, len(ids), batch):
                chunk = ids[start:start + batch]
                data, err = _bulk_fetch_account_info(chunk, realm)
                wg_calls += 1
                # Counted once the call returns (error included, as before) so a
                # chunk cut short by the soft limit is excluded from `scanned`.
                examined += len(chunk)
                if err == 'INVALID_ACCOUNT_ID':
                    data = _per_player_account_fallback(chunk, realm)
                elif err:
                    # Transient batch failure: leave the cursor untouched so these
                    # rows are retried next run rather than rotated past unchecked.
                    chunk_errors += 1
                    self.stderr.write(
                        f"recapture_lapsed_players: batch failed realm={realm} err={err}")
                    if note_chunk_failure(f"last err={err}"):
                        break
                    continue

                # Buffered so a chunk that turns out to be wholly unusable can be
                # discarded rather than rotated past unchecked.
                chunk_checked = []
                usable = 0
                for pid in chunk:
                    info = data.get(str(pid)) if data else None
                    if not info:
                        no_data += 1
                        chunk_checked.append(by_id[pid][0])
                        continue
                    # We got a real answer for this row -> it counts toward rotation.
                    usable += 1
                    chunk_checked.append(by_id[pid][0])
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

                if usable == 0:
                    # Nothing usable for 100 real ids is the outage signature, not
                    # natural noise (`no_data` runs 2-23 per 30,000 scanned). Do not
                    # stamp: we have no answer for these rows.
                    if note_chunk_failure("chunk returned no usable account data"):
                        break
                else:
                    consecutive_chunk_failures = 0
                    checked_ids.extend(chunk_checked)
                    if len(checked_ids) >= CURSOR_STAMP_CHUNK:
                        flush()
                if delay:
                    time.sleep(delay)
        except SoftTimeLimitExceeded:
            # The worker's soft limit landed mid-scan. Everything already flushed
            # is durable; finalize the tail and report a PARTIAL run rather than
            # discarding the whole pass. The hard limit is the finalizer's budget.
            truncated = True
            # The signal can land INSIDE a write's transaction handling, which
            # leaves the psycopg connection with an open server-side transaction
            # while Django's own state says autocommit. The next atomic() entry
            # then dies on `can't change 'autocommit' now: connection in
            # transaction status ACTIVE` — 2026-08-15 asia lost its whole
            # snapshot that way, 117ms into the finalizer. Drop the connection so
            # the finalizing flush() (and the sample read below) reconnect clean.
            # Guarded on `in_atomic_block`: that flag being False is precisely
            # the desync (Django thinks autocommit, the server has a live
            # transaction), and closing while it is True would mark the
            # connection `closed_in_transaction` and break the caller's
            # transaction — which is what a Django TestCase always is.
            try:
                if connection.connection is not None and not connection.in_atomic_block:
                    connection.close()
            except Exception:  # pragma: no cover - defensive
                logger.warning("recapture: connection reset failed realm=%s",
                               realm, exc_info=True)

        # NEVER let the finalizer's failure suppress the snapshot. Before this,
        # `flush()` sat bare and the snapshot write was downstream of it, so any
        # exception here erased the whole run's record — the identical shape to
        # the pre-v5.1.6 bug (output gated behind a write that can fail), just
        # displaced by one frame. It surfaced only as staleness, a day late.
        flush_failed = False
        try:
            flush()
        except Exception:
            flush_failed = True
            logger.exception(
                "recapture_lapsed_players realm=%s: finalizing flush failed; "
                "earlier incremental flushes are still durable", realm)

        into7d = into7d_clanned + into7d_clanless
        still_lapsed = lapsed_clanned + lapsed_clanless
        scanned = examined
        mode = "APPLY (promoted + cursor stamped)" if apply else "DETECT-ONLY (no writes)"
        rate = (advanced / scanned * 100) if scanned else 0.0

        # One structured line for any worker whose INFO does propagate (secondary).
        logger.info(
            "recapture-summary realm=%s mode=%s band=%d-%d scanned=%d/%d partial=%s "
            "wg_calls=%d advanced=%d into7d=%d into7d_clanless=%d still_lapsed=%d "
            "still_dormant=%d hidden=%d no_data=%d errors=%d",
            realm, ("apply" if apply else "detect"), min_days, max_days, scanned,
            len(ids), truncated, wg_calls, advanced, into7d, into7d_clanless,
            still_lapsed, still_dormant, hidden, no_data, chunk_errors,
        )
        if truncated:
            logger.warning(
                "recapture_lapsed_players realm=%s TRUNCATED by the worker soft time "
                "limit after %d/%d candidates — writes up to that point are durable",
                realm, scanned, len(ids),
            )

        # Durable per-run snapshot — the /recapture skill's source of truth.
        snapshot = {
            "captured_at": now_dt.isoformat(),
            "realm": realm,
            "mode": "apply" if apply else "detect",
            "band_days": [min_days, max_days],
            "active_days": active_days,
            "limit": limit,
            # `partial` MUST be read before `scanned`: a truncated run has the same
            # scanned-below-limit signature as a healthy pass that exhausted the pool.
            "partial": truncated,
            # `aborted` is a SEPARATE axis from `partial`: partial means the soft
            # time limit cut the scan, aborted means the upstream died. A boolean
            # rather than a `status` string on purpose -- the ops email's
            # `_check_generic_shape` keys on `status` and would fire a second,
            # redundant condition for every aborted pass.
            "aborted": aborted,
            "abort_reason": abort_reason,
            "candidates": len(ids),
            "scanned": scanned,
            "wg_calls": wg_calls,
            "chunk_errors": chunk_errors,
            "no_data": no_data,
            "hidden": hidden,
            "still_dormant": still_dormant,
            "advanced": advanced,
            "yield_frac": round(advanced / scanned, 4) if scanned else 0.0,
            "into7d": into7d,
            "into7d_clanned": into7d_clanned,
            "into7d_clanless": into7d_clanless,
            "still_lapsed": still_lapsed,
            "still_lapsed_clanless": lapsed_clanless,
            "cursor_stamped": cursor_stamped,
            # A THIRD axis, independent of `partial` and `aborted`: the scan's
            # own numbers are honest, but the tail write did not land, so
            # `cursor_stamped` is short of `scanned` and those rows retry next
            # run. A boolean rather than a `status` string for the same reason
            # `aborted` is — the ops email's `_check_generic_shape` keys on
            # `status` and would fire a second, redundant condition.
            "flush_failed": flush_failed,
            # Wall-clock for the pass. `captured_at` is stamped at task RECEIPT,
            # not completion, so duration was previously reconstructible only
            # from `journalctl "succeeded in Ns"` — which is why every budget
            # question so far has needed a droplet login. This is what a
            # near-miss detector (duration > 85% of the soft limit) keys on: it
            # would have flagged asia days before either failure mode bit.
            "duration_s": round((timezone.now() - now_dt).total_seconds(), 1),
        }
        try:
            os.makedirs(RECAPTURE_BENCHMARK_DIR, exist_ok=True)
            fname = f"{now_dt.strftime('%Y-%m-%d_%H%MZ')}_{realm}.json"
            with open(os.path.join(RECAPTURE_BENCHMARK_DIR, fname), "w") as handle:
                json.dump(snapshot, handle, indent=2)
        except Exception:
            logger.warning("recapture snapshot write failed (dir=%s)",
                           RECAPTURE_BENCHMARK_DIR, exc_info=True)

        out(f"=== recapture_lapsed_players  realm={realm}  band={min_days}-{max_days}d  {mode} ===")
        if aborted:
            out(f"  *** ABORTED: {abort_reason} after {scanned}/{len(ids)} candidates. "
                f"Rows in the failed chunks were NOT cursor-stamped and retry next run. ***")
        if truncated:
            out(f"  *** PARTIAL: soft time limit hit after {scanned}/{len(ids)} candidates "
                f"(everything below is what this run actually persisted) ***")
        if flush_failed:
            out("  *** TAIL FLUSH FAILED: the buffered slice did not land. Earlier "
                "incremental flushes are durable; those rows keep a NULL cursor "
                "and retry next run. ***")
        out(f"  scanned={scanned}  WG_calls={wg_calls}  chunk_errors={chunk_errors}  "
            f"no_data={no_data}  hidden={hidden}")
        out(f"  still_dormant={still_dormant}  advanced(returned)={advanced}  "
            f"yield={rate:.2f}% of scanned")
        out(f"  -> reactivated INTO active_7d (floor harvests free): {into7d}  "
            f"[clanned={into7d_clanned}  CLANLESS={into7d_clanless}]")
        out(f"  -> advanced but STILL lapsed (out of floor scope): {still_lapsed}  "
            f"[clanned={lapsed_clanned}  CLANLESS={lapsed_clanless}]")
        if apply:
            out(f"  cursor stamped on {cursor_stamped} checked rows (LRU rotation advanced)")
        else:
            out(f"  (detect-only: {wg_calls} WG calls made, {advanced} reactivations detected, 0 writes)")
        if samples:
            # Cosmetic read, and it runs after the snapshot is already durable —
            # never let it be the thing that fails the task.
            try:
                clan_names = dict(
                    Clan.objects.filter(
                        id__in={cid for *_, cid, _ in samples if cid is not None})
                    .values_list('id', 'name'))
            except Exception:
                logger.warning("recapture: sample clan-name lookup failed realm=%s",
                               realm, exc_info=True)
                clan_names = {}
            out("  sample reactivations (name | stored -> new | +days | clan | bucket):")
            for name, stored, new_date, adv, clan_id, bucket in samples:
                clan = 'clanless' if clan_id is None else (clan_names.get(clan_id) or '?')
                out(f"    {name[:24]:24}  {str(stored):10} -> {str(new_date):10}  "
                    f"+{adv}d  {clan[:18]:18}  {bucket}")

        # BaseCommand.execute() returns handle()'s value to call_command, so the
        # Beat task can report a truncated or aborted pass honestly instead of
        # "completed". `aborted` wins: it is the cause, truncation would be an
        # effect (they are mutually exclusive by control flow anyway).
        if aborted:
            return "aborted"
        return "partial" if truncated else None
