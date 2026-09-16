"""Read-only spike: is tier 7 deep enough to carry a ship-standings board?

Recomputes the top-player board in memory for the target tiers at two trailing
windows and reports every depth statistic SPLIT BY TIER, so T7 can be judged
against T8 (the shallowest tier currently shipped) rather than against an
absolute count. Writes nothing.

Two floors per window for free: the aggregate runs at the lower
SHIP_BADGE_MIN_BATTLES and the higher floor is applied by re-filtering the
same rows in Python (identical to what a second aggregate would return).

    ssh root@battlestats.online 'cd /opt/battlestats-server/current/server \
      && set -a && . /etc/battlestats-server.env && . /etc/battlestats-server.secrets.env && set +a \
      && SPIKE_WINDOWS=60,85 SPIKE_TIERS=7,8 SPIKE_FLOORS=20,30 \
         /opt/battlestats-server/venv/bin/python manage.py shell' \
      < server/scripts/spike_ship_board_tier7.py 2>&1 | grep --line-buffered -v "Loading environment"

Cost: one BattleEvent group-aggregate per (realm, window). No treemap union
(it would inject T10 ships and contaminate the per-tier split).
Sibling: spike_ship_board_window.py (window widen, pooled across tiers).
"""
import os
import statistics as st
import time
from datetime import timedelta

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone as tz

from warships.data import (SHIP_LEADERBOARD_WINDOW_DAYS, _elevated_work_mem,
                           _pool_zscores, _season_window_datetimes)
from warships.models import BattleEvent, Ship

WINDOWS = [int(x) for x in os.getenv('SPIKE_WINDOWS', '60,85').split(',')]
REALMS = os.getenv('SPIKE_REALMS', 'na,eu,asia').split(',')
TIERS = sorted({int(x) for x in os.getenv('SPIKE_TIERS', '7,8').split(',')})
FLOORS = [int(x) for x in os.getenv('SPIKE_FLOORS', '20,30').split(',')]

min_pop = int(os.getenv('SHIP_BADGE_MIN_SHIP_POPULATION', '20'))
min_pop_cv = int(os.getenv('SHIP_BADGE_MIN_SHIP_POPULATION_CV', '10'))
min_pop_sub = int(os.getenv('SHIP_BADGE_MIN_SHIP_POPULATION_SUB', '12'))
list_size = int(os.getenv('SHIP_BADGE_LIST_SIZE', '15'))
prior_b = int(os.getenv('SHIP_BADGE_PRIOR_BATTLES', '50'))
prior_wr = float(os.getenv('SHIP_BADGE_PRIOR_WR', '0.5'))
w_w = float(os.getenv('SHIP_BADGE_WEIGHT_WINS', '0.6'))
w_d = float(os.getenv('SHIP_BADGE_WEIGHT_DAMAGE', '0.25'))
w_k = float(os.getenv('SHIP_BADGE_WEIGHT_KILLS', '0.15'))
min_wr = float(os.getenv('SHIP_BADGE_MIN_WIN_RATE', '50'))
live_tiers = os.getenv('SHIP_BADGE_TIERS', '?')

print(f"ENV live_window={SHIP_LEADERBOARD_WINDOW_DAYS} live_tiers={live_tiers} "
      f"windows={WINDOWS} spike_tiers={TIERS} floors={FLOORS} "
      f"pop={min_pop}/{min_pop_cv}/{min_pop_sub} prior={prior_b}@{prior_wr} "
      f"w={w_w}/{w_d}/{w_k} min_wr={min_wr} list_size={list_size}", flush=True)

today = tz.now().date()
target = {s.ship_id: (s.name, s.tier, s.ship_type)
          for s in Ship.objects.filter(tier__in=TIERS)}
roster = {t: sum(1 for v in target.values() if v[1] == t) for t in TIERS}
print(f"targets={len(target)} roster_by_tier={roster}", flush=True)


def pull(realm, days, floor):
    """One grouped aggregate: per (ship, player) window totals at `floor` battles."""
    since, until = _season_window_datetimes(today - timedelta(days=days), today)
    agg = (BattleEvent.objects
           .filter(ship_id__in=list(target), mode='random',
                   detected_at__gte=since, detected_at__lt=until,
                   player__realm=realm, player__is_hidden=False)
           .values('ship_id', 'player_id')
           .annotate(battles=Sum('battles_delta'), wins=Sum('wins_delta'),
                     damage=Sum('damage_delta'), frags=Sum('frags_delta'))
           .filter(battles__gte=floor))
    t0 = time.time()
    with transaction.atomic(), _elevated_work_mem():
        rows = list(agg)
    return rows, time.time() - t0


