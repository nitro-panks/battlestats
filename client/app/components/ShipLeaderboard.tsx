'use client';

// Inline ship leaderboard — the filterable ship explorer under the landing
// treemap.
//
// Pick a TIER (8/9/10/11 — the tiers we compute ship data for) and a TYPE
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
import { useT } from '../context/LocaleContext';
import { shipClass } from '../lib/shipIdentity';
import NationFlag from './NationFlag';
import ShipToolLink from './ShipToolLink';
import TopShipIcon from './TopShipIcon';
import {
    buildPlayerPath,
    buildShipBucketPath,
    buildShipPath,
    isShiplessBucket,
    SHIP_BUCKET_TIERS,
    SHIP_TYPES,
    type ShipType,
    type Tier,
    type WrPct,
} from '../lib/entityRoutes';
import { sortRows, type SortDir } from '../lib/tableSort';
import CopyLinkButton from './CopyLinkButton';
import { trackEvent } from '../lib/umami';
import wrColor from '../lib/wrColor';
import SubmarineEasterEgg from './SubmarineEasterEgg';
import CarrierEasterEgg from './CarrierEasterEgg';

// The tier/type vocabulary and its URL encoding live in lib/entityRoutes, so
// the shareable /ships/<bucket> route and this component cannot disagree about
// what a bucket is. Re-exported here because RealmTopShipsTreemapSVG and the
// tests import these names from this module.
export type { Tier, ShipType, WrPct } from '../lib/entityRoutes';
export { SHIP_TYPES };
const TIERS: readonly Tier[] = SHIP_BUCKET_TIERS;

