#!/usr/bin/env python3
"""
Simulated load test: 10 concurrent users browsing battlestats.

Each user follows a realistic journey:
  1. Landing page (4 parallel API calls)
  2. Search for a player (autocomplete)
  3. Player detail (player lookup + chart endpoints + analytics POST)
  4. Clan detail (clan lookup + data endpoints + analytics POST)
  5. Return to landing

Usage:
  python tests/load/load_test.py [--base-url URL] [--users N] [--think-time SEC]

Defaults:
  --base-url  http://localhost:8888   (hit Django directly; use :3001 for full-stack via Next.js proxy)
  --users     10
  --think-time 3  (seconds between actions, randomized ±1s)

Output: per-request timing log + summary statistics.
"""

import argparse
import asyncio
import json
import random
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import quote

import aiohttp

# --- Known test data -----------------------------------------------------------

PLAYER_NAMES = [
    "Black_Magician", "Rusty_Bucket__", "Lapplandhex", "Ivane",
    "xzerosangel", "GoldRush21", "senorange", "Umbrellarduu",
    "ffael", "zaiko_2016_steel", "Aquilam", "BullSomicTree1",
    "dj_dan92", "Hensen", "Squid69lips", "jorwann97",
    "Staff0369", "Harvey_Birdman_07", "SubRMC", "Garrick40",
    "lil_boots",
]

SEARCH_QUERIES = ["Bla", "Rus", "lil", "Gold", "Squ", "Iva", "Gar", "Sub", "Hen", "dj_"]


# --- Metrics collection -------------------------------------------------------

@dataclass
class RequestMetric:
    user_id: int
    step: str
    method: str
    url: str
    status: int
    elapsed_ms: float
    error: str | None = None


@dataclass
class LoadTestResults:
    metrics: list[RequestMetric] = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0

    def add(self, m: RequestMetric):
        self.metrics.append(m)

    def summary(self) -> str:
        if not self.metrics:
            return "No requests recorded."

        total = len(self.metrics)
        errors = [m for m in self.metrics if m.error or m.status >= 400]
        times = [m.elapsed_ms for m in self.metrics]
        times.sort()

        def percentile(p):
            idx = int(len(times) * p / 100)
            return times[min(idx, len(times) - 1)]

        by_step: dict[str, list[float]] = {}
        for m in self.metrics:
            by_step.setdefault(m.step, []).append(m.elapsed_ms)

        lines = [
            "",
            "=" * 70,
            "LOAD TEST SUMMARY",
            "=" * 70,
            f"Duration:       {self.end_time - self.start_time:.1f}s",
            f"Total requests: {total}",
            f"Errors:         {len(errors)}",
            f"Min:            {times[0]:.0f}ms",
            f"Median:         {percentile(50):.0f}ms",
            f"P90:            {percentile(90):.0f}ms",
            f"P95:            {percentile(95):.0f}ms",
            f"P99:            {percentile(99):.0f}ms",
            f"Max:            {times[-1]:.0f}ms",
            "",
            "Per-step breakdown:",
            f"  {'Step':<30} {'Count':>6} {'Median':>8} {'P95':>8} {'Max':>8} {'Errors':>7}",
            f"  {'-'*30} {'-'*6} {'-'*8} {'-'*8} {'-'*8} {'-'*7}",
        ]

        for step in sorted(by_step.keys()):
            st = sorted(by_step[step])
            step_errors = sum(1 for m in self.metrics if m.step == step and (m.error or m.status >= 400))
            p50 = st[int(len(st) * 0.5)]
            p95 = st[min(int(len(st) * 0.95), len(st) - 1)]
            lines.append(
                f"  {step:<30} {len(st):>6} {p50:>7.0f}ms {p95:>7.0f}ms {st[-1]:>7.0f}ms {step_errors:>7}"
            )

        if errors:
            lines.append("")
            lines.append("Errors:")
            for e in errors[:20]:
                lines.append(f"  [{e.user_id}] {e.step} {e.method} {e.url} -> {e.status} {e.error or ''}")
            if len(errors) > 20:
                lines.append(f"  ... and {len(errors) - 20} more")

        lines.append("=" * 70)
        return "\n".join(lines)


