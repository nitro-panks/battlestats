from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

import requests
from django.conf import settings as django_settings
from django.core.cache import cache

from warships.api.client import DEFAULT_REALM, get_base_url
from warships.models import Clan, Player
from warships.player_records import BlockedAccountError, get_or_create_canonical_player


APP_ID = os.environ.get("WG_APP_ID")
REQUEST_TIMEOUT = 20
PAGE_SIZE = 100
BATCH_SIZE = 100

log = logging.getLogger("crawl")


def _env_float(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return default

    return value if value >= 0 else default


def _crawl_request_delay(core_only: bool = False) -> float:
    if core_only:
        return _env_float(
            "CLAN_CRAWL_CORE_ONLY_RATE_LIMIT_DELAY",
            _env_float("CLAN_CRAWL_RATE_LIMIT_DELAY", 0.25),
        )

    return _env_float("CLAN_CRAWL_RATE_LIMIT_DELAY", 0.25)


def _touch_crawl_heartbeat(heartbeat_callback: Optional[Callable[[], None]]) -> None:
    if heartbeat_callback is not None:
        heartbeat_callback()


def _now():
    if getattr(django_settings, "USE_TZ", False):
        return datetime.now(timezone.utc)
    return datetime.now()


def _from_ts(ts):
    if getattr(django_settings, "USE_TZ", False):
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    return datetime.fromtimestamp(ts)


# --- Crawl yield-by-source instrumentation -------------------------------
# Measures the crawl's *marginal* value per pass: net-new active players and
# dormant->active re-detections (yield the observation floor structurally
# cannot produce, since it is gated on already-active players) vs. players the
# floor already covers (overlap). Lets us decide whether the daily full
# re-walk is still earning its cost. See the bulk-battle-observation runbook
# Benchmarks section. Best-effort throughout: instrumentation never breaks the
# crawl.

CRAWL_YIELD_TTL = 60 * 60 * 24 * 21  # mirror CLAN_CRAWL_PASS_MARKER_TTL (21d)
CRAWL_YIELD_BENCHMARK_DIR = os.getenv(
    "CRAWL_YIELD_BENCHMARK_DIR",
    "/opt/battlestats-server/shared/benchmarks/crawl-yield",
)
CRAWL_YIELD_BUCKETS = (
    "discovered_active",   # net-new account, currently active  -> floor-impossible yield
    "discovered_dormant",  # net-new account, dormant           -> universe completion only
    "reactivated",         # known, dormant->active this write  -> floor-impossible yield
    "refreshed_active",    # known, already active              -> overlap (floor covers it)
    "still_dormant",       # known, stayed dormant              -> no active value this pass
)


class CrawlUpstreamFailure(RuntimeError):
    """Raised when a run of consecutive per-clan fetch failures says the upstream
    is down rather than that a few clans are dead, so the pass must abort instead
    of walking the rest of the list and returning as if it had completed.

    Carries the partial `summary` so the caller can log what the pass did manage,
    and `consecutive_failures` so the abort reason is legible in the logs.
    """

    def __init__(self, summary: dict, consecutive_failures: int = 0):
        self.summary = summary
        self.consecutive_failures = consecutive_failures
        super().__init__(
            f"aborting crawl pass after {consecutive_failures} consecutive "
            f"clan fetch failures (processed {summary.get('clans_processed')}, "
            f"failed {summary.get('clans_failed')})")


def _max_consecutive_clan_failures() -> int:
    """Consecutive failed `clans/info/` fetches that abort the pass; 0 disables.

    A healthy pass fails essentially nothing (0 in 9,625 NA clans, 1 in a full EU
    pass observed 2026-08-11), so 25 sits far above the noise floor. Aborting
    early is cheap: the pass marker survives, so the next dispatch resumes and
    skips every clan already walked.
    """
    try:
        return int(os.getenv("CLAN_CRAWL_MAX_CONSECUTIVE_FAILURES", "25"))
    except ValueError:
        return 25


def _crawl_yield_enabled() -> bool:
    return os.getenv("CRAWL_YIELD_INSTRUMENT_ENABLED", "1") == "1"


def _crawl_active_cutoff():
    """Active-window cutoff date, mirroring the observation floor's
    BATTLE_OBSERVATION_FLOOR_DAYS so "active" means the identical thing in both
    instruments. A player is active iff last_battle_date >= cutoff."""
    days = int(os.getenv("BATTLE_OBSERVATION_FLOOR_DAYS", "7"))
    return _now().date() - timedelta(days=days)


def _classify_player_yield(created: bool, old_lbd, new_lbd, cutoff) -> str:
    """Bucket a crawled player by what *this* crawl write surfaced. Credit is
    given to the crawl only when its own write crossed the active threshold: if
    the floor (or an on-view refresh) already moved the player active between
    passes, old_lbd is already active and this counts as overlap, not yield."""
    new_active = bool(new_lbd and new_lbd >= cutoff)
    was_active = bool(old_lbd and old_lbd >= cutoff)
    if created:
        return "discovered_active" if new_active else "discovered_dormant"
    if new_active and not was_active:
        return "reactivated"
    if new_active:
        return "refreshed_active"
    return "still_dormant"


def _crawl_yield_pass_id(fresh_after: Optional[datetime]) -> str:
    return fresh_after.isoformat() if fresh_after is not None else "adhoc"


def _crawl_yield_key(realm: str, pass_id: str) -> str:
    return f"crawl:yield:{realm}:{pass_id}"


def _flush_crawl_yield(realm: str, pass_id: str, pending: Dict[str, int]) -> None:
    """Additively merge a batch of bucket counts into the pass's Redis
    aggregate so counts survive task redelivery across a multi-day pass. The
    crawls queue is -c 1 with one realm crawling at a time, so this get-modify-
    set has a single writer and is race-free."""
    if not pending:
        return
    try:
        key = _crawl_yield_key(realm, pass_id)
        agg = cache.get(key) or {}
        for bucket, count in pending.items():
            agg[bucket] = agg.get(bucket, 0) + count
        cache.set(key, agg, timeout=CRAWL_YIELD_TTL)
    except Exception:
        log.warning("crawl-yield flush failed (realm=%s)", realm, exc_info=True)


def emit_crawl_yield_snapshot(realm: str, fresh_after: Optional[datetime]) -> Optional[dict]:
    """At pass completion, flush the pass's accumulated yield counts to a
    durable per-pass JSON snapshot (sibling of the observation-floor
    benchmarks) plus a structured log line, then clear the Redis aggregate.
    Returns the snapshot dict, or None if disabled / no real pass."""
    if not _crawl_yield_enabled() or fresh_after is None:
        return None
    pass_id = _crawl_yield_pass_id(fresh_after)
    key = _crawl_yield_key(realm, pass_id)
    try:
        agg = cache.get(key) or {}
    except Exception:
        log.warning("crawl-yield read failed (realm=%s)", realm, exc_info=True)
        return None
    counts = {bucket: int(agg.get(bucket, 0)) for bucket in CRAWL_YIELD_BUCKETS}
    classified = sum(counts.values())
    yield_total = counts["discovered_active"] + counts["reactivated"]
    overlap = counts["refreshed_active"]
    snapshot = {
        "captured_at": _now().isoformat(),
        "realm": realm,
        "pass_started_at": pass_id,
        "active_window_days": int(os.getenv("BATTLE_OBSERVATION_FLOOR_DAYS", "7")),
        "players_classified": classified,
        "buckets": counts,
        "yield_total": yield_total,
        "overlap_total": overlap,
        "yield_frac": round(yield_total / classified, 4) if classified else 0.0,
        "overlap_frac": round(overlap / classified, 4) if classified else 0.0,
    }
    log.info("crawl-yield realm=%s pass=%s %s", realm, pass_id, snapshot)
    try:
        os.makedirs(CRAWL_YIELD_BENCHMARK_DIR, exist_ok=True)
        fname = f"{_now().strftime('%Y-%m-%d_%H%MZ')}_{realm}.json"
        with open(os.path.join(CRAWL_YIELD_BENCHMARK_DIR, fname), "w") as handle:
            json.dump(snapshot, handle, indent=2)
    except Exception:
        log.warning("crawl-yield snapshot write failed (dir=%s)",
                    CRAWL_YIELD_BENCHMARK_DIR, exc_info=True)
    try:
        cache.delete(key)
    except Exception:
        pass
    return snapshot


def _api_get(endpoint: str, params: Dict, realm: str = DEFAULT_REALM, request_delay: float = 0.25) -> Optional[Dict]:
    if request_delay > 0:
        time.sleep(request_delay)
    params["application_id"] = APP_ID
    base_url = get_base_url(realm)
    try:
        resp = requests.get(
            base_url + endpoint,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
    except requests.RequestException as exc:
        log.error("Request failed for %s: %s", endpoint, exc)
        return None
    except ValueError as exc:
        log.error("Bad JSON from %s: %s", endpoint, exc)
        return None

    if body.get("status") != "ok":
        log.error("API error for %s: %s", endpoint, body.get("error"))
        return None

    return body


def fetch_clan_list_page(page: int, realm: str = DEFAULT_REALM, request_delay: float = 0.25) -> tuple[Optional[List[Dict]], int]:
    """One `clans/list/` page. The batch is None when the fetch failed, which is
    distinct from an ok response carrying no rows."""
    body = _api_get(
        "clans/list/",
        {
            "fields": "clan_id,tag,name,members_count",
            "page_no": page,
            "limit": PAGE_SIZE,
        },
        realm=realm,
        request_delay=request_delay,
    )
    if body is None:
        return None, 0

    total = body.get("meta", {}).get("total", 0)
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    return body.get("data", []) or [], total_pages


def fetch_member_ids(clan_id: int, realm: str = DEFAULT_REALM, request_delay: float = 0.25) -> List[int]:
    body = _api_get(
        "clans/info/",
        {"clan_id": clan_id, "fields": "members_ids"},
        realm=realm,
        request_delay=request_delay,
    )
    if body is None:
        return []
    clan_data = body.get("data", {}).get(str(clan_id)) or {}
    return clan_data.get("members_ids", []) or []


def fetch_clan_info(clan_id: int, realm: str = DEFAULT_REALM, request_delay: float = 0.25) -> Dict:
    body = _api_get(
        "clans/info/",
        {
            "clan_id": clan_id,
            "fields": "members_count,tag,name,clan_id,description,leader_id,leader_name",
        },
        realm=realm,
        request_delay=request_delay,
    )
    if body is None:
        return {}
    return body.get("data", {}).get(str(clan_id)) or {}


def fetch_players_bulk(player_ids: List[int], realm: str = DEFAULT_REALM, request_delay: float = 0.25) -> Dict:
    if not player_ids:
        return {}
    body = _api_get(
        "account/info/",
        {"account_id": ",".join(str(pid) for pid in player_ids)},
        realm=realm,
        request_delay=request_delay,
    )
    if body is None:
        return {}
    return body.get("data", {}) or {}


def save_clan(info: Dict, realm: str = DEFAULT_REALM) -> Clan:
    clan, _ = Clan.objects.update_or_create(
        clan_id=info["clan_id"],
        realm=realm,
        defaults={
            "name": info.get("name", ""),
            "tag": info.get("tag", ""),
            "members_count": info.get("members_count", 0),
            "description": info.get("description", ""),
            "leader_id": info.get("leader_id"),
            "leader_name": info.get("leader_name", ""),
            "last_fetch": _now(),
        },
    )
    return clan


def save_player(player_data: Dict, clan: Clan, realm: str = DEFAULT_REALM, core_only: bool = False, cutoff=None) -> Optional[str]:
    from warships.data import compute_player_verdict, refresh_player_explorer_summary, update_achievements_data, update_player_efficiency_data

    if player_data is None:
        return None

    pid = player_data.get("account_id")
    if not pid:
        return None

    try:
        player, created = get_or_create_canonical_player(pid, realm=realm)
    except BlockedAccountError:
        log.info("Skipping blocked account %s during clan crawl", pid)
        return None
    # Capture the prior activity date *before* the WG write overwrites it — this
    # is what lets the yield classifier tell a dormant->active re-detection
    # (floor-impossible yield) from a refresh of an already-active player.
    old_lbd = player.last_battle_date
    player.name = player_data.get("nickname", player.name or "")
    player.clan = clan

    player.creation_date = (
        _from_ts(player_data["created_at"])
        if player_data.get("created_at")
        else player.creation_date
    )
    player.last_battle_date = (
        _from_ts(player_data["last_battle_time"]).date()
        if player_data.get("last_battle_time")
        else player.last_battle_date
    )

    if player.last_battle_date:
        player.days_since_last_battle = (
            _now().date() - player.last_battle_date).days

    if player_data.get("hidden_profile"):
        player.is_hidden = True
        player.efficiency_json = None
        player.efficiency_updated_at = None
        player.verdict = None
    else:
        player.is_hidden = False
        stats = player_data.get("statistics") or {}
        pvp = stats.get("pvp") or {}
        player.total_battles = stats.get("battles", 0)
        player.pvp_battles = pvp.get("battles", 0)
        player.pvp_wins = pvp.get("wins", 0)
        player.pvp_losses = pvp.get("losses", 0)
        player.pvp_frags = pvp.get("frags", 0)
        player.pvp_survived_battles = pvp.get("survived_battles", 0)
        if player.pvp_battles > 0:
            player.pvp_ratio = round(
                player.pvp_wins / player.pvp_battles * 100, 2)
        player.pvp_survival_rate = (
            round(player.pvp_survived_battles / player.pvp_battles * 100, 2)
            if player.pvp_battles
            else None
        )
        from warships.data import _calculate_actual_kdr
        player.pvp_deaths, player.actual_kdr = _calculate_actual_kdr(
            player.pvp_battles,
            player.pvp_frags,
            player.pvp_survived_battles,
        )
        player.verdict = compute_player_verdict(
            pvp_battles=player.pvp_battles,
            pvp_ratio=player.pvp_ratio,
            pvp_survival_rate=player.pvp_survival_rate,
        )

    player.last_fetch = _now()
    player.save()

    if not player.is_hidden and not core_only:
        update_player_efficiency_data(player, realm=realm)
        update_achievements_data(player.player_id, realm=realm)

    if not core_only:
        refresh_player_explorer_summary(player)

    if cutoff is None:
        return None
    return _classify_player_yield(
        created, old_lbd, player.last_battle_date, cutoff)


CLAN_LIST_PAGE_ATTEMPTS = 4
CLAN_LIST_RETRY_BACKOFF_S = 5.0


def _fetch_clan_list_page_with_retry(page: int, realm: str, request_delay: float,
                                     heartbeat_callback: Optional[Callable[[], None]] = None,
                                     ) -> tuple[List[Dict], int]:
    """Fetch a `clans/list/` page, retrying a failed fetch with backoff.

    A page that still fails raises CrawlUpstreamFailure. Before this a failed page
    read as the end of the list: on 2026-09-29 one WG 504 on NA page 79 cut the
    walk to 7,800 of ~36,100 clans, and the pass then closed as complete, wrote a
    yield snapshot 36% low, and cleared its resume marker.
    """
    for attempt in range(1, CLAN_LIST_PAGE_ATTEMPTS + 1):
        batch, total_pages = fetch_clan_list_page(
            page, realm=realm, request_delay=request_delay)
        if batch is not None:
            return batch, total_pages
        if attempt < CLAN_LIST_PAGE_ATTEMPTS:
            log.warning("clans/list/ page %d failed (realm=%s, attempt %d/%d); retrying",
                        page, realm, attempt, CLAN_LIST_PAGE_ATTEMPTS)
            _touch_crawl_heartbeat(heartbeat_callback)
            time.sleep(CLAN_LIST_RETRY_BACKOFF_S * 2 ** (attempt - 1))
    log.error("clans/list/ page %d failed %d times (realm=%s); aborting the pass",
              page, CLAN_LIST_PAGE_ATTEMPTS, realm)
    raise CrawlUpstreamFailure(_crawl_summary(0, 0, 0, 0, {}),
                               consecutive_failures=CLAN_LIST_PAGE_ATTEMPTS)


def crawl_clan_ids(limit: Optional[int] = None, heartbeat_callback: Optional[Callable[[], None]] = None, realm: str = DEFAULT_REALM, request_delay: float = 0.25) -> List[Dict]:
    all_clans: List[Dict] = []
    page = 1
    _touch_crawl_heartbeat(heartbeat_callback)

    first_batch, total_pages = _fetch_clan_list_page_with_retry(
        page, realm, request_delay, heartbeat_callback)
    if not first_batch:
        log.error("First page of clans/list/ came back empty")
        return []

    all_clans.extend(first_batch)
    log.info("Page 1/%d — %d clans (total pages: %d)",
             total_pages, len(first_batch), total_pages)

    for page in range(2, total_pages + 1):
        _touch_crawl_heartbeat(heartbeat_callback)
        if limit and len(all_clans) >= limit:
            break
        batch, _ = _fetch_clan_list_page_with_retry(
            page, realm, request_delay, heartbeat_callback)
        if not batch:
            # An ok response with no rows: the list shrank under the walk (clans
            # disband mid-pass), so this really is the end.
            log.warning("Empty page %d, stopping pagination", page)
            break
        all_clans.extend(batch)
        if page % 50 == 0:
            log.info("Page %d/%d — %d clans so far",
                     page, total_pages, len(all_clans))

    if limit:
        all_clans = all_clans[:limit]

    log.info("Collected %d clan IDs", len(all_clans))
    return all_clans


def _crawl_summary(clans_processed: int, clans_failed: int, players_saved: int,
                   skipped: int, yield_counts: Dict[str, int]) -> dict:
    """One shape for both the completed return and the aborted exception payload,
    so a partial pass reports the same keys as a full one."""
    return {
        "clans_processed": clans_processed,
        "clans_failed": clans_failed,
        "players_saved": players_saved,
        "skipped": skipped,
        "yield": yield_counts,
    }


def crawl_clan_members(clan_stubs: List[Dict], resume: bool = False, heartbeat_callback: Optional[Callable[[], None]] = None, realm: str = DEFAULT_REALM, core_only: bool = False, request_delay: float = 0.25, fresh_after: Optional[datetime] = None) -> dict:
    from warships.data import refresh_clan_cached_aggregates, reconcile_clan_departures

    total = len(clan_stubs)
    clans_processed = 0
    players_saved = 0
    skipped = 0

    # Upstream-failure guard. A dead clan fails its info fetch and is skipped,
    # which is right for one clan and wrong for the whole list: on 2026-08-10 WG's
    # NA clans/info went to 504 and then stopped resolving, and the pass walked
    # 4,324 of 35,898 clans, failed the other 31,573, and still returned normally
    # — so the caller emitted a yield snapshot describing 12% coverage as a full
    # pass and cleared the resume marker. `clans_failed` makes that visible in the
    # summary; a run of `max_consecutive_failures` aborts the pass instead.
    clans_failed = 0
    consecutive_failures = 0
    max_consecutive_failures = _max_consecutive_clan_failures()

    # Yield-by-source instrumentation. `cutoff` (computed once) classifies each
    # saved player; `yield_counts` is this execution's running total returned in
    # the summary; `yield_pending` buffers counts flushed to the pass's Redis
    # aggregate so they survive task redelivery across a multi-day pass.
    yield_enabled = _crawl_yield_enabled()
    cutoff = _crawl_active_cutoff() if yield_enabled else None
    pass_id = _crawl_yield_pass_id(fresh_after)
    yield_counts = {bucket: 0 for bucket in CRAWL_YIELD_BUCKETS}
    yield_pending: Dict[str, int] = {}

    for i, stub in enumerate(clan_stubs, 1):
        _touch_crawl_heartbeat(heartbeat_callback)
        clan_id = stub["clan_id"]

        # Resume skip. With no `fresh_after`, "resume" means "skip any clan ever
        # fetched" (the original manual `--resume` semantics). With a
        # `fresh_after` cutoff (run-scoped resume from the scheduled task), only
        # skip clans already fetched *during the current pass* — clans whose
        # last_fetch predates this pass are re-crawled so periodic refresh is
        # preserved across passes. See runbook-na-crawl-restart-loop-starves-refresh.
        if resume:
            already = Clan.objects.filter(
                clan_id=clan_id, realm=realm, last_fetch__isnull=False)
            if fresh_after is not None:
                already = already.filter(last_fetch__gte=fresh_after)
            if already.exists():
                skipped += 1
                continue

        info = fetch_clan_info(clan_id, realm=realm,
                               request_delay=request_delay)
        if not info:
            clans_failed += 1
            consecutive_failures += 1
            log.warning("[%d/%d] Failed to fetch info for clan %d (%d in a row)",
                        i, total, clan_id, consecutive_failures)
            if (max_consecutive_failures
                    and consecutive_failures >= max_consecutive_failures):
                # Flush first: the aggregate is what the resumed pass continues
                # accumulating into, so the counts earned before the outage must
                # not die with this execution.
                if yield_enabled:
                    _flush_crawl_yield(realm, pass_id, yield_pending)
                log.error(
                    "Aborting crawl pass (realm=%s) at clan %d/%d after %d "
                    "consecutive failed info fetches — treating this as an "
                    "upstream outage, not %d dead clans. The pass marker is "
                    "kept so the next dispatch resumes here.",
                    realm, i, total, consecutive_failures, consecutive_failures)
                raise CrawlUpstreamFailure(
                    _crawl_summary(clans_processed, clans_failed,
                                   players_saved, skipped, yield_counts),
                    consecutive_failures=consecutive_failures)
            continue
        consecutive_failures = 0

        clan = save_clan(info, realm=realm)
        members_count = info.get("members_count", 0)

        if members_count == 0:
            clans_processed += 1
            continue

        member_ids = fetch_member_ids(
            clan_id, realm=realm, request_delay=request_delay)
        if not member_ids:
            log.warning("[%d/%d] No member IDs for [%s] %s",
                        i, total, clan.tag, clan.name)
            clans_processed += 1
            continue

        for batch_start in range(0, len(member_ids), BATCH_SIZE):
            batch_ids = member_ids[batch_start: batch_start + BATCH_SIZE]
            player_map = fetch_players_bulk(
                batch_ids, realm=realm, request_delay=request_delay)

            for _pid_str, pdata in player_map.items():
                bucket = save_player(
                    pdata, clan, realm=realm, core_only=core_only, cutoff=cutoff)
                players_saved += 1
                if bucket:
                    yield_counts[bucket] += 1
                    yield_pending[bucket] = yield_pending.get(bucket, 0) + 1

        reconcile_clan_departures(clan, member_ids, realm=realm)
        refresh_clan_cached_aggregates(str(clan.clan_id), realm=realm)

        clans_processed += 1
        if clans_processed % 25 == 0:
            log.info(
                "[%d/%d] Processed %d clans, %d players saved, %d skipped",
                i,
                total,
                clans_processed,
                players_saved,
                skipped,
            )
            if yield_enabled:
                _flush_crawl_yield(realm, pass_id, yield_pending)
                yield_pending = {}

    if yield_enabled:
        _flush_crawl_yield(realm, pass_id, yield_pending)
        yield_pending = {}

    log.info("Done. Clans processed: %d, failed: %d, skipped: %d, players saved: %d",
             clans_processed, clans_failed, skipped, players_saved)
    return _crawl_summary(clans_processed, clans_failed, players_saved,
                          skipped, yield_counts)


def run_clan_crawl(
    resume: bool = False,
    dry_run: bool = False,
    limit: Optional[int] = None,
    heartbeat_callback: Optional[Callable[[], None]] = None,
    realm: str = DEFAULT_REALM,
    core_only: bool = False,
    fresh_after: Optional[datetime] = None,
) -> dict[str, int | bool]:
    from warships.tasks import queue_efficiency_rank_snapshot_refresh

    request_delay = _crawl_request_delay(core_only=core_only)

    if not APP_ID:
        raise RuntimeError("WG_APP_ID environment variable is not set")

    log.info("Starting crawl (realm=%s, resume=%s, fresh_after=%s, dry_run=%s, limit=%s, core_only=%s, request_delay=%.3fs)",
             realm, resume, fresh_after, dry_run, limit, core_only, request_delay)

    clan_stubs = crawl_clan_ids(
        limit=limit,
        heartbeat_callback=heartbeat_callback,
        realm=realm,
        request_delay=request_delay,
    )
    if not clan_stubs:
        raise RuntimeError("Failed to fetch clan list")

    if dry_run:
        log.info("Dry run complete — %d clans found", len(clan_stubs))
        return {
            "realm": realm,
            "resume": resume,
            "dry_run": True,
            "limit": limit,
            "clans_found": len(clan_stubs),
        }

    summary = crawl_clan_members(
        clan_stubs,
        resume=resume,
        heartbeat_callback=heartbeat_callback,
        realm=realm,
        core_only=core_only,
        request_delay=request_delay,
        fresh_after=fresh_after,
    )
    if summary.get("players_saved", 0) > 0 and not core_only:
        queue_efficiency_rank_snapshot_refresh(realm=realm)
    summary.update({
        "realm": realm,
        "resume": resume,
        "dry_run": False,
        "limit": limit,
        "clans_found": len(clan_stubs),
    })
    return summary