// Win-rate-percentile filter for the ship LIST: narrows each ship's displayed
// stats (battles, avg dmg, kills/battle, WR) to the top N% of that ship's
// players by win rate — answering "how are good/great players doing with these
// ships?". `null` is the default realm-wide aggregate. Must match the backend's
// SHIP_LIST_WR_PCTS (50/25). The backend's membership gate is the same in both
// paths (full-population battles >= SHIP_LIST_MIN_BATTLES), so switching pills
// is not *meant* to change which ships are listed — but the all-view and the pct
// buckets are warmed by different tasks into separate cache keys, so a pill can
// be serving an older window and thus a genuinely different ship set. See the
// note on dataBasisHint; the tooltip promises nothing about ship membership.
// (WrPct itself is declared in lib/entityRoutes and re-exported above.)
// `label` for the 50/25 pills is a plain percent literal (no translation
// case — digits + "%" read the same in every locale); `null` ("All") is
// wired through common.all at render time (fix round 1, F2) since "All" now
// renders translated in EfficiencyBadgeTable's filter bar and would
// otherwise be the one pill left English in the same release.
const WR_PCTS: { value: WrPct; label: string | null }[] = [
    { value: null, label: null },
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
// is true for every bucket the game has no hulls for (T9 sub/CV, T11 sub) and
// for any resolved-but-shipless bucket, distinct from a still-loading one.
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

/**
 * Seed state for the shareable /ships/<bucket> route. When present the URL is
 * the WHOLE truth: localStorage is neither read nor written, so a recipient's
 * remembered bucket and column can never override what the sharer sent. Absent
 * (the landing page), the component keeps its localStorage behaviour unchanged.
 */
export interface ShipLeaderboardInitialView {
    tier: Tier;
    type: ShipType;
    wrPct: WrPct;
    sort: { key: keyof ListShip; dir: SortDir } | null;
}

interface ShipLeaderboardProps {
    onBucket?: (bucket: ShipBucket) => void;
    initial?: ShipLeaderboardInitialView;
    /** Mirror filter changes into the address bar (the /ships route only). */
    syncUrl?: boolean;
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

// Section header, matching the treemap's <h2> directly above (same size/weight/
// tracking, muted rather than accent) so the two landing sections read as a pair.
const HEADING_CLASS =
    'text-sm font-semibold uppercase tracking-wide text-[var(--text-muted)]';
// Compact enough that the filter row (Tier ×3 · Type ×5 · WR ≥ ×3) fits on one
// line inside the 850px site column's content box. The section title and its
// info hint sit on their own line above, so they cost the row nothing.
const PILL_BASE =
    'inline-flex items-center rounded-md border px-2 py-1.5 text-xs font-semibold uppercase tracking-wide transition-colors';
const PILL_ON = 'border-[var(--accent-mid)] bg-[var(--accent-mid)] text-white';
const PILL_OFF =
    'border-[var(--border)] bg-[var(--bg-page)] text-[var(--accent-mid)] hover:bg-[var(--accent-faint)]';

// Every column is click-sortable. Sort lives client-side over the already-fetched
// rows; until a header is clicked the server's natural order (win rate for the
// list, rank for the board) is preserved (`sort === null`). New numeric columns
// open descending (best-first); text columns open ascending (A→Z).
// sortRows/SortDir moved to lib/tableSort so the Open Graph card ranks the top 3
// with the exact comparator the table the user shared was using.

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
    // A sort carried in a shared URL. Present, it seeds the table AND suppresses
    // the persisted restore below — otherwise the recipient's remembered column
    // would land a beat later and quietly re-sort the shared view.
    seed?: { key: keyof T; dir: SortDir } | null,
    seeded = false,
) {
    const [sort, setSort] = useState<{ key: keyof T; dir: SortDir } | null>(seed ?? null);

    useEffect(() => {
        if (seeded || !storageKey || typeof window === 'undefined') return;
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
    }, [storageKey, seeded]);

    const onSort = (key: keyof T) => {
        // Compute the next sort from the current render's value (not inside the
        // setState updater) so analytics fire exactly once, never doubled.
        const next: { key: keyof T; dir: SortDir } =
            sort && sort.key === key
                ? { key, dir: sort.dir === 'asc' ? 'desc' : 'asc' }
                : { key, dir: textKeys.includes(key) ? 'asc' : 'desc' };
        setSort(next);
        onChange?.(next.key, next.dir);
        // A seeded table is showing someone else's shared view; do not let it
        // overwrite the visitor's own remembered column.
        if (!seeded && storageKey && typeof window !== 'undefined') {
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

// The listing floor is the backend's SHIP_LIST_MIN_BATTLES (server/warships/
// data.py, default 50; verified 2026-08-12 to have no override in the droplet's
// /etc/battlestats-server.env, so 50 is the live value). It is hardcoded rather
// than read from the payload deliberately: the all-view and each pct bucket
// serve durable `:published` copies that can be weeks old, so a newly added
// payload field would leave this sentence blank on the live site until every
// cached bucket rotated.
const MIN_BATTLES_TO_LIST = 50;

// Derives the window length from the served payload's date bounds so the copy
// always matches the actual standings window (30/45/60/90 as
// SHIP_LEADERBOARD_WINDOW_DAYS advances) rather than a hardcoded number.
//
// Deliberately makes NO claim that the listed ship set is identical across the
// WR pills. The backend gates membership on full-population battles in both the
// all-path and the percentile path, but the two are warmed by different tasks
// and cached under separate keys, so in practice they can be serving different
// windows and therefore different ship lists (observed 2026-08-12: NA T10
// Destroyer served All from a 2026-07-25 window and its 50/25 buckets from
// 2026-08-12 — Fuyutsuki listed in the pct buckets, absent from All, because
// warm_realm_top_ships_task had been dying on its 540s soft time limit and only
// the pct half of the chain was still landing). Describe what the filter does;
// do not promise what the cache cannot honor.
const dataBasisHint = (windowDays: number | null): string =>
    `Stats are aggregated from battle observations recorded during the ${
        windowDays ? `rolling trailing ${windowDays}-day window` : 'rolling standings window'
    }. A ship needs at least ${MIN_BATTLES_TO_LIST} battles in that window to be listed. The WR filter narrows each ship’s stats to its top 50% or 25% of players by win rate.`;

// Info affordance with a hover/focus tooltip — styled to match the circle-info
// buttons in the Players/Clans landing sections below (FontAwesomeIcon + the
// same accent-light icon button + `hidden group-hover:block` tooltip). Reveal is
// CSS-only so it works without JS state. Deliberately NOT `relative`: the panel
// resolves its `absolute left-0 top-full` against the header block instead, so
// it always drops under the header flush with the column's left edge. Anchoring
// it to the icon would trail the icon wherever the title's last line ends and
// push the panel off-screen once the title wraps on a phone.
const InfoHint: React.FC<{ text: string }> = ({ text }) => (
    <span className="group inline-flex items-center align-middle">
        <button
            type="button"
            aria-label={text}
            className="inline-flex h-5 w-5 items-center justify-center rounded-full text-[10px] text-[var(--accent-light)] transition-colors hover:text-[var(--accent-mid)] focus:outline-none focus-visible:text-[var(--accent-mid)]"
        >
            <FontAwesomeIcon icon={faCircleInfo} className="text-[10px]" aria-hidden="true" />
        </button>
        <span
            role="tooltip"
            className="pointer-events-none absolute left-0 top-full z-20 mt-2 hidden w-60 max-w-[calc(100vw-2rem)] whitespace-normal rounded-md border border-[var(--border)] bg-[var(--bg-page)] px-3 py-2 text-left text-xs normal-case leading-snug tracking-normal text-[var(--text-primary)] shadow-lg group-hover:block group-focus-within:block"
        >
            {text}
        </span>
    </span>
);

const ShipLeaderboard = forwardRef<ShipLeaderboardHandle, ShipLeaderboardProps>(({ onBucket, initial, syncUrl = false }, ref) => {
    const { realm } = useRealm();
    const t = useT();
    const sectionRef = useRef<HTMLElement>(null);
    // Mirror onBucket into a ref so the emit effect doesn't depend on the parent
    // passing a stable callback identity (it re-emits on real state change only).
    const onBucketRef = useRef(onBucket);
    useEffect(() => { onBucketRef.current = onBucket; });

    // Land on T10 Battleships so the board shows real standings immediately
    // (these buckets are pre-warmed daily — see warm_realm_top_ships_task).
    const [tier, setTier] = useState<Tier | null>(initial?.tier ?? 10);
    const [type, setType] = useState<ShipType | null>(initial?.type ?? 'Battleship');
    // WR-percentile filter applies to the ship LIST only (not the drilled-in
    // player board). Defaults to the top 50% ("how are good players doing with
    // these ships?"); `null` is the all-players view. The default landing bucket
    // is pre-warmed (warm_realm_top_ships_task) so this view loads instantly.
    const [wrPct, setWrPct] = useState<WrPct>(initial ? initial.wrPct : 50);
    const [selectedShip, setSelectedShip] = useState<{ id: number; name: string } | null>(null);

    // Restore the persisted tier/type/WR on mount (post-SSR, so the first client
    // render still matches the server's default markup — no hydration mismatch).
    // The list fetch is gated on this so it fires once, with the restored bucket,
    // instead of flashing the default bucket first.
    // `initial` (a shared /ships link) outranks the stored preference. Without
    // this the recipient of "here are the T9 destroyers" opens the link and sees
    // their own last bucket instead — the feature failing silently, for exactly
    // the people it was used on. Same precedence shape as the locale rule
    // (`?lang=` > `bs-locale` > autodetect).
    const [prefsRestored, setPrefsRestored] = useState(false);
    const hasInitialView = initial !== undefined;
    useEffect(() => {
        if (hasInitialView) {
            setPrefsRestored(true);
            return;
        }
        const stored = readStoredShipLbPrefs();
        if (stored) {
            if (stored.tier !== undefined) setTier(stored.tier);
            if (stored.type !== undefined) setType(stored.type);
            if (stored.wrPct !== undefined) setWrPct(stored.wrPct);
        }
        setPrefsRestored(true);
    }, [hasInitialView]);

    // Persist the selection on every change (once restore has run, so the initial
    // default render never overwrites a stored preference). Treemap drill-downs
    // also set tier/type, so they persist too — which matches user intent.
    useEffect(() => {
        // A shared link is someone else's view; writing it back would silently
        // rewrite the visitor's own remembered bucket just for opening a link.
        if (hasInitialView || !prefsRestored || tier == null || type == null) return;
        try {
            window.localStorage.setItem(SHIP_LB_PREFS_KEY, JSON.stringify({ tier, type, wrPct }));
        } catch {
            // localStorage unavailable
        }
    }, [hasInitialView, prefsRestored, tier, type, wrPct]);

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

    // The tables own their sort, but a Share link has to reproduce what is on
    // screen, so the live column is mirrored up here. Seeded from the URL on the
    // /ships route; null means the server's natural order.
    const [listSort, setListSort] = useState<{ key: keyof ListShip; dir: SortDir } | null>(
        initial?.sort ?? null,
    );
    const [boardSort, setBoardSort] = useState<{ key: keyof LeaderboardPlayer; dir: SortDir } | null>(null);

    const onListSort = (key: keyof ListShip, dir: SortDir) => {
        setListSort({ key, dir });
        trackSort('ships')(String(key), dir);
    };
    const onBoardSort = (key: keyof LeaderboardPlayer, dir: SortDir) => {
        setBoardSort({ key, dir });
        trackSort('players')(String(key), dir);
    };

    const bothSelected = tier != null && type != null;
    // Some buckets have no hulls in the game at all (`isShiplessBucket`), so no
    // fetch may be issued for them: the endpoint would 400 in any env whose
    // SHIP_BADGE_TIERS excludes the tier (e.g. local dev) and is pointless in
    // prod. Two of them — the T9 submarine and the T9 carrier — have their own
    // easter eggs; the rest (T11 submarines) get a plain explanation. Gate the
    // fetch effect on `isShipless`, the render on the specific predicate.
    const isSubEasterEgg = tier === 9 && type === 'Submarine';
    const isCarrierEasterEgg = tier === 9 && type === 'AirCarrier';
    const isEasterEgg = isSubEasterEgg || isCarrierEasterEgg;
    const isShipless = bothSelected && isShiplessBucket(tier as Tier, type as ShipType);
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
        if (!prefsRestored || !bothSelected || selectedShip || isShipless) return;
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
    }, [realm, tier, type, wrPct, bothSelected, selectedShip, isShipless, prefsRestored]);

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
        const stale = !isShipless && listBucketKey !== `${tier}|${type}|${wrPct}`;
        const resolvedOnce = list !== null || listError;
        const loading = listLoading || stale || (!resolvedOnce && !isShipless);
        onBucketRef.current?.({
            tier,
            type,
            wrPct,
            ships: isShipless ? [] : (list ?? []),
            totalBattles: listTotalBattles,
            windowStart: listWindow.start,
            windowEnd: listWindow.end,
            loading,
            pending: listPending,
            empty: isShipless
                || (!stale && resolvedOnce && !listLoading && !listPending && (list?.length ?? 0) === 0),
        });
    }, [tier, type, wrPct, list, listLoading, listPending, listTotalBattles, listWindow, listBucketKey, isShipless, listError]);

    const typeLabel = useMemo(() => (type ? shipClass(type)?.label ?? type : null), [type]);

    // The shareable address for the bucket currently on screen. The landing page
    // keeps its own URL, so this is built from live state rather than read from
    // the address bar. Null for buckets with no hulls in the game, which have
    // nothing to show a recipient.
    const listShareUrl = useMemo(() => {
        if (tier == null || type == null || isShipless) return null;
        return buildShipBucketPath({
            tier,
            type,
            realm,
            wrPct,
            sort: listSort ? String(listSort.key) : null,
            dir: listSort?.dir ?? null,
        });
    }, [tier, type, realm, wrPct, listSort, isShipless]);

    // The drill-down already has a real route of its own; sharing it just points
    // at /ship/<id>-<slug> with the sort the sharer was looking at.
    const boardShareUrl = useMemo(() => {
        if (!selectedShip) return null;
        const base = buildShipPath(selectedShip.id, selectedShip.name, realm);
        if (!boardSort) return base;
        return `${base}&sort=${encodeURIComponent(String(boardSort.key))}&dir=${boardSort.dir}`;
    }, [selectedShip, realm, boardSort]);

    // Which link the one Share button copies, decided by which view is showing.
    const shareUrl = selectedShip ? boardShareUrl : listShareUrl;

    // On /ships/<bucket> the address bar is the view, so keep the two in step as
    // pills are clicked. replace() rather than push() so Back leaves the page
    // instead of walking every filter click. Never runs on the landing page.
    useEffect(() => {
        if (!syncUrl || !listShareUrl || typeof window === 'undefined') return;
        const current = `${window.location.pathname}${window.location.search}`;
        if (current !== listShareUrl) {
            window.history.replaceState(null, '', listShareUrl);
        }
    }, [syncUrl, listShareUrl]);

    // Window length in days, derived from the served payload's date bounds so the
    // header and the info tooltip always name the actual standings window
    // (30/45/60/90 as SHIP_LEADERBOARD_WINDOW_DAYS advances) rather than a
    // hardcoded number. Null until the first payload resolves — callers drop the
    // window clause entirely rather than render a placeholder.
    const windowDays = useMemo(() => {
        if (!listWindow.start || !listWindow.end) return null;
        const startMs = Date.parse(listWindow.start);
        const endMs = Date.parse(listWindow.end);
        if (Number.isNaN(startMs) || Number.isNaN(endMs)) return null;
        return Math.round((endMs - startMs) / 86_400_000);
    }, [listWindow]);

    // Plain form (no window clause) — the <section>'s aria-label and the
    // fallback heading text share this rather than a second hardcoded
    // "Ship leaderboard" literal.
    const baseHeadingLabel = t('landing.shipLeaderboard.heading', { suffix: '' });
    // The "· last N days rolling" clause used to be an English literal built
    // right here — the composed-template blocker: translating the outer
    // landing.shipLeaderboard.heading key alone would still have shipped
    // "함선 리더보드 · last 60 days rolling". landing.shipLeaderboard.windowSuffix
    // resolves the clause through t() first, so the whole heading is one
    // translated sentence in any locale.
    const headingLabel = windowDays
        ? t('landing.shipLeaderboard.heading', {
            suffix: ` · ${t('landing.shipLeaderboard.windowSuffix', { days: windowDays })}`,
        })
        : baseHeadingLabel;
    // The key now drives BOTH the visible text and the accessible name — it
    // used to drive only the aria-label below while the JSX rendered
    // hardcoded English literals, which would have shipped a translated
    // accessible name over untranslated visible text the moment anyone
    // populated this key in ko/ja. The trailing word ("rolling"/"leaderboard")
    // still gets its own nowrap group so the info-hint icon can never orphan
    // onto its own line (see the comment on that group below); splitting at
    // the LAST SPACE derives that word from the template's own output instead
    // of a second, hand-maintained literal, so the two can't drift again.
    // ko and ja both translate `landing.shipLeaderboard.heading` as of this
    // commit (see app/i18n/ko.ts, ja.ts), so the no-ASCII-space branch below
    // is a LIVE path, not a hypothetical one: whenever windowDays hasn't
    // resolved yet, headingLabel falls back to baseHeadingLabel with an empty
    // suffix, which in Japanese is plain "艦艇リーダーボード" — no ASCII space
    // anywhere, since Japanese does not word-space. lastSpaceIdx is -1 there,
    // and the whole heading renders inside the nowrap span with the icon
    // instead of splitting at a trailing word — correct behaviour, and the
    // reason this branch must not be deleted as unreachable dead code.
    const lastSpaceIdx = headingLabel.lastIndexOf(' ');
    const headingLead = lastSpaceIdx === -1 ? '' : headingLabel.slice(0, lastSpaceIdx + 1);
    const headingLastWord = lastSpaceIdx === -1 ? headingLabel : headingLabel.slice(lastSpaceIdx + 1);

    return (
        <section ref={sectionRef} className="mt-2 pt-8" aria-label={baseHeadingLabel}>
            {/* Filter bar + results fill the site column (layout.tsx owns the width). */}
            <div>
            {/* Section header: title · standings window · info hint. Mirrors the
                treemap header above it (h2 + circle-info affordance on its own
                line) so the two landing sections read as siblings; the filter
                pills get the whole next row to themselves.
                Inline flow rather than flex: the title wraps to two lines on a
                phone, and inline keeps the icon trailing the last word instead
                of stranding it against the right edge. `relative` here is the
                anchor InfoHint's panel drops from. */}
            <div className="relative mb-2">
                {/* The icon lives inside the heading purely for line-breaking
                    (see the nowrap group below); `aria-label` pins the heading's
                    accessible name to the title so the hint's long tooltip text
                    is not announced as part of it. The button keeps its own
                    label and stays independently reachable. */}
                <h2
                    className={`${HEADING_CLASS} inline align-middle`}
                    aria-label={headingLabel}
                >
                    {headingLead}
                    {/* The last word and the icon share a nowrap group so the
                        icon can never orphan onto a line of its own — which it
                        does at the widths where the title exactly fills the
                        column (~375px). */}
                    <span className="whitespace-nowrap">
                        {headingLastWord}
                        <span className="ml-2">
                            <InfoHint text={dataBasisHint(windowDays)} />
                        </span>
                    </span>
                </h2>
            </div>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                <div className="flex flex-wrap items-center gap-2">
                    {/* whitespace-nowrap: CJK has no spaces, so a short label can
                        break mid-character once the flex row squeezes it below
                        its content width (the ThemeToggle chip hit this first —
                        see its comment). 티어/Tier/... are one- or two-character
                        tokens that should never wrap in any language. */}
                    <span className="whitespace-nowrap text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]">{t('common.tier')}</span>
                    {/* Loop var renamed from `t` (fix round 1, F4) — it shadowed
                        the translator `const t = useT()` above. Harmless while
                        nothing inside this callback called t(), but the next
                        person translating a pill's title INSIDE the map would
                        silently get the tier number instead of the function,
                        and it would still typecheck. */}
                    {TIERS.map((tierValue) => (
                        <button
                            key={tierValue}
                            type="button"
                            onClick={() => chooseTier(tierValue)}
                            className={`${PILL_BASE} ${tier === tierValue ? PILL_ON : PILL_OFF}`}
                            aria-pressed={tier === tierValue}
                        >
                            {tierValue}
                        </button>
                    ))}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    {/* whitespace-nowrap: same CJK wrap risk as the Tier label
                        above. */}
                    <span className="whitespace-nowrap text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]">{t('common.type')}</span>
                    {/* Loop var renamed from `t` — same shadowing fix as the
                        Tier pills above (fix round 1, F4). */}
                    {SHIP_TYPES.map((typeValue) => {
                        const cls = shipClass(typeValue);
                        return (
                            <button
                                key={typeValue}
                                type="button"
                                onClick={() => chooseType(typeValue)}
                                className={`${PILL_BASE} ${type === typeValue ? PILL_ON : PILL_OFF}`}
                                aria-pressed={type === typeValue}
                                title={cls?.label ?? typeValue}
                            >
                                {cls?.abbr ?? typeValue}
                            </button>
                        );
                    })}
                </div>
                {/* WR-percentile group sits to the right of SS — list-only, so it is
                    hidden while a ship board is open (it would not apply there). */}
                <div className="flex flex-wrap items-center gap-2">
                    {!selectedShip && (
                        <>
                            {/* "WR ≥" stays hardcoded English in every locale — a
                                decision, not an omission. The localized
                                asia.wows-numbers.com ranking tables keep "WR Diff"
                                in Latin in BOTH ko and ja; the community reads "WR"
                                as an untranslated abbreviation in both languages,
                                same as "PR" elsewhere in that corpus. See
                                agents/work-items/i18n-terminology-research.md's
                                "Deliberately untranslated: WR ≥" section. No
                                common.* key exists for this on purpose — do not
                                add one. */}
                            <span className="text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]">WR&nbsp;&ge;</span>
                            {WR_PCTS.map(({ value, label }) => (
                                <button
                                    // Was `key={label}` — collided once the null
                                    // row's label stopped being the static string
                                    // 'All' and started resolving through t()
                                    // (fix round 1, F2). `value` is unique and
                                    // stable regardless of locale.
                                    key={String(value)}
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
                                    {label ?? t('common.all')}
                                </button>
                            ))}
                        </>
                    )}
                </div>
                {/* Share lives at the end of the filter row in BOTH views — the
                    list and the drill-down board — so it never moves between
                    them. Only the destination changes: the bucket's /ships URL,
                    or the selected ship's /ship URL. */}
                {shareUrl ? (
                    <div className="ml-auto">
                        <CopyLinkButton
                            eventName={selectedShip ? 'ship-board-share' : 'ship-list-share'}
                            ariaLabel={
                                selectedShip
                                    ? `Copy a link to the ${selectedShip.name} player standings`
                                    : `Copy a link to the ${headingLabel} standings`
                            }
                            url={shareUrl}
                        />
                    </div>
                ) : null}
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
                ) : isShipless ? (
                    <p className="py-6 text-sm text-[var(--text-muted)]">
                        World of Warships has no {`T${tier} ${typeLabel ?? ''}`.trim()}.
                    </p>
                ) : selectedShip ? (
                    <ShipBoard
                        realm={realm}
                        fallbackName={selectedShip.name}
                        board={board}
                        loading={boardLoading}
                        error={boardError}
                        onClear={clearShip}
                        onSortChange={onBoardSort}
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
                        onSortChange={onListSort}
                        seedSort={initial?.sort ?? null}
                        seeded={hasInitialView}
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
    seedSort?: { key: keyof ListShip; dir: SortDir } | null;
    seeded?: boolean;
}> = ({ ships, totalBattles, loading, error, pending, wrPct, tierTypeLabel, onOpen, onSortChange, seedSort = null, seeded = false }) => {
    const { sort, onSort } = useTableSort<ListShip>(
        ['ship_name'],
        onSortChange,
        SHIP_LIST_SORT_STORAGE_KEY,
        seedSort,
        seeded,
    );
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
            <ul className="max-h-[560px] space-y-2 overflow-y-auto sm:hidden">
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
                        <div className="hidden max-h-[580px] overflow-y-auto sm:block">
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

                        <ul className="max-h-[560px] space-y-2 overflow-y-auto sm:hidden">
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
