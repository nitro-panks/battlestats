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
    is_new_ship?: boolean;
    is_ranked_only_period?: boolean;
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
    has_recent_24h_activity?: boolean;
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

// Canonical battle-history fetch URL + cache key. Shared by the card's own
// fetch and PlayerRouteView's parallel prefetch so they dedupe onto the same
// in-flight request (sharedJsonFetch keys on cacheKey). Keep these in lockstep —
// if they drift, the prefetch silently becomes a duplicate request instead of a
// dedup (guarded by a test).
export const BATTLE_HISTORY_FETCH_TTL_MS = 60_000;

export const battleHistoryFetchUrl = (
    playerName: string, realm: string, window: string = 'week', mode: string = 'random',
): string =>
    `/api/player/${encodeURIComponent(playerName)}/battle-history/`
    + `?window=${window}&mode=${mode}`
    + `&realm=${encodeURIComponent(realm)}`;

export const battleHistoryCacheKey = (
    playerName: string, realm: string,
    window: string = 'week', mode: string = 'random', cacheBust: number = 0, refreshNonce: number = 0,
): string => `battle-history:${playerName}:${realm}:${window}:${mode}:${cacheBust}:${refreshNonce}`;

/**
 * Eagerly fire the initial (week / random) battle-history fetch so it runs in
 * PARALLEL with the player-profile fetch, instead of starting only after the
 * profile resolves and PlayerDetail mounts the card. The card's own first fetch
 * dedupes onto this via the shared cacheKey (or hits the warm 60s cache), so it
 * costs no extra request — it just moves the battle-history round-trip off the
 * serial critical path, shaving it off T1.
 *
 * Fire-and-forget: this runs before we know `is_hidden` (hidden players never
 * render the card), but the request is cheap and the card handles its own
 * errors/404 — so do NOT gate this on is_hidden (that info isn't here yet).
 */
export const prefetchBattleHistory = (playerName: string, realm: string): void => {
    void fetchSharedJson<BattleHistoryPayload>(battleHistoryFetchUrl(playerName, realm), {
        label: 'BattleHistoryCard:week:random',
        ttlMs: BATTLE_HISTORY_FETCH_TTL_MS,
        cacheKey: battleHistoryCacheKey(playerName, realm),
        responseHeaders: ['X-Ranked-Observation-Pending'],
    }).catch(() => { /* the card re-fetches + surfaces errors on mount */ });
};

