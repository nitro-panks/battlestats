'use client';

// Inline ship leaderboard — the filterable ship explorer under the landing
// treemap.
//
// Pick a TIER (8/9/10 — the tiers we compute ship data for) and a TYPE
// (BB/CA/DD/CV/SS); the ship list (`/api/realm/<realm>/ships`) shows that bucket
// ranked by realm-wide win rate, mirroring the BattleEvent population stats the
// treemap above already uses. Clicking a ship swaps the list IN PLACE for that
// ship's player board (the existing `/api/realm/<realm>/ship/<id>/leaderboard`),
// and Clear returns to the list for the still-selected tier/type. No navigation,
// no new full-page route.

import React, { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faCircleInfo } from '@fortawesome/free-solid-svg-icons';
import { fetchSharedJson } from '../lib/sharedJsonFetch';
import { useRealm } from '../context/RealmContext';
import { shipClass } from '../lib/shipIdentity';
import NationFlag from './NationFlag';
import ShipToolLink from './ShipToolLink';
import TopShipIcon from './TopShipIcon';
import { buildPlayerPath } from '../lib/entityRoutes';
import { trackEvent } from '../lib/umami';
import wrColor from '../lib/wrColor';
import SubmarineEasterEgg from './SubmarineEasterEgg';
import CarrierEasterEgg from './CarrierEasterEgg';

export type Tier = 8 | 9 | 10;
// Raw `Ship.ship_type` strings the backend filters on (note: "AirCarrier", no
// space). These are the `type` query-param values the new endpoint accepts.
export const SHIP_TYPES = ['Battleship', 'Cruiser', 'Destroyer', 'AirCarrier', 'Submarine'] as const;
export type ShipType = (typeof SHIP_TYPES)[number];
const TIERS: Tier[] = [8, 9, 10];

// Win-rate-percentile filter for the ship LIST: narrows each ship's displayed
// stats (battles, avg dmg, kills/battle, WR) to the top N% of that ship's
// players by win rate — answering "how are good/great players doing with these
// ships?". `null` is the default realm-wide aggregate. Must match the backend's
// SHIP_LIST_WR_PCTS (50/25). Does NOT change which ships are listed.
export type WrPct = 50 | 25 | null;
const WR_PCTS: { value: WrPct; label: string }[] = [
    { value: null, label: 'All' },
    { value: 50, label: '50%' },
    { value: 25, label: '25%' },
];

// Persist the landing tier/type/WR selection so it survives a return visit.
// Stored under one key; read once on mount (after SSR, so no hydration mismatch)
// and written on every change. Each field is validated on read so a malformed or
// stale value falls back to the component default rather than fetching garbage.
// wrPct's `null` ("All") is a real stored value, distinct from "absent".
const SHIP_LB_PREFS_KEY = 'bs-ship-leaderboard';

interface ShipLbPrefs {
    tier: Tier;
    type: ShipType;
    wrPct: WrPct;
}

function readStoredShipLbPrefs(): Partial<ShipLbPrefs> | null {
    if (typeof window === 'undefined') return null;
    try {
        const raw = window.localStorage.getItem(SHIP_LB_PREFS_KEY);
        if (!raw) return null;
        const parsed = JSON.parse(raw) as Record<string, unknown>;
        if (!parsed || typeof parsed !== 'object') return null;
        const out: Partial<ShipLbPrefs> = {};
        if (TIERS.includes(parsed.tier as Tier)) out.tier = parsed.tier as Tier;
        if (SHIP_TYPES.includes(parsed.type as ShipType)) out.type = parsed.type as ShipType;
        if (parsed.wrPct === null || parsed.wrPct === 50 || parsed.wrPct === 25) out.wrPct = parsed.wrPct as WrPct;
        return out;
    } catch {
        return null;
    }
}

// Imperative handle the landing treemap drives to drill straight into a ship's
// player board in place (see runbook-treemap-shipleaderboard-handoff). Kept as a
// command rather than lifted state so this component keeps owning its list/board
// state and there is no prop↔state sync race after the user hits Clear.
export interface ShipLeaderboardHandle {
    selectShip(sel: { id: number; name: string; tier: Tier; type: ShipType }): void;
}

// The resolved ship bucket this component emits upward (to PlayerSearch → the
// treemap) on every filter change / load transition, so the treemap can render
// the same tier+type (+ WR-percentile) selection without a second fetch. `empty`
// is true for the T9 sub/CV easter-egg buckets and any resolved-but-shipless
// bucket, distinct from a still-loading one.
export interface ShipBucket {
    tier: Tier | null;
    type: ShipType | null;
    wrPct: WrPct;
    ships: ListShip[];
    totalBattles: number;
    windowStart?: string;
    windowEnd?: string;
    loading: boolean;
    pending: boolean;
    empty: boolean;
}

interface ShipLeaderboardProps {
    onBucket?: (bucket: ShipBucket) => void;
}

// Exported so the landing treemap (RealmTopShipsTreemapSVG) can render the same
// bucket this component fetches — the treemap is fed the resolved `ListShip[]`
// via PlayerSearch rather than fetching its own copy.
export interface ListShip {
    ship_id: number;
    ship_name: string;
    ship_type: string | null;
    tier: number | null;
    nation: string;
    is_premium: boolean;
    battles: number;
    win_rate: number;
    avg_damage: number;
    kills_per_battle: number;
}

