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
import { buildPlayerPath } from '../lib/entityRoutes';
import { trackEvent } from '../lib/umami';
import wrColor from '../lib/wrColor';
import SubmarineEasterEgg from './SubmarineEasterEgg';

export type Tier = 8 | 9 | 10;
// Raw `Ship.ship_type` strings the backend filters on (note: "AirCarrier", no
// space). These are the `type` query-param values the new endpoint accepts.
export const SHIP_TYPES = ['Battleship', 'Cruiser', 'Destroyer', 'AirCarrier', 'Submarine'] as const;
export type ShipType = (typeof SHIP_TYPES)[number];
const TIERS: Tier[] = [8, 9, 10];

// Imperative handle the landing treemap drives to drill straight into a ship's
// player board in place (see runbook-treemap-shipleaderboard-handoff). Kept as a
// command rather than lifted state so this component keeps owning its list/board
// state and there is no prop↔state sync race after the user hits Clear.
export interface ShipLeaderboardHandle {
    selectShip(sel: { id: number; name: string; tier: Tier; type: ShipType }): void;
}

interface ListShip {
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
    ships: ListShip[];
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
    };
    players: LeaderboardPlayer[];
}

// The list only changes once per 2-week season; a 1h client TTL keeps a long-open
// tab from showing a stale window past a boundary (backend serves it warm).
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

function useTableSort<T>(
    textKeys: ReadonlyArray<keyof T>,
    onChange?: (key: keyof T, dir: SortDir) => void,
) {
    const [sort, setSort] = useState<{ key: keyof T; dir: SortDir } | null>(null);
    const onSort = (key: keyof T) => {
        // Compute the next sort from the current render's value (not inside the
        // setState updater) so analytics fire exactly once, never doubled.
        const next: { key: keyof T; dir: SortDir } =
            sort && sort.key === key
                ? { key, dir: sort.dir === 'asc' ? 'desc' : 'asc' }
                : { key, dir: textKeys.includes(key) ? 'asc' : 'desc' };
        setSort(next);
        onChange?.(next.key, next.dir);
    };
    return { sort, onSort };
}

const SortButton: React.FC<{ label: string; active: boolean; dir: SortDir; onClick: () => void }> = ({
    label,
    active,
    dir,
    onClick,
}) => (
    <button
        type="button"
        onClick={onClick}
        className={`group inline-flex items-center gap-1 font-medium uppercase tracking-wide transition-colors hover:text-[var(--accent-mid)] ${
            active ? 'text-[var(--accent-mid)]' : ''
        }`}
    >
        <span>{label}</span>
        <span
            aria-hidden
            className={`text-[9px] leading-none ${active ? 'opacity-100' : 'opacity-0 group-hover:opacity-40'}`}
        >
            {active && dir === 'asc' ? '▲' : '▼'}
        </span>
    </button>
);

const ariaSort = (active: boolean, dir: SortDir): 'ascending' | 'descending' | 'none' =>
    active ? (dir === 'asc' ? 'ascending' : 'descending') : 'none';

const DATA_BASIS_HINT =
    'Stats are aggregated from battle observations recorded during the current two-week period.';

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

