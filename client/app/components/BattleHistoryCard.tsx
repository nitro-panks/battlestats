'use client';

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { fetchSharedJson, isAbortError } from '../lib/sharedJsonFetch';
import { degradationMonitor } from '../lib/degradationMonitor';
import { usePlayerRequestSignal } from '../context/PlayerRequestScopeContext';
import wrColor from '../lib/wrColor';
import { chartColors } from '../lib/chartTheme';
import { useTheme } from '../context/ThemeContext';
import { useT } from '../context/LocaleContext';
import type { StringKey } from '../i18n';
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
    // Exact career wins for this mode. Added so the strip's overall-WR line can
    // anchor to integers: `lifetime_win_rate` beside it is rounded to ONE
    // decimal by the backend, which is coarser than the two decimals the page
    // states career WR to. Optional — a backend older than this field simply
    // leaves the strip on the rounded anchor.
    lifetime_wins?: number | null;
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
    as_of: string;
    totals: BattleHistoryTotals;
    by_ship: BattleHistoryByShip[];
    by_day: BattleHistoryByDay[];
}

export type BattleHistoryMode = 'random' | 'ranked';
// Key maps, not literals: the label is resolved through t() at the call site
// so it follows the live locale. Kept as tables (rather than inlined switches)
// because every consumer indexes them by the mode/window it already holds.
const MODE_LABEL_KEY: Record<BattleHistoryMode, StringKey> = {
    random: 'battleHistory.mode.random', ranked: 'battleHistory.mode.ranked',
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

// The window the card opens on, and therefore the window the prefetch must warm.
// These two are one decision: the builders below default to it so the prefetch
// can never drift off the window the card actually mounts with — a drift would
// cost every player view a second query on this endpoint family rather than the
// dedup the prefetch exists to get. Also the strip's fixed domain (see
// STRIP_DOMAIN_DAYS).
//
// The card OPENS on Month (30 days). `ninety` is one pill click away and is
// also the automatic fallback for a player with nothing in the last 30 days
// — see the fallback effect in the component. Note this is no longer the
// window the strip fetches: the strip always pulls STRIP_FETCH_WINDOW (90d)
// so the 90d pill has its data ready to animate into and the fallback has
// something to decide on. That costs a second request per card — month for
// the view, ninety for the strip — which cannot be collapsed, because
// totals and by_ship are aggregated server-side per window and a 30d view is
// not derivable from a 90d payload.
export const DEFAULT_BATTLE_HISTORY_WINDOW = 'month';

// The window the trend strip always fetches, independent of the pill. Wider
// than the default view on purpose: the strip is the backdrop the 90d pill
// animates out to, and the emptiness of the trailing 30 days (which decides
// the fallback) is read off it.
export const STRIP_FETCH_WINDOW = 'ninety';

export const battleHistoryFetchUrl = (
    playerName: string, realm: string,
    window: string = DEFAULT_BATTLE_HISTORY_WINDOW, mode: string = 'random',
): string =>
    `/api/player/${encodeURIComponent(playerName)}/battle-history/`
    + `?window=${window}&mode=${mode}`
    + `&realm=${encodeURIComponent(realm)}`;

export const battleHistoryCacheKey = (
    playerName: string, realm: string,
    window: string = DEFAULT_BATTLE_HISTORY_WINDOW, mode: string = 'random',
    cacheBust: number = 0, refreshNonce: number = 0,
): string => `battle-history:${playerName}:${realm}:${window}:${mode}:${cacheBust}:${refreshNonce}`;

/**
 * Eagerly fire the initial (Month / random) battle-history fetch so it runs in
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
        label: `BattleHistoryCard:${DEFAULT_BATTLE_HISTORY_WINDOW}:random`,
        ttlMs: BATTLE_HISTORY_FETCH_TTL_MS,
        cacheKey: battleHistoryCacheKey(playerName, realm),
        responseHeaders: ['X-Ranked-Observation-Pending', 'X-Ship-Pop-Pending'],
        signal,
    }).catch(() => { /* the card re-fetches + surfaces errors on mount */ });
};

// Single source of truth for "does this payload light the tab that hosts this
// card?" Mode-scoped since the pill was removed (2026-07-13): the Activity tab
// (random) lights only on in-window random battles; the Ranked tab's section
// (ranked) also accepts `available_modes` so a season-edge zero-window doesn't
// hide a genuinely ranked-active player — ranked totals are scoped to the
// player's CURRENT season server-side, so someone who played the previous
// season days ago has zero in-window battles while still being active.
//
// That disjunct is only sound because `available_modes` is scoped to the
// REQUESTED WINDOW server-side (views.py `_build_battle_history_payload`).
// It used to be a probe over all dates, making its width the rollup retention
// (BATTLE_HISTORY_ARCHIVE_RETENTION_DAYS; prod=105, pinned in
// server/deploy/deploy_to_droplet.sh) while the pills, the strip and the
// 30d->90d fallback were all judged on the window — so a player whose last ranked
// battles were 66 days ago lit the
// Ranked tab onto an activity view with nothing to draw (2026-08-24,
// `briansayshello` NA). If that probe is ever widened again, this breaks.
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
    // Exact career random-battle counts, when the host has them. The payload's
    // `totals.lifetime_win_rate` is rounded to ONE decimal by the backend
    // (views.py: round(100.0 * wins / battles, 1)), so a strip anchored to it
    // lands up to 0.05% off the career WR the page header shows — 63.50 against
    // 63.53 for nekonomae/na. These override that anchor with the integers the
    // header itself is computed from, so the newest day's readout equals the
    // header exactly. Random mode only; the ranked baseline is a different
    // aggregate and still comes from the payload.
    overallBattles?: number | null;
    overallWins?: number | null;
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
    // Reports the window this card is currently scoped to, so a sibling surface
    // can re-scope with the pill. Unlike onAvailabilityChange this fires EVERY
    // time the window moves, and covers all three ways it can: the stored-pick
    // restore, the automatic 90d fallback, and a pill click. A host that wires
    // only the click would leave a reader whose sticky pick is 90d looking at a
    // 30d sibling.
    onWindowChange?: (window: BattleHistoryWindow) => void;
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
            {/* whitespace-nowrap is load-bearing for ko/ja, not cosmetic: CJK
                has no spaces, so 티어/艦種 wrap PER CHARACTER in a narrow th
                and the header column collapses to a vertical stack. English
                never showed it (its labels are single unbreakable words), and
                jsdom does no layout, so only a screenshot catches it. */}
            <span className="whitespace-nowrap">{children}</span>
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