interface ShipsByTierType {
    realm: string;
    tier: number;
    ship_type: string;
    // Total battles across every ship of this tier+type in the window — the
    // denominator for each ship's class/tier share %. Optional so a payload from
    // before this field shipped (e.g. a durable `:published` fallback served
    // mid-deploy) degrades to battles-only rather than NaN%.
    total_battles?: number;
    // Rolling-window bounds (date-only ISO, UTC) the treemap heading reads. Same
    // window the /ship board + medals use; optional so an old durable `:published`
    // fallback payload degrades gracefully.
    window_start?: string;
    window_end?: string;
    ships: ListShip[];
    // True when a cold win-rate-percentile bucket is still being computed by a
    // background warm (the heavy per-player aggregation). The client polls until
    // a non-pending payload (with ships) lands. Absent on ready payloads.
    pending?: boolean;
}

interface LeaderboardPlayer {
    rank: number;
    player_name: string;
    win_rate: number;
    battles: number;
    avg_damage: number;
    kills_per_battle: number;
}

interface ShipLeaderboardPayload {
    realm: string;
    ship: {
        ship_id: number;
        name: string;
        tier: number | null;
        ship_type: string | null;
        nation: string;
        is_premium: boolean;
        shiptool_code?: string | null;
    };
    players: LeaderboardPlayer[];
}

// The list changes once per night (rolling trailing window, recomputed with the
// nightly snapshot); a 1h client TTL keeps a long-open tab from showing the
// previous day's window for long (backend serves it warm).
const LIST_FETCH_TTL_MS = 3_600_000;
const BOARD_FETCH_TTL_MS = 900_000; // 15 min, matching the /ship page.

const HEADING_CLASS =
    'mr-2 text-sm font-semibold uppercase tracking-wide text-[var(--accent-mid)]';
const PILL_BASE =
    'inline-flex items-center rounded-md border px-3 py-1.5 text-sm font-semibold uppercase tracking-wide transition-colors';
const PILL_ON = 'border-[var(--accent-mid)] bg-[var(--accent-mid)] text-white';
const PILL_OFF =
    'border-[var(--border)] bg-[var(--bg-page)] text-[var(--accent-mid)] hover:bg-[var(--accent-faint)]';

// Every column is click-sortable. Sort lives client-side over the already-fetched
// rows; until a header is clicked the server's natural order (win rate for the
// list, rank for the board) is preserved (`sort === null`). New numeric columns
// open descending (best-first); text columns open ascending (A→Z).
type SortDir = 'asc' | 'desc';

function sortRows<T>(rows: T[], key: keyof T, dir: SortDir): T[] {
    const factor = dir === 'asc' ? 1 : -1;
    return [...rows].sort((a, b) => {
        const av = a[key];
        const bv = b[key];
        if (typeof av === 'string' && typeof bv === 'string') {
            return av.localeCompare(bv) * factor;
        }
        return (Number(av) - Number(bv)) * factor;
    });
}

// A ship's share of all battles played in its tier+class bucket this window,
// formatted for the Battles column ("12.4%"). Returns null when the denominator
// is missing/zero (old payload, empty bucket) so the caller renders battles
// only. Tiny-but-nonzero shares clamp to "<0.1%" rather than rounding to 0.0%.
function classSharePct(battles: number, total: number | undefined): string | null {
    if (!total || total <= 0) return null;
    const pct = (battles / total) * 100;
    if (pct > 0 && pct < 0.1) return '<0.1%';
    return `${pct.toFixed(1)}%`;
}

// Sort persistence: when a `storageKey` is supplied the chosen column/dir is
// remembered in localStorage so a visitor's preferred default (e.g. Avg dmg
// instead of the server's win-rate order) survives reloads. The persisted value
// is hydrated in an effect — not the useState initializer — because localStorage
// is client-only and reading it during the initial render would desync SSR/CSR.
function useTableSort<T>(
    textKeys: ReadonlyArray<keyof T>,
    onChange?: (key: keyof T, dir: SortDir) => void,
    storageKey?: string,
) {
    const [sort, setSort] = useState<{ key: keyof T; dir: SortDir } | null>(null);

    useEffect(() => {
        if (!storageKey || typeof window === 'undefined') return;
        try {
            const raw = window.localStorage.getItem(storageKey);
            if (!raw) return;
            const parsed = JSON.parse(raw);
            if (parsed && parsed.key && (parsed.dir === 'asc' || parsed.dir === 'desc')) {
                setSort({ key: parsed.key as keyof T, dir: parsed.dir });
            }
        } catch {
            /* ignore a malformed persisted sort — fall back to natural order */
        }
    }, [storageKey]);

    const onSort = (key: keyof T) => {
        // Compute the next sort from the current render's value (not inside the
        // setState updater) so analytics fire exactly once, never doubled.
        const next: { key: keyof T; dir: SortDir } =
            sort && sort.key === key
                ? { key, dir: sort.dir === 'asc' ? 'desc' : 'asc' }
                : { key, dir: textKeys.includes(key) ? 'asc' : 'desc' };
        setSort(next);
        onChange?.(next.key, next.dir);
        if (storageKey && typeof window !== 'undefined') {
            try {
                window.localStorage.setItem(storageKey, JSON.stringify(next));
            } catch {
                /* private mode / quota — persistence is best-effort */
            }
        }
    };
    return { sort, onSort };
}

// localStorage key for the inline ship-list column sort (persisted per browser).
const SHIP_LIST_SORT_STORAGE_KEY = 'battlestats:ship-list:sort';

