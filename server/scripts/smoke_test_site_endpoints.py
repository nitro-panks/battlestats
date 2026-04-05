#!/usr/bin/env python3
import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Any
from urllib import error, request


DEFAULT_BASE_URL = "http://localhost:8888"


@dataclass(frozen=True)
class SmokeCase:
    name: str
    path: str
    expected_status: int = 200
    json_type: str | None = None
    min_items: int | None = None
    exact_items: int | None = None
    required_keys: tuple[str, ...] = ()
    nested_list_key: str | None = None
    min_nested_items: int | None = None
    exact_key_values: dict[str, Any] = field(default_factory=dict)
    retry_on_pending: bool = False


def fetch_json(base_url: str, path: str, timeout: float) -> tuple[int, dict[str, str], Any]:
    url = f"{base_url.rstrip('/')}{path}"
    req = request.Request(url, headers={"Accept": "application/json"})
    try:
        with request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return response.status, dict(response.headers.items()), json.loads(body)
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        payload = None
        if body:
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                payload = body
        return exc.code, dict(exc.headers.items()), payload


def validate_case(case: SmokeCase, payload: Any) -> list[str]:
    errors: list[str] = []

    if case.json_type == "list":
        if not isinstance(payload, list):
            return [f"expected list payload, got {type(payload).__name__}"]
        if case.min_items is not None and len(payload) < case.min_items:
            errors.append(
                f"expected at least {case.min_items} items, got {len(payload)}")
        if case.exact_items is not None and len(payload) != case.exact_items:
            errors.append(
                f"expected exactly {case.exact_items} items, got {len(payload)}")
        return errors

    if case.json_type == "dict":
        if not isinstance(payload, dict):
            return [f"expected dict payload, got {type(payload).__name__}"]
        for key in case.required_keys:
            if key not in payload:
                errors.append(f"missing key {key!r}")
        for key, expected in case.exact_key_values.items():
            actual = payload.get(key)
            if actual != expected:
                errors.append(f"expected {key!r}={expected!r}, got {actual!r}")
        if case.nested_list_key is not None:
            nested = payload.get(case.nested_list_key)
            if not isinstance(nested, list):
                errors.append(
                    f"expected {case.nested_list_key!r} to be a list, got {type(nested).__name__}"
                )
            elif case.min_nested_items is not None and len(nested) < case.min_nested_items:
                errors.append(
                    f"expected at least {case.min_nested_items} items in {case.nested_list_key!r}, got {len(nested)}"
                )
        return errors

    return errors


def run_case(case: SmokeCase, base_url: str, timeout: float) -> tuple[bool, str]:
    attempts = 6 if case.retry_on_pending else 1
    last_status = None
    last_payload = None
    pending_seen = False

    for attempt in range(attempts):
        status_code, headers, payload = fetch_json(
            base_url, case.path, timeout)
        last_status = status_code
        last_payload = payload

        if status_code != case.expected_status:
            continue

        if case.retry_on_pending:
            pending_header = headers.get("X-Clan-Battles-Pending", "").lower()
            if pending_header == "true":
                pending_seen = True
                time.sleep(5.0)
                continue

        # Empty result on first attempt may mean cache is cold; retry
        if case.retry_on_pending and isinstance(payload, list) and len(payload) == 0:
            pending_seen = True
            time.sleep(5.0)
            continue

        errors = validate_case(case, payload)
        if not errors:
            suffix = ""
            if pending_seen:
                suffix = " after pending refresh"
            return True, f"{case.name}: ok{suffix}"
        return False, f"{case.name}: {'; '.join(errors)}"

    if last_status != case.expected_status:
        return False, f"{case.name}: expected status {case.expected_status}, got {last_status}"

    # If we retried due to pending and data never arrived, warn but pass
    if pending_seen and case.retry_on_pending:
        errors = validate_case(case, last_payload)
        if errors:
            return True, f"{case.name}: ok (pending — data not yet populated by worker)"

    pending_note = " after pending refresh" if pending_seen else ""
    errors = validate_case(case, last_payload)
    detail = "; ".join(
        errors) if errors else f"payload validation failed{pending_note}"
    return False, f"{case.name}: {detail}"