def board(rows, min_battles):
    """Mirror of compute_ship_top_player_snapshot's ranking, minus the writes."""
    by = {}
    for r in rows:
        if (r['battles'] or 0) < min_battles:
            continue
        by.setdefault(r['ship_id'], []).append(dict(r))
    out, pools = {}, {}
    for sid, pool in by.items():
        stype = target.get(sid, (None, None, None))[2]
        floor = min_pop_cv if stype == 'AirCarrier' else (min_pop_sub if stype == 'Submarine' else min_pop)
        pools[sid] = (len(pool), floor)
        if len(pool) < floor:
            continue
        pb = sum(e['battles'] or 0 for e in pool)
        mdpb = sum(e['damage'] or 0 for e in pool) / pb if pb else 0.0
        mkpb = sum(e['frags'] or 0 for e in pool) / pb if pb else 0.0
        for e in pool:
            b = e['battles'] or 0
            w = e['wins'] or 0
            e['wr'] = 100.0 * w / b if b else 0.0
            d = b + prior_b
            e['_wr'] = (w + prior_b * prior_wr) / d
            e['_d'] = ((e['damage'] or 0) + prior_b * mdpb) / d
            e['_k'] = ((e['frags'] or 0) + prior_b * mkpb) / d
        zw = _pool_zscores([e['_wr'] for e in pool])
        zd = _pool_zscores([e['_d'] for e in pool])
        zk = _pool_zscores([e['_k'] for e in pool])
        for e, a, b_, c in zip(pool, zw, zd, zk):
            e['_s'] = w_w * a + w_d * b_ + w_k * c
        pool.sort(key=lambda e: (-e['_s'], -(e['battles'] or 0)))
        ranked = [e for e in pool if e['wr'] >= min_wr] if min_wr > 0 else pool
        out[sid] = [(e['player_id'], e['battles'], round(e['wr'], 1)) for e in ranked[:list_size]]
    return out, pools


def med(xs):
    return round(st.median(xs), 1) if xs else None


def pct(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    return s[max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))]


def report(tag, b, pools, tier, mb):
    ids = [s for s in b if target[s][1] == tier]
    seen = [s for s in pools if target[s][1] == tier]
    if not ids:
        print(f"    T{tier} {tag}: ranked=0 (ships with any qualifying pool={len(seen)})")
        return
    psz = [pools[s][0] for s in ids]
    r1 = [b[s][0][1] for s in ids if b[s]]
    rows = [x[1] for s in ids for x in b[s]]
    marg = sum(1 for s in ids if pools[s][0] - pools[s][1] <= 3)
    short = sum(1 for s in ids if len(b[s]) < list_size)
    empty = sum(1 for s in ids if not b[s])
    near = sum(1 for x in rows if x < mb + 5)
    print(f"    T{tier} {tag}: ranked={len(ids)}/{roster[tier]} roster ({100*len(ids)/max(1,roster[tier]):.0f}%), "
          f"of {len(seen)} with any pool | pool med={med(psz)} p25={pct(psz,0.25)} min={min(psz)} | "
          f"#1_battles med={med(r1)} min={min(r1) if r1 else None} | "
          f"rows={len(rows)} med={med(rows)} within5_of_floor={near} ({100*near/max(1,len(rows)):.0f}%) | "
          f"short_boards(<{list_size})={short} empty_after_wr_gate={empty} | marginal(pool<=floor+3)={marg}")


BY_TYPE = os.getenv('SPIKE_BY_TYPE', '0') == '1'


def report_by_type(tag, b, pools, tier):
    """Per (tier, ship_type) ranked counts — the axis the product surfaces.

    `/ships/<tier>-<type>` is one indexable route per bucket, and both warm
    loops iterate tier x type, so a tier's depth only answers half the
    question: a bucket with three hulls in the roster is a thin page no matter
    how deep the tier reads in aggregate.
    """
    for stype in ('Battleship', 'Cruiser', 'Destroyer', 'AirCarrier', 'Submarine'):
        roster_n = sum(1 for v in target.values() if v[1] == tier and v[2] == stype)
        ids = [s for s in b if target[s][1] == tier and target[s][2] == stype]
        seen = [s for s in pools if target[s][1] == tier and target[s][2] == stype]
        if not roster_n:
            print(f"      T{tier} {stype:12s} {tag}: roster=0 (structurally empty bucket)")
            continue
        psz = [pools[s][0] for s in ids]
        short = sum(1 for s in ids if len(b[s]) < list_size)
        print(f"      T{tier} {stype:12s} {tag}: ranked={len(ids)}/{roster_n} roster "
              f"(any pool={len(seen)}) | pool med={med(psz)} min={min(psz) if psz else None} "
              f"| short_boards={short}")


for realm in REALMS:
    print(f"\n=== {realm} today={today} (window B is a proxy for 90d; earliest event 2026-06-13)", flush=True)
    for days in WINDOWS:
        rows, el = pull(realm, days, min(FLOORS))
        print(f"  {days}d agg={el:.1f}s rows(battles>={min(FLOORS)})={len(rows)}", flush=True)
        for f in FLOORS:
            b, pools = board(rows, f)
            for t in TIERS:
                report(f"{days}d/floor{f}", b, pools, t, f)
                if BY_TYPE:
                    report_by_type(f"{days}d/floor{f}", b, pools, t)
        del rows
print("\nDONE", flush=True)