// `align` must match the column's text alignment. The arrow slot always
// occupies width (opacity-0 when inactive), so it has to sit on the side AWAY
// from the data edge — after the label in left-aligned columns, before it in
// right-aligned ones — or the header label drifts ~13px off the numbers below.
const SortButton: React.FC<{
    label: string;
    active: boolean;
    dir: SortDir;
    onClick: () => void;
    align?: 'left' | 'right';
}> = ({ label, active, dir, onClick, align = 'left' }) => {
    const arrow = (
        <span
            aria-hidden
            className={`text-[9px] leading-none ${active ? 'opacity-100' : 'opacity-0 group-hover:opacity-40'}`}
        >
            {active && dir === 'asc' ? '▲' : '▼'}
        </span>
    );
    return (
        <button
            type="button"
            onClick={onClick}
            className={`group inline-flex items-center gap-1 font-medium uppercase tracking-wide transition-colors hover:text-[var(--accent-mid)] ${
                active ? 'text-[var(--accent-mid)]' : ''
            }`}
        >
            {align === 'right' && arrow}
            <span>{label}</span>
            {align === 'left' && arrow}
        </button>
    );
};

const ariaSort = (active: boolean, dir: SortDir): 'ascending' | 'descending' | 'none' =>
    active ? (dir === 'asc' ? 'ascending' : 'descending') : 'none';

const DATA_BASIS_HINT =
    'Stats are aggregated from battle observations recorded during the rolling trailing 30-day window. ' +
    'The WR filter narrows each ship’s stats to its top 50% or 25% of players by win rate (the ships listed never change).';

// Info affordance with a hover/focus tooltip — styled to match the circle-info
// buttons in the Players/Clans landing sections below (FontAwesomeIcon + the
// same accent-light icon button + `hidden group-hover:block` tooltip). Reveal is
// CSS-only so it works without JS state. The tooltip is right-anchored because
// this icon sits at the right edge of the filter row.
const InfoHint: React.FC<{ text: string }> = ({ text }) => (
    <div className="group relative inline-flex items-center">
        <button
            type="button"
            aria-label={text}
            className="inline-flex h-5 w-5 items-center justify-center rounded-full text-[10px] text-[var(--accent-light)] transition-colors hover:text-[var(--accent-mid)] focus:outline-none focus-visible:text-[var(--accent-mid)]"
        >
            <FontAwesomeIcon icon={faCircleInfo} className="text-[10px]" aria-hidden="true" />
        </button>
        <div
            role="tooltip"
            className="pointer-events-none absolute right-0 top-full z-20 mt-2 hidden w-60 max-w-[calc(100vw-2rem)] rounded-md border border-[var(--border)] bg-[var(--bg-page)] px-3 py-2 text-left text-xs normal-case leading-snug tracking-normal text-[var(--text-primary)] shadow-lg group-hover:block group-focus-within:block"
        >
            {text}
        </div>
    </div>
);