// Shared bar geometry for the trend strip: `n` bars laid across a 0–100 viewBox
// x-domain separated by a fixed gap. The bracket beneath the strip runs the same
// numbers, so its ends land exactly on bar edges at any container width.
const STRIP_VIEW_W = 100;
const STRIP_BAR_GAP = 0.5;
const stripBarWidth = (n: number): number =>
    (STRIP_VIEW_W - STRIP_BAR_GAP * (n - 1)) / n;

// A measure line under the strip reporting which slice of the strip's SHOWN
// domain the selected window pill actually covers: a rule with
// a tick at each end, right-anchored to the newest day and growing leftward into
// the past as the window widens.
//
// ALWAYS MOUNTED, never conditionally rendered and never keyed on the data-
// presence signal. CSS transitions do not run on first render, so a bracket that
// mounts on demand would pop into place with no motion on 90d → Month — and the
// motion is the whole point. Only opacity and the group transform are driven from
// state. At the full domain the bracket expands to the strip's entire width as it
// fades to nothing, dissolving exactly as it stops carrying information.
const WindowRangeBracket: React.FC<{ spanDays: number; domainDays: number }> = ({
    spanDays, domainDays,
}) => {
    const H = 9;
    const barW = stripBarWidth(domainDays);
    // Left edge of the span's first bar. The right edge stays pinned at
    // STRIP_VIEW_W — the newest day — so the bracket only ever grows leftward.
    const left = (domainDays - spanDays) * (barW + STRIP_BAR_GAP);
    const scaleX = (STRIP_VIEW_W - left) / STRIP_VIEW_W;
    return (
        <svg
            viewBox={`0 0 ${STRIP_VIEW_W} ${H}`}
            width="100%"
            height={H}
            preserveAspectRatio="none"
            aria-hidden="true"
            focusable="false"
            // Sits 5px clear of the strip's baseline so it reads as a separate
            // measure line rather than a chart axis. `overflow: visible` keeps
            // the end ticks whole: they are centred on x=0 and x=100, so half
            // of each 2px stroke would otherwise be clipped by the viewport.
            // The 1px that hangs past each edge does not reach a scrolling
            // ancestor (verified at 900px and 480px) — padding the viewBox
            // instead would break the bracket's exact bar-edge alignment.
            style={{ marginTop: 5, overflow: 'visible' }}
        >
            {/* Drawn once as a unit spanning the full domain, then placed by a
                single transitioned transform — see .window-range-bracket, which
                also pins transform-box/transform-origin so this scales about the
                viewBox origin rather than its centre. */}
            <g
                data-testid="window-range-bracket"
                className="window-range-bracket"
                style={{
                    transform: `translate(${left.toFixed(3)}px, 0px) scale(${scaleX.toFixed(5)}, 1)`,
                    opacity: spanDays >= domainDays ? 0 : 1,
                }}
                stroke="var(--text-muted)"
                strokeWidth={2}
                strokeLinecap="round"
                vectorEffect="non-scaling-stroke"
            >
                <line x1={0} y1={1.5} x2={0} y2={7.5} vectorEffect="non-scaling-stroke" />
                <line
                    x1={0}
                    y1={4.5}
                    x2={STRIP_VIEW_W}
                    y2={4.5}
                    vectorEffect="non-scaling-stroke"
                />
                <line
                    x1={STRIP_VIEW_W}
                    y1={1.5}
                    x2={STRIP_VIEW_W}
                    y2={7.5}
                    vectorEffect="non-scaling-stroke"
                />
            </g>
        </svg>
    );
};

// Hard cap on the bar y-domain: 50 battles/day. Early daily-data backfills
// observed multi-day gaps as a single spike (e.g. 250 games on one day), which
// flattened every normal <20-game day to no visible height. We pin the domain
// to 50 (auto-scaling below that when no day reaches it) and clamp any over-cap
// day to full height. The clamp is not annotated: the readout reports the day's
// true battle count on every day, capped or not, so there is nothing the reader
// has to be told twice.
const STRIP_BAR_CAP = 50;

// The strip's readout line, sitting directly above the bars in a FIXED-height
// row: the hovered day and the player's overall (lifetime) win rate at the end
// of it, and nothing else. It is never conditionally mounted — a row that
// appeared on hover would shove the treemaps below it down by its own height on
// every mouse-over. Idle state reads the newest day (the Google Finance
// convention: the crosshair moves the quote, it doesn't conjure it).
const StripReadoutRow: React.FC<{
    date: string | null;
    overall: number | null;
    /** Day-over-day change in that overall WR, or null when there is no prior
     *  day to measure against. Shown only when it rounds off zero: a day the
     *  line did not move has no delta to report. */
    delta: number | null;
    /** The day's own record. Right-justified at the far end of the row: the
     *  left cluster reports the CAREER line's position, this reports what the
     *  player actually did on the hovered day. Omitted on a day with no
     *  battles — "0W 0L" is noise, and the bar stub already says it. */
    wins: number | null;
    battles: number | null;
    live: boolean;
}> = ({ date, overall, delta, wins, battles, live }) => (
    <div
        data-testid="strip-readout"
        // Typeface and weight match the per-ship table's ship-name cell: the body
        // face (Inter) at font-medium, not the Courier the numeric columns use.
        // `tabular-nums` stays — the crosshair sweeps continuously, and
        // proportional digits would make the row twitch sideways on every frame.
        className="flex h-5 items-center gap-2 overflow-hidden whitespace-nowrap text-sm font-medium leading-5 tabular-nums"
        style={{ opacity: date == null ? 0 : live ? 1 : 0.72 }}
    >
        {date != null && (
            <>
                <span className="text-[var(--text-strong)]">{date}</span>
                {overall != null && (
                    <span style={{ color: wrColor(overall) }}>{overall.toFixed(2)}%</span>
                )}
                {delta != null && Math.abs(delta) >= 0.005 && (
                    // Same two tones the per-ship table's Δ column uses, so a
                    // rising line reads the same colour in both places.
                    <span
                        data-testid="strip-readout-delta"
                        style={{ color: delta > 0 ? '#74c476' : '#a50f15' }}
                    >
                        {delta > 0 ? '+' : '−'}{Math.abs(delta).toFixed(2)}
                    </span>
                )}
                {battles != null && battles > 0 && wins != null && (
                    // `ml-auto` rather than a spacer element: it pins this to the
                    // right edge without disturbing the gap-2 rhythm of the left
                    // cluster. Deliberately untinted — the row already spends its
                    // two colours on the WR value and the delta, and a green W /
                    // red L here would read as a third, competing verdict.
                    <span
                        data-testid="strip-readout-record"
                        className="ml-auto text-[var(--text-muted)]"
                    >
                        {/* The W/L letters ride at 0.75em: they are unit labels,
                            not data. Shrinking them lets the two counts carry the
                            row's weight while the letters stay legible enough to
                            say which is which. Baseline-aligned by default, which
                            is what keeps the pair reading as one token. */}
                        {wins}<span className="text-[0.75em]">W</span>
                        {' '}
                        {battles - wins}<span className="text-[0.75em]">L</span>
                    </span>
                )}
            </>
        )}
    </div>
);