# --- HTTP helpers --------------------------------------------------------------

async def timed_request(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    user_id: int,
    step: str,
    results: LoadTestResults,
    **kwargs,
) -> dict | list | str | None:
    """Make a request, record timing, return parsed body or None on error."""
    t0 = time.monotonic()
    error = None
    status = 0
    body = None
    try:
        async with session.request(method, url, **kwargs) as resp:
            status = resp.status
            text = await resp.text()
            if resp.content_type and "json" in resp.content_type:
                try:
                    body = json.loads(text)
                except json.JSONDecodeError:
                    body = text
            else:
                body = text
            if status >= 400:
                error = text[:200]
    except Exception as exc:
        error = str(exc)[:200]
        status = 0
    elapsed = (time.monotonic() - t0) * 1000
    results.add(RequestMetric(user_id, step, method, url, status, elapsed, error))
    tag = "ERR" if error else "OK"
    print(f"  [user {user_id:>2}] {step:<30} {method} {url:<60} {status} {elapsed:>7.0f}ms {tag}")
    return body


# --- User journey --------------------------------------------------------------

async def user_journey(
    user_id: int,
    base_url: str,
    think_time: float,
    results: LoadTestResults,
):
    """Simulate one user's browsing session."""
    timeout = aiohttp.ClientTimeout(total=30)
    visitor_key = f"loadtest-visitor-{user_id}-{uuid.uuid4().hex[:8]}"
    session_key = f"loadtest-session-{user_id}-{uuid.uuid4().hex[:8]}"
    async with aiohttp.ClientSession(timeout=timeout) as session:
        # Pick random test data for this user
        player_name = random.choice(PLAYER_NAMES)
        search_q = random.choice(SEARCH_QUERIES)

        # --- Step 1: Landing page (4 parallel requests) ---
        landing_urls = [
            (f"{base_url}/api/landing/clans/?mode=random&limit=30", "landing:clans"),
            (f"{base_url}/api/landing/warm-best/", "landing:warm-best"),
            (f"{base_url}/api/landing/recent-clans/", "landing:recent-clans"),
            (f"{base_url}/api/landing/recent/", "landing:recent-players"),
        ]
        await asyncio.gather(*[
            timed_request(session, "GET", url, user_id, step, results)
            for url, step in landing_urls
        ])
        await _think(think_time)

        # --- Step 2: Search autocomplete ---
        await timed_request(
            session, "GET",
            f"{base_url}/api/landing/player-suggestions/?q={quote(search_q)}",
            user_id, "search:autocomplete", results,
        )
        await _think(think_time * 0.5)

        # --- Step 3: Player detail ---
        player_resp = await timed_request(
            session, "GET",
            f"{base_url}/api/player/{quote(player_name)}/",
            user_id, "player:lookup", results,
        )

        player_id = None
        clan_id = None
        if isinstance(player_resp, dict):
            player_id = player_resp.get("player_id")
            clan_info = player_resp.get("clan") or {}
            if isinstance(clan_info, dict):
                clan_id = clan_info.get("clan_id")

        if player_id:
            # Parallel chart fetches (like the real frontend)
            chart_endpoints = [
                (f"{base_url}/api/fetch/tier_data/{player_id}", "player:tier_data"),
                (f"{base_url}/api/fetch/activity_data/{player_id}", "player:activity_data"),
                (f"{base_url}/api/fetch/type_data/{player_id}", "player:type_data"),
                (f"{base_url}/api/fetch/randoms_data/{player_id}", "player:randoms_data"),
                (f"{base_url}/api/fetch/ranked_data/{player_id}", "player:ranked_data"),
                (f"{base_url}/api/fetch/player_summary/{player_id}", "player:summary"),
            ]
            await asyncio.gather(*[
                timed_request(session, "GET", url, user_id, step, results)
                for url, step in chart_endpoints
            ])

            # Analytics POST
            await timed_request(
                session, "POST",
                f"{base_url}/api/analytics/entity-view/",
                user_id, "player:analytics", results,
                json={
                    "event_uuid": str(uuid.uuid4()),
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                    "entity_type": "player",
                    "entity_id": player_id,
                    "entity_name": player_name,
                    "route_path": f"/player/{player_name}",
                    "visitor_key": visitor_key,
                    "session_key": session_key,
                },
            )
        await _think(think_time)

        # --- Step 4: Clan detail (if player had a clan) ---
        if clan_id:
            await timed_request(
                session, "GET",
                f"{base_url}/api/clan/{clan_id}",
                user_id, "clan:lookup", results,
            )

            clan_data_endpoints = [
                (f"{base_url}/api/fetch/clan_data/{clan_id}", "clan:data"),
                (f"{base_url}/api/fetch/clan_members/{clan_id}", "clan:members"),
                (f"{base_url}/api/fetch/clan_battle_seasons/{clan_id}", "clan:cb_seasons"),
            ]
            await asyncio.gather(*[
                timed_request(session, "GET", url, user_id, step, results)
                for url, step in clan_data_endpoints
            ])

            await timed_request(
                session, "POST",
                f"{base_url}/api/analytics/entity-view/",
                user_id, "clan:analytics", results,
                json={
                    "event_uuid": str(uuid.uuid4()),
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                    "entity_type": "clan",
                    "entity_id": clan_id,
                    "entity_name": f"clan-{clan_id}",
                    "route_path": f"/clan/{clan_id}",
                    "visitor_key": visitor_key,
                    "session_key": session_key,
                },
            )
            await _think(think_time)

        # --- Step 5: Return to landing ---
        await asyncio.gather(*[
            timed_request(session, "GET", url, user_id, step, results)
            for url, step in landing_urls
        ])

    print(f"  [user {user_id:>2}] Journey complete.")