const ShipLeaderboard = forwardRef<ShipLeaderboardHandle, ShipLeaderboardProps>(({ onBucket }, ref) => {
    const { realm } = useRealm();
    const sectionRef = useRef<HTMLElement>(null);
    // Mirror onBucket into a ref so the emit effect doesn't depend on the parent
    // passing a stable callback identity (it re-emits on real state change only).
    const onBucketRef = useRef(onBucket);
    useEffect(() => { onBucketRef.current = onBucket; });

    // Land on T10 Battleships so the board shows real standings immediately
    // (these buckets are pre-warmed daily — see warm_realm_top_ships_task).
    const [tier, setTier] = useState<Tier | null>(10);
    const [type, setType] = useState<ShipType | null>('Battleship');
    // WR-percentile filter applies to the ship LIST only (not the drilled-in
    // player board). Defaults to the top 50% ("how are good players doing with
    // these ships?"); `null` is the all-players view. The default landing bucket
    // is pre-warmed (warm_realm_top_ships_task) so this view loads instantly.
    const [wrPct, setWrPct] = useState<WrPct>(50);
    const [selectedShip, setSelectedShip] = useState<{ id: number; name: string } | null>(null);

    // Restore the persisted tier/type/WR on mount (post-SSR, so the first client
    // render still matches the server's default markup — no hydration mismatch).
    // The list fetch is gated on this so it fires once, with the restored bucket,
    // instead of flashing the default bucket first.
    const [prefsRestored, setPrefsRestored] = useState(false);
    useEffect(() => {
        const stored = readStoredShipLbPrefs();
        if (stored) {
            if (stored.tier !== undefined) setTier(stored.tier);
            if (stored.type !== undefined) setType(stored.type);
            if (stored.wrPct !== undefined) setWrPct(stored.wrPct);
        }
        setPrefsRestored(true);
    }, []);

    // Persist the selection on every change (once restore has run, so the initial
    // default render never overwrites a stored preference). Treemap drill-downs
    // also set tier/type, so they persist too — which matches user intent.
    useEffect(() => {
        if (!prefsRestored || tier == null || type == null) return;
        try {
            window.localStorage.setItem(SHIP_LB_PREFS_KEY, JSON.stringify({ tier, type, wrPct }));
        } catch {
            // localStorage unavailable
        }
    }, [prefsRestored, tier, type, wrPct]);

    const [list, setList] = useState<ListShip[] | null>(null);
    const [listTotalBattles, setListTotalBattles] = useState(0);
    // Rolling-window bounds from the last resolved list payload — surfaced to the
    // treemap heading (via onBucket) so it can show the same date range.
    const [listWindow, setListWindow] = useState<{ start?: string; end?: string }>({});
    // The tier|type|wrPct the current `list` was fetched for. On a filter switch
    // `list` still holds the PREVIOUS bucket until the new fetch resolves; the
    // treemap uses this to know its ships are stale (so it dims + waits rather than
    // painting the old bucket under the new heading).
    const [listBucketKey, setListBucketKey] = useState<string | null>(null);
    const [listLoading, setListLoading] = useState(false);
    const [listError, setListError] = useState(false);
    // True while a cold WR-percentile bucket is being computed server-side and we
    // are polling for it (drives a distinct "crunching" message vs first load).
    const [listPending, setListPending] = useState(false);

    const [board, setBoard] = useState<ShipLeaderboardPayload | null>(null);
    const [boardLoading, setBoardLoading] = useState(false);
    const [boardError, setBoardError] = useState(false);

    // Changing either filter abandons any open ship board (a stale ship under a
    // new filter is nonsense) and resets the list. `control` records which pill
    // the user clicked so the Umami log reads clearly (tier vs type).
    const chooseTier = (t: Tier) => {
        if (t === tier) return;
        setTier(t);
        setSelectedShip(null);
        trackEvent('ship-leaderboard-filter', { realm, control: 'tier', tier: t, type: type ?? '' });
    };
    const chooseType = (t: ShipType) => {
        if (t === type) return;
        setType(t);
        setSelectedShip(null);
        trackEvent('ship-leaderboard-filter', { realm, control: 'type', tier: tier ?? 0, type: t });
    };
    // WR-percentile filter — list-only, so it never abandons a board (the pills
    // are hidden while drilled in). 0 stands in for "all" in the analytics log.
    const chooseWrPct = (p: WrPct) => {
        if (p === wrPct) return;
        setWrPct(p);
        trackEvent('ship-leaderboard-wr-filter', { realm, wr_pct: p ?? 0, tier: tier ?? 0, type: type ?? '' });
    };

    // Column-sort analytics, one event for both tables (scope distinguishes the
    // ship list from the player board). Built here so realm lives in one place.
    const trackSort = (scope: 'ships' | 'players') => (column: string, dir: SortDir) =>
        trackEvent('ship-leaderboard-sort', { realm, scope, column, dir });

    const bothSelected = tier != null && type != null;
    // World of Warships has no Tier 9 submarine and no Tier 9 aircraft carrier
    // (carriers are even-tier only), so both buckets are always empty. Each
    // short-circuits to its own easter egg and must issue NO fetch — the endpoint
    // would 400 in any env where SHIP_BADGE_TIERS excludes 9 (e.g. local dev) and
    // is pointless in prod. Gate both the fetch effect and the render branch on
    // these predicates.
    const isSubEasterEgg = tier === 9 && type === 'Submarine';
    const isCarrierEasterEgg = tier === 9 && type === 'AirCarrier';
    const isEasterEgg = isSubEasterEgg || isCarrierEasterEgg;
    const eggKind = isSubEasterEgg ? 't9-submarine' : isCarrierEasterEgg ? 't9-carrier' : null;

    // Count every time an easter egg surfaces. The render branch is the single
    // source of truth for "the user is looking at it", so fire off the predicate
    // — independent of whether they reached it tier-first or type-first. A ref
    // edge-triggers it (once per activation, reset on exit) so a realm flip while
    // it's on screen doesn't double-count.
    const eggTrackedRef = useRef(false);
    useEffect(() => {
        if (eggKind) {
            if (!eggTrackedRef.current) {
                eggTrackedRef.current = true;
                trackEvent('ship-leaderboard-easter-egg', { realm, egg: eggKind });
            }
        } else {
            eggTrackedRef.current = false;
        }
    }, [eggKind, realm]);

    // Ship list fetch (only with both filters set and no ship drilled into).
    // The default "all" view is client-cached (LIST_FETCH_TTL_MS); the WR
    // percentile views are NOT — they may come back `pending` (a cold bucket
    // being computed by a background warm), so we bypass the settled cache
    // (ttlMs:0) and poll until ships land. This also avoids a pending stub
    // poisoning the client cache. The server cache + in-flight dedup keep the
    // warm-bucket re-fetches cheap.
    const listReqId = useRef(0);
    useEffect(() => {
        if (!prefsRestored || !bothSelected || selectedShip || isEasterEgg) return;
        const reqId = ++listReqId.current;
        setListLoading(true);
        setListError(false);
        setListPending(false);

        const wrParam = wrPct ? `&wr_pct=${wrPct}` : '';
        const wrTag = wrPct ?? 'all';
        const url = `/api/realm/${encodeURIComponent(realm)}/ships?tier=${tier}&type=${encodeURIComponent(type as string)}${wrParam}`;
        // Poll cadence for a pending percentile bucket: ~3s × 16 ≈ 48s, comfortably
        // over the heaviest observed cold compute (~28s) plus warm-queue latency.
        const POLL_MS = 3000;
        const MAX_POLLS = 16;
        let polls = 0;
        let timer: ReturnType<typeof setTimeout> | undefined;

        const run = () => {
            fetchSharedJson<ShipsByTierType>(url, {
                label: `ShipsByTierType:${realm}:${tier}:${type}:${wrTag}`,
                ttlMs: wrPct ? 0 : LIST_FETCH_TTL_MS,
                cacheKey: wrPct ? undefined : `ships-by:${realm}:${tier}:${type}:${wrTag}`,
            })
                .then(({ data }) => {
                    if (reqId !== listReqId.current) return;
                    if (data.pending && polls < MAX_POLLS) {
                        polls += 1;
                        setListPending(true);
                        timer = setTimeout(run, POLL_MS);
                        return;
                    }
                    setList(data.ships ?? []);
                    setListTotalBattles(data.total_battles ?? 0);
                    setListWindow({ start: data.window_start, end: data.window_end });
                    setListBucketKey(`${tier}|${type}|${wrPct}`);
                    setListPending(false);
                    setListLoading(false);
                })
                .catch(() => {
                    if (reqId !== listReqId.current) return;
                    setListError(true);
                    setListPending(false);
                    setListLoading(false);
                });
        };
        run();
        return () => {
            if (timer) clearTimeout(timer);
        };
    }, [realm, tier, type, wrPct, bothSelected, selectedShip, isEasterEgg, prefsRestored]);

    // Ship board fetch (drill-down) — reuses the existing /ship leaderboard.
    const boardReqId = useRef(0);
    useEffect(() => {
        if (!selectedShip) return;
        const reqId = ++boardReqId.current;
        setBoardLoading(true);
        setBoardError(false);
        setBoard(null);
        fetchSharedJson<ShipLeaderboardPayload>(
            `/api/realm/${encodeURIComponent(realm)}/ship/${selectedShip.id}/leaderboard`,
            {
                label: `ShipLeaderboard:${realm}:${selectedShip.id}`,
                ttlMs: BOARD_FETCH_TTL_MS,
                cacheKey: `ship-lb:${realm}:${selectedShip.id}`,
            },
        )
            .then(({ data }) => {
                if (reqId !== boardReqId.current) return;
                setBoard(data);
                setBoardLoading(false);
            })
            .catch(() => {
                if (reqId !== boardReqId.current) return;
                setBoardError(true);
                setBoardLoading(false);
            });
    }, [realm, selectedShip]);

    const openShip = (s: ListShip) => {
        setSelectedShip({ id: s.ship_id, name: s.ship_name });
        trackEvent('ship-leaderboard-drilldown', { realm, ship_id: s.ship_id, source: 'row' });
    };
    const clearShip = () => {
        setSelectedShip(null);
        trackEvent('ship-leaderboard-clear', { realm });
    };

    // Imperative drill-down from the landing treemap: set tier+type+ship in one
    // batched update (so the dormant list effect never fires for the new bucket)
    // and scroll the board into view. tier/type are set directly rather than via
    // chooseTier/chooseType so a later Clear lands on the right tier/type list.
    useImperativeHandle(ref, () => ({
        selectShip({ id, name, tier: t, type: ty }) {
            setTier(t);
            setType(ty);
            setSelectedShip({ id, name });
            trackEvent('ship-leaderboard-drilldown', { realm, ship_id: id, source: 'treemap' });
            sectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        },
    }), [realm]);

    // Emit the resolved bucket upward (→ PlayerSearch → the landing treemap) on
    // every filter change / load transition, so the treemap renders the SAME
    // tier+type (+ WR-percentile) selection off this fetch — no second request.
    // Keyed on state, not on onBucket's identity (mirrored in a ref above).
    useEffect(() => {
        // `list` lags one render behind a filter switch (the refetch runs in an
        // effect after this commit), so it may still hold the previous bucket.
        // Flag that as stale so the treemap dims the old map and waits, never
        // painting the prior bucket's ships under the new heading.
        const stale = !isEasterEgg && listBucketKey !== `${tier}|${type}|${wrPct}`;
        const resolvedOnce = list !== null || listError;
        const loading = listLoading || stale || (!resolvedOnce && !isEasterEgg);
        onBucketRef.current?.({
            tier,
            type,
            wrPct,
            ships: isEasterEgg ? [] : (list ?? []),
            totalBattles: listTotalBattles,
            windowStart: listWindow.start,
            windowEnd: listWindow.end,
            loading,
            pending: listPending,
            empty: isEasterEgg
                || (!stale && resolvedOnce && !listLoading && !listPending && (list?.length ?? 0) === 0),
        });
    }, [tier, type, wrPct, list, listLoading, listPending, listTotalBattles, listWindow, listBucketKey, isEasterEgg, listError]);

    const typeLabel = useMemo(() => (type ? shipClass(type)?.label ?? type : null), [type]);

    return (
        <section ref={sectionRef} className="mt-2 pt-8" aria-label="Ship leaderboard">
            {/* Filter bar + results share one centered column, narrower than the
                treemap above and sized to the filter bar's fixed control row, so
                the table lines up under the filter bar rather than stretching wide. */}
            <div className="mx-auto max-w-[830px]">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                <h3 className={HEADING_CLASS}>Ships</h3>
                <div className="flex flex-wrap items-center gap-2">
                    <span className="text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]">Tier</span>
                    {TIERS.map((t) => (
                        <button
                            key={t}
                            type="button"
                            onClick={() => chooseTier(t)}
                            className={`${PILL_BASE} ${tier === t ? PILL_ON : PILL_OFF}`}
                            aria-pressed={tier === t}
                        >
                            {t}
                        </button>
                    ))}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    <span className="text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]">Type</span>
                    {SHIP_TYPES.map((t) => {
                        const cls = shipClass(t);
                        return (
                            <button
                                key={t}
                                type="button"
                                onClick={() => chooseType(t)}
                                className={`${PILL_BASE} ${type === t ? PILL_ON : PILL_OFF}`}
                                aria-pressed={type === t}
                                title={cls?.label ?? t}
                            >
                                {cls?.abbr ?? t}
                            </button>
                        );
                    })}
                </div>
                {/* WR-percentile group sits to the right of SS — list-only, so it is
                    hidden while a ship board is open (it would not apply there). */}
                <div className="flex flex-wrap items-center gap-2">
                    {!selectedShip && (
                        <>
                            <span className="text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]">WR&nbsp;&ge;</span>
                            {WR_PCTS.map(({ value, label }) => (
                                <button
                                    key={label}
                                    type="button"
                                    onClick={() => chooseWrPct(value)}
                                    className={`${PILL_BASE} ${wrPct === value ? PILL_ON : PILL_OFF}`}
                                    aria-pressed={wrPct === value}
                                    title={
                                        value === null
                                            ? 'All players'
                                            : `Top ${value}% of players by win rate`
                                    }
                                >
                                    {label}
                                </button>
                            ))}
                        </>
                    )}
                    {/* Data-basis hint sits at the end of the row. */}
                    <InfoHint text={DATA_BASIS_HINT} />
                </div>
            </div>

            <div className="mt-4">
                {!bothSelected ? (
                    <p className="py-6 text-sm text-[var(--text-muted)]">
                        Pick a tier and a type to rank ships by win rate.
                    </p>
                ) : isSubEasterEgg ? (
                    <SubmarineEasterEgg />
                ) : isCarrierEasterEgg ? (
                    <CarrierEasterEgg />
                ) : selectedShip ? (
                    <ShipBoard
                        realm={realm}
                        fallbackName={selectedShip.name}
                        board={board}
                        loading={boardLoading}
                        error={boardError}
                        onClear={clearShip}
                        onSortChange={trackSort('players')}
                    />
                ) : (
                    <ShipList
                        ships={list}
                        totalBattles={listTotalBattles}
                        loading={listLoading}
                        error={listError}
                        pending={listPending}
                        wrPct={wrPct}
                        tierTypeLabel={`T${tier} ${typeLabel ?? ''}`.trim()}
                        onOpen={openShip}
                        onSortChange={trackSort('ships')}
                    />
                )}
            </div>
            </div>
        </section>
    );
});