const InlineSparkline: React.FC<{
    days: BattleHistoryByDay[];
    /** How many trailing days are SHOWN. `days` may hold more; the rest are
     *  positioned off the left edge and clipped, so a domain change glides. */
    domainDays: number;
    ariaLabel: string;
    lifetimeBattles?: number | null;
    /** Exact career wins, when the host has them. Preferred over deriving a win
     *  count from `lifetimeWinRate`, which the backend has already rounded. */
    lifetimeWins?: number | null;
    lifetimeWinRate?: number | null;
}> = ({
    days, domainDays, ariaLabel, lifetimeBattles, lifetimeWins, lifetimeWinRate,
}) => {
    // Stable per-instance ids for the WR-line draw-reveal clipPath and the
    // hovered bar's inner-halo clip (colons from useId aren't valid in a
    // url(#...) fragment, so strip them).
    const uid = React.useId().replace(/:/g, '');
    const wrClipId = `sparkline-wr-${uid}`;
    const haloClipId = `sparkline-halo-${uid}`;
    const stripClipId = `sparkline-strip-${uid}`;
    // Crosshair position, in viewBox x (0–100) rather than a day index: the rule
    // tracks the pointer CONTINUOUSLY, at pointer granularity, instead of
    // snapping between 30 discrete stops. Storing viewBox units also survives a
    // domain change for free — the coordinate space is the same at 30d and 90d.
    // Declared above the short-data bail below; a hook after an early return is
    // a conditional hook call.
    const [hoverX, setHoverX] = React.useState<number | null>(null);
    // Pointer events fire faster than paint. Coalesce them onto one frame so a
    // fast sweep doesn't queue a re-render of every bar per event.
    const frame = React.useRef<number | null>(null);
    const pending = React.useRef<number | null>(null);
    React.useEffect(() => () => {
        if (frame.current != null) cancelAnimationFrame(frame.current);
    }, []);
    if (days.length < 2) return null;
    const W = STRIP_VIEW_W;
    const H = 64;
    const gap = STRIP_BAR_GAP;
    // Geometry is the SHOWN domain's, not the array's. `offset` is how many
    // leading days fall outside it; those bars get a negative x and are clipped
    // by the viewport rather than unmounted, so widening/narrowing the domain
    // moves every bar along one continuous path instead of popping half of them
    // in and out of existence.
    const shown = Math.min(domainDays, days.length);
    const offset = days.length - shown;
    const barW = stripBarWidth(shown);
    // Every scale below is computed over the VISIBLE days only. Carrying the
    // 90-day maximum into the 30-day view would flatten it against a peak the
    // reader can no longer see.
    const visible = days.slice(offset);
    const maxBattles = Math.min(STRIP_BAR_CAP, Math.max(1, ...visible.map(d => d.battles)));
    const centerX = (i: number): number => i * (barW + gap) + barW / 2;
    // One source for the bar rectangle, so the hover halo cannot drift off the
    // bar it is meant to outline. Clamped to the capped domain, so an over-cap
    // day pins to full height instead of overflowing the chart.
    const barRect = (d: BattleHistoryByDay): { h: number; y: number } => {
        const h = d.battles === 0
            ? 2
            : Math.max(4, Math.min(1, d.battles / maxBattles) * (H - 2));
        return { h, y: H - h };
    };

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
    //
    // `wrSeries` is kept at VISIBLE-day indexing and hoisted out of the guard
    // below, because the readout reads it by day index. `wrPts` cannot serve that
    // purpose: it SKIPS null days, so its indices drift out of alignment with the
    // days for any player whose lifetime origin falls inside the window.
    const wrPad = 2;
    const wrSeries: (number | null)[] = new Array(visible.length).fill(null);
    const wrPts: { x: number; y: number }[] = [];
    if (
        lifetimeBattles != null && lifetimeBattles > 0
        && (lifetimeWins != null || lifetimeWinRate != null)
    ) {
        let cumBattles = lifetimeBattles;
        // Exact wins when the host has them. Otherwise derive from the rate —
        // but do NOT round to a whole win: at 14k career battles a half-win of
        // rounding is 0.003% of WR, and the anchor is a figure the page header
        // states to two decimals. The derived path is already lossy (the rate
        // arrives at one decimal); rounding on top of it compounds.
        let cumWins = lifetimeWins ?? lifetimeBattles * (lifetimeWinRate! / 100);
        for (let i = visible.length - 1; i >= 0; i -= 1) {
            wrSeries[i] = cumBattles > 0 ? (cumWins / cumBattles) * 100 : null;
            cumBattles -= visible[i].battles;
            cumWins -= visible[i].wins;
        }
        const vals = wrSeries.filter((v): v is number => v != null);
        if (vals.length >= 1) {
            const minV = Math.min(...vals);
            const maxV = Math.max(...vals);
            const range = Math.max(maxV - minV, 0.0001);
            const padding = range * 0.15 + 0.0001;
            const yMin = minV - padding;
            const span = (maxV + padding) - yMin;
            wrSeries.forEach((v, i) => {
                if (v == null) return;
                wrPts.push({ x: centerX(i), y: wrPad + (1 - (v - yMin) / span) * (H - 2 * wrPad) });
            });
        }
    }
    const wrPoints = wrPts.map(p => `${p.x.toFixed(2)},${p.y.toFixed(2)}`);

    // Crosshair. The rule sits wherever the pointer is; the READOUT and the bar
    // halo snap to the nearest bar CENTRE, so the gaps between bars still resolve
    // to a day rather than to nothing, and the halo says which day the numbers
    // above belong to while the rule moves continuously between them.
    const crossX = hoverX == null ? null : Math.max(0, Math.min(W, hoverX));
    const hovered = crossX == null
        ? null
        : Math.max(0, Math.min(shown - 1, Math.round((crossX - barW / 2) / (barW + gap))));
    // Idle readout: the newest day carrying battles, so the row says something
    // true before the reader ever moves the mouse.
    let idleIdx = shown - 1;
    for (let i = shown - 1; i >= 0; i -= 1) {
        if (visible[i].battles > 0) { idleIdx = i; break; }
    }
    const readIdx = hovered ?? idleIdx;
    // The dot rides the WR line itself, interpolated between the two data points
    // the rule falls between — not parked on the nearest one. Snapping it would
    // make it stutter along a line the rule crosses smoothly. Outside the drawn
    // span (a lifetime origin inside the window leaves the left end blank) there
    // is no line to sit on, so there is no dot.
    let dotY: number | null = null;
    if (crossX != null && wrPts.length >= 2 && crossX >= wrPts[0].x && crossX <= wrPts[wrPts.length - 1].x) {
        for (let i = 0; i < wrPts.length - 1; i += 1) {
            const a = wrPts[i];
            const b = wrPts[i + 1];
            if (crossX >= a.x && crossX <= b.x) {
                const t = b.x === a.x ? 0 : (crossX - a.x) / (b.x - a.x);
                dotY = a.y + (b.y - a.y) * t;
                break;
            }
        }
    }
    // Pointer → viewBox x, measured off the wrapper div, whose box is the SVG's.
    // Hit-testing the bars themselves would drop every gap pixel.
    const handlePointer = (e: React.PointerEvent<HTMLDivElement>) => {
        const rect = e.currentTarget.getBoundingClientRect();
        if (rect.width <= 0) return;
        pending.current = ((e.clientX - rect.left) / rect.width) * W;
        if (frame.current != null) return;
        frame.current = requestAnimationFrame(() => {
            frame.current = null;
            if (pending.current != null) setHoverX(pending.current);
        });
    };
    const clearPointer = () => {
        if (frame.current != null) {
            cancelAnimationFrame(frame.current);
            frame.current = null;
        }
        pending.current = null;
        setHoverX(null);
    };

    // The card mounts with an all-zero padded window first, then the real days
    // land when the async battle-history fetch resolves. Flip this key on that
    // empty→populated transition so the bar-rise (and the WR-line draw) play
    // their entrance once when data arrives — and stay put across live-refresh
    // polls (the key is stable while data is present, so it doesn't re-fire).
    const hasBattleData = days.some(d => d.battles > 0);
    const entranceKey = hasBattleData ? 'ready' : 'empty';
    // The bars glide between domains (a CSS transition on x/width — they keep
    // their DOM nodes, keyed by date). The WR polyline cannot: `points` is not
    // an animatable property. So it re-runs its left-to-right draw whenever the
    // shown domain changes, which reads as the line redrawing itself over the
    // new span rather than snapping to it. Hover is deliberately NOT in this
    // key — a crosshair sweep must not re-trigger the draw-reveal (which is
    // also the animationend signal the Insights tabs gate on).
    const wrEntranceKey = `${entranceKey}:${shown}`;
    // Inner halo geometry for the hovered day. Skipped on an empty day, whose
    // bar is a 2px stub a 1px halo would fill solid; the rule marks those.
    const haloDay = hovered != null ? visible[hovered] : null;
    const halo = haloDay != null && haloDay.battles > 0
        ? { x: hovered! * (barW + gap), ...barRect(haloDay) }
        : null;

    return (
        <div>
            <StripReadoutRow
                date={visible.length > 0 ? visible[readIdx].date : null}
                overall={visible.length > 0 ? wrSeries[readIdx] ?? null : null}
                delta={
                    visible.length > 0 && readIdx > 0
                        && wrSeries[readIdx] != null && wrSeries[readIdx - 1] != null
                        ? wrSeries[readIdx]! - wrSeries[readIdx - 1]!
                        : null
                }
                wins={visible.length > 0 ? visible[readIdx].wins : null}
                battles={visible.length > 0 ? visible[readIdx].battles : null}
                live={hovered != null}
            />
            {/* Positioning context for the crosshair dot. The dot is an HTML
                overlay, not an SVG <circle>: preserveAspectRatio="none" stretches
                x by ~8.5x at card width, which would smear a circle into an
                ellipse. The viewBox height (64) equals the rendered height, so
                y is 1:1 with pixels and x maps straight to a percentage. */}
            <div
                data-testid="strip-hit-area"
                className="relative"
                onPointerMove={handlePointer}
                onPointerLeave={clearPointer}
            >
                <svg
                    viewBox={`0 0 ${W} ${H}`}
                    width="100%"
                    height={H}
                    preserveAspectRatio="none"
                    aria-label={ariaLabel}
                    role="img"
                    // The crosshair over the newest day sits at x=98.6 of 100, so
                    // half its stroke would be clipped by the viewport — and today
                    // is the bar readers hover most. Same escape WindowRangeBracket
                    // takes; the 1px that hangs past the edge reaches no scrolling
                    // ancestor. The bars get an EXPLICIT clip below, because they
                    // were relying on the viewport this opens up.
                    style={{ overflow: 'visible' }}
                >
                    {/* Days outside the shown domain sit at negative x and are
                        clipped rather than unmounted, so a 30d↔90d change moves
                        every bar along one continuous path instead of popping half
                        of them in and out of existence. That clip used to be the
                        SVG viewport; `overflow: visible` above hands it back, so
                        the strip states it outright. */}
                    <defs>
                        <clipPath id={stripClipId}>
                            <rect x={0} y={0} width={W} height={H} />
                        </clipPath>
                    </defs>
                    {/* Keyed on the data-presence transition so the bars remount and
                        replay their grow-from-the-x-axis entrance when the real window
                        lands (the padded all-zero stubs they mount with don't count). */}
                    <g key={entranceKey} clipPath={`url(#${stripClipId})`}>
                        {days.map((d, i) => {
                            const x = (i - offset) * (barW + gap);
                            const { h: totalH, y: totalY } = barRect(d);
                            const winsH = d.battles > 0 ? (d.wins / d.battles) * totalH : 0;
                            const winsY = H - winsH;
                            const wr = d.battles > 0 ? (d.wins / d.battles) * 100 : null;
                            return (
                                // Each day's bars rise from the x-axis (scaleY 0→1, origin
                                // bottom) with a small left-to-right stagger so they sweep
                                // in alongside the WR-line draw. Both rects share the group
                                // transform, so the wins overlay stays pinned to the total.
                                // No <title>: the per-bar native tooltip was replaced by the
                                // crosshair readout above (one row, no hover delay, and it
                                // reports the overall-WR delta a <title> could not).
                                <g
                                    key={d.date}
                                    className="sparkline-bar-rise"
                                    data-date={d.date}
                                    data-battles={d.battles}
                                    style={{ animationDelay: `${i * 18}ms` }}
                                >
                                    <rect x={x} y={totalY} width={barW} height={totalH} fill="rgba(120,120,120,0.25)" rx="0.5" />
                                    {winsH > 0 && (
                                        <rect x={x} y={winsY} width={barW} height={winsH} fill={wrColor(wr)} opacity={0.85} rx="0.5" />
                                    )}
                                </g>
                            );
                        })}
                    </g>
                    {/* Inner halo on the hovered day. An SVG stroke straddles the
                        path, so a plain 1px outline would grow the bar by half a
                        pixel on every side. Drawing it at 2px and clipping to the
                        bar's own rect throws the outer half away, leaving exactly
                        1px INSIDE the bar's existing footprint — the bar's outer
                        dimensions do not change. non-scaling-stroke keeps it 1
                        device pixel on both axes despite the ~8.5x x-stretch. */}
                    {halo != null && (
                        <>
                            <defs>
                                <clipPath id={haloClipId}>
                                    <rect x={halo.x} y={halo.y} width={barW} height={halo.h} rx="0.5" />
                                </clipPath>
                            </defs>
                            <rect
                                data-testid="strip-bar-halo"
                                x={halo.x}
                                y={halo.y}
                                width={barW}
                                height={halo.h}
                                rx="0.5"
                                fill="none"
                                stroke="var(--text-strong)"
                                strokeWidth={2}
                                strokeOpacity={0.75}
                                vectorEffect="non-scaling-stroke"
                                clipPath={`url(#${haloClipId})`}
                                pointerEvents="none"
                            />
                        </>
                    )}
                    {/* Crosshair rule, drawn over the bars and under the WR line so
                        the line it is reading stays on top. */}
                    {crossX != null && (
                        <line
                            data-testid="strip-crosshair"
                            x1={crossX}
                            x2={crossX}
                            y1={0}
                            y2={H}
                            stroke="var(--text-muted)"
                            strokeWidth={1}
                            strokeOpacity={0.9}
                            vectorEffect="non-scaling-stroke"
                            pointerEvents="none"
                        />
                    )}
                    {wrPoints.length >= 2 && (
                        <>
                            {/* Clip rect wiped left→right by CSS (.sparkline-wr-reveal) to
                                "draw" the WR line along its path of travel. Keyed on the
                                same entrance signal as the bars so the draw plays once
                                when data lands and stays put across live-refresh polls. */}
                            <defs>
                                <clipPath id={wrClipId}>
                                    <rect
                                        key={wrEntranceKey}
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
                            cx={wrPts[0].x}
                            cy={wrPts[0].y}
                            r={1.75}
                            fill="var(--accent-secondary-mid)"
                            vectorEffect="non-scaling-stroke"
                        />
                    )}
                </svg>
                {crossX != null && dotY != null && (
                    <span
                        data-testid="strip-crosshair-dot"
                        aria-hidden
                        className="pointer-events-none absolute block h-[7px] w-[7px] rounded-full border-2"
                        style={{
                            left: `${crossX}%`,
                            top: dotY,
                            transform: 'translate(-50%, -50%)',
                            borderColor: 'var(--accent-secondary-mid)',
                            backgroundColor: 'var(--bg-card)',
                        }}
                    />
                )}
            </div>
        </div>
    );
};


