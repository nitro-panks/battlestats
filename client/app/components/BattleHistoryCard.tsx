'use client';

import React, { useEffect, useMemo, useState } from 'react';
import { fetchSharedJson } from '../lib/sharedJsonFetch';
import wrColor from '../lib/wrColor';
import { chartColors } from '../lib/chartTheme';
import { useTheme } from '../context/ThemeContext';

export interface BattleHistoryByShip {
    ship_id: number;
    ship_name: string;
    ship_tier: number | null;
    ship_type: string | null;
    battles: number;
    wins: number;
    losses: number;
    win_rate: number;
    damage: number;
    avg_damage: number;
    frags: number;
    xp: number;
    planes_killed: number;
    survived_battles: number;
    lifetime_battles?: number | null;
    lifetime_win_rate?: number | null;
    delta_win_rate?: number | null;
}

export interface BattleHistoryByDay {
    date: string;
    battles: number;
    wins: number;
    damage: number;
    frags: number;
}

export interface BattleHistoryTotals {
    battles: number;
    wins: number;
    losses: number;
    win_rate: number;
    damage: number;
    avg_damage: number;
    frags: number;
    xp: number;
    planes_killed: number;
    survived_battles: number;
    survival_rate: number;
    lifetime_battles?: number | null;
    lifetime_win_rate?: number | null;
    delta_win_rate?: number | null;
}

export interface BattleHistoryPayload {
    window_days?: number | null;
    windows?: number;
    period?: 'daily' | 'weekly' | 'monthly' | 'yearly';
    mode?: 'random' | 'ranked' | 'combined';
    available_modes?: ('random' | 'ranked')[];
    as_of: string;
    totals: BattleHistoryTotals;
    by_ship: BattleHistoryByShip[];
    by_day: BattleHistoryByDay[];
}

type Mode = 'random' | 'ranked' | 'combined';
const MODE_LABEL: Record<Mode, string> = {
    random: 'Random', ranked: 'Ranked', combined: 'All',
};
const MODES: Mode[] = ['random', 'ranked', 'combined'];

// On-render ranked-observation refresh: when the API responds with
// `X-Ranked-Observation-Pending: true`, a 3-WG-call refresh is in
// flight. Poll the endpoint up to N times so the card rehydrates with
// fresh ranked deltas as soon as the task completes.
const RANKED_PENDING_RETRY_DELAY_MS = 2000;
const RANKED_PENDING_RETRY_LIMIT = 6;

interface BattleHistoryCardProps {
    playerName: string;
    realm: string;
    days?: number;
}

const formatInt = (n: number): string => n.toLocaleString();
const formatPercent = (n: number): string => `${n.toFixed(1)}%`;

const tierBlue = (tier: number | null | undefined): string => {
    if (tier == null) return 'var(--text-muted)';
    const clamped = Math.max(1, Math.min(11, tier));
    // Saturation ramps 25% (T1, pale) → 95% (T11, deep). Lightness held at
    // 50% so the color reads on both light and dark themes.
    const sat = 25 + ((clamped - 1) / 10) * 70;
    return `hsl(215, ${sat}%, 50%)`;
};

type SortKey = 'ship_name' | 'ship_tier' | 'ship_type' | 'battles' | 'win_rate'
    | 'avg_damage' | 'kdr';

const computeKdr = (frags: number, battles: number, survived: number): number => {
    const deaths = Math.max(0, battles - survived);
    if (deaths <= 0) return frags;
    return frags / deaths;
};

// Format KDR for the per-ship table: trim trailing zeros so 1.50 → "1.5"
// and 1.00 → "1". The totals tile keeps full toFixed(2) for column alignment.
const formatTableKdr = (v: number): string => v.toFixed(2).replace(/\.?0+$/, '');

const SHIP_TYPE_LABEL: Record<string, string> = {
    Destroyer: 'DD',
    Cruiser: 'CA',
    Battleship: 'BB',
    AirCarrier: 'CV',
    Submarine: 'SS',
};

const shipTypeShort = (type: string | null | undefined): string => {
    if (!type) return '—';
    return SHIP_TYPE_LABEL[type] ?? type.slice(0, 2).toUpperCase();
};
type SortDirection = 'asc' | 'desc';

const DEFAULT_DIRECTION: Record<SortKey, SortDirection> = {
    ship_name: 'asc', ship_tier: 'asc', ship_type: 'asc',
    battles: 'desc', win_rate: 'desc', avg_damage: 'desc',
    kdr: 'desc',
};