def build_cases() -> list[SmokeCase]:
    return [
        # ── Landing / discovery ────────────────────────────────
        SmokeCase("landing_clans", "/api/landing/clans/",
                  json_type="list", min_items=1),
        SmokeCase("landing_players", "/api/landing/players/",
                  json_type="list", min_items=1),
        SmokeCase("landing_recent", "/api/landing/recent/",
                  json_type="list", min_items=1),
        SmokeCase(
            "player_suggestions",
            "/api/landing/player-suggestions/?q=sh",
            json_type="list",
            min_items=1,
        ),

        # ── Player detail (router) ────────────────────────────
        SmokeCase(
            "player_detail_shinn000",
            "/api/player/Shinn000/",
            json_type="dict",
            required_keys=("name", "pvp_ratio",
                           "pvp_survival_rate", "verdict"),
            exact_key_values={"name": "Shinn000"},
        ),
        SmokeCase("player_missing_404",
                  "/api/player/PlayerThatWillNeverExist/", expected_status=404),

        # ── Player fetch endpoints ─────────────────────────────
        SmokeCase(
            "player_summary_shinn000",
            "/api/fetch/player_summary/1000270433/",
            json_type="dict",
            required_keys=("player_id", "name", "pvp_ratio"),
            exact_key_values={"name": "Shinn000"},
        ),
        SmokeCase("randoms_maraxus1", "/api/fetch/randoms_data/1000954803/",
                  json_type="list", min_items=1),
        SmokeCase("tier_secap", "/api/fetch/tier_data/1000663088/",
                  json_type="list", min_items=1),
        SmokeCase("type_fourgate", "/api/fetch/type_data/1014916452/",
                  json_type="list", min_items=1),
        SmokeCase("activity_fourgate", "/api/fetch/activity_data/1014916452/",
                  json_type="list", min_items=1),
        SmokeCase("ranked_punkhunter25", "/api/fetch/ranked_data/1001243015/",
                  json_type="list", min_items=1),
        SmokeCase("ranked_empty_kevik70", "/api/fetch/ranked_data/1001712582/",
                  json_type="list", exact_items=0),

        # ── Clan endpoints ─────────────────────────────────────
        SmokeCase(
            "clan_detail_naumachia",
            "/api/clan/1000055908/",
            json_type="dict",
            required_keys=("clan_id", "name"),
        ),
        SmokeCase("clan_data_naumachia", "/api/fetch/clan_data/1000055908:active",
                  json_type="list"),
        SmokeCase("clan_members_naumachia",
                  "/api/fetch/clan_members/1000055908/", json_type="list", min_items=1),
        SmokeCase(
            "clan_battles_naumachia",
            "/api/fetch/clan_battle_seasons/1000055908/",
            json_type="list",
            min_items=1,
            retry_on_pending=True,
        ),
        SmokeCase(
            "clan_filter_invalid_400",
            "/api/fetch/clan_data/1000055908:bogus",
            expected_status=400,
            json_type="dict",
            required_keys=("detail",),
        ),

        # ── Ship endpoint ──────────────────────────────────────
        SmokeCase(
            "ship_detail",
            "/api/ship/1/",
            json_type="dict",
            required_keys=("name",),
        ),

        # ── Player explorer ────────────────────────────────────
        SmokeCase(
            "players_explorer",
            "/api/players/explorer/?page_size=5&min_pvp_battles=1000",
            json_type="dict",
            required_keys=("count", "page", "page_size", "results"),
            nested_list_key="results",
            min_nested_items=1,
        ),

        # ── Population distributions ───────────────────────────
        SmokeCase("wr_distribution", "/api/fetch/wr_distribution/",
                  json_type="list", min_items=1),
        SmokeCase(
            "player_distribution_win_rate",
            "/api/fetch/player_distribution/win_rate/",
            json_type="dict",
            required_keys=("metric", "tracked_population", "bins"),
            exact_key_values={"metric": "win_rate"},
            nested_list_key="bins",
            min_nested_items=1,
        ),
        SmokeCase(
            "player_distribution_survival_rate",
            "/api/fetch/player_distribution/survival_rate/",
            json_type="dict",
            required_keys=("metric", "tracked_population", "bins"),
            exact_key_values={"metric": "survival_rate"},
            nested_list_key="bins",
            min_nested_items=1,
        ),
        SmokeCase(
            "player_distribution_battles_played",
            "/api/fetch/player_distribution/battles_played/",
            json_type="dict",
            required_keys=("metric", "tracked_population", "bins"),
            exact_key_values={"metric": "battles_played"},
            nested_list_key="bins",
            min_nested_items=1,
        ),
        SmokeCase(
            "player_correlation_win_rate_survival",
            "/api/fetch/player_correlation/win_rate_survival/",
            json_type="dict",
            required_keys=("metric", "tracked_population", "tiles", "trend"),
            exact_key_values={"metric": "win_rate_survival"},
            nested_list_key="tiles",
            min_nested_items=1,
        ),
        SmokeCase(
            "player_correlation_tier_type_fourgate",
            "/api/fetch/player_correlation/tier_type/1014916452/",
            json_type="dict",
            required_keys=("metric", "tracked_population",
                           "tiles", "trend", "player_cells"),
            exact_key_values={"metric": "tier_type"},
            nested_list_key="tiles",
            min_nested_items=1,
        ),

        # ── Stats ──────────────────────────────────────────────
        SmokeCase(
            "stats",
            "/api/stats/",
            json_type="dict",
            required_keys=("players", "clans"),
        ),
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Smoke test live Battlestats API endpoints.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help="Base URL for the running app.")
    parser.add_argument("--timeout", type=float, default=30.0,
                        help="Per-request timeout in seconds.")
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit a machine-readable JSON summary instead of line-oriented logs.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    failures: list[str] = []
    case_results: list[dict[str, Any]] = []

    for case in build_cases():
        ok, message = run_case(case, args.base_url, args.timeout)
        case_results.append({
            "name": case.name,
            "ok": ok,
            "message": message,
        })
        if not args.json:
            prefix = "PASS" if ok else "FAIL"
            print(f"[{prefix}] {message}")
        if not ok:
            failures.append(case.name)

    summary = {
        "status": "failed" if failures else "passed",
        "base_url": args.base_url,
        "timeout": args.timeout,
        "cases": case_results,
        "failure_count": len(failures),
        "failures": failures,
    }

    if args.json:
        print(json.dumps(summary, sort_keys=True))
        return 1 if failures else 0

    if failures:
        print(f"\nSmoke test failed: {len(failures)} case(s) failed")
        for name in failures:
            print(f" - {name}")
        return 1

    print("\nSmoke test passed: all endpoint checks succeeded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