type Period = 'daily' | 'weekly' | 'monthly' | 'yearly';

// `ninety` is the ONE extended-lookback pill (2026-09-19). 45/60/75 each held
// that role in turn (v4.4.0, v5.3.11, v5.8.x) and then briefly coexisted as
// three pills; readers treat them as rungs on the way to the longest window,
// not as separate choices, so the row is back to Day / Week / Month / 90d.
// The backend still ACCEPTS fortyfive/sixty/seventyfive for bookmarked and
// shared URLs — they are simply unreachable from the UI, exactly like `year`,
// and so are absent from this union. A stale localStorage pref naming one
// fails `isStickyWindow` and falls back to Month, which is the wanted
// behaviour: no migration, no reader stranded on a pill that is not there.
// Retention is 105d and capture reaches back 98 days (2026-06-13), so 90d is
// real depth rather than a labelled short window.
// `year` is intentionally excluded from VISIBLE_WINDOWS — capture started
// 2026-04-28 so a 365-day view won't carry meaningful additional context
// for the next ~12 months. The backend still accepts ?window=year for
// back-compat, but no pill exposes it. Re-add to VISIBLE_WINDOWS once
// >180 days of capture have accumulated.
export type BattleHistoryWindow = 'day' | 'week' | 'month' | 'ninety' | 'year';
const VISIBLE_WINDOWS: ReadonlyArray<BattleHistoryWindow> = [
    'day', 'week', 'month', 'ninety',
];
// `year` has no pill (see VISIBLE_WINDOWS above) and so no translated key —
// it is unreachable UI, and inventing a key for it would put an untranslatable
// string into the dictionaries' coverage denominator.
const WINDOW_LABEL_KEY: Record<BattleHistoryWindow, StringKey | null> = {
    day: 'battleHistory.window.day',
    week: 'battleHistory.window.week',
    month: 'battleHistory.window.month',
    ninety: 'battleHistory.window.ninety',
    year: null,
};
const WINDOW_TITLE: Record<BattleHistoryWindow, string> = {
    day: 'Today (UTC calendar date, matching the trend strip\'s last bar)',
    week: 'Last 7 days',
    month: 'Last 30 days',
    ninety: 'Last 90 days',
    year: 'Last 365 days',
};
// Tooltip shown when a window pill is disabled for having no battles in its
// span. Every window's emptiness is derived client-side from the 90-day strip
// the card already holds — Day included, since 2026-07-30 made it a calendar
// window like the rest (it previously needed a backend flag because a rolling
// 24h span could not be read off calendar buckets).
const WINDOW_TITLE_EMPTY: Record<BattleHistoryWindow, string> = {
    day: 'No battles today',
    week: 'No battles in the last 7 days',
    month: 'No battles in the last 30 days',
    ninety: 'No battles in the last 90 days',
    year: 'No battles in the last 365 days',
};
const WINDOW_HEADER_KEY: Record<BattleHistoryWindow, StringKey | null> = {
    day: 'battleHistory.header.today',
    week: 'battleHistory.header.last7',
    month: 'battleHistory.header.last30',
    ninety: 'battleHistory.header.last90',
    year: null,
};
const WINDOW_HEADER_FALLBACK: Record<BattleHistoryWindow, string> = {
    day: 'Today',
    week: 'Last 7 days',
    month: 'Last 30 days',
    ninety: 'Last 90 days',
    year: 'Last 365 days',
};
// The trend strip's date domain, FIXED for every window pill. The strip is a
// constant backdrop: toggling Day/Week/Month/90d re-scopes the tiles,
// treemaps and table below it, but never reflows a single bar. The selected
// span is reported instead by the WindowRangeBracket beneath the strip.
export const STRIP_DOMAIN_DAYS = 90;

