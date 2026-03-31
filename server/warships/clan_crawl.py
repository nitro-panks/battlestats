from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

import requests
from django.conf import settings as django_settings

from warships.models import Clan, Player
from warships.player_records import BlockedAccountError, get_or_create_canonical_player


BASE_URL = "https://api.worldofwarships.com/wows/"
APP_ID = os.environ.get("WG_APP_ID")
REQUEST_TIMEOUT = 20
PAGE_SIZE = 100
RATE_LIMIT_DELAY = 0.25
BATCH_SIZE = 100

log = logging.getLogger("crawl")


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


def _api_get(endpoint: str, params: Dict) -> Optional[Dict]:
    time.sleep(RATE_LIMIT_DELAY)
    params["application_id"] = APP_ID
    try:
        resp = requests.get(
            BASE_URL + endpoint,
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


def fetch_clan_list_page(page: int) -> tuple[List[Dict], int]:
    body = _api_get(
        "clans/list/",
        {
            "fields": "clan_id,tag,name,members_count",
            "page_no": page,
            "limit": PAGE_SIZE,
        },
    )
    if body is None:
        return [], 0

    total = body.get("meta", {}).get("total", 0)
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    return body.get("data", []) or [], total_pages


def fetch_member_ids(clan_id: int) -> List[int]:
    body = _api_get(
        "clans/info/",
        {"clan_id": clan_id, "fields": "members_ids"},
    )
    if body is None:
        return []
    clan_data = body.get("data", {}).get(str(clan_id)) or {}
    return clan_data.get("members_ids", []) or []


def fetch_clan_info(clan_id: int) -> Dict:
    body = _api_get(
        "clans/info/",
        {
            "clan_id": clan_id,
            "fields": "members_count,tag,name,clan_id,description,leader_id,leader_name",
        },
    )
    if body is None:
        return {}
    return body.get("data", {}).get(str(clan_id)) or {}


def fetch_players_bulk(player_ids: List[int]) -> Dict:
    if not player_ids:
        return {}
    body = _api_get(
        "account/info/",
        {"account_id": ",".join(str(pid) for pid in player_ids)},
    )
    if body is None:
        return {}
    return body.get("data", {}) or {}


def save_clan(info: Dict) -> Clan:
    clan, _ = Clan.objects.update_or_create(
        clan_id=info["clan_id"],
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


def save_player(player_data: Dict, clan: Clan) -> None:
    from warships.data import compute_player_verdict, refresh_player_explorer_summary, update_achievements_data, update_player_efficiency_data

    if player_data is None:
        return

    pid = player_data.get("account_id")
    if not pid:
        return

    try:
        player, _created = get_or_create_canonical_player(pid)
    except BlockedAccountError:
        log.info("Skipping blocked account %s during clan crawl", pid)
        return
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

    if not player.is_hidden:
        update_player_efficiency_data(player)
        update_achievements_data(player.player_id)

    refresh_player_explorer_summary(player)


def crawl_clan_ids(limit: Optional[int] = None, heartbeat_callback: Optional[Callable[[], None]] = None) -> List[Dict]:
    all_clans: List[Dict] = []
    page = 1
    _touch_crawl_heartbeat(heartbeat_callback)

    first_batch, total_pages = fetch_clan_list_page(page)
    if not first_batch:
        log.error("Failed to fetch first page of clans/list/")
        return []

    all_clans.extend(first_batch)
    log.info("Page 1/%d — %d clans (total pages: %d)",
             total_pages, len(first_batch), total_pages)

    for page in range(2, total_pages + 1):
        _touch_crawl_heartbeat(heartbeat_callback)
        if limit and len(all_clans) >= limit:
            break
        batch, _ = fetch_clan_list_page(page)
        if not batch:
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


def crawl_clan_members(clan_stubs: List[Dict], resume: bool = False, heartbeat_callback: Optional[Callable[[], None]] = None) -> dict[str, int]:
    total = len(clan_stubs)
    clans_processed = 0
    players_saved = 0
    skipped = 0

    for i, stub in enumerate(clan_stubs, 1):
        _touch_crawl_heartbeat(heartbeat_callback)
        clan_id = stub["clan_id"]

        if resume and Clan.objects.filter(clan_id=clan_id, last_fetch__isnull=False).exists():
            skipped += 1
            continue

        info = fetch_clan_info(clan_id)
        if not info:
            log.warning("[%d/%d] Failed to fetch info for clan %d",
                        i, total, clan_id)
            continue

        clan = save_clan(info)
        members_count = info.get("members_count", 0)

        if members_count == 0:
            clans_processed += 1
            continue

        member_ids = fetch_member_ids(clan_id)
        if not member_ids:
            log.warning("[%d/%d] No member IDs for [%s] %s",
                        i, total, clan.tag, clan.name)
            clans_processed += 1
            continue

        for batch_start in range(0, len(member_ids), BATCH_SIZE):
            batch_ids = member_ids[batch_start: batch_start + BATCH_SIZE]
            player_map = fetch_players_bulk(batch_ids)

            for _pid_str, pdata in player_map.items():
                save_player(pdata, clan)
                players_saved += 1

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

    log.info("Done. Clans processed: %d, skipped: %d, players saved: %d",
             clans_processed, skipped, players_saved)
    return {
        "clans_processed": clans_processed,
        "players_saved": players_saved,
        "skipped": skipped,
    }


def run_clan_crawl(
    resume: bool = False,
    dry_run: bool = False,
    limit: Optional[int] = None,
    heartbeat_callback: Optional[Callable[[], None]] = None,
) -> dict[str, int | bool]:
    from warships.tasks import queue_efficiency_rank_snapshot_refresh

    if not APP_ID:
        raise RuntimeError("WG_APP_ID environment variable is not set")

    log.info("Starting crawl (resume=%s, dry_run=%s, limit=%s)",
             resume, dry_run, limit)

    clan_stubs = crawl_clan_ids(
        limit=limit,
        heartbeat_callback=heartbeat_callback,
    )
    if not clan_stubs:
        raise RuntimeError("Failed to fetch clan list")

    if dry_run:
        log.info("Dry run complete — %d clans found", len(clan_stubs))
        return {
            "resume": resume,
            "dry_run": True,
            "limit": limit,
            "clans_found": len(clan_stubs),
        }

    summary = crawl_clan_members(
        clan_stubs,
        resume=resume,
        heartbeat_callback=heartbeat_callback,
    )
    if summary.get("players_saved", 0) > 0:
        queue_efficiency_rank_snapshot_refresh()
    summary.update({
        "resume": resume,
        "dry_run": False,
        "limit": limit,
        "clans_found": len(clan_stubs),
    })
    return summary
