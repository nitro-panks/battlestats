'use client';

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { fetchSharedJson, isAbortError } from '../lib/sharedJsonFetch';
import { degradationMonitor } from '../lib/degradationMonitor';
import { usePlayerRequestSignal } from '../context/PlayerRequestScopeContext';
import wrColor from '../lib/wrColor';
import { chartColors } from '../lib/chartTheme';
import { useTheme } from '../context/ThemeContext';
import { trackEvent } from '../lib/umami';
import ShipStats from './ShipStats';
import BattleHistoryTreemaps, { damageRatioColor } from './BattleHistoryTreemaps';

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
    // Realm-wide average damage on this ship over the trailing 30d random
    // window (the ShipStats baseline convention). Null when the ship's
    // population sample is too thin. Colors the damage treemap.
    ship_pop_avg_damage?: number | null;
}

export interface BattleHistoryByDay {
    date: string;
    battles: number;
    wins: number;
    damage: number;
    frags: number;
}

interface BattleHistoryTotals {
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
    // Present (e.g. "Season 29") when the card is scoped to the player's
    // current ranked season — used to label the ranked header in place of
    // the date-window label. Null/absent for random/combined.
    ranked_season_name?: string | null;
    has_recent_24h_activity?: boolean;
    as_of: string;
    totals: BattleHistoryTotals;
    by_ship: BattleHistoryByShip[];
    by_day: BattleHistoryByDay[];
}

export type BattleHistoryMode = 'random' | 'ranked';
const MODE_LABEL: Record<BattleHistoryMode, string> = {
    random: 'Random Battles', ranked: 'Ranked',
};
const MODE_TITLE: Record<BattleHistoryMode, string> = {
    random: 'Random battles only',
    ranked: 'Ranked battles only (sums across active seasons)',
};
const MODE_NOUN: Record<BattleHistoryMode, string> = {
    random: 'random', ranked: 'ranked',
};

// On-render ranked-observation refresh: when the API responds with
// `X-Ranked-Observation-Pending: true`, a 3-WG-call refresh is in
// flight. Poll the endpoint up to N times so the card rehydrates with
// fresh ranked deltas as soon as the task completes.
const RANKED_PENDING_RETRY_DELAY_MS = 2000;
const RANKED_PENDING_RETRY_LIMIT = 6;

// On-render ship-population baseline warm: `X-Ship-Pop-Pending: true` means
// some damage-treemap baselines (`ship_pop_avg_damage`) were cache-misses and
// a background per-ship warm is running. Poll a bit slower and longer than
// the ranked refresh — each retry hydrates whatever baselines have landed so
// far (tiles colorize progressively); stragglers just stay neutral.
const SHIP_POP_PENDING_RETRY_DELAY_MS = 3000;
const SHIP_POP_PENDING_RETRY_LIMIT = 10;

// Canonical battle-history fetch URL + cache key. Shared by the card's own
// fetch and PlayerRouteView's parallel prefetch so they dedupe onto the same
// in-flight request (sharedJsonFetch keys on cacheKey). Keep these in lockstep —
// if they drift, the prefetch silently becomes a duplicate request instead of a
// dedup (guarded by a test).
export const BATTLE_HISTORY_FETCH_TTL_MS = 60_000;

export const battleHistoryFetchUrl = (
    playerName: string, realm: string, window: string = 'month', mode: string = 'random',
): string =>
    `/api/player/${encodeURIComponent(playerName)}/battle-history/`
    + `?window=${window}&mode=${mode}`
    + `&realm=${encodeURIComponent(realm)}`;

export const battleHistoryCacheKey = (
    playerName: string, realm: string,
    window: string = 'month', mode: string = 'random', cacheBust: number = 0, refreshNonce: number = 0,
): string => `battle-history:${playerName}:${realm}:${window}:${mode}:${cacheBust}:${refreshNonce}`;

/**
 * Eagerly fire the initial (month / random) battle-history fetch so it runs in
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
export const prefetchBattleHistory = (playerName: string, realm: string, signal?: AbortSignal): void => {
    void fetchSharedJson<BattleHistoryPayload>(battleHistoryFetchUrl(playerName, realm), {
        label: 'BattleHistoryCard:month:random',
        ttlMs: BATTLE_HISTORY_FETCH_TTL_MS,
        cacheKey: battleHistoryCacheKey(playerName, realm),
        responseHeaders: ['X-Ranked-Observation-Pending', 'X-Ship-Pop-Pending'],
        signal,
    }).catch(() => { /* the card re-fetches + surfaces errors on mount */ });
};

// Single source of truth for "does this payload light the tab that hosts this
// card?" Mode-scoped since the pill was removed (2026-07-13): the Activity tab
// (random) lights only on in-window random battles; the Ranked tab's section
// (ranked) also accepts recent ranked rows (available_modes) so a season-edge
// zero-window doesn't hide a genuinely ranked-active player.
export const battleHistoryIndicatesActivity = (
    payload: BattleHistoryPayload,
    mode: BattleHistoryMode = 'random',
): boolean => {
    const hasBattles = !!(payload.totals && payload.totals.battles > 0);
    if (mode === 'ranked') {
        return hasBattles || (payload.available_modes ?? []).includes('ranked');
    }
    return hasBattles;
};