// How many of those days the strip actually SHOWS. Day/Week/Month read against
// a 30-day backdrop — the span they measure is legible there, where against 90
// a single day is a sliver. Picking 90d widens the backdrop to the full held
// domain, animated (the bars glide, see .sparkline-bar-rise rect in
// globals.css). With one extended pill there is no longer a window whose span
// sits INSIDE the wide backdrop, so 90d dissolves the bracket to full-width
// exactly as Month does against the 30-day backdrop; the visible-bracket case
// is Day/Week only. Re-adding a narrower extended pill would bring it back
// without further work — the bracket is derived from span vs domain.
//
// The strip still holds all STRIP_DOMAIN_DAYS days at every setting; the days
// outside the shown domain are positioned off the left edge of the viewBox and
// clipped, never unmounted. That is what makes the change a glide in BOTH
// directions rather than a glide one way and a pop the other.
export const stripDomainForWindow = (w: BattleHistoryWindow): number =>
    (WINDOW_SPAN_DAYS[w] > 30 ? STRIP_DOMAIN_DAYS : 30);

// Days each window pill covers — the span the bracket brackets. Clamped against
// STRIP_DOMAIN_DAYS at the call site so `year` (still typed, no pill exposes it)
// cannot drive the bracket off the left edge.
const WINDOW_SPAN_DAYS: Record<BattleHistoryWindow, number> = {
    day: 1, week: 7, month: 30, ninety: 90, year: 365,
};