interface SortableThProps {
    sortKey: SortKey;
    activeKey: SortKey;
    direction: SortDirection;
    onSortClick: (key: SortKey) => void;
    children: React.ReactNode;
    tooltip?: string;
}

const SortableTh: React.FC<SortableThProps> = ({
    sortKey, activeKey, direction, onSortClick, children, tooltip,
}) => {
    const active = activeKey === sortKey;
    const arrow = active ? (direction === 'asc' ? '▲' : '▼') : '';
    return (
        <th
            scope="col"
            className="py-2 px-2 cursor-help select-none hover:text-[var(--text-strong)] text-center"
            onClick={() => onSortClick(sortKey)}
            aria-sort={active ? (direction === 'asc' ? 'ascending' : 'descending') : 'none'}
            title={tooltip}
        >
            <span>{children}</span>
            <span className="ml-1 text-[10px]" aria-hidden="true">{arrow || '↕'}</span>
        </th>
    );
};

interface WrCellProps {
    periodWinRate: number;
    lifetimeWinRate: number | null | undefined;
    deltaWinRate: number | null | undefined;
    stacked?: boolean;
}

const WrCell: React.FC<WrCellProps> = ({
    periodWinRate, lifetimeWinRate, deltaWinRate, stacked = false,
}) => {
    const tone = deltaWinRate == null
        ? 'var(--text-muted)'
        : deltaWinRate > 0
            ? '#74c476'
            : deltaWinRate < 0
                ? '#a50f15'
                : 'var(--text-muted)';
    const signedDelta = deltaWinRate == null
        ? null
        : `${deltaWinRate > 0 ? '+' : ''}${deltaWinRate.toFixed(1)}%`;
    const lifetimeMissing = lifetimeWinRate == null;
    const tooltip = lifetimeMissing
        ? `Period ${formatPercent(periodWinRate)} · Lifetime N/A (never played)`
        : `Period ${formatPercent(periodWinRate)} · Lifetime ${formatPercent(lifetimeWinRate)}${signedDelta != null ? ` (Δ${signedDelta})` : ''}`;
    const periodEl = (
        <span style={{ color: wrColor(periodWinRate) }} className="font-semibold">
            {formatPercent(periodWinRate)}
        </span>
    );
    const lifetimeEl = !lifetimeMissing ? (
        <span className="text-xs" style={{ color: wrColor(lifetimeWinRate) }}>
            {formatPercent(lifetimeWinRate)}
        </span>
    ) : (
        <span className="text-xs text-[var(--text-muted)]">N/A</span>
    );
    const deltaEl = signedDelta != null ? (
        <span className="text-xs font-medium" style={{ color: tone }}>
            Δ{signedDelta}
        </span>
    ) : (
        <span className="text-xs text-[var(--text-muted)]">—</span>
    );

    if (stacked) {
        return (
            <span className="tabular-nums flex flex-col items-start" title={tooltip}>
                {periodEl}
                <span className="inline-grid grid-cols-[3rem_4rem] gap-2 items-baseline whitespace-nowrap">
                    <span className="text-left">{lifetimeEl}</span>
                    <span className="text-left">{deltaEl}</span>
                </span>
            </span>
        );
    }
    return (
        <span
            className="tabular-nums inline-grid grid-cols-[3.5rem_3rem_4rem] gap-2 items-baseline whitespace-nowrap"
            title={tooltip}
        >
            <span className="text-right">{periodEl}</span>
            <span className="text-right">{lifetimeEl}</span>
            <span className="text-right">{deltaEl}</span>
        </span>
    );
};

const buildWindowedDays = (
    days: BattleHistoryByDay[],
    windowDays: number,
): BattleHistoryByDay[] => {
    const byDate = new Map(days.map((d) => [d.date, d]));
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const padded: BattleHistoryByDay[] = [];
    for (let i = windowDays - 1; i >= 0; i -= 1) {
        const d = new Date(today);
        d.setDate(today.getDate() - i);
        const iso = d.toISOString().slice(0, 10);
        const existing = byDate.get(iso);
        padded.push(existing ?? {
            date: iso,
            battles: 0, wins: 0, damage: 0, frags: 0,
        });
    }
    return padded;
};

interface SparklinePoint {
    date: string;
    value: number;
    color: string;
    tooltip: string;
}