ShipLeaderboard.displayName = 'ShipLeaderboard';

const SHIP_NAME_LINK =
    'text-left font-medium text-[var(--accent-mid)] transition-colors hover:text-[var(--accent-dark)] hover:underline';
const PLAYER_LINK =
    'rounded-sm text-[var(--accent-mid)] hover:underline focus-visible:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-mid)] focus-visible:ring-offset-1';

const ShipList: React.FC<{
    ships: ListShip[] | null;
    totalBattles: number;
    loading: boolean;
    error: boolean;
    pending: boolean;
    wrPct: WrPct;
    tierTypeLabel: string;
    onOpen: (s: ListShip) => void;
    onSortChange: (key: keyof ListShip, dir: SortDir) => void;
}> = ({ ships, totalBattles, loading, error, pending, wrPct, tierTypeLabel, onOpen, onSortChange }) => {
    const { sort, onSort } = useTableSort<ListShip>(['ship_name'], onSortChange, SHIP_LIST_SORT_STORAGE_KEY);
    const sortedShips = useMemo(
        () => (ships && sort ? sortRows(ships, sort.key, sort.dir) : ships),
        [ships, sort],
    );

    // A cold percentile bucket is being computed server-side — show a distinct
    // one-time "crunching" message rather than the stale all-population list.
    if (pending) {
        return (
            <p className="py-6 text-sm text-[var(--text-muted)]">
                Crunching stats for the top {wrPct}% of each ship’s players… this can take
                a few seconds the first time, then it’s instant.
            </p>
        );
    }
    if (loading && !ships) {
        return <p className="py-6 text-sm text-[var(--text-muted)]">Loading ships…</p>;
    }
    if (error) {
        return <p className="py-6 text-sm text-[var(--text-muted)]">Couldn’t load ships. Try another filter.</p>;
    }
    if (!sortedShips || sortedShips.length === 0) {
        return <p className="py-6 text-sm text-[var(--text-muted)]">No ranked ships for {tierTypeLabel}.</p>;
    }
    const colSort = (key: keyof ListShip) => ({
        active: sort?.key === key,
        dir: (sort?.key === key ? sort.dir : 'desc') as SortDir,
        onClick: () => onSort(key),
    });
    return (
        <>
            {wrPct && (
                <p className="mb-2 text-xs text-[var(--text-muted)]">
                    Showing stats for the <span className="font-semibold text-[var(--accent-mid)]">top {wrPct}%</span> of
                    each ship’s players by win rate.
                </p>
            )}
            {/* Desktop: dense table, win rate the only color, ship name the action.
                Viewport caps to ~15 rows; the rest scroll under a sticky header. */}
            <div className="hidden max-h-[580px] overflow-y-auto sm:block">
            <table className="w-full text-sm">
                <thead className="sticky top-0 z-10 bg-[var(--bg-page)]">
                    <tr className="border-b border-[var(--border)] text-left text-xs uppercase tracking-wide text-[var(--text-muted)]">
                        <th className="py-2 pl-2 pr-8" aria-sort={ariaSort(sort?.key === 'ship_name', colSort('ship_name').dir)}>
                            <SortButton label="Ship" {...colSort('ship_name')} />
                        </th>
                        <th className="py-2 pr-8 text-right" aria-sort={ariaSort(sort?.key === 'battles', colSort('battles').dir)}>
                            <SortButton label="Battles" align="right" {...colSort('battles')} />
                        </th>
                        <th className="py-2 pr-8 text-right" aria-sort={ariaSort(sort?.key === 'avg_damage', colSort('avg_damage').dir)}>
                            <SortButton label="Avg dmg" align="right" {...colSort('avg_damage')} />
                        </th>
                        <th className="py-2 pr-8 text-right" aria-sort={ariaSort(sort?.key === 'kills_per_battle', colSort('kills_per_battle').dir)}>
                            <SortButton label="Kills/battle" align="right" {...colSort('kills_per_battle')} />
                        </th>
                        <th className="py-2 text-right" aria-sort={ariaSort(sort?.key === 'win_rate', colSort('win_rate').dir)}>
                            <SortButton label="Win rate" align="right" {...colSort('win_rate')} />
                        </th>
                    </tr>
                </thead>
                <tbody>
                    {sortedShips.map((s) => (
                        <tr key={s.ship_id} className="transition-colors hover:bg-[var(--bg-hover)]">
                            <td className="py-2 pl-2 pr-8">
                                <button type="button" className={`${SHIP_NAME_LINK} inline-flex items-center gap-2`} onClick={() => onOpen(s)}>
                                    <NationFlag nation={s.nation} />
                                    {s.ship_name}
                                </button>
                            </td>
                            <td className="py-2 pr-8 text-right tabular-nums text-[var(--text-primary)]">
                                {s.battles.toLocaleString()}
                                {classSharePct(s.battles, totalBattles) && (
                                    <span className="ml-1 text-[var(--text-muted)]">({classSharePct(s.battles, totalBattles)})</span>
                                )}
                            </td>
                            <td className="py-2 pr-8 text-right tabular-nums text-[var(--text-primary)]">{s.avg_damage.toLocaleString()}</td>
                            <td className="py-2 pr-8 text-right tabular-nums text-[var(--text-muted)]">{s.kills_per_battle.toFixed(2)}</td>
                            <td className="py-2 text-right tabular-nums font-semibold" style={{ color: wrColor(s.win_rate) }}>
                                {s.win_rate.toFixed(1)}%
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>
            </div>

            {/* Mobile: stacked cards — ship + win rate primary, the rest secondary.
                Capped height with scroll, mirroring the desktop viewport. */}
            <ul className="max-h-[560px] max-w-[900px] space-y-2 overflow-y-auto sm:hidden">
                {sortedShips.map((s) => (
                    <li key={s.ship_id} className="rounded-md border border-[var(--border)] bg-[var(--bg-surface)] p-3">
                        <div className="flex items-center justify-between gap-2">
                            <button type="button" className={`${SHIP_NAME_LINK} inline-flex min-w-0 items-center gap-2`} onClick={() => onOpen(s)}>
                                <NationFlag nation={s.nation} />
                                <span className="truncate">{s.ship_name}</span>
                            </button>
                            <span className="shrink-0 tabular-nums font-semibold" style={{ color: wrColor(s.win_rate) }}>
                                {s.win_rate.toFixed(1)}%
                            </span>
                        </div>
                        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs tabular-nums text-[var(--text-muted)]">
                            <span>
                                <span className="text-[var(--text-primary)]">{s.battles.toLocaleString()}</span> battles
                                {classSharePct(s.battles, totalBattles) && ` (${classSharePct(s.battles, totalBattles)})`}
                            </span>
                            <span><span className="text-[var(--text-primary)]">{s.avg_damage.toLocaleString()}</span> avg dmg</span>
                            <span>{s.kills_per_battle.toFixed(2)} kills/battle</span>
                        </div>
                    </li>
                ))}
            </ul>
        </>
    );
};

const ShipBoard: React.FC<{
    realm: string;
    fallbackName: string;
    board: ShipLeaderboardPayload | null;
    loading: boolean;
    error: boolean;
    onClear: () => void;
    onSortChange: (key: keyof LeaderboardPlayer, dir: SortDir) => void;
}> = ({ realm, fallbackName, board, loading, error, onClear, onSortChange }) => {
    const ship = board?.ship;
    const players = useMemo(() => board?.players ?? [], [board]);
    // Top-3 medal, mirroring the full /ship page (ShipRouteView): the same
    // gold/silver/bronze TopShipIcon next to the player name. The drill-down shows
    // the identical leaderboard, so the podium treatment stays visually identical.
    const shipName = ship?.name ?? fallbackName;
    const medal = (rank: number) =>
        rank <= 3 ? <TopShipIcon rank={rank} shipName={shipName} tier={ship?.tier} realm={realm} size="podium" /> : null;
    const { sort, onSort } = useTableSort<LeaderboardPlayer>(['player_name'], onSortChange);
    // Player click-through analytics. ship_id + rank are low-cardinality and
    // tell us which standings drive profile visits; player name is omitted to
    // keep event-data cardinality low (Umami convention).
    const trackPlayerClick = (rank: number) =>
        trackEvent('ship-leaderboard-player-click', {
            realm,
            ship_id: ship?.ship_id ?? 0,
            rank,
        });
    const sortedPlayers = useMemo(
        () => (sort ? sortRows(players, sort.key, sort.dir) : players),
        [players, sort],
    );
    const colSort = (key: keyof LeaderboardPlayer) => ({
        active: sort?.key === key,
        dir: (sort?.key === key ? sort.dir : 'desc') as SortDir,
        onClick: () => onSort(key),
    });
    return (
        <>
            <div className="flex flex-wrap items-center gap-3">
                <button
                    type="button"
                    onClick={onClear}
                    className="inline-flex items-center rounded-md border border-[var(--border)] bg-[var(--bg-page)] px-2.5 py-1 text-xs font-semibold uppercase tracking-wide text-[var(--accent-mid)] transition-colors hover:bg-[var(--accent-faint)]"
                >
                    ‹ Clear
                </button>
                <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-[var(--text-strong)]">
                    {ship?.name ?? fallbackName}
                    <ShipToolLink
                        code={ship?.shiptool_code}
                        shipName={ship?.name ?? fallbackName}
                        realm={realm}
                        shipId={ship?.ship_id}
                    />
                </span>
            </div>

            <div className="mt-3">
                {loading ? (
                    <p className="py-6 text-sm text-[var(--text-muted)]">Loading leaderboard…</p>
                ) : error ? (
                    <p className="py-6 text-sm text-[var(--text-muted)]">Couldn’t load this ship’s leaderboard.</p>
                ) : sortedPlayers.length === 0 ? (
                    <p className="py-6 text-sm text-[var(--text-muted)]">No ranked players for this ship yet.</p>
                ) : (
                    <>
                        <div className="hidden max-h-[580px] max-w-[900px] overflow-y-auto sm:block">
                        <table className="w-full text-sm">
                            <thead className="sticky top-0 z-10 bg-[var(--bg-page)]">
                                <tr className="border-b border-[var(--border)] text-left text-xs uppercase tracking-wide text-[var(--text-muted)]">
                                    <th className="py-2 pl-2 pr-3" aria-sort={ariaSort(sort?.key === 'rank', colSort('rank').dir)}>
                                        <SortButton label="#" {...colSort('rank')} />
                                    </th>
                                    <th className="py-2 pr-8" aria-sort={ariaSort(sort?.key === 'player_name', colSort('player_name').dir)}>
                                        <SortButton label="Player" {...colSort('player_name')} />
                                    </th>
                                    <th className="py-2 pr-8 text-right" aria-sort={ariaSort(sort?.key === 'win_rate', colSort('win_rate').dir)}>
                                        <SortButton label="Win rate" align="right" {...colSort('win_rate')} />
                                    </th>
                                    <th className="py-2 pr-8 text-right" aria-sort={ariaSort(sort?.key === 'battles', colSort('battles').dir)}>
                                        <SortButton label="Battles" align="right" {...colSort('battles')} />
                                    </th>
                                    <th className="py-2 pr-8 text-right" aria-sort={ariaSort(sort?.key === 'avg_damage', colSort('avg_damage').dir)}>
                                        <SortButton label="Avg dmg" align="right" {...colSort('avg_damage')} />
                                    </th>
                                    <th className="py-2 text-right" aria-sort={ariaSort(sort?.key === 'kills_per_battle', colSort('kills_per_battle').dir)}>
                                        <SortButton label="Kills/battle" align="right" {...colSort('kills_per_battle')} />
                                    </th>
                                </tr>
                            </thead>
                            <tbody>
                                {sortedPlayers.map((p) => (
                                    <tr key={p.rank} className="transition-colors hover:bg-[var(--bg-hover)]">
                                        <td className="py-2 pl-2 pr-3 tabular-nums text-[var(--text-muted)]">{p.rank}</td>
                                        <td className="py-2 pr-8">
                                            <span className="inline-flex items-center gap-2">
                                                <Link href={buildPlayerPath(p.player_name, realm)} className={PLAYER_LINK} onClick={() => trackPlayerClick(p.rank)}>
                                                    {p.player_name}
                                                </Link>
                                                {medal(p.rank)}
                                            </span>
                                        </td>
                                        <td className="py-2 pr-8 text-right tabular-nums font-semibold" style={{ color: wrColor(p.win_rate) }}>{p.win_rate.toFixed(1)}%</td>
                                        <td className="py-2 pr-8 text-right tabular-nums text-[var(--text-primary)]">{p.battles.toLocaleString()}</td>
                                        <td className="py-2 pr-8 text-right tabular-nums text-[var(--text-primary)]">{p.avg_damage.toLocaleString()}</td>
                                        <td className="py-2 text-right tabular-nums text-[var(--text-muted)]">{p.kills_per_battle.toFixed(2)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                        </div>

                        <ul className="max-h-[560px] max-w-[900px] space-y-2 overflow-y-auto sm:hidden">
                            {sortedPlayers.map((p) => (
                                <li key={p.rank} className="rounded-md border border-[var(--border)] bg-[var(--bg-surface)] p-3">
                                    <div className="flex items-center justify-between gap-2">
                                        <span className="inline-flex min-w-0 items-center gap-2">
                                            <span className="w-5 shrink-0 text-right tabular-nums text-[var(--text-muted)]">{p.rank}</span>
                                            <Link href={buildPlayerPath(p.player_name, realm)} className={`${PLAYER_LINK} truncate`} onClick={() => trackPlayerClick(p.rank)}>
                                                {p.player_name}
                                            </Link>
                                            {medal(p.rank)}
                                        </span>
                                        <span className="shrink-0 tabular-nums font-semibold" style={{ color: wrColor(p.win_rate) }}>{p.win_rate.toFixed(1)}%</span>
                                    </div>
                                    <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs tabular-nums text-[var(--text-muted)]">
                                        <span><span className="text-[var(--text-primary)]">{p.battles.toLocaleString()}</span> battles</span>
                                        <span><span className="text-[var(--text-primary)]">{p.avg_damage.toLocaleString()}</span> avg dmg</span>
                                        <span>{p.kills_per_battle.toFixed(2)} kills/battle</span>
                                    </div>
                                </li>
                            ))}
                        </ul>
                    </>
                )}
            </div>
        </>
    );
};

export default ShipLeaderboard;