async def _think(base_seconds: float):
    """Simulate user think time with ±1s jitter."""
    delay = max(0.5, base_seconds + random.uniform(-1, 1))
    await asyncio.sleep(delay)


# --- Orchestration -------------------------------------------------------------

async def run_load_test(base_url: str, num_users: int, think_time: float):
    results = LoadTestResults()
    results.start_time = time.monotonic()

    print(f"\nStarting load test: {num_users} users against {base_url}")
    print(f"Think time: {think_time}s (±1s jitter)")
    print(f"Player pool: {len(PLAYER_NAMES)} names")
    print("-" * 70)

    # Stagger user arrivals over ~30 seconds
    stagger = 30.0 / num_users
    tasks = []
    for i in range(num_users):
        tasks.append(asyncio.create_task(
            _delayed_journey(i + 1, i * stagger, base_url, think_time, results)
        ))

    await asyncio.gather(*tasks)
    results.end_time = time.monotonic()
    print(results.summary())
    return results


async def _delayed_journey(user_id, delay, base_url, think_time, results):
    await asyncio.sleep(delay)
    print(f"  [user {user_id:>2}] Starting journey...")
    await user_journey(user_id, base_url, think_time, results)


def main():
    parser = argparse.ArgumentParser(description="Battlestats load test")
    parser.add_argument("--base-url", default="http://localhost:8888",
                        help="Base URL (default: http://localhost:8888)")
    parser.add_argument("--users", type=int, default=10,
                        help="Number of concurrent users (default: 10)")
    parser.add_argument("--think-time", type=float, default=3.0,
                        help="Think time between steps in seconds (default: 3)")
    args = parser.parse_args()

    try:
        asyncio.run(run_load_test(args.base_url, args.users, args.think_time))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(1)


if __name__ == "__main__":
    main()