const ShipLeaderboard = forwardRef<ShipLeaderboardHandle>((_props, ref) => {
    const { realm } = useRealm();
    const sectionRef = useRef<HTMLElement>(null);

    // Land on T10 Battleships so the board shows real standings immediately
    // (these buckets are pre-warmed daily — see warm_realm_top_ships_task).
    const [tier, setTier] = useState<Tier | null>(10);
    const [type, setType] = useState<ShipType | null>('Battleship');
    const [selectedShip, setSelectedShip] = useState<{ id: number; name: string } | null>(null);

    const [list, setList] = useState<ListShip[] | null>(null);
    const [listLoading, setListLoading] = useState(false);
    const [listError, setListError] = useState(false);

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

    // Column-sort analytics, one event for both tables (scope distinguishes the
    // ship list from the player board). Built here so realm lives in one place.
    const trackSort = (scope: 'ships' | 'players') => (column: string, dir: SortDir) =>
        trackEvent('ship-leaderboard-sort', { realm, scope, column, dir });

    const bothSelected = tier != null && type != null;
    // The T9 + Submarine combo (World of Warships has no such ship) short-circuits
    // to the easter egg and must issue NO fetch — the endpoint would 400 in any
    // env where SHIP_BADGE_TIERS excludes 9 (e.g. local dev) and is pointless in
    // prod. Gate both the fetch effect and the render branch on this predicate.
    const isSubEasterEgg = tier === 9 && type === 'Submarine';

    // Count every time the T9-submarine animation surfaces. The render branch is
    // the single source of truth for "the user is looking at it", so fire off the
    // predicate — independent of whether they reached it tier-first or type-first.
    // A ref edge-triggers it (once per activation, reset on exit) so a realm flip
    // while it's on screen doesn't double-count.
    const eggTrackedRef = useRef(false);
    useEffect(() => {
        if (isSubEasterEgg) {
            if (!eggTrackedRef.current) {
                eggTrackedRef.current = true;
                trackEvent('ship-leaderboard-easter-egg', { realm, egg: 't9-submarine' });
            }
        } else {
            eggTrackedRef.current = false;
        }
    }, [isSubEasterEgg, realm]);

    // Ship list fetch (only with both filters set and no ship drilled into).
    const listReqId = useRef(0);
    useEffect(() => {
        if (!bothSelected || selectedShip || isSubEasterEgg) return;
        const reqId = ++listReqId.current;
        setListLoading(true);
        setListError(false);
        fetchSharedJson<ShipsByTierType>(
            `/api/realm/${encodeURIComponent(realm)}/ships?tier=${tier}&type=${encodeURIComponent(type as string)}`,
            {
                label: `ShipsByTierType:${realm}:${tier}:${type}`,
                ttlMs: LIST_FETCH_TTL_MS,
                cacheKey: `ships-by:${realm}:${tier}:${type}`,
            },
        )
            .then(({ data }) => {
                if (reqId !== listReqId.current) return;
                setList(data.ships ?? []);
                setListLoading(false);
            })
            .catch(() => {
                if (reqId !== listReqId.current) return;
                setListError(true);
                setListLoading(false);
            });
    }, [realm, tier, type, bothSelected, selectedShip, isSubEasterEgg]);

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

    const typeLabel = useMemo(() => (type ? shipClass(type)?.label ?? type : null), [type]);

    return (
        <section ref={sectionRef} className="mt-2 pt-8" aria-label="Ship leaderboard">
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
                    {/* Data-basis hint sits at the end of the row, right of SS. */}
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
                        loading={listLoading}
                        error={listError}
                        tierTypeLabel={`T${tier} ${typeLabel ?? ''}`.trim()}
                        onOpen={openShip}
                        onSortChange={trackSort('ships')}
                    />
                )}
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
    loading: boolean;
    error: boolean;
    tierTypeLabel: string;
    onOpen: (s: ListShip) => void;
    onSortChange: (key: keyof ListShip, dir: SortDir) => void;
}> = ({ ships, loading, error, tierTypeLabel, onOpen, onSortChange }) => {
    const { sort, onSort } = useTableSort<ListShip>(['ship_name'], onSortChange);
    const sortedShips = useMemo(
        () => (ships && sort ? sortRows(ships, sort.key, sort.dir) : ships),
        [ships, sort],
    );

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
            {/* Desktop: dense table, win rate the only color, ship name the action.
                Viewport caps to ~15 rows; the rest scroll under a sticky header. */}
            <div className="hidden max-h-[580px] max-w-[900px] overflow-y-auto sm:block">
            <table className="w-full text-sm">
                <thead className="sticky top-0 z-10 bg-[var(--bg-page)]">
                    <tr className="border-b border-[var(--border)] text-left text-xs uppercase tracking-wide text-[var(--text-muted)]">
                        <th className="py-2 pl-2 pr-8" aria-sort={ariaSort(sort?.key === 'ship_name', colSort('ship_name').dir)}>
                            <SortButton label="Ship" {...colSort('ship_name')} />
                        </th>
                        <th className="py-2 pr-8 text-right" aria-sort={ariaSort(sort?.key === 'battles', colSort('battles').dir)}>
                            <SortButton label="Battles" {...colSort('battles')} />
                        </th>
                        <th className="py-2 pr-8 text-right" aria-sort={ariaSort(sort?.key === 'avg_damage', colSort('avg_damage').dir)}>
                            <SortButton label="Avg dmg" {...colSort('avg_damage')} />
                        </th>
                        <th className="py-2 pr-8 text-right" aria-sort={ariaSort(sort?.key === 'kills_per_battle', colSort('kills_per_battle').dir)}>
                            <SortButton label="Kills/battle" {...colSort('kills_per_battle')} />
                        </th>
                        <th className="py-2 text-right" aria-sort={ariaSort(sort?.key === 'win_rate', colSort('win_rate').dir)}>
                            <SortButton label="Win rate" {...colSort('win_rate')} />
                        </th>
                    </tr>
                </thead>
                <tbody>
                    {sortedShips.map((s) => (
                        <tr key={s.ship_id} className="transition-colors hover:bg-[var(--bg-hover)]">
                            <td className="py-2 pl-2 pr-8">
                                <button type="button" className={SHIP_NAME_LINK} onClick={() => onOpen(s)}>
                                    {s.ship_name}
                                </button>
                            </td>
                            <td className="py-2 pr-8 text-right tabular-nums text-[var(--text-primary)]">{s.battles.toLocaleString()}</td>
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
                            <button type="button" className={`${SHIP_NAME_LINK} truncate`} onClick={() => onOpen(s)}>
                                {s.ship_name}
                            </button>
                            <span className="shrink-0 tabular-nums font-semibold" style={{ color: wrColor(s.win_rate) }}>
                                {s.win_rate.toFixed(1)}%
                            </span>
                        </div>
                        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs tabular-nums text-[var(--text-muted)]">
                            <span><span className="text-[var(--text-primary)]">{s.battles.toLocaleString()}</span> battles</span>
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
    const cls = ship ? shipClass(ship.ship_type) : null;
    const subtitle = ship
        ? `T${ship.tier ?? '?'} ${cls?.label ?? ship.ship_type ?? ''}`.trim()
        : '';
    const players = useMemo(() => board?.players ?? [], [board]);
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
                <span className="text-sm font-semibold text-[var(--text-strong)]">
                    {ship?.name ?? fallbackName}
                    {subtitle && <span className="ml-2 font-normal text-[var(--text-muted)]">· {subtitle}</span>}
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
                                        <SortButton label="Win rate" {...colSort('win_rate')} />
                                    </th>
                                    <th className="py-2 pr-8 text-right" aria-sort={ariaSort(sort?.key === 'battles', colSort('battles').dir)}>
                                        <SortButton label="Battles" {...colSort('battles')} />
                                    </th>
                                    <th className="py-2 pr-8 text-right" aria-sort={ariaSort(sort?.key === 'avg_damage', colSort('avg_damage').dir)}>
                                        <SortButton label="Avg dmg" {...colSort('avg_damage')} />
                                    </th>
                                    <th className="py-2 text-right" aria-sort={ariaSort(sort?.key === 'kills_per_battle', colSort('kills_per_battle').dir)}>
                                        <SortButton label="Kills/battle" {...colSort('kills_per_battle')} />
                                    </th>
                                </tr>
                            </thead>
                            <tbody>
                                {sortedPlayers.map((p) => (
                                    <tr key={p.rank} className="transition-colors hover:bg-[var(--bg-hover)]">
                                        <td className="py-2 pl-2 pr-3 tabular-nums text-[var(--text-muted)]">{p.rank}</td>
                                        <td className="py-2 pr-8">
                                            <Link href={buildPlayerPath(p.player_name, realm)} className={PLAYER_LINK} onClick={() => trackPlayerClick(p.rank)}>
                                                {p.player_name}
                                            </Link>
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