// Inline sparkline that lives inside the totals tile (between Win rate
// and Avg damage). No hover values, no day markers — just a continuous
// polyline scaled to the window's value range.
const InlineSparkline: React.FC<{ values: number[]; ariaLabel: string }> = ({
    values, ariaLabel,
}) => {
    if (values.length < 2) return null;
    const width = 100;
    const height = 28;
    const pad = 2;
    const minV = Math.min(...values);
    const maxV = Math.max(...values);
    const range = Math.max(maxV - minV, 0.0001);
    const padding = range * 0.15 + 0.0001;
    const yMin = minV - padding;
    const yMax = maxV + padding;
    const span = yMax - yMin;
    const points = values.map((v, i) => {
        const x = pad + (i * (width - 2 * pad)) / Math.max(1, values.length - 1);
        const y = height - pad - ((v - yMin) / span) * (height - 2 * pad);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');
    return (
        <svg
            viewBox={`0 0 ${width} ${height}`}
            width="30%"
            height={height}
            preserveAspectRatio="none"
            className="block"
            aria-label={ariaLabel}
            role="img"
        >
            <polyline
                fill="none"
                stroke="var(--accent-mid)"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
                points={points}
            />
        </svg>
    );
};

const buildOverallWrSeries = (
    days: BattleHistoryByDay[],
    totals: BattleHistoryTotals,
): number[] | null => {
    // Need lifetime baseline to anchor the running overall WR.
    const lifetimeBattles = totals.lifetime_battles ?? null;
    const lifetimeWr = totals.lifetime_win_rate ?? null;
    if (lifetimeBattles == null || lifetimeWr == null || lifetimeBattles <= 0) {
        return null;
    }
    const lifetimeWins = Math.round(lifetimeBattles * (lifetimeWr / 100));
    const periodBattles = totals.battles;
    const periodWins = totals.wins;
    const priorBattles = Math.max(0, lifetimeBattles - periodBattles);
    const priorWins = Math.max(0, lifetimeWins - periodWins);

    let cumBattles = 0;
    let cumWins = 0;
    return days.map((d) => {
        cumBattles += d.battles;
        cumWins += d.wins;
        const denom = priorBattles + cumBattles;
        return denom > 0 ? (100 * (priorWins + cumWins)) / denom : 0;
    });
};

// Fallback when no lifetime baseline is available — plot battles-per-day
// as the value series. Same visual contract as the WR series; just a line.
const buildBattlesPerDaySeries = (days: BattleHistoryByDay[]): number[] => (
    days.map((d) => d.battles)
);

type Period = 'daily' | 'weekly' | 'monthly' | 'yearly';
const PERIOD_DEFAULT_WINDOWS: Record<Period, number> = {
    daily: 7, weekly: 12, monthly: 12, yearly: 5,
};
const PERIOD_LABEL: Record<Period, string> = {
    daily: 'Daily', weekly: 'Weekly', monthly: 'Monthly', yearly: 'Yearly',
};

const BattleHistoryCard: React.FC<BattleHistoryCardProps> = ({
    playerName,
    realm,
    days = 7,
}) => {
    const [payload, setPayload] = useState<BattleHistoryPayload | null>(null);
    const [error, setError] = useState<Error | null>(null);
    const [loading, setLoading] = useState(true);
    const [period, setPeriod] = useState<Period>('daily');
    const [mode, setMode] = useState<Mode>('random');
    const [userPickedMode, setUserPickedMode] = useState(false);
    const { theme } = useTheme();
    const palette = chartColors[theme];

    const shipTypeColor = (type: string | null | undefined): string => {
        switch (type) {
            case 'Destroyer': return palette.shipDD;
            case 'Cruiser': return palette.shipCA;
            case 'Battleship': return palette.shipBB;
            case 'AirCarrier': return palette.shipCV;
            case 'Submarine': return palette.shipSS;
            default: return palette.shipDefault;
        }
    };

    useEffect(() => {
        let cancelled = false;
        let pollTimer: ReturnType<typeof setTimeout> | null = null;
        let pendingAttempts = 0;
        setLoading(true);
        const windows = period === 'daily'
            ? days
            : PERIOD_DEFAULT_WINDOWS[period];

        const fetchOnce = (cacheBust: number = 0) => {
            const url = `/api/player/${encodeURIComponent(playerName)}/battle-history/`
                + `?period=${period}&windows=${windows}&mode=${mode}`
                + `&realm=${encodeURIComponent(realm)}`;
            fetchSharedJson<BattleHistoryPayload>(url, {
                label: `BattleHistoryCard:${period}:${mode}`,
                ttlMs: 60_000,
                cacheKey: `battle-history:${playerName}:${realm}:${period}:${windows}:${mode}:${cacheBust}`,
                responseHeaders: ['X-Ranked-Observation-Pending'],
            })
                .then(({ data, headers }) => {
                    if (cancelled) return;
                    setPayload(data);
                    setError(null);
                    const pending = headers['X-Ranked-Observation-Pending'] === 'true';
                    if (
                        pending
                        && (mode === 'ranked' || mode === 'combined')
                        && pendingAttempts < RANKED_PENDING_RETRY_LIMIT
                    ) {
                        pendingAttempts += 1;
                        pollTimer = setTimeout(
                            () => fetchOnce(pendingAttempts),
                            RANKED_PENDING_RETRY_DELAY_MS,
                        );
                    }
                })
                .catch((e: unknown) => {
                    if (!cancelled) {
                        setError(e instanceof Error ? e : new Error(String(e)));
                        setPayload(null);
                    }
                })
                .finally(() => {
                    if (!cancelled && pollTimer === null) setLoading(false);
                });
        };

        fetchOnce();
        return () => {
            cancelled = true;
            if (pollTimer !== null) clearTimeout(pollTimer);
        };
    }, [playerName, realm, days, period, mode]);

    // Auto-select the right default mode based on what the player actually
    // has data in. Skipped once the user has explicitly clicked a pill.
    //   - only one mode available → switch to that one (e.g. ranked-only
    //     player gets defaulted to Ranked)
    //   - both modes available → switch to Combined ("All") so the user
    //     sees their full activity by default
    //   - default initial state is 'random' so the first fetch matches the
    //     pre-Phase-5 contract; the auto-switch may then fire one refetch
    useEffect(() => {
        if (userPickedMode) return;
        const available = payload?.available_modes;
        if (!available) return;
        if (available.length === 1 && available[0] !== mode) {
            setMode(available[0]);
            return;
        }
        if (
            available.length >= 2
            && available.includes('random')
            && available.includes('ranked')
            && mode !== 'combined'
        ) {
            setMode('combined');
        }
    }, [payload?.available_modes, mode, userPickedMode]);

    const [sort, setSort] = useState<{ key: SortKey; direction: SortDirection }>({
        key: 'battles', direction: 'desc',
    });

    const onSortClick = (key: SortKey) => {
        setSort((s) => s.key === key
            ? { key, direction: s.direction === 'asc' ? 'desc' : 'asc' }
            : { key, direction: DEFAULT_DIRECTION[key] });
    };

    const visibleByShip = useMemo(() => {
        const rows = (payload?.by_ship ?? []).map((r) => ({
            ...r,
            kdr: computeKdr(r.frags, r.battles, r.survived_battles),
        }));
        const sortVal = (row: typeof rows[number]): string | number => {
            const v = (row as Record<string, unknown>)[sort.key];
            if (v == null) return sort.direction === 'asc' ? Infinity : -Infinity;
            return typeof v === 'string' ? v.toLowerCase() : (v as number);
        };
        rows.sort((a, b) => {
            const av = sortVal(a);
            const bv = sortVal(b);
            if (av < bv) return sort.direction === 'asc' ? -1 : 1;
            if (av > bv) return sort.direction === 'asc' ? 1 : -1;
            return 0;
        });
        return rows.slice(0, 12);
    }, [payload?.by_ship, sort]);

    if (loading || error) {
        return null;
    }
    const totals = payload?.totals;
    const hasBattles = !!(totals && typeof totals.battles === 'number'
        && totals.battles > 0);
    // Pill visibility derives from `available_modes`:
    //   only random → no pills (single mode is implicit)
    //   only ranked → just Ranked (no Random, no All)
    //   both        → Random | Ranked | All
    const availableModes = payload?.available_modes ?? ['random'];
    const hasRandom = availableModes.includes('random');
    const hasRanked = availableModes.includes('ranked');
    const visibleModes: Mode[] = hasRandom && hasRanked
        ? ['random', 'ranked', 'combined']
        : hasRanked
            ? ['ranked']
            : [];
    // When the user has actively switched off the default `random` mode,
    // keep the card visible even with zero rows so the pill stays
    // reachable. Default-mode empty stays null to preserve the
    // pre-Phase-5 contract: cards never appear for players with no
    // recent random battles.
    if (!payload || (!hasBattles && mode === 'random')) {
        return null;
    }

    return (
        <section
            data-testid="battle-history-card"
            className="mt-6 rounded-md border border-[var(--accent-faint)] bg-[var(--bg-card)] p-5"
            aria-label="Recent battles"
        >
            <header className="flex flex-wrap items-baseline justify-between gap-2">
                <div className="flex flex-wrap items-baseline gap-3">
                    <h2 className="text-sm font-semibold uppercase tracking-wide text-[var(--text-muted)]">
                        {period === 'daily'
                            ? `Last ${payload.windows ?? payload.window_days} days`
                            : `Last ${payload.windows} ${period === 'weekly' ? 'weeks'
                                : period === 'monthly' ? 'months' : 'years'}`}
                    </h2>
                    {/* Period pill row hidden — only Daily exists today
                        and a single-option pill is just visual noise.
                        Weekly/monthly/yearly will reappear here when the
                        period rollups ship. To restore: render the
                        ['daily'] map back as a pill row, or expand to
                        ['daily','weekly','monthly','yearly']. */}
                    {visibleModes.length >= 2 && (
                        <div
                            className="flex items-center gap-1 text-xs"
                            role="group"
                            aria-label="Battle mode"
                        >
                            {visibleModes.map((m) => (
                                <button
                                    key={m}
                                    type="button"
                                    onClick={() => {
                                        setMode(m);
                                        setUserPickedMode(true);
                                    }}
                                    className={`rounded px-2 py-0.5 transition-colors ${
                                        mode === m
                                            ? 'bg-[var(--accent-mid)] text-[var(--bg-card)] font-semibold'
                                            : 'text-[var(--text-muted)] hover:text-[var(--text-strong)]'
                                    }`}
                                    aria-pressed={mode === m}
                                    title={m === 'random'
                                        ? 'Random battles only'
                                        : m === 'ranked'
                                            ? 'Ranked battles only (sums across active seasons)'
                                            : 'Random + ranked combined (lifetime delta unavailable)'}
                                >
                                    {MODE_LABEL[m]}
                                </button>
                            ))}
                        </div>
                    )}
                </div>
                {/* Header summary text removed — duplicates the totals tile
                    cells (Battles, Win rate, Avg damage) directly below. */}
            </header>
            {!hasBattles && (
                <p className="mt-4 text-sm text-[var(--text-muted)]">
                    No {MODE_LABEL[mode].toLowerCase()} battles in this window.
                </p>
            )}
            {hasBattles && (() => {
                const deaths = Math.max(0, totals!.battles - totals!.survived_battles);
                const kdr = deaths > 0 ? totals!.frags / deaths : totals!.frags;
                // Sparkline + helpers (InlineSparkline, buildOverallWrSeries,
                // buildBattlesPerDaySeries, buildWindowedDays) intentionally
                // kept in the file — disabled here to declutter the totals
                // tile but available if we want to re-enable. To restore,
                // uncomment the InlineSparkline cell below and bump the grid
                // back to sm:grid-cols-6.
                return (
                    <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-5 sm:items-end">
                        <div>
                            <div className="text-xs text-[var(--text-muted)]">Battles</div>
                            <div className="text-lg font-semibold text-[var(--text-strong)]">{formatInt(totals!.battles)}</div>
                        </div>
                        <div>
                            <div className="text-xs text-[var(--text-muted)]">Win rate</div>
                            <div className="text-lg">
                                <WrCell
                                    periodWinRate={totals!.win_rate}
                                    lifetimeWinRate={totals!.lifetime_win_rate}
                                    deltaWinRate={totals!.delta_win_rate}
                                    stacked
                                />
                            </div>
                        </div>
                        {/* <div className="pb-1">
                            <InlineSparkline
                                values={
                                    buildOverallWrSeries(
                                        (payload.period ?? 'daily') === 'daily'
                                            ? buildWindowedDays(payload.by_day, payload.window_days ?? payload.windows ?? 7)
                                            : payload.by_day,
                                        totals!,
                                    ) ?? buildBattlesPerDaySeries(
                                        (payload.period ?? 'daily') === 'daily'
                                            ? buildWindowedDays(payload.by_day, payload.window_days ?? payload.windows ?? 7)
                                            : payload.by_day,
                                    )
                                }
                                ariaLabel="Win-rate trend across the period"
                            />
                        </div> */}
                        <div>
                            <div className="text-xs text-[var(--text-muted)]">Avg damage</div>
                            <div className="text-lg font-semibold text-[var(--text-strong)]">{formatInt(totals!.avg_damage)}</div>
                        </div>
                        <div>
                            <div className="text-xs text-[var(--text-muted)]">Frags</div>
                            <div className="text-lg font-semibold text-[var(--text-strong)]">{formatInt(totals!.frags)}</div>
                        </div>
                        <div>
                            <div className="text-xs text-[var(--text-muted)]">KDR</div>
                            <div className="text-lg font-semibold text-[var(--text-strong)]">{kdr.toFixed(2)}</div>
                        </div>
                    </div>
                );
            })()}
            {hasBattles && (
            <div className="mt-6 overflow-x-auto border-t border-[var(--accent-faint)] pt-4">
                <table className="w-full text-left text-sm">
                    <thead>
                        <tr className="border-b border-[var(--accent-faint)] text-xs uppercase tracking-wide text-[var(--text-muted)]">
                            <SortableTh sortKey="ship_name" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick} tooltip="Ship played in the period. Click to sort A–Z.">Ship</SortableTh>
                            <SortableTh sortKey="ship_tier" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick} tooltip="Ship tier (1–10, with the lowest tier ships being the smallest, less powerful, with the highest tier ships being the largest, most powerful). Click to sort by tier.">Tier</SortableTh>
                            <SortableTh sortKey="ship_type" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick} tooltip="Hull type — DD = Destroyer, CL/CA = Cruiser, BB = Battleship, CV = Carrier, SS = Submarine. Click to sort by type.">Type</SortableTh>
                            <SortableTh sortKey="battles" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick} tooltip="Battles played on this ship in the selected period. Click to sort by volume.">#</SortableTh>
                            <SortableTh sortKey="win_rate" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick} tooltip="Win rate this period · lifetime win rate · delta vs lifetime. Color codes use Wargaming community thresholds. Click to sort by period WR.">Win Rate</SortableTh>
                            <SortableTh sortKey="avg_damage" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick} tooltip="Average damage dealt per battle on this ship in the selected period. Click to sort.">Avg dmg</SortableTh>
                            <SortableTh sortKey="kdr" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick} tooltip="Kill/Death ratio — frags ÷ deaths this period. Hover a row to see raw frag and death counts. Click to sort.">KDR</SortableTh>
                        </tr>
                    </thead>
                    <tbody>
                        {visibleByShip.map((row) => (
                            <tr
                                key={row.ship_id}
                                className="border-b border-[var(--accent-faint)] last:border-b-0"
                            >
                                <td className="py-1.5 pr-2 text-[var(--text-strong)]">
                                    {row.ship_name || `Ship ${row.ship_id}`}
                                </td>
                                <td className="py-1.5 px-2 text-center tabular-nums text-[var(--text-muted)]">
                                    {row.ship_tier ?? '—'}
                                </td>
                                <td
                                    className="py-1.5 px-2 text-center font-semibold"
                                    style={{ color: shipTypeColor(row.ship_type) }}
                                    title={row.ship_type ?? ''}
                                >
                                    {shipTypeShort(row.ship_type)}
                                </td>
                                <td className="py-1.5 px-2 text-center tabular-nums text-[var(--text-strong)]">{formatInt(row.battles)}</td>
                                <td className="py-1.5 pr-2 text-right">
                                    <WrCell
                                        periodWinRate={row.win_rate}
                                        lifetimeWinRate={row.lifetime_win_rate}
                                        deltaWinRate={row.delta_win_rate}
                                    />
                                </td>
                                <td className="py-1.5 pr-2 text-right tabular-nums text-[var(--text-strong)]">{formatInt(row.avg_damage)}</td>
                                <td
                                    className="py-1.5 px-2 text-center tabular-nums text-[var(--text-strong)]"
                                    title={`${row.frags} frags / ${Math.max(0, row.battles - row.survived_battles)} deaths`}
                                >
                                    {formatTableKdr(row.kdr)}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            )}
        </section>
    );
};

export default BattleHistoryCard;