// Window-pill persistence. The pick sticks per (realm, player, mode) — the same
// scope the treemap color metric uses — so a reader who works in Week on one
// account keeps Week there without imposing it on the next player they open,
// and the Ranked tab's pick never moves the Activity tab underneath them.
//
// Only pills a user can actually reach are honoured on read: `year` is a valid
// BattleHistoryWindow the backend still accepts but no pill exposes, so a stale
// or hand-edited value naming it would strand the reader on a window they cannot
// see selected and cannot leave by clicking the pill they are on.
const WINDOW_PREF_KEY = 'battlestats:battle-history:window';

const isStickyWindow = (v: unknown): v is BattleHistoryWindow =>
    typeof v === 'string' && (VISIBLE_WINDOWS as ReadonlyArray<string>).includes(v);

export const readWindowPref = (
    scope: string | null | undefined,
): BattleHistoryWindow | null => {
    if (!scope || typeof globalThis.window === 'undefined') {
        return null;
    }
    try {
        const stored = globalThis.window.localStorage.getItem(`${WINDOW_PREF_KEY}:${scope}`);
        return isStickyWindow(stored) ? stored : null;
    } catch {
        // storage unavailable (private mode / disabled) — fall back to default
        return null;
    }
};

export const writeWindowPref = (
    scope: string | null | undefined, w: BattleHistoryWindow,
): void => {
    if (!scope || typeof globalThis.window === 'undefined') {
        return;
    }
    try {
        globalThis.window.localStorage.setItem(`${WINDOW_PREF_KEY}:${scope}`, w);
    } catch {
        // ignore storage failures (private mode / quota)
    }
};