interface BattleHistoryCardProps {
    playerName: string;
    realm: string;
    days?: number;
    // Bumped by the live-update poll; folded into the fetch deps + cacheKey so
    // the battle-history re-fetches after a visit-driven refresh lands.
    refreshNonce?: number;
    // `embedded` drops the standalone card chrome (border/bg/margin) so the card
    // can live inside the Insights "Activity" tab panel, which already provides
    // the surrounding surface. Embedded mode also never collapses to bare `null`
    // on the pristine-empty default — it renders the sparkline/header/pills/
    // "no battles" chrome instead, so an active tab is never blank. Hard `null`
    // (error / no payload) is reserved for the no-content states the parent
    // handles by switching tabs.
    embedded?: boolean;
    // Locks the embedded card to its parent panel's height: the card becomes a
    // flex column filling 100% height and the per-ship table flex-fills the space
    // left below the overview, scrolling within it (instead of the fixed 800px
    // cap). Also compacts the table's font. Used by the height-locked Activity
    // tab; the Ranked tab (not height-locked) leaves it off and keeps the cap.
    fillHeight?: boolean;
    // Fixed battle mode for this instance — the card no longer switches modes
    // itself (the Random|Ranked|All pill was removed 2026-07-13; the Ranked
    // tab hosts its own mode="ranked" instance).
    mode?: BattleHistoryMode;
    // Reports whether the card has any activity worth surfacing, so the parent
    // can pick the default tab and dark-out the Activity tab when there's
    // nothing to show. The second arg surfaces the payload's available modes so
    // a ranked-only player can be routed to the Ranked tab. Fired once per
    // (player, realm) from the first resolved payload — never re-fired on user
    // window switches, so toggling to an empty window can't retroactively
    // disable the tab the user is on.
    onAvailabilityChange?: (
        available: boolean,
        availableModes: ReadonlyArray<'random' | 'ranked'>,
    ) => void;
    // Fired when the sparkline's D3 entrance (the WR-line draw-reveal) finishes,
    // so a parent can sequence its own animation after the chart settles. Fires
    // once when the populated reveal completes; not fired when the player has no
    // WR line to draw (no battles / pure-ranked with no lifetime baseline).
    onSparklineAnimationEnd?: () => void;
    // Optional node rendered in the header immediately to the LEFT of the mode
    // caption ("Ranked" / "Random Battles"), sized to sit inline beside it. The
    // Ranked tab passes its History/Activity sub-view toggle here so the control
    // shares the caption's line instead of taking its own row above the card.
    captionLeading?: React.ReactNode;
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

// Format frags/battle to one decimal (e.g. 1.5, 0.0) for the per-ship table;
// the totals-band Frags/Battle tile matches this precision.
const formatTableKdr = (v: number): string => v.toFixed(1);

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

// Session (period) win rate — the left of the two split WR columns. Sortable
// by `win_rate`. Just the period %, colored by the WG community thresholds.
// Avg-damage cell colored on the same diverging player-vs-population scale as
// the ships treemap (red below the ship's realm 30d average, neutral at it,
// green above). Falls back to the plain strong text when no baseline exists.
const AvgDamageCell: React.FC<{
    avgDamage: number;
    popAvgDamage: number | null | undefined;
}> = ({ avgDamage, popAvgDamage }) => {
    const ratio = popAvgDamage != null && popAvgDamage > 0
        ? avgDamage / popAvgDamage
        : null;
    if (ratio == null) {
        return (
            <span
                className="text-[var(--text-strong)]"
                title="No ship-average damage baseline to compare against"
            >
                {formatInt(avgDamage)}
            </span>
        );
    }
    const signedPct = `${ratio >= 1 ? '+' : ''}${((ratio - 1) * 100).toFixed(0)}%`;
    return (
        <span
            className="font-semibold"
            style={{ color: damageRatioColor(ratio) }}
            title={`${signedPct} vs this ship's realm 30d average (${formatInt(popAvgDamage!)}). Color scales with that gap: red below the ship average, gray at it, green above.`}
        >
            {formatInt(avgDamage)}
        </span>
    );
};

const SessionWrCell: React.FC<{ periodWinRate: number }> = ({ periodWinRate }) => (
    <span
        className="tabular-nums font-semibold"
        style={{ color: wrColor(periodWinRate) }}
        title={`Session win rate ${formatPercent(periodWinRate)}`}
    >
        {periodWinRate.toFixed(1)}
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
        : `${deltaWinRate > 0 ? '+' : ''}${deltaWinRate.toFixed(1)}`;
    const tooltip = lifetimeMissing
        ? `Lifetime N/A (never played) · Session ${formatPercent(periodWinRate)}`
        : `Lifetime ${formatPercent(lifetimeWinRate)}${signedDelta != null ? ` (Δ${signedDelta}%)` : ''} · Session ${formatPercent(periodWinRate)}`;

    const deltaEl = signedDelta != null ? (
        <span className="font-medium" style={{ color: tone }}>
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
        <span className="text-[var(--text-muted)]">—</span>
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
            className="tabular-nums inline-grid grid-cols-[5ch_6ch] gap-1 items-baseline whitespace-nowrap"
            title={tooltip}
        >
            <span className="text-right" style={{ color: wrColor(lifetimeWinRate) }}>
                {lifetimeWinRate.toFixed(1)}
            </span>
            <span className="text-right">{deltaEl}</span>
        </span>
    );
};

export const buildWindowedDays = (
    days: BattleHistoryByDay[],
    windowDays: number,
): BattleHistoryByDay[] => {
    const byDate = new Map(days.map((d) => [d.date, d]));
    // Backend buckets battles by UTC calendar date (Django USE_TZ=False, TIME_ZONE=UTC),
    // so anchor the window to UTC "today". Using the browser-local date would put the
    // last slot a day behind the backend bucket for any viewer behind UTC, making
    // today's battles fall outside the window and vanish from the sparkline.
    const now = new Date();
    const todayUTC = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
    const padded: BattleHistoryByDay[] = [];
    for (let i = windowDays - 1; i >= 0; i -= 1) {
        const d = new Date(todayUTC);
        d.setUTCDate(d.getUTCDate() - i);
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
    // Stable per-instance id for the WR-line draw-reveal clipPath (colons from
    // useId aren't valid in a url(#...) fragment, so strip them).
    const wrClipId = `sparkline-wr-${React.useId().replace(/:/g, '')}`;
    if (days.length < 2) return null;
    const W = 100;
    const H = 64;
    const gap = 0.5;
    const barW = (W - gap * (days.length - 1)) / days.length;
    // Hard-cap the bar y-domain at 50 battles/day. Early daily-data backfills
    // observed multi-day gaps as a single spike (e.g. 250 games on one day),
    // which flattened every normal <20-game day to no visible height. We pin the
    // domain to 50 (auto-scaling below that when no day reaches it) and clamp any
    // over-cap day to full height; the true count stays in the tooltip.
    const BAR_CAP = 50;
    const maxBattles = Math.min(BAR_CAP, Math.max(1, ...days.map(d => d.battles)));

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

    // The card mounts with an all-zero padded window first, then the real days
    // land when the async battle-history fetch resolves. Flip this key on that
    // empty→populated transition so the bar-rise (and the WR-line draw) play
    // their entrance once when data arrives — and stay put across live-refresh
    // polls (the key is stable while data is present, so it doesn't re-fire).
    const hasBattleData = days.some(d => d.battles > 0);
    const entranceKey = hasBattleData ? 'ready' : 'empty';

    return (
        <svg
            viewBox={`0 0 ${W} ${H}`}
            width="100%"
            height={H}
            preserveAspectRatio="none"
            aria-label={ariaLabel}
            role="img"
        >
            {/* Keyed on the data-presence transition so the bars remount and
                replay their grow-from-the-x-axis entrance when the real window
                lands (the padded all-zero stubs they mount with don't count). */}
            <g key={entranceKey}>
                {days.map((d, i) => {
                    const x = i * (barW + gap);
                    // Clamp the bar to the capped domain so an over-cap day pins to
                    // full height instead of overflowing the chart.
                    const totalH = d.battles === 0
                        ? 2
                        : Math.max(4, Math.min(1, d.battles / maxBattles) * (H - 2));
                    const totalY = H - totalH;
                    const winsH = d.battles > 0 ? (d.wins / d.battles) * totalH : 0;
                    const winsY = H - winsH;
                    const wr = d.battles > 0 ? (d.wins / d.battles) * 100 : null;
                    const losses = d.battles - d.wins;
                    const tooltip = d.battles > 0
                        ? `${d.date}: ${d.battles} battles — ${d.wins}W / ${losses}L (${wr!.toFixed(1)}%)${d.battles > BAR_CAP ? ` · bar capped at ${BAR_CAP}` : ''}`
                        : `${d.date}: no battles`;
                    return (
                        // Each day's bars rise from the x-axis (scaleY 0→1, origin
                        // bottom) with a small left-to-right stagger so they sweep
                        // in alongside the WR-line draw. Both rects share the group
                        // transform, so the wins overlay stays pinned to the total.
                        <g
                            key={d.date}
                            className="sparkline-bar-rise"
                            style={{ animationDelay: `${i * 18}ms` }}
                        >
                            <title>{tooltip}</title>
                            <rect x={x} y={totalY} width={barW} height={totalH} fill="rgba(120,120,120,0.25)" rx="0.5" />
                            {winsH > 0 && (
                                <rect x={x} y={winsY} width={barW} height={winsH} fill={wrColor(wr)} opacity={0.85} rx="0.5" />
                            )}
                        </g>
                    );
                })}
            </g>
            {wrPoints.length >= 2 && (
                <>
                    {/* Clip rect wiped left→right by CSS (.sparkline-wr-reveal) to
                        "draw" the WR line along its path of travel. Keyed on the
                        same entrance signal as the bars so the draw plays once
                        when data lands and stays put across live-refresh polls. */}
                    <defs>
                        <clipPath id={wrClipId}>
                            <rect
                                key={entranceKey}
                                className="sparkline-wr-reveal"
                                x={0}
                                y={0}
                                width={W}
                                height={H}
                            />
                        </clipPath>
                    </defs>
                    <polyline
                        points={wrPoints.join(' ')}
                        fill="none"
                        stroke="var(--accent-secondary-mid)"
                        strokeWidth={1.75}
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        vectorEffect="non-scaling-stroke"
                        clipPath={`url(#${wrClipId})`}
                    />
                </>
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
// Tooltip shown when a window pill is disabled for having no battles in its
// span. Day's emptiness is a backend flag (has_recent_24h_activity); week/
// month are derived client-side from the month by_day the card already holds.
const WINDOW_TITLE_EMPTY: Record<BattleHistoryWindow, string> = {
    day: 'No battles in the last 24 hours',
    week: 'No battles in the last 7 days',
    month: 'No battles in the last 30 days',
    year: 'No battles in the last 365 days',
};
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
    embedded = false,
    fillHeight = false,
    mode = 'random',
    onAvailabilityChange,
    onSparklineAnimationEnd,
    captionLeading,
}) => {
    const requestSignal = usePlayerRequestSignal();
    const [payload, setPayload] = useState<BattleHistoryPayload | null>(null);
    const [monthByDay, setMonthByDay] = useState<BattleHistoryByDay[]>([]);
    // True once the month fetch below has resolved for the current
    // (player, realm, mode). Gates the derived week/month empty-pill disable
    // so a still-loading card never dims a pill on stale/absent data — pills
    // stay enabled until the data is authoritative (the safe direction).
    const [monthLoaded, setMonthLoaded] = useState(false);
    // Lifetime baseline from the month fetch, used to anchor the sparkline's
    // overall-WR overlay line. Null in modes without a lifetime (e.g. combined).
    const [monthLifetime, setMonthLifetime] = useState<{
        battles: number | null; winRate: number | null;
    }>({ battles: null, winRate: null });
    const [error, setError] = useState<Error | null>(null);
    const [loading, setLoading] = useState(true);
    const [window, setWindow] = useState<BattleHistoryWindow>('month');
    const [userPickedWindow, setUserPickedWindow] = useState(false);
    // Ship selected in the table → its combat profile (ShipStats) shows below
    // the rollup separator. Clicking the same row again clears it (toggle).
    const [selectedShip, setSelectedShip] = useState<{
        ship_id: number; ship_name: string; ship_tier: number | null; ship_type: string | null;
    } | null>(null);
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
                responseHeaders: ['X-Ranked-Observation-Pending', 'X-Ship-Pop-Pending'],
                signal: requestSignal,
            })
                .then(({ data, headers }) => {
                    if (cancelled) return;
                    setPayload(data);
                    setError(null);
                    const rankedPending = headers['X-Ranked-Observation-Pending'] === 'true'
                        && mode === 'ranked';
                    const shipPopPending = headers['X-Ship-Pop-Pending'] === 'true';
                    // Ranked pending keeps its original tighter cadence; the
                    // ship-pop warm gets the slower/longer schedule. When both
                    // are pending the ranked cadence wins (a retry serves both).
                    const retryLimit = rankedPending
                        ? RANKED_PENDING_RETRY_LIMIT : SHIP_POP_PENDING_RETRY_LIMIT;
                    const retryDelay = rankedPending
                        ? RANKED_PENDING_RETRY_DELAY_MS : SHIP_POP_PENDING_RETRY_DELAY_MS;
                    if ((rankedPending || shipPopPending) && pendingAttempts < retryLimit) {
                        pendingAttempts += 1;
                        pollTimer = setTimeout(
                            () => fetchOnce(pendingAttempts),
                            retryDelay * degradationMonitor.getPollIntervalMultiplier(),
                        );
                    }
                })
                .catch((e: unknown) => {
                    // Page navigated away / realm switched — benign.
                    if (isAbortError(e)) return;
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
    }, [playerName, realm, window, mode, refreshNonce, requestSignal]);

    // Reset the loaded gate whenever the entity/mode identity changes, so the
    // month fetch below re-establishes it rather than the empty-pill disable
    // acting on the previous player's data (a refresh-poll re-fetch keeps it).
    useEffect(() => {
        setMonthLoaded(false);
    }, [playerName, realm, mode]);

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
                signal: requestSignal,
            },
        )
            .then(({ data }) => {
                if (cancelled) return;
                setMonthByDay(data.by_day ?? []);
                setMonthLifetime({
                    battles: data.totals?.lifetime_battles ?? null,
                    winRate: data.totals?.lifetime_win_rate ?? null,
                });
                setMonthLoaded(true);
            })
            .catch(() => { /* sparkline stays empty on error */ });
        return () => { cancelled = true; };
    }, [playerName, realm, mode, refreshNonce, requestSignal]);

    // Availability is a one-shot, stable signal: report it from the FIRST
    // resolved payload (or error) per (player, realm), then latch. Basing it on
    // the live `window` would let a user toggling to an empty window flip
    // the signal false and disable the tab they're actively reading.
    const availabilityReportedRef = useRef(false);
    useEffect(() => {
        availabilityReportedRef.current = false;
    }, [playerName, realm]);

    useEffect(() => {
        if (!onAvailabilityChange || availabilityReportedRef.current) return;
        if (error) {
            availabilityReportedRef.current = true;
            onAvailabilityChange(false, []);
            return;
        }
        if (!payload) return;
        availabilityReportedRef.current = true;
        onAvailabilityChange(
            battleHistoryIndicatesActivity(payload, mode),
            payload.available_modes ?? ['random'],
        );
    }, [payload, error, mode, onAvailabilityChange]);

    const [sort, setSort] = useState<{ key: SortKey; direction: SortDirection }>({
        key: 'battles', direction: 'desc',
    });

    const onSortClick = (key: SortKey) => {
        // Compute the next sort outside the state updater so the analytics event
        // fires exactly once (a setState reducer can run twice under StrictMode).
        const direction: SortDirection = sort.key === key
            ? (sort.direction === 'asc' ? 'desc' : 'asc')
            : DEFAULT_DIRECTION[key];
        setSort({ key, direction });
        trackEvent('battle-history-sort', { key, direction, mode, window });
    };

    // Toggle the ShipStats combat panel for a table row. Clicking the already-
    // selected ship hides it; clicking a different ship switches to it.
    const toggleShip = (row: {
        ship_id: number; ship_name: string; ship_tier?: number | null; ship_type?: string | null;
    }, source: 'row' | 'treemap' = 'row') => {
        const isOpening = !selectedShip || selectedShip.ship_id !== row.ship_id;
        setSelectedShip(isOpening
            ? {
                ship_id: row.ship_id,
                ship_name: row.ship_name,
                ship_tier: row.ship_tier ?? null,
                ship_type: row.ship_type ?? null,
            }
            : null);
        trackEvent(isOpening ? 'ship-stats-open' : 'ship-stats-close', {
            ship_id: row.ship_id, source, mode, window, realm,
        });
    };

    // Close from the modal's ✕ button, backdrop click, or Escape (distinct
    // sources for analytics).
    const closeShipStats = (source: 'button' | 'backdrop' | 'escape' = 'button') => {
        if (selectedShip) {
            trackEvent('ship-stats-close', {
                ship_id: selectedShip.ship_id, source, mode, window, realm,
            });
        }
        setSelectedShip(null);
    };

    // Escape closes the combat-profile modal, matching the site's modal
    // convention (StreamerSubmissionModal). Registered only while open.
    const selectedShipId = selectedShip?.ship_id ?? null;
    useEffect(() => {
        if (selectedShipId == null) return;
        const onKey = (e: KeyboardEvent) => {
            if (e.key === 'Escape') {
                trackEvent('ship-stats-close', {
                    ship_id: selectedShipId, source: 'escape', mode, window, realm,
                });
                setSelectedShip(null);
            }
        };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [selectedShipId, mode, window, realm]);

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
    if (error) {
        return null;
    }
    if (!payload) {
        // Embedded in the Activity tab the panel is already active, so a bare
        // null would read as a blank tab. Show a skeleton until the first
        // payload (warmed by PlayerRouteView's prefetch, so usually instant).
        return embedded ? (
            <div
                className="flex animate-pulse items-center justify-center rounded-md border border-[var(--accent-faint)] bg-[var(--bg-surface)] text-sm text-[var(--text-muted)]"
                style={{ minHeight: 360 }}
            >
                Loading battles…
            </div>
        ) : null;
    }
    const totals = payload?.totals;
    const hasBattles = !!(totals && typeof totals.battles === 'number'
        && totals.battles > 0);
    // Standalone: hide the card when the user is at the implicit default
    // (window=month — matching the always-month sparkline) AND there's no
    // data — the card never appears for players with no battles in the default
    // 30d window. An explicit window pick keeps the card visible so the pill
    // row stays reachable.
    // Embedded: never collapse to null here — the hosting tab is already active,
    // so render the chrome (sparkline/header/pills/"no battles") instead. The
    // parent dark-outs the tab and switches away when availability is false.
    if (!embedded && (
        !hasBattles
        && window === 'month' && !userPickedWindow
    )) {
        return null;
    }

    const monthDays = buildWindowedDays(monthByDay, 30);
    const sparkline = (
        <InlineSparkline
            days={monthDays}
            ariaLabel="30-day battle activity"
            lifetimeBattles={monthLifetime.battles}
            lifetimeWinRate={monthLifetime.winRate}
        />
    );
    // Empty-window pill disable. Day emptiness is the backend 24h flag; week/
    // month are derived from the trailing slice of the month by_day the card
    // already holds (gated on monthLoaded so a loading card never dims on
    // stale/absent data). A pill dims + goes unclickable when its window has
    // no battles — but never the window currently being viewed (handled at the
    // call site via isActive), so the active pill stays interactive.
    const sumTrailingBattles = (n: number): number =>
        monthDays.slice(Math.max(0, monthDays.length - n))
            .reduce((s, d) => s + (d.battles || 0), 0);
    const isWindowEmpty = (w: BattleHistoryWindow): boolean => {
        if (w === 'day') return payload?.has_recent_24h_activity === false;
        if (!monthLoaded) return false;
        if (w === 'week') return sumTrailingBattles(7) === 0;
        if (w === 'month') return sumTrailingBattles(30) === 0;
        return false;
    };
    return (
        <section
            data-testid="battle-history-card"
            className={embedded
                ? (fillHeight ? 'flex h-full min-h-0 w-full flex-col' : 'w-full')
                : 'mt-6 rounded-md border border-[var(--accent-faint)] bg-[var(--bg-card)] p-5'}
            aria-label="Recent battles"
        >
            {/* Card order (reordered 2026-07-13): overview block first —
                header (window pills, the most-used control on the page, must
                sit ABOVE the content it re-scopes), summary tiles (the
                headline numbers), then the month-pinned sparkline as a thin
                trend strip closing the overview — followed by the drill-down
                surfaces (treemaps, then the per-ship table). */}
            {/* Every non-table child is shrink-0: under the fillHeight clamp
                (the insights panel's maxHeight) ONLY the scrollable table may
                absorb the squeeze — without this, a tall treemap block makes
                the flex clamp crush the header/stat-strip (clipped giant
                numerals). Harmless outside flex layout. */}
            <header className="flex shrink-0 flex-wrap items-baseline justify-between gap-2">
                <div className="flex flex-wrap items-baseline gap-3">
                    <h2 className="whitespace-nowrap text-sm font-semibold uppercase tracking-wide text-[var(--text-muted)]">
                        {/* Ranked is season-scoped server-side, so label it
                            with the season (e.g. "Season 29") rather than the
                            date-window — the bars/totals/WR are all that
                            season, and the season framing is how players think
                            about ranked. Falls back to the window label. */}
                        {mode === 'ranked' && payload?.ranked_season_name
                            ? payload.ranked_season_name
                            : WINDOW_HEADER[window]}
                    </h2>
                    <div className="flex items-center gap-1 text-xs" role="group" aria-label="Lookback window">
                        {VISIBLE_WINDOWS.map((w) => {
                            // Dim + disable any window with no battles in its
                            // span (day via the backend 24h flag, week/month
                            // derived from the month by_day), but never the
                            // window currently being viewed — the active pill
                            // stays interactive even in an empty span.
                            const isActive = window === w;
                            const disabled = !isActive && isWindowEmpty(w);
                            return (
                                <button
                                    key={w}
                                    type="button"
                                    onClick={() => {
                                        if (disabled) return;
                                        if (!isActive) {
                                            trackEvent(`player-history-${w}`, { realm });
                                        }
                                        setWindow(w);
                                        setUserPickedWindow(true);
                                    }}
                                    aria-pressed={isActive}
                                    aria-disabled={disabled}
                                    disabled={disabled}
                                    title={disabled ? WINDOW_TITLE_EMPTY[w] : WINDOW_TITLE[w]}
                                    className={`rounded px-2 py-0.5 transition-colors ${
                                        disabled
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
                {/* Right group: an optional caption-leading control (the Ranked
                    tab's sub-view toggle) sits inline to the LEFT of the static
                    mode caption. Caption: Random Battles on the Activity tab,
                    Ranked on the Ranked tab. Replaced the Random|Ranked|All pill
                    (removed 2026-07-13: 35 sessions/90d ever touched it). */}
                <div className="ml-auto flex items-center gap-1.5">
                    {captionLeading}
                    <span
                        // Same bg/text pairing as the page-top stat boxes
                        // (Win Rate / PvP Battles / …) so the caption reads as
                        // part of that family rather than a bright action chip.
                        className="rounded bg-[var(--accent-faint)] px-2 py-0.5 text-xs font-semibold text-[var(--accent-dark)]"
                        title={MODE_TITLE[mode]}
                    >
                        {MODE_LABEL[mode]}
                    </span>
                </div>
                {/* Header summary text removed — duplicates the totals tile
                    cells (Battles, Win rate, Avg damage) directly below. */}
            </header>
            {!hasBattles && (
                <p className="mt-4 text-sm text-[var(--text-muted)]">
                    No {MODE_NOUN[mode]} battles in this window.
                </p>
            )}
            {hasBattles && (() => {
                const kdr = totals!.battles > 0 ? totals!.frags / totals!.battles : 0;
                // Distinct ships played in the window — one per by_ship row.
                const distinctShips = payload?.by_ship?.length ?? 0;
                // The WR cluster is Window WR + WR Δ only — the lifetime
                // "Overall WR" tile was dropped 2026-07-13 as a duplicate of
                // the page-top Win Rate card; the Δ tile keeps the lifetime
                // comparison (window minus lifetime) without restating it.
                const deltaWr = totals!.delta_win_rate;
                const deltaTone = deltaWr == null
                    ? 'var(--text-muted)'
                    : deltaWr > 0 ? '#74c476' : deltaWr < 0 ? '#a50f15' : 'var(--text-muted)';
                // Three logical groups spanning the full card width (matching
                // the sparkline below): count (Battles) left, the WR cluster
                // centered by justify-between, the combat cluster flush right
                // with right-aligned tiles. Mobile keeps a flat 2-col grid —
                // the `contents` wrappers collapse so all seven tiles flow
                // into it; at sm they become flex clusters.
                return (
                    // Subtle neutral-gray wash (the sparkline bars' neutral,
                    // lighter) sets the summary band off from the chart
                    // surfaces around it — deliberately gray, not the blue
                    // accent-faint tint, so it stays quiet in both themes.
                    // Three bordered cells — one per column — inside a bordered
                    // gray "background" box. Cells overlap the shared edge into a
                    // single 1px rule (border-t stacked on mobile, border-l in the
                    // sm row); content is centered in each cell.
                    <div className="mt-4 flex shrink-0 flex-col overflow-hidden rounded-md border border-[var(--border)] bg-[rgba(120,120,120,0.12)] sm:flex-row">
                        <div className="flex flex-1 items-end px-4 py-3">
                        <div className="flex-1 text-center">
                            <div className="text-xs text-[var(--text-muted)]">Battles</div>
                            <div className="font-['Courier_New',Courier,monospace] text-2xl font-semibold text-[var(--text-strong)]">{formatInt(totals!.battles)}</div>
                        </div>
                        <div className="flex-1 text-center">
                            <div className="text-xs text-[var(--text-muted)]">Ships</div>
                            <div className="font-['Courier_New',Courier,monospace] text-2xl font-semibold text-[var(--text-strong)]">{formatInt(distinctShips)}</div>
                        </div>
                        </div>
                        <div className="flex flex-1 items-end border-t border-[var(--border)] px-4 py-3 sm:border-l sm:border-t-0">
                        <div className="flex-1 text-center">
                            <div className="text-xs text-[var(--text-muted)]">Window WR</div>
                            <div
                                className="font-['Courier_New',Courier,monospace] text-2xl font-semibold tabular-nums"
                                style={{ color: wrColor(totals!.win_rate) }}
                                title={`Win rate over this window — ${formatPercent(totals!.win_rate)}`}
                            >
                                {formatPercent(totals!.win_rate)}
                            </div>
                        </div>
                        <div className="flex-1 text-center">
                            <div className="text-xs text-[var(--text-muted)]">WR Δ</div>
                            {deltaWr != null ? (
                                // A step smaller than the primary stats — the
                                // delta qualifies Window WR rather than
                                // standing on its own.
                                <div
                                    className="font-['Courier_New',Courier,monospace] text-lg leading-8 font-semibold tabular-nums"
                                    style={{ color: deltaTone }}
                                    title={`Session win rate ${deltaWr > 0 ? 'above' : deltaWr < 0 ? 'below' : 'even with'} lifetime by ${Math.abs(deltaWr).toFixed(1)}%`}
                                >
                                    {deltaWr > 0 ? '+' : ''}{deltaWr.toFixed(1)}%
                                </div>
                            ) : (
                                <div
                                    className="font-['Courier_New',Courier,monospace] text-lg leading-8 font-semibold text-[var(--text-muted)]"
                                    title="No lifetime baseline to compare against"
                                >
                                    —
                                </div>
                            )}
                        </div>
                        </div>
                        <div className="flex flex-1 items-end border-t border-[var(--border)] px-4 py-3 sm:border-l sm:border-t-0">
                        <div className="flex-1 text-center">
                            <div className="text-xs text-[var(--text-muted)]">Avg damage</div>
                            <div className="font-['Courier_New',Courier,monospace] text-2xl font-semibold text-[var(--text-strong)]">{formatInt(totals!.avg_damage)}</div>
                        </div>
                        {/* One per-battle frag tile — the old "Frags" total
                            (low-signal) and "Avg KDR" (which was already
                            frags ÷ battles under a misleading name) collapsed
                            into it, 2026-07-13. The raw total lives in the
                            tooltip; the table's F/B column is this same
                            metric per ship. */}
                        <div className="flex-1 text-center">
                            <div className="text-xs text-[var(--text-muted)]">Frags/Battle</div>
                            <div
                                className="font-['Courier_New',Courier,monospace] text-2xl font-semibold text-[var(--text-strong)]"
                                title={`${formatInt(totals!.frags)} frags over ${formatInt(totals!.battles)} battles this window`}
                            >
                                {kdr.toFixed(1)}
                            </div>
                        </div>
                        </div>
                    </div>
                );
            })()}
            <div
                className="mt-5 w-full shrink-0 pb-5"
                // The WR-line draw-reveal is the sparkline's longest entrance
                // animation; its bubbled animationend (caught here at the painted
                // wrapper, since the rect itself lives in <defs>) marks "the D3
                // sparkline finished". Filter by name so the 30 bar-rise events
                // don't trigger it. Idempotent for the caller.
                onAnimationEnd={(e) => {
                    if (e.animationName === 'sparkline-wr-reveal') {
                        onSparklineAnimationEnd?.();
                    }
                }}
            >
                {sparkline}
            </div>
            {/* Spacer where the sparkline/treemap rule used to be — the rule is
                gone but its 20px slot stays so the rhythm is unchanged. */}
            <div className="h-5 shrink-0" aria-hidden />
            {/* Three mini-treemaps summarizing the SELECTED window+mode (the
                same rows as the table below) — unlike the sparkline, which is
                pinned to the month window. Area = volume, color = win rate. */}
            {hasBattles && (
                <div className="shrink-0">
                    <BattleHistoryTreemaps
                        byShip={payload.by_ship ?? []}
                        selectedShipId={selectedShip?.ship_id ?? null}
                        onShipClick={(row) => toggleShip(row, 'treemap')}
                    />
                </div>
            )}
            {/* Combat profile for the ship selected in the treemaps or the
                table below — a modal overlay hovering above the card (an inline
                panel here used to push the ships table out of the clamped tab
                panel and into the content below it). Portaled to <body> so no
                ancestor overflow/transform can clip or misanchor the fixed
                overlay. Toggled by ship clicks; backdrop click, Escape, and the
                panel's ✕ all close it. */}
            {hasBattles && selectedShip ? createPortal(
                <div
                    className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4 py-6"
                    onClick={(e) => {
                        if (e.target === e.currentTarget) closeShipStats('backdrop');
                    }}
                    role="dialog"
                    aria-modal="true"
                    aria-label={`Combat profile for ${selectedShip.ship_name || `Ship ${selectedShip.ship_id}`}`}
                >
                    <div className="max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-md shadow-xl">
                        <ShipStats
                            playerName={playerName}
                            realm={realm}
                            shipId={selectedShip.ship_id}
                            shipName={selectedShip.ship_name}
                            onClose={closeShipStats}
                        />
                    </div>
                </div>,
                document.body,
            ) : null}
            {/* fillHeight (Activity / Ranked-activity): the table flex-fills the
                space left below the overview and scrolls within the panel's
                clamp — with every sibling shrink-0 it is the ONLY child that
                absorbs the squeeze; the min-h floor keeps a few rows visible
                even under a tall treemap block (the panel grows past the cap in
                that extreme rather than crushing the table away). Other
                embedded uses (Ranked history) keep the tall 800px cap;
                standalone keeps the compact 60vh. */}
            {hasBattles && (
            <div className={`mt-2 overflow-auto ${fillHeight ? 'min-h-[200px] flex-1' : embedded ? 'max-h-[800px]' : 'max-h-[60vh]'}`}>
                <table className="w-full min-w-[34rem] text-left text-base">
                    <thead>
                        <tr className="border-b border-[var(--accent-faint)] text-xs uppercase tracking-wide text-[var(--text-muted)]">
                            <SortableTh sortKey="ship_name" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick} tooltip="Ship played in the period. Click to sort A–Z.">Ship</SortableTh>
                            <SortableTh sortKey="ship_tier" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick} tooltip="Ship tier (1–10, with the lowest tier ships being the smallest, less powerful, with the highest tier ships being the largest, most powerful). Click to sort by tier.">Tier</SortableTh>
                            <SortableTh sortKey="ship_type" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick} tooltip="Hull type — DD = Destroyer, CL/CA = Cruiser, BB = Battleship, CV = Carrier, SS = Submarine. Click to sort by type.">Type</SortableTh>
                            <SortableTh sortKey="battles" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick} tooltip="Battles played on this ship in the selected period. Click to sort by volume.">#</SortableTh>
                            <SortableTh sortKey="win_rate" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick} tooltip="Win rate over the selected window on this ship. Color codes use Wargaming community thresholds. Click to sort by window WR.">WR %</SortableTh>
                            <SortableTh sortKey="lifetime_win_rate" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick} tooltip="Overall (lifetime) win rate and its delta (Δ) vs this window. Click to sort by overall WR.">Overall WR %</SortableTh>
                            <SortableTh sortKey="avg_damage" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick} tooltip="Average damage dealt per battle on this ship in the selected period, colored against the ship's realm-wide 30-day average — red below it, gray at it, green above. Click to sort.">Avg dmg</SortableTh>
                            <SortableTh sortKey="kdr" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick} tooltip="Frags/Battle — average kills per battle this period (frags ÷ battles). Hover a row to see raw frag + battle counts. Click to sort.">F/B</SortableTh>
                        </tr>
                    </thead>
                    <tbody>
                        {visibleByShip.map((row) => (
                            <tr
                                key={row.ship_id}
                                onClick={() => toggleShip(row)}
                                className={`cursor-pointer border-b border-[var(--accent-faint)] transition-colors last:border-b-0 hover:bg-[var(--accent-faint)] ${selectedShip?.ship_id === row.ship_id ? 'bg-[var(--accent-faint)]' : ''}`}
                            >
                                <td className="py-1.5 align-middle pr-2 text-[var(--text-strong)]">
                                    {/* Real button on the name keeps the row keyboard-
                                        accessible without overriding the <tr> row role. */}
                                    <button
                                        type="button"
                                        onClick={(event) => { event.stopPropagation(); toggleShip(row); }}
                                        aria-expanded={selectedShip?.ship_id === row.ship_id}
                                        aria-label={`Toggle combat profile for ${row.ship_name || `Ship ${row.ship_id}`}`}
                                        className="text-left font-medium text-[var(--text-strong)] underline-offset-2 hover:underline"
                                    >
                                        {row.ship_name || `Ship ${row.ship_id}`}
                                    </button>
                                </td>
                                <td className="py-1.5 align-middle px-2 text-center font-['Courier_New',Courier,monospace] tabular-nums text-[var(--text-strong)]">
                                    {row.ship_tier ?? '—'}
                                </td>
                                <td
                                    className="py-1.5 align-middle px-2 text-center text-sm font-semibold"
                                    style={{ color: shipTypeColor(row.ship_type) }}
                                    title={row.ship_type ?? ''}
                                >
                                    {shipTypeShort(row.ship_type)}
                                </td>
                                <td className="py-1.5 align-middle px-2 text-center font-['Courier_New',Courier,monospace] tabular-nums text-[var(--text-strong)]">{formatInt(row.battles)}</td>
                                <td className="py-1.5 align-middle px-2 text-right font-['Courier_New',Courier,monospace]">
                                    <SessionWrCell periodWinRate={row.win_rate} />
                                </td>
                                <td className="py-1.5 align-middle pr-2 text-right font-['Courier_New',Courier,monospace]">
                                    <OverallWrCell
                                        periodWinRate={row.win_rate}
                                        lifetimeWinRate={row.lifetime_win_rate}
                                        deltaWinRate={row.delta_win_rate}
                                        isNewShip={row.is_new_ship}
                                        isRankedOnlyPeriod={row.is_ranked_only_period}
                                    />
                                </td>
                                <td className="py-1.5 align-middle pr-2 text-right font-['Courier_New',Courier,monospace] tabular-nums">
                                    <AvgDamageCell
                                        avgDamage={row.avg_damage}
                                        popAvgDamage={row.ship_pop_avg_damage}
                                    />
                                </td>
                                <td
                                    className="py-1.5 align-middle px-2 text-center font-['Courier_New',Courier,monospace] tabular-nums text-[var(--text-strong)]"
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