interface BattleHistoryCardProps {
    playerName: string;
    realm: string;
    days?: number;
    // Bumped by the live-update poll; folded into the fetch deps + cacheKey so
    // the battle-history re-fetches after a visit-driven refresh lands.
    refreshNonce?: number;
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
    | 'lifetime_win_rate' | 'avg_damage' | 'kdr';

// Average kills per battle for the period (frags / battles).
// Renamed semantically from K/D — the BattleHistory table reports
// per-session frag rate, not lifetime K/D-ratio. Example: 3 games,
// 6 frags, 0 deaths → 2.00 (was 6.00 under the old kills/deaths math).
const computeKdr = (frags: number, battles: number): number => {
    if (battles <= 0) return 0;
    return frags / battles;
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
    battles: 'desc', win_rate: 'desc', lifetime_win_rate: 'desc',
    avg_damage: 'desc', kdr: 'desc',
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
    isNewShip?: boolean;
    isRankedOnlyPeriod?: boolean;
    stacked?: boolean;
}

const WrCell: React.FC<WrCellProps> = ({
    periodWinRate, lifetimeWinRate, deltaWinRate,
    isNewShip = false, isRankedOnlyPeriod = false, stacked = false,
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
    ) : isNewShip ? (
        <span
            className="text-[10px] font-bold uppercase tracking-wider rounded-sm px-1.5 py-[1px]"
            style={{
                color: 'var(--accent-mid)',
                backgroundColor: 'var(--accent-faint)',
            }}
            title="First-time random battles in this ship — no prior state to compute a delta against."
        >
            NEW
        </span>
    ) : isRankedOnlyPeriod ? (
        <span
            className="text-[10px] font-bold uppercase tracking-wider rounded-sm px-1.5 py-[1px]"
            style={{
                color: 'var(--text-muted)',
                backgroundColor: 'var(--accent-faint)',
            }}
            title="All this ship's battles in the window were ranked — no random lifetime to anchor a delta against."
        >
            RANKED
        </span>
    ) : (
        <span className="text-xs text-[var(--text-muted)]">—</span>
    );

    // Render shapes:
    //  * NEW ship, no priors        → <period%> / <NEW badge>
    //  * NEW ship, sparse priors    → <period%> / <lifetime%> / NEW
    //  * Ranked-only-in-period      → <period%> / <RANKED badge>
    //  * Other (no baseline at all) → <period%>
    //  * Default                    → <period%> / <lifetime%> / Δsigned
    const newWithoutLifetime = lifetimeMissing && isNewShip;
    const rankedOnlyBadge = lifetimeMissing && !isNewShip && isRankedOnlyPeriod;
    const periodOnlyCollapse = lifetimeMissing && signedDelta == null
        && !isNewShip && !isRankedOnlyPeriod;

    if (stacked) {
        if (periodOnlyCollapse) {
            return (
                <span className="tabular-nums flex flex-col items-start" title={tooltip}>
                    {periodEl}
                </span>
            );
        }
        if (newWithoutLifetime || rankedOnlyBadge) {
            return (
                <span className="tabular-nums flex flex-col items-start" title={tooltip}>
                    {periodEl}
                    {deltaEl}
                </span>
            );
        }
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
    if (periodOnlyCollapse) {
        return (
            <span
                className="tabular-nums inline-grid grid-cols-[3.5rem] items-baseline whitespace-nowrap"
                title={tooltip}
            >
                <span className="text-right">{periodEl}</span>
            </span>
        );
    }
    if (newWithoutLifetime || rankedOnlyBadge) {
        return (
            <span
                className="tabular-nums inline-grid grid-cols-[3.5rem_4rem] gap-2 items-baseline whitespace-nowrap"
                title={tooltip}
            >
                <span className="text-right">{periodEl}</span>
                <span className="text-right">{deltaEl}</span>
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

// Session (period) win rate — the left of the two split WR columns. Sortable
// by `win_rate`. Just the period %, colored by the WG community thresholds.
const SessionWrCell: React.FC<{ periodWinRate: number }> = ({ periodWinRate }) => (
    <span
        className="tabular-nums font-semibold"
        style={{ color: wrColor(periodWinRate) }}
        title={`Session win rate ${formatPercent(periodWinRate)}`}
    >
        {formatPercent(periodWinRate)}
    </span>
);

// Overall (lifetime) win rate + delta vs the session — the right of the two
// split WR columns. Sortable by `lifetime_win_rate`. When the lifetime baseline
// is missing the cell collapses to the NEW / RANKED / — marker (no delta to
// anchor), matching the legacy combined cell's badge semantics.
const OverallWrCell: React.FC<{
    periodWinRate: number;
    lifetimeWinRate: number | null | undefined;
    deltaWinRate: number | null | undefined;
    isNewShip?: boolean;
    isRankedOnlyPeriod?: boolean;
}> = ({
    periodWinRate, lifetimeWinRate, deltaWinRate,
    isNewShip = false, isRankedOnlyPeriod = false,
}) => {
    const lifetimeMissing = lifetimeWinRate == null;
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
    const tooltip = lifetimeMissing
        ? `Lifetime N/A (never played) · Session ${formatPercent(periodWinRate)}`
        : `Lifetime ${formatPercent(lifetimeWinRate)}${signedDelta != null ? ` (Δ${signedDelta})` : ''} · Session ${formatPercent(periodWinRate)}`;

    const deltaEl = signedDelta != null ? (
        <span className="text-xs font-medium" style={{ color: tone }}>
            Δ{signedDelta}
        </span>
    ) : isNewShip ? (
        <span
            className="text-[10px] font-bold uppercase tracking-wider rounded-sm px-1.5 py-[1px]"
            style={{ color: 'var(--accent-mid)', backgroundColor: 'var(--accent-faint)' }}
            title="First-time random battles in this ship — no prior state to compute a delta against."
        >
            NEW
        </span>
    ) : isRankedOnlyPeriod ? (
        <span
            className="text-[10px] font-bold uppercase tracking-wider rounded-sm px-1.5 py-[1px]"
            style={{ color: 'var(--text-muted)', backgroundColor: 'var(--accent-faint)' }}
            title="All this ship's battles in the window were ranked — no random lifetime to anchor a delta against."
        >
            RANKED
        </span>
    ) : (
        <span className="text-xs text-[var(--text-muted)]">—</span>
    );

    if (lifetimeMissing) {
        return (
            <span className="tabular-nums whitespace-nowrap" title={tooltip}>
                {deltaEl}
            </span>
        );
    }
    return (
        <span
            className="tabular-nums inline-grid grid-cols-[3rem_4rem] gap-2 items-baseline whitespace-nowrap"
            title={tooltip}
        >
            <span className="text-right text-xs" style={{ color: wrColor(lifetimeWinRate) }}>
                {formatPercent(lifetimeWinRate)}
            </span>
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

const InlineSparkline: React.FC<{
    days: BattleHistoryByDay[];
    ariaLabel: string;
    lifetimeBattles?: number | null;
    lifetimeWinRate?: number | null;
}> = ({
    days, ariaLabel, lifetimeBattles, lifetimeWinRate,
}) => {
    if (days.length < 2) return null;
    const W = 100;
    const H = 64;
    const gap = 0.5;
    const barW = (W - gap * (days.length - 1)) / days.length;
    const maxBattles = Math.max(1, ...days.map(d => d.battles));

    // Overlay: a continuous line tracing the player's OVERALL (lifetime) win rate
    // over the window — not the per-day session WR. Anchored to the lifetime
    // baseline (battles + WR as of now), we walk backward day by day, subtracting
    // each day's battles/wins, to reconstruct the lifetime aggregate at the end of
    // every prior day. Because lifetime battle counts dwarf a day's handful of
    // games, this drifts only slightly — so we auto-scale the line to its own
    // min/max range (15% padding) to make that drift visible, rather than mapping
    // the full 0–100% axis. Empty days inherit the prior aggregate, so the line is
    // naturally continuous. Modes without a lifetime baseline (e.g. pure ranked)
    // omit the line.
    const wrPad = 2;
    const wrPoints: string[] = [];
    if (
        lifetimeBattles != null && lifetimeBattles > 0
        && lifetimeWinRate != null
    ) {
        let cumBattles = lifetimeBattles;
        let cumWins = Math.round(lifetimeBattles * (lifetimeWinRate / 100));
        const series: (number | null)[] = new Array(days.length).fill(null);
        for (let i = days.length - 1; i >= 0; i -= 1) {
            series[i] = cumBattles > 0 ? (cumWins / cumBattles) * 100 : null;
            cumBattles -= days[i].battles;
            cumWins -= days[i].wins;
        }
        const vals = series.filter((v): v is number => v != null);
        if (vals.length >= 1) {
            const minV = Math.min(...vals);
            const maxV = Math.max(...vals);
            const range = Math.max(maxV - minV, 0.0001);
            const padding = range * 0.15 + 0.0001;
            const yMin = minV - padding;
            const span = (maxV + padding) - yMin;
            series.forEach((v, i) => {
                if (v == null) return;
                const cx = i * (barW + gap) + barW / 2;
                const cy = wrPad + (1 - (v - yMin) / span) * (H - 2 * wrPad);
                wrPoints.push(`${cx.toFixed(2)},${cy.toFixed(2)}`);
            });
        }
    }

    return (
        <svg
            viewBox={`0 0 ${W} ${H}`}
            width="100%"
            height={H}
            preserveAspectRatio="none"
            aria-label={ariaLabel}
            role="img"
        >
            {days.map((d, i) => {
                const x = i * (barW + gap);
                const totalH = d.battles === 0
                    ? 2
                    : Math.max(4, (d.battles / maxBattles) * (H - 2));
                const totalY = H - totalH;
                const winsH = d.battles > 0 ? (d.wins / d.battles) * totalH : 0;
                const winsY = H - winsH;
                const wr = d.battles > 0 ? (d.wins / d.battles) * 100 : null;
                const losses = d.battles - d.wins;
                const tooltip = d.battles > 0
                    ? `${d.date}: ${d.battles} battles — ${d.wins}W / ${losses}L (${wr!.toFixed(1)}%)`
                    : `${d.date}: no battles`;
                return (
                    <g key={d.date}>
                        <title>{tooltip}</title>
                        <rect x={x} y={totalY} width={barW} height={totalH} fill="rgba(120,120,120,0.25)" rx="0.5" />
                        {winsH > 0 && (
                            <rect x={x} y={winsY} width={barW} height={winsH} fill={wrColor(wr)} opacity={0.85} rx="0.5" />
                        )}
                    </g>
                );
            })}
            {wrPoints.length >= 2 && (
                <polyline
                    points={wrPoints.join(' ')}
                    fill="none"
                    stroke="var(--accent-secondary-mid)"
                    strokeWidth={1.75}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    vectorEffect="non-scaling-stroke"
                />
            )}
            {wrPoints.length === 1 && (
                <circle
                    cx={Number(wrPoints[0].split(',')[0])}
                    cy={Number(wrPoints[0].split(',')[1])}
                    r={1.75}
                    fill="var(--accent-secondary-mid)"
                    vectorEffect="non-scaling-stroke"
                />
            )}
        </svg>
    );
};


type Period = 'daily' | 'weekly' | 'monthly' | 'yearly';

// `year` is intentionally excluded from VISIBLE_WINDOWS — capture started
// 2026-04-28 so a 365-day view won't carry meaningful additional context
// for the next ~12 months. The backend still accepts ?window=year for
// back-compat, but no pill exposes it. Re-add to VISIBLE_WINDOWS once
// >180 days of capture have accumulated.
type BattleHistoryWindow = 'day' | 'week' | 'month' | 'year';
const VISIBLE_WINDOWS: ReadonlyArray<BattleHistoryWindow> = ['day', 'week', 'month'];
const WINDOW_LABEL: Record<BattleHistoryWindow, string> = {
    day: 'Day', week: 'Week', month: 'Month', year: 'Year',
};
const WINDOW_TITLE: Record<BattleHistoryWindow, string> = {
    day: 'Last 24 hours from now (rolling, not today\'s calendar date)',
    week: 'Last 7 days',
    month: 'Last 30 days',
    year: 'Last 365 days',
};
const WINDOW_TITLE_DAY_DISABLED = 'No battles in the last 24 hours';
const WINDOW_HEADER: Record<BattleHistoryWindow, string> = {
    day: 'Last 24 hours',
    week: 'Last 7 days',
    month: 'Last 30 days',
    year: 'Last 365 days',
};

const BattleHistoryCard: React.FC<BattleHistoryCardProps> = ({
    playerName,
    realm,
    days = 7,
    refreshNonce = 0,
}) => {
    const [payload, setPayload] = useState<BattleHistoryPayload | null>(null);
    const [monthByDay, setMonthByDay] = useState<BattleHistoryByDay[]>([]);
    // Lifetime baseline from the month fetch, used to anchor the sparkline's
    // overall-WR overlay line. Null in modes without a lifetime (e.g. combined).
    const [monthLifetime, setMonthLifetime] = useState<{
        battles: number | null; winRate: number | null;
    }>({ battles: null, winRate: null });
    const [error, setError] = useState<Error | null>(null);
    const [loading, setLoading] = useState(true);
    const [window, setWindow] = useState<BattleHistoryWindow>('week');
    const [userPickedWindow, setUserPickedWindow] = useState(false);
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

        const fetchOnce = (cacheBust: number = 0) => {
            // Shared builders so the initial (week/random) fetch dedupes onto
            // PlayerRouteView's parallel prefetch via an identical cacheKey.
            const url = battleHistoryFetchUrl(playerName, realm, window, mode);
            fetchSharedJson<BattleHistoryPayload>(url, {
                label: `BattleHistoryCard:${window}:${mode}`,
                ttlMs: BATTLE_HISTORY_FETCH_TTL_MS,
                cacheKey: battleHistoryCacheKey(playerName, realm, window, mode, cacheBust, refreshNonce),
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
    }, [playerName, realm, window, mode, refreshNonce]);

    // Separate fetch that always retrieves the month window for the sparkline,
    // independent of whichever window the user has selected. fetchSharedJson
    // deduplicates against the main fetch when window === 'month'.
    useEffect(() => {
        let cancelled = false;
        fetchSharedJson<BattleHistoryPayload>(
            battleHistoryFetchUrl(playerName, realm, 'month', mode),
            {
                label: `BattleHistoryCard:sparkline`,
                ttlMs: BATTLE_HISTORY_FETCH_TTL_MS,
                cacheKey: battleHistoryCacheKey(playerName, realm, 'month', mode, 0, refreshNonce),
            },
        )
            .then(({ data }) => {
                if (cancelled) return;
                setMonthByDay(data.by_day);
                setMonthLifetime({
                    battles: data.totals?.lifetime_battles ?? null,
                    winRate: data.totals?.lifetime_win_rate ?? null,
                });
            })
            .catch(() => { /* sparkline stays empty on error */ });
        return () => { cancelled = true; };
    }, [playerName, realm, mode, refreshNonce]);

    // Auto-select the right default mode based on what the player actually
    // has data in. Skipped once the user has explicitly clicked a pill.
    //   - both modes available → keep the initial 'random' default so the
    //     player sees their random battles first (Ranked/All pills remain
    //     available to switch to)
    //   - only one mode available → switch to that one. The edge case here is
    //     a ranked-only player, who gets defaulted to Ranked since there's no
    //     random data to show.
    //   - default initial state is 'random' so the first fetch matches the
    //     pre-Phase-5 contract; the ranked-only auto-switch may then fire one
    //     refetch.
    useEffect(() => {
        if (userPickedMode) return;
        const available = payload?.available_modes;
        if (!available) return;
        if (available.length === 1 && available[0] !== mode) {
            setMode(available[0]);
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
            kdr: computeKdr(r.frags, r.battles),
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
        return rows;
    }, [payload?.by_ship, sort]);

    // Only bail before the FIRST payload (or on error). On a re-fetch — a
    // window/mode switch or a live-update `refreshNonce` rehydrate — we keep
    // rendering the existing `payload` instead of collapsing to null. Returning
    // null mid-refresh unmounted the whole card, so the live-update rehydrate
    // made it blink out and back in, shifting the page content. Holding the
    // prior data lets React reconcile the new rows in place — a smooth swap.
    // (The header live-refresh pill already signals "Loading…" during the pull.)
    if (error || !payload) {
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
    // Hide the card only when the user is at the implicit defaults
    // (mode=random, window=week) AND there's no data — preserves the
    // pre-Phase-5 contract that the card never appears for players with
    // no recent random battles in the default 7d window. Any explicit
    // user pick (different mode or different window) keeps the card
    // visible so the pill row stays reachable.
    if (!payload || (
        !hasBattles
        && mode === 'random' && !userPickedMode
        && window === 'week' && !userPickedWindow
    )) {
        return null;
    }

    const sparkline = (
        <InlineSparkline
            days={buildWindowedDays(monthByDay, 30)}
            ariaLabel="30-day battle activity"
            lifetimeBattles={monthLifetime.battles}
            lifetimeWinRate={monthLifetime.winRate}
        />
    );
    return (
        <section
            data-testid="battle-history-card"
            className="mt-6 rounded-md border border-[var(--accent-faint)] bg-[var(--bg-card)] p-5"
            aria-label="Recent battles"
        >
            <div className="w-full pb-6">{sparkline}</div>
            <hr className="mb-6 border-[var(--accent-faint)]" />
            <header className="flex flex-wrap items-baseline justify-between gap-2">
                <div className="flex flex-wrap items-baseline gap-3">
                    <h2 className="w-36 shrink-0 whitespace-nowrap text-sm font-semibold uppercase tracking-wide text-[var(--text-muted)]">
                        {WINDOW_HEADER[window]}
                    </h2>
                    <div className="flex w-40 shrink-0 items-center gap-1 text-xs" role="group" aria-label="Lookback window">
                        {VISIBLE_WINDOWS.map((w) => {
                            // Only Day is conditionally disabled — Week/Month
                            // always have something useful to render (even if
                            // it's an empty-state with the pill row reachable).
                            const dayDisabled = w === 'day'
                                && payload?.has_recent_24h_activity === false;
                            const isActive = window === w;
                            return (
                                <button
                                    key={w}
                                    type="button"
                                    onClick={() => {
                                        if (dayDisabled) return;
                                        setWindow(w);
                                        setUserPickedWindow(true);
                                    }}
                                    aria-pressed={isActive}
                                    aria-disabled={dayDisabled}
                                    disabled={dayDisabled}
                                    title={dayDisabled ? WINDOW_TITLE_DAY_DISABLED : WINDOW_TITLE[w]}
                                    className={`rounded px-2 py-0.5 transition-colors ${
                                        dayDisabled
                                            ? 'text-[var(--text-muted)] opacity-40 cursor-not-allowed'
                                            : isActive
                                                ? 'bg-[var(--accent-secondary-mid)] text-[var(--bg-card)] font-semibold'
                                                : 'text-[var(--accent-secondary-mid)] hover:text-[var(--text-strong)]'
                                    }`}
                                >
                                    {WINDOW_LABEL[w]}
                                </button>
                            );
                        })}
                    </div>
                </div>
                {visibleModes.length >= 2 && (
                    <div
                        className="flex items-center gap-1 text-xs ml-auto"
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
                {/* Header summary text removed — duplicates the totals tile
                    cells (Battles, Win rate, Avg damage) directly below. */}
            </header>
            {!hasBattles && (
                <p className="mt-4 text-sm text-[var(--text-muted)]">
                    No {MODE_LABEL[mode].toLowerCase()} battles in this window.
                </p>
            )}
            {hasBattles && (() => {
                const kdr = totals!.battles > 0 ? totals!.frags / totals!.battles : 0;
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
                        <div>
                            <div className="text-xs text-[var(--text-muted)]">Avg damage</div>
                            <div className="text-lg font-semibold text-[var(--text-strong)]">{formatInt(totals!.avg_damage)}</div>
                        </div>
                        <div>
                            <div className="text-xs text-[var(--text-muted)]">Frags</div>
                            <div className="text-lg font-semibold text-[var(--text-strong)]">{formatInt(totals!.frags)}</div>
                        </div>
                        <div>
                            <div className="text-xs text-[var(--text-muted)]">Avg KDR</div>
                            <div className="text-lg font-semibold text-[var(--text-strong)]">{kdr.toFixed(2)}</div>
                        </div>
                    </div>
                );
            })()}
            {hasBattles && (
            <div className="mt-6 max-h-[60vh] overflow-auto border-t border-[var(--accent-faint)] pt-4">
                <table className="w-full text-left text-sm">
                    <thead>
                        <tr className="border-b border-[var(--accent-faint)] text-xs uppercase tracking-wide text-[var(--text-muted)]">
                            <SortableTh sortKey="ship_name" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick} tooltip="Ship played in the period. Click to sort A–Z.">Ship</SortableTh>
                            <SortableTh sortKey="ship_tier" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick} tooltip="Ship tier (1–10, with the lowest tier ships being the smallest, less powerful, with the highest tier ships being the largest, most powerful). Click to sort by tier.">Tier</SortableTh>
                            <SortableTh sortKey="ship_type" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick} tooltip="Hull type — DD = Destroyer, CL/CA = Cruiser, BB = Battleship, CV = Carrier, SS = Submarine. Click to sort by type.">Type</SortableTh>
                            <SortableTh sortKey="battles" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick} tooltip="Battles played on this ship in the selected period. Click to sort by volume.">#</SortableTh>
                            <SortableTh sortKey="win_rate" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick} tooltip="Session win rate — wins this period on this ship. Color codes use Wargaming community thresholds. Click to sort by session WR.">WR/S</SortableTh>
                            <SortableTh sortKey="lifetime_win_rate" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick} tooltip="Overall (lifetime) win rate and its delta (Δ) vs this session. Click to sort by overall WR.">WR/O</SortableTh>
                            <SortableTh sortKey="avg_damage" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick} tooltip="Average damage dealt per battle on this ship in the selected period. Click to sort.">Avg dmg</SortableTh>
                            <SortableTh sortKey="kdr" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick} tooltip="Average kills per battle this period (frags ÷ battles). Hover a row to see raw frag + battle counts. Click to sort.">Avg KDR</SortableTh>
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
                                <td className="py-1.5 px-2 text-right">
                                    <SessionWrCell periodWinRate={row.win_rate} />
                                </td>
                                <td className="py-1.5 pr-2 text-right">
                                    <OverallWrCell
                                        periodWinRate={row.win_rate}
                                        lifetimeWinRate={row.lifetime_win_rate}
                                        deltaWinRate={row.delta_win_rate}
                                        isNewShip={row.is_new_ship}
                                        isRankedOnlyPeriod={row.is_ranked_only_period}
                                    />
                                </td>
                                <td className="py-1.5 pr-2 text-right tabular-nums text-[var(--text-strong)]">{formatInt(row.avg_damage)}</td>
                                <td
                                    className="py-1.5 px-2 text-center tabular-nums text-[var(--text-strong)]"
                                    title={`${row.frags} frags / ${row.battles} battles`}
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
