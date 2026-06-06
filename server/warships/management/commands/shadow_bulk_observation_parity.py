"""Phase-2 READ-ONLY parity shadow for the bulk observation floor (R1).

Fetches each sampled player BOTH ways — the legacy single-player path
(`_fetch_player_personal_data` + `_fetch_ship_stats_for_player`) and the bulk
path (`_bulk_fetch_account_info` + `_bulk_fetch_ship_stats`) — and compares the
would-be observation payloads WITHOUT writing any `BattleObservation`. This is
the phase-2 gate from
`agents/runbooks/runbook-bulk-battle-observation-capture-2026-06-06.md`: it
validates the fetch-shape parity (D1) that the unit-test mocks cannot — that the
bulk account/ships slices byte-match the single fetches, including the Phase 7
`main_battery`/`torpedoes` columns that flow into `ships_stats_json`.

Zero DB writes — safe to run against prod repeatedly.

Usage:
    python manage.py shadow_bulk_observation_parity --realm na --limit 50
    python manage.py shadow_bulk_observation_parity --realm eu --player-ids 123,456 --verbose
    python manage.py shadow_bulk_observation_parity --realm na --json
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from warships.models import DEFAULT_REALM, Player, VALID_REALMS

log = logging.getLogger("battle_observation_floor")

DEFAULT_LIMIT = 50
DEFAULT_DAYS = 7


def _observation_fingerprint(acct, ships):
    """The would-be persisted observation for (acct, ships), or None if the
    path would write nothing (hidden/empty account).

    Mirrors `record_observation_from_payloads`: coerce → serialize, applying
    the same `dict → []` ships normalization the legacy path uses. ships are
    sorted by ship_id so list ordering between the two WG endpoints can't
    produce a spurious mismatch. Returns a plain dict for cheap deep-equality.
    """
    from warships.incremental_battles import (
        _serialize_ships_payload,
        coerce_observation_payload,
    )

    if isinstance(ships, dict):
        ships = []
    snapshot = coerce_observation_payload(acct or {}, ships or [])
    if snapshot is None:
        return None
    last_bt = snapshot.last_battle_time
    return {
        "pvp_battles": snapshot.pvp_battles,
        "pvp_wins": snapshot.pvp_wins,
        "pvp_losses": snapshot.pvp_losses,
        "pvp_frags": snapshot.pvp_frags,
        "pvp_survived_battles": snapshot.pvp_survived_battles,
        "last_battle_time": last_bt.isoformat() if last_bt else None,
        "ships": sorted(
            _serialize_ships_payload(snapshot), key=lambda s: s["ship_id"]),
    }


def compare_player(acct_single, ships_single, acct_bulk, ships_bulk):
    """Pure parity comparison for one player. Returns (verdict, detail).

    Verdicts:
      * match              — both paths would write the identical observation,
                             or both would skip (hidden/absent).
      * mismatch           — both would write but the payloads differ (the
                             dangerous case: bulk would persist wrong data).
      * bulk_skips_capturable — legacy would write, bulk would skip this tick
                             (the bulk ships slice is absent/SKIP). A coverage
                             gap, not a data-corruption risk.
      * legacy_skips_only  — bulk would write but legacy would skip (rare;
                             usually a hidden/empty divergence worth a look).
    """
    single_fp = _observation_fingerprint(acct_single, ships_single)

    bulk_skips = ships_bulk is None or ships_bulk == "SKIP"
    if bulk_skips:
        if single_fp is None:
            return "match", {"note": "both skip"}
        return "bulk_skips_capturable", {
            "single_battles": single_fp.get("pvp_battles")}

    bulk_fp = _observation_fingerprint(acct_bulk, ships_bulk)
    if single_fp is None:
        if bulk_fp is None:
            return "match", {"note": "both skip"}
        return "legacy_skips_only", {"bulk_battles": bulk_fp.get("pvp_battles")}
    if single_fp == bulk_fp:
        return "match", {"ships": len(single_fp.get("ships", []))}
    if bulk_fp is None:
        return "legacy_skips_only", {"note": "bulk coerced empty"}

    # Both write but differ — surface the specific divergence.
    diffs = {}
    for key in ("pvp_battles", "pvp_wins", "pvp_losses", "pvp_frags",
                "pvp_survived_battles", "last_battle_time"):
        if single_fp.get(key) != bulk_fp.get(key):
            diffs[key] = {"single": single_fp.get(key), "bulk": bulk_fp.get(key)}
    if single_fp.get("ships") != bulk_fp.get("ships"):
        diffs["ships"] = {
            "single_count": len(single_fp.get("ships", [])),
            "bulk_count": len(bulk_fp.get("ships", [])),
        }
    return "mismatch", {"diffs": diffs}


class Command(BaseCommand):
    help = (
        "READ-ONLY phase-2 parity shadow for the bulk observation floor: "
        "fetch sampled players both ways and compare the would-be observation "
        "payloads without writing anything."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--realm", default=DEFAULT_REALM, choices=sorted(VALID_REALMS),
            help=f"Realm to sample. Default: {DEFAULT_REALM}.",
        )
        parser.add_argument(
            "--limit", type=int, default=DEFAULT_LIMIT,
            help=f"Number of players to sample. Default: {DEFAULT_LIMIT}.",
        )
        parser.add_argument(
            "--days", type=int, default=DEFAULT_DAYS,
            help=f"Activity window for sampling. Default: {DEFAULT_DAYS}.",
        )
        parser.add_argument(
            "--player-ids", default="", dest="player_ids",
            help="Comma-separated player_ids to check instead of sampling.",
        )
        parser.add_argument(
            "--json", action="store_true", dest="as_json",
            help="Emit a machine-readable JSON summary instead of text.",
        )
        parser.add_argument(
            "--verbose", action="store_true",
            help="Print per-player results for every non-match.",
        )

    def _sample_ids(self, realm, days, limit):
        cutoff = (timezone.now() - timedelta(days=days)).date()
        qs = (
            Player.objects.filter(
                realm=realm, is_hidden=False, last_battle_date__gte=cutoff,
            )
            .order_by("-last_battle_date", "name")
            .values_list("player_id", flat=True)
        )
        if limit and limit > 0:
            qs = qs[:limit]
        return list(qs)

    def handle(self, *args, **options):
        from warships.api.players import (
            _bulk_fetch_account_info,
            _fetch_player_personal_data,
        )
        from warships.api.ships import (
            _bulk_fetch_ship_stats,
            _fetch_ship_stats_for_player,
        )

        realm = options["realm"]
        limit = options["limit"]
        days = options["days"]
        as_json = options["as_json"]
        verbose = options["verbose"]

        if options["player_ids"].strip():
            try:
                player_ids = [
                    int(x) for x in options["player_ids"].split(",") if x.strip()
                ]
            except ValueError:
                raise CommandError("--player-ids must be comma-separated integers")
        else:
            player_ids = self._sample_ids(realm, days, limit)

        if not player_ids:
            self.stdout.write(self.style.WARNING("no players to compare"))
            return

        # Bulk-fetch the whole sample in chunks of 100 (as the real path does),
        # then single-fetch each player and compare.
        bulk_acct: dict = {}
        bulk_ships: dict = {}
        for start in range(0, len(player_ids), 100):
            chunk = player_ids[start:start + 100]
            acct_map, acct_err = _bulk_fetch_account_info(chunk, realm)
            ship_map, ship_err = _bulk_fetch_ship_stats(chunk, realm)
            if acct_err or ship_err:
                log.warning(
                    "shadow: bulk fetch error on chunk (acct=%s ship=%s) — "
                    "those players will read as bulk-absent", acct_err, ship_err)
            bulk_acct.update(acct_map or {})
            bulk_ships.update(ship_map or {})

        verdicts: dict = {}
        details: list = []
        for pid in player_ids:
            try:
                acct_single = _fetch_player_personal_data(pid, realm=realm)
                ships_single = _fetch_ship_stats_for_player(pid, realm=realm)
            except Exception as exc:  # noqa: BLE001
                log.warning("shadow: single fetch failed for %s: %s", pid, exc)
                verdicts["single_fetch_error"] = verdicts.get(
                    "single_fetch_error", 0) + 1
                continue

            verdict, detail = compare_player(
                acct_single, ships_single,
                bulk_acct.get(str(pid)), bulk_ships.get(str(pid)),
            )
            verdicts[verdict] = verdicts.get(verdict, 0) + 1
            if verdict != "match":
                details.append({"player_id": pid, "verdict": verdict, **detail})

        total = len(player_ids)
        matched = verdicts.get("match", 0)
        mismatched = verdicts.get("mismatch", 0)

        if as_json:
            self.stdout.write(json.dumps({
                "realm": realm,
                "total": total,
                "verdicts": verdicts,
                "details": details,
            }, indent=2, default=str))
            return

        self.stdout.write(
            f"realm={realm} sampled={total}: match={matched} "
            f"mismatch={mismatched} "
            f"bulk_skips_capturable={verdicts.get('bulk_skips_capturable', 0)} "
            f"legacy_skips_only={verdicts.get('legacy_skips_only', 0)} "
            f"single_fetch_error={verdicts.get('single_fetch_error', 0)}"
        )
        if verbose and details:
            for d in details:
                self.stdout.write(f"  {d}")

        if mismatched:
            self.stdout.write(self.style.ERROR(
                f"PARITY MISMATCH on {mismatched} player(s) — do NOT enable "
                f"the bulk floor on this realm. Re-run with --verbose to inspect."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                "no payload mismatches — fetch-shape parity holds for this sample"
            ))