const BattleHistoryCard: React.FC<BattleHistoryCardProps> = ({
    playerName,
    realm,
    days = 7,
    refreshNonce = 0,
    embedded = false,
    fillHeight = false,
    mode = 'random',
    overallBattles = null,
    overallWins = null,
    onAvailabilityChange,
    onSparklineAnimationEnd,
    onWindowChange,
    captionLeading,
}) => {
    const requestSignal = usePlayerRequestSignal();
    // Identity every remembered pick on this card is scoped to — the treemap
    // color metric and the window pill. Realm is part of the key because the
    // same name can be a different account on another realm; the name is
    // lowercased so a link that differs only in case still resolves to the one
    // stored pick. Mode is in the key because the player page mounts this card
    // twice (Activity = random, Ranked = ranked) over different data — the dmg
    // baseline is random-only, so the metric that reads best genuinely differs
    // between the two, and a Ranked-tab window pick must not move the Activity
    // tab underneath the reader.
    const prefScope = useMemo(
        () => `${realm}:${playerName.toLowerCase()}:${mode}`,
        [realm, playerName, mode],
    );
    const [payload, setPayload] = useState<BattleHistoryPayload | null>(null);
    const [stripByDay, setStripByDay] = useState<BattleHistoryByDay[]>([]);
    // True once the month fetch below has resolved for the current
    // (player, realm, mode). Gates the derived week/month empty-pill disable
    // so a still-loading card never dims a pill on stale/absent data — pills
    // stay enabled until the data is authoritative (the safe direction).
    const [stripLoaded, setStripLoaded] = useState(false);
    // The strip's own payload, and whether its fetch has SETTLED (resolved or
    // failed). `stripLoaded` deliberately stays false on failure so the
    // empty-pill rule keeps pills enabled on absent data; availability needs the
    // opposite — it must still latch if the strip never arrives.
    const [stripPayload, setStripPayload] = useState<BattleHistoryPayload | null>(null);
    const [stripSettled, setStripSettled] = useState(false);
    // Lifetime baseline from the month fetch, used to anchor the sparkline's
    // overall-WR overlay line. Null in modes without a lifetime (e.g. combined).
    const [stripLifetime, setStripLifetime] = useState<{
        battles: number | null; wins: number | null; winRate: number | null;
    }>({ battles: null, wins: null, winRate: null });
    const [error, setError] = useState<Error | null>(null);
    const [loading, setLoading] = useState(true);
    const [window, setWindow] = useState<BattleHistoryWindow>(
        DEFAULT_BATTLE_HISTORY_WINDOW,
    );
    const [userPickedWindow, setUserPickedWindow] = useState(false);
    // The scope whose stored window pick has been applied to state. Compared
    // against the live `prefScope` rather than held as a bare boolean so that
    // opening a second player does not let the first player's window drive one
    // fetch before the new pick resolves: while they differ, the main fetch
    // below holds. localStorage is read in an effect, never in the useState
    // initializer, because it is client-only and reading it during the initial
    // render would desync SSR from CSR (same rule as the treemap pref).
    const [windowPrefScope, setWindowPrefScope] = useState<string | null>(null);
    useEffect(() => {
        const stored = readWindowPref(prefScope);
        setWindow(stored ?? DEFAULT_BATTLE_HISTORY_WINDOW);
        // A restored pick counts as explicit ONLY when it differs from the
        // default. At the default the reader is where an untouched card would
        // have put them, so the standalone no-battles collapse below must still
        // apply — otherwise remembering "90d" would make empty cards appear for
        // players who previously had none.
        setUserPickedWindow(stored !== null && stored !== DEFAULT_BATTLE_HISTORY_WINDOW);
        setWindowPrefScope(prefScope);
    }, [prefScope]);
    // Publish the live window to any host that asked for it. Driven off the
    // state rather than the pill handler on purpose: the restore above and the
    // 90d fallback below both move `window` without a click, and a host that
    // missed those would be scoped to a window the reader is not looking at.
    //
    // Gated on the restored scope for the same reason the main fetch is: the
    // initial state is the DEFAULT window, so an ungated publish would announce
    // `month` on mount and correct itself to the stored pick a tick later —
    // and a host that fetches per window would visibly render the wrong span
    // first. The restore batches setWindow with setWindowPrefScope, so the
    // first publish this gate allows already carries the remembered pick.
    useEffect(() => {
        if (windowPrefScope !== prefScope) return;
        onWindowChange?.(window);
    }, [window, onWindowChange, windowPrefScope, prefScope]);
    // Ship selected in the table → its combat profile (ShipStats) shows below
    // the rollup separator. Clicking the same row again clears it (toggle).
    const [selectedShip, setSelectedShip] = useState<{
        ship_id: number; ship_name: string; ship_tier: number | null; ship_type: string | null;
    } | null>(null);
    const { theme } = useTheme();
    const t = useT();
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
        // Hold until the stored window pick for THIS scope has been applied.
        // Without the gate a reader whose remembered pill is Week would spend a
        // request on the default window first and then immediately re-fetch —
        // two round trips on every player open, for a value we already knew.
        // The always-default strip fetch below is unaffected and still dedupes
        // onto PlayerRouteView's prefetch, so nothing is lost by waiting a tick.
        if (windowPrefScope !== prefScope) return;
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
    }, [playerName, realm, window, mode, refreshNonce, requestSignal,
        prefScope, windowPrefScope]);

    // Reset the loaded gate whenever the entity/mode identity changes, so the
    // month fetch below re-establishes it rather than the empty-pill disable
    // acting on the previous player's data (a refresh-poll re-fetch keeps it).
    useEffect(() => {
        setStripLoaded(false);
        setStripSettled(false);
        setStripPayload(null);
    }, [playerName, realm, mode]);

    // Separate fetch backing the trend strip, independent of the window driving
    // the bars/table. It ALWAYS pulls the full STRIP_DOMAIN_DAYS window: the
    // strip's domain is fixed, so this never re-fires on a pill click. At the
    // default window it is the same url + cacheKey as the main fetch, so
    // fetchSharedJson collapses the two into one request.
    useEffect(() => {
        // Gated on the same pref resolution as the main fetch above purely to
        // preserve their ORDER: the window the reader is waiting on must be
        // requested before the constant backdrop, and the priority queue serves
        // in arrival order. This fetch's url does not depend on the stored pick.
        if (windowPrefScope !== prefScope) return;
        let cancelled = false;
        fetchSharedJson<BattleHistoryPayload>(
            battleHistoryFetchUrl(playerName, realm, STRIP_FETCH_WINDOW, mode),
            {
                label: `BattleHistoryCard:sparkline`,
                ttlMs: BATTLE_HISTORY_FETCH_TTL_MS,
                cacheKey: battleHistoryCacheKey(
                    playerName, realm, STRIP_FETCH_WINDOW, mode, 0, refreshNonce,
                ),
                signal: requestSignal,
            },
        )
            .then(({ data }) => {
                if (cancelled) return;
                setStripByDay(data.by_day ?? []);
                setStripLifetime({
                    battles: data.totals?.lifetime_battles ?? null,
                    wins: data.totals?.lifetime_wins ?? null,
                    winRate: data.totals?.lifetime_win_rate ?? null,
                });
                setStripPayload(data);
                setStripLoaded(true);
                setStripSettled(true);
            })
            .catch(() => {
                // Sparkline stays empty on error — but availability must still
                // latch, or the hosting tab sits in its default state forever.
                if (!cancelled) setStripSettled(true);
            });
        return () => { cancelled = true; };
    }, [playerName, realm, mode, refreshNonce, requestSignal,
        prefScope, windowPrefScope]);

    // Fallback to 90d for a player with nothing in the last 30 days. The card
    // opens on Month; if that span is empty but the wider one is not, showing an
    // empty Month is strictly worse than showing the battles that exist — so the
    // strip's own data promotes the view once it lands. The other pills then dim
    // themselves through the usual empty-window rule.
    //
    // This is a DERIVATION, not a pick, and the distinction is the whole reason
    // it does not call writeWindowPref: persisting it would pin a returning
    // player to 90d forever, long after they start playing again and Month is
    // the better view. It also defers to a real stored pick and to any pill the
    // reader has touched this session.
    useEffect(() => {
        if (!stripLoaded) return;
        if (windowPrefScope !== prefScope) return;
        if (userPickedWindow || readWindowPref(prefScope) !== null) return;
        if (window !== DEFAULT_BATTLE_HISTORY_WINDOW) return;
        const trailing = (n: number): number =>
            buildWindowedDays(stripByDay, n).reduce((sum, d) => sum + (d.battles || 0), 0);
        if (trailing(WINDOW_SPAN_DAYS[DEFAULT_BATTLE_HISTORY_WINDOW]) === 0
            && trailing(STRIP_DOMAIN_DAYS) > 0) {
            setWindow('ninety');
        }
        // `window` is deliberately absent from the deps: this runs on the strip
        // landing, and re-running it when the window changes would fight a
        // reader who clicks back to an empty Month on purpose.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [stripLoaded, stripByDay, prefScope, windowPrefScope, userPickedWindow]);

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
        // Judge on the WIDEST span the card can show — the strip's 90 days —
        // not on whichever window is selected. The card opens on Month, so a
        // player whose last battles were 45 days ago has an empty month payload;
        // reading availability off that told the parent "no activity" and got
        // the Activity tab disabled before the 30d-empty fallback could promote
        // them to 90d. That is precisely the population the fallback exists for.
        // Falls back to the main payload only if the strip never arrives.
        // That fallback is one window NARROWER (Month, so `available_modes`
        // covers 30 days rather than 90): on a failed strip fetch a ranked
        // player whose rows sit in the 30-90 day band reports false and the
        // parent opens the Ranked tab on its History sub-view instead of
        // Activity. Accepted — History is populated for exactly that player,
        // the ranked TAB itself gates on `hasKnownRankedGames` rather than on
        // this signal, and it self-corrects on the next load. The random path
        // is untouched: it never consults `available_modes`.
        if (!stripSettled) return;
        const judged = stripPayload ?? payload;
        if (!judged) return;
        availabilityReportedRef.current = true;
        onAvailabilityChange(
            battleHistoryIndicatesActivity(judged, mode),
            judged.available_modes ?? ['random'],
        );
    }, [payload, stripPayload, stripSettled, error, mode, onAvailabilityChange]);

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
    // `stripLoaded` is load-bearing here, not defensive: the fallback below
    // switches a 30d-empty player to 90d only once the strip resolves. Without
    // the gate the card collapses to null on the empty month payload first and
    // then reappears when the fallback lands — a visible flash for exactly the
    // population the fallback exists to serve.
    if (!embedded && stripLoaded && (
        !hasBattles
        && window === DEFAULT_BATTLE_HISTORY_WINDOW && !userPickedWindow
    )) {
        return null;
    }

    // The strip is the same STRIP_DOMAIN_DAYS window on every pill.
    // buildWindowedDays zero-fills any span the data doesn't cover, so the
    // pre-retention-fill region simply renders empty by design. The same array
    // backs the empty-pill derivation below via trailing slices.
    const stripDays = buildWindowedDays(stripByDay, STRIP_DOMAIN_DAYS);
    // Day/Week/Month read against 30 days; 90d widens to the full held domain.
    const stripDomain = stripDomainForWindow(window);
    const spanDays = Math.min(WINDOW_SPAN_DAYS[window], stripDomain);
    const sparkline = (
        <>
            <InlineSparkline
                days={stripDays}
                domainDays={stripDomain}
                ariaLabel={`${stripDomain}-day battle activity`}
                // Guarded on > 0 rather than nullish-coalesced: a 0 would win the
                // ?? and silently suppress the WR line. The two sources are the
                // same DB field by construction (views.py reads player.pvp_battles
                // for the random lifetime), so this only ever matters if that
                // stops being true.
                lifetimeBattles={
                    overallBattles != null && overallBattles > 0
                        ? overallBattles : stripLifetime.battles
                }
                // Exact wins from whichever source has them: the host's prop
                // (random, available without a backend deploy) or the payload
                // (both modes, once the backend ships `lifetime_wins`). Ranked
                // has no prop — its baseline is a different aggregate — so it
                // rides the payload.
                lifetimeWins={
                    (overallBattles != null && overallBattles > 0 ? overallWins : null)
                    ?? stripLifetime.wins
                }
                lifetimeWinRate={stripLifetime.winRate}
            />
            <WindowRangeBracket spanDays={spanDays} domainDays={stripDomain} />
        </>
    );
    // Empty-window pill disable. Day emptiness is the backend 24h flag; week/
    // week/month/90d are derived from trailing slices of the strip by_day the
    // already holds (gated on stripLoaded so a loading card never dims on
    // stale/absent data). A pill dims + goes unclickable when its window has
    // no battles — but never the window currently being viewed (handled at the
    // call site via isActive), so the active pill stays interactive.
    const sumTrailingBattles = (n: number): number =>
        stripDays.slice(Math.max(0, stripDays.length - n))
            .reduce((s, d) => s + (d.battles || 0), 0);
    const isWindowEmpty = (w: BattleHistoryWindow): boolean => {
        // Uniform across all four pills: one array, one bucketing, so a pill's
        // enabled state cannot disagree with the window it opens. Gated on
        // stripLoaded so a loading card never dims a pill on absent data.
        if (!stripLoaded) return false;
        if (w === 'year') return false;
        return sumTrailingBattles(WINDOW_SPAN_DAYS[w]) === 0;
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
                            : (WINDOW_HEADER_KEY[window] ? t(WINDOW_HEADER_KEY[window]!) : WINDOW_HEADER_FALLBACK[window])}
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
                                        writeWindowPref(prefScope, w);
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
                                    {WINDOW_LABEL_KEY[w] ? t(WINDOW_LABEL_KEY[w]!) : w}
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
                        {t(MODE_LABEL_KEY[mode])}
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
                            <div className="text-xs text-[var(--text-muted)]">{t('common.battles')}</div>
                            <div className="font-['Courier_New',Courier,monospace] text-2xl font-semibold text-[var(--text-strong)]">{formatInt(totals!.battles)}</div>
                        </div>
                        <div className="flex-1 text-center">
                            <div className="text-xs text-[var(--text-muted)]">{t('battleHistory.tile.ships')}</div>
                            <div className="font-['Courier_New',Courier,monospace] text-2xl font-semibold text-[var(--text-strong)]">{formatInt(distinctShips)}</div>
                        </div>
                        </div>
                        <div className="flex flex-1 items-end border-t border-[var(--border)] px-4 py-3 sm:border-l sm:border-t-0">
                        <div className="flex-1 text-center">
                            <div className="text-xs text-[var(--text-muted)]">{t('battleHistory.tile.windowWr')}</div>
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
                            <div className="text-xs text-[var(--text-muted)]">{t('battleHistory.tile.avgDamage')}</div>
                            <div className="font-['Courier_New',Courier,monospace] text-2xl font-semibold text-[var(--text-strong)]">{formatInt(totals!.avg_damage)}</div>
                        </div>
                        {/* One per-battle frag tile — the old "Frags" total
                            (low-signal) and "Avg KDR" (which was already
                            frags ÷ battles under a misleading name) collapsed
                            into it, 2026-07-13. The raw total lives in the
                            tooltip; the table's F/B column is this same
                            metric per ship. */}
                        <div className="flex-1 text-center">
                            <div className="text-xs text-[var(--text-muted)]">{t('battleHistory.tile.fragsPerBattle')}</div>
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
                        prefScope={prefScope}
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
                            <SortableTh sortKey="ship_name" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick} tooltip="Ship played in the period. Click to sort A–Z.">{t('common.ship')}</SortableTh>
                            <SortableTh sortKey="ship_tier" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick} tooltip="Ship tier (1–10, with the lowest tier ships being the smallest, less powerful, with the highest tier ships being the largest, most powerful). Click to sort by tier.">{t('common.tier')}</SortableTh>
                            <SortableTh sortKey="ship_type" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick} tooltip="Hull type — DD = Destroyer, CL/CA = Cruiser, BB = Battleship, CV = Carrier, SS = Submarine. Click to sort by type.">{t('common.type')}</SortableTh>
                            <SortableTh sortKey="battles" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick} tooltip="Battles played on this ship in the selected period. Click to sort by volume.">#</SortableTh>
                            <SortableTh sortKey="win_rate" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick} tooltip="Win rate over the selected window on this ship. Color codes use Wargaming community thresholds. Click to sort by window WR.">WR %</SortableTh>
                            <SortableTh sortKey="lifetime_win_rate" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick} tooltip="Overall (lifetime) win rate and its delta (Δ) vs this window. Click to sort by overall WR.">Overall WR %</SortableTh>
                            <SortableTh sortKey="avg_damage" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick} tooltip="Average damage dealt per battle on this ship in the selected period, colored against the ship's realm-wide 30-day average — red below it, gray at it, green above. Click to sort.">{t('common.avgDamage')}</SortableTh>
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
