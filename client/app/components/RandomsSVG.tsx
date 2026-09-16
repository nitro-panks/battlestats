import React, { useEffect, useState, useRef, useMemo } from 'react';
import * as d3 from 'd3';
import { fetchSharedJson, isAbortError } from '../lib/sharedJsonFetch';
import { degradationMonitor } from '../lib/degradationMonitor';
import { usePlayerRequestSignal } from '../context/PlayerRequestScopeContext';
import { chartColors, type ChartTheme } from '../lib/chartTheme';
import { useRealm } from '../context/RealmContext';
import { withRealm } from '../lib/realmParams';
import { trackEvent } from '../lib/umami';
import { SHIP_TYPE_ABBREV } from './tierTypeDietModel';
import {
    battleHistoryFetchUrl,
    battleHistoryCacheKey,
    BATTLE_HISTORY_FETCH_TTL_MS,
    DEFAULT_BATTLE_HISTORY_WINDOW,
    type BattleHistoryPayload,
} from './BattleHistoryCard';

/**
 * A filter pre-selection handed in from another surface (the Profile tab's
 * "Random Battles by Tier" figure drilling down into a tier x class).
 *
 * `nonce` is what makes a repeat drill-down work: the request is applied once
 * per nonce, so clicking a second cell while the Ships tab is already open
 * re-applies rather than being swallowed as an unchanged prop. Ship classes
 * arrive in the tier/type payload's vocabulary ("Aircraft Carrier"), which is
 * not this payload's ("AirCarrier"), so they are matched by abbreviation.
 */
export interface RandomsFilterRequest {
    shipTypes: string[];
    /** Empty means "leave the tier filter alone" (a whole-class drill-down). */
    tiers: number[];
    nonce: number;
}

interface RandomsSVGProps {
    playerId: number;
    playerName: string;
    isLoading?: boolean;
    theme?: ChartTheme;
    filterRequest?: RandomsFilterRequest | null;
    /**
     * Compact variant (the Activity tab's copy of this chart). No controls at
     * all: no filter pills, no cutoff sliders, no Activity mode toggle, no
     * freshness line. The ship set is exactly "played in the window" — which is
     * why the tier-5 floor and every pill/slider predicate are bypassed rather
     * than merely hidden. A hidden filter is an invisible filter, and this
     * variant has no pill to reveal or undo one.
     */
    compact?: boolean;
    /**
     * Battle-history window the played-in-window join is read over. The Ships
     * tab leaves this at the card's default (30d); the Activity tab hands down
     * whatever its window pill currently reads, so the compact chart re-scopes
     * with the pill.
     */
    windowName?: string;
    /** Mirrors the host card's refresh nonce so the join shares its cacheKey. */
    refreshNonce?: number;
}

// Per-ship stats over the trailing 30-day random-battle window, joined into the
// (otherwise lifetime) randoms chart by ship name. `deltaWinRate` is the
// window's percentage-point shift in the ship's cumulative win rate (backend
// `views.py`); null when there is no computable prior-to-window sample (a
// brand-new ship, or a baseline too thin for a delta). Membership in the map
// means the ship was played in the last 30 days.
interface RandomsWindowStat {
    deltaWinRate: number | null;
    battles: number;
    /** The window's own record on this ship. Carried for the compact hover
     *  line, which quotes what the captain actually did this window in place
     *  of the lifetime win total. */
    wins: number;
    losses: number;
}
type RandomsWindowMap = Map<string, RandomsWindowStat>;

interface RandomsRow {
    pvp_battles: number;
    ship_name: string;
    ship_chart_name: string;
    ship_type: string;
    ship_tier: number;
    win_ratio: number;
    wins: number;
}

// Activity filter mode: All shows every ship; Window Only hides ships not
// played in the trailing 30-day window.
type ActivityMode = 'all' | 'window';
const ACTIVITY_MODE_OPTIONS: Array<{ value: ActivityMode; label: string }> = [
    { value: 'all', label: 'All' },
    { value: 'window', label: 'Window Only' },
];

// The Clear control. Deliberately NOT `filterButtonClass`: it is an action, not
// a selectable filter value, so it carries no pressed state and reads quieter
// than the pills it sits beside.
const CLEAR_FILTERS_BUTTON_CLASS = 'border border-transparent px-2 py-1 text-xs font-medium text-[var(--text-secondary)] underline decoration-dotted underline-offset-2 transition-colors hover:text-[var(--accent-dark)]';

const normalizeRandomsRows = (data: unknown): RandomsRow[] => {
    if (Array.isArray(data)) {
        return data as RandomsRow[];
    }

    console.warn('Unexpected randoms data payload:', data);
    return [];
};

// Per-row slot height for the scrollable bar list. ~22px keeps each bar
// at roughly the same density the old fixed-height (top-20) chart rendered at.
const RANDOMS_ROW_HEIGHT_PX = 22;
// Visible height of the scroll viewport; taller ship lists scroll within this.
// Roughly matches the Activity-tab battle-history table cap so the Ships chart
// uses the same vertical room instead of being pinned to a shorter box.
const RANDOMS_CHART_MAX_VIEWPORT_PX = 825;
// Floor for the battles bar so low-volume tail ships stay visible rather than
// collapsing to a 1px sliver on the linear scale. The wins overlay stays a true
// fraction of this (possibly floored) width, so win rate reads correctly.
const RANDOMS_MIN_BAR_PX = 6;
const RANDOMS_CHART_SHIFT_RIGHT_PX = 15;
const RANDOMS_CHART_RIGHT_EXTENSION_PX = 10;

const selectRandomsColorByWr = (winRatio: number, theme: ChartTheme): string => {
    const colors = chartColors[theme];
    if (winRatio > 0.65) return colors.wrElite;
    if (winRatio >= 0.60) return colors.wrSuperUnicum;
    if (winRatio >= 0.56) return colors.wrUnicum;
    if (winRatio >= 0.54) return colors.wrVeryGood;
    if (winRatio >= 0.52) return colors.wrGood;
    if (winRatio >= 0.50) return colors.wrAboveAvg;
    if (winRatio >= 0.45) return colors.wrAverage;
    if (winRatio >= 0.40) return colors.wrBelowAvg;
    return colors.wrBad;
};

const drawBattlePlotDesign1 = (
    containerElement: HTMLDivElement,
    data: RandomsRow[],
    theme: ChartTheme,
    windowMap: RandomsWindowMap,
    onHover?: (datum: RandomsRow | null) => void,
) => {
    const colors = chartColors[theme];
    type RandomsChartRow = RandomsRow & { rowKey: string };

    const rows: RandomsChartRow[] = data.map((datum, index) => ({ ...datum, rowKey: `row-${index}` }));
    const labelByRowKey = new Map(rows.map((row) => [row.rowKey, row.ship_chart_name]));
    const rowByKey = new Map(rows.map((row) => [row.rowKey, row]));
    const containerWidth = containerElement.clientWidth;
    const compact = containerWidth < 580;
    const totalSvgWidth = Math.max(containerWidth || 0, 280) + RANDOMS_CHART_RIGHT_EXTENSION_PX;
    // Right margin reserves room for the WR% label AND, to its right, the
    // win-rate delta pill (task 4). Narrowed by 100px vs the old 176 to extend the
    // bars' reach (the max line) that much further right into the formerly-empty
    // gutter; still leaves room for the WR% label + a pill on all but the very
    // longest pill-bearing bars.
    // left is widened to fit the longer ship names at the larger axis font below.
    const margin = compact
        ? { top: 8, right: 88, bottom: 48, left: 60 }
        // right is 86 (not 76) so the end-of-row labels pull 10px left of the
        // plot edge, clearing the vertical scroll bar (the +10 RIGHT_EXTENSION
        // that keeps the chart width stable when the scroll bar appears would
        // otherwise slide those labels under it).
        : { top: 8, right: 86, bottom: 48, left: 85 + RANDOMS_CHART_SHIFT_RIGHT_PX };
    const axisFontSize = compact ? '11px' : '12px';
    const width = totalSvgWidth - margin.left - margin.right;
    // Height grows with the number of ships so the full list renders at a
    // consistent per-row density; the React container scrolls past the viewport.
    const height = rows.length * RANDOMS_ROW_HEIGHT_PX;
    const totalSvgHeight = height + margin.top + margin.bottom;

    const svgRoot = d3.select(containerElement)
        .append('svg')
        .attr('width', totalSvgWidth)
        .attr('height', totalSvgHeight);

    const svg = svgRoot
        .append('g')
        .attr('transform', `translate(${margin.left}, ${margin.top})`);

    const maxBattles = Math.max(d3.max(data, (datum: RandomsRow) => datum.pvp_battles) || 0, 15);
    const x = d3.scaleLinear()
        .domain([0, maxBattles * 1.08])
        .range([0, width]);

    const y = d3.scaleBand()
        .range([0, height])
        .domain(rows.map((datum) => datum.rowKey))
        .padding(0.18);

    const foregroundBarHeight = y.bandwidth();
    const backgroundBarHeight = Math.max(3, Math.round(foregroundBarHeight * 0.5));
    const backgroundBarOffset = (foregroundBarHeight - backgroundBarHeight) / 2;
    const foregroundBarOffset = 0;

    const tickCount = compact ? 3 : 5;
    const xGrid = d3.axisBottom(x).ticks(tickCount).tickSize(-height).tickFormat(() => '');
    svg.append('g')
        .attr('transform', `translate(0, ${height})`)
        .attr('class', 'randoms-grid')
        .call(xGrid);

    svg.select('.randoms-grid')?.select('.domain')?.remove();
    svg.selectAll('.randoms-grid line')
        .style('stroke', colors.gridLine)
        .style('stroke-width', 1);

    svg.append('g')
        .attr('transform', `translate(0, ${height})`)
        .style('color', colors.labelMuted)
        .call(d3.axisBottom(x).ticks(tickCount).tickFormat((value: number) => d3.format(',')(value)).tickSizeOuter(0))
        .selectAll('text')
        .style('font-size', axisFontSize);

    const truncateLabel = (label: string, maxLen: number) => label.length > maxLen ? label.slice(0, maxLen) + '\u2026' : label;
    const yAxis = svg.append('g')
        .style('color', colors.labelMid)
        .call(d3.axisLeft(y).tickSize(0).tickPadding(compact ? 4 : 6).tickFormat((value: number) => {
            const label = labelByRowKey.get(String(value)) ?? '';
            return compact ? truncateLabel(label, 8) : label;
        }));
    yAxis.selectAll('text')
        .style('font-size', axisFontSize)
        .style('font-weight', '500');

    svg.selectAll('.domain').style('stroke', colors.axisLine);

    svg.append('text')
        .attr('x', width)
        .attr('y', height + 38)
        .attr('text-anchor', 'end')
        .style('font-size', axisFontSize)
        .style('fill', colors.labelText)
        .text('Random battles');

    // The hovered-ship readout is rendered as an HTML element above the scroll
    // viewport (see RandomsSVG) so it stays visible no matter how far the list is
    // scrolled — an in-SVG group would scroll off-screen on long ship lists.
    const renderDetails = (datum: RandomsRow | null) => {
        onHover?.(datum);
    };

    // Battles bar width, floored so the long tail stays visible. The wins overlay
    // is drawn as win_ratio of this width, so the colored fraction still reads as
    // the true win rate even when the bar is floored.
    const barWidth = (datum: RandomsChartRow) => Math.max(x(datum.pvp_battles), RANDOMS_MIN_BAR_PX);

    const nodes = svg.selectAll('.randoms-row')
        .data(rows)
        .enter()
        .append('g')
        .classed('randoms-row', true)
        .style('cursor', 'default')
        .on('mouseover', function (this: SVGGElement, _event: MouseEvent, datum: RandomsChartRow) {
            renderDetails(datum);
            d3.select(this).select('.randoms-wins-bar').transition()
                .duration(70)
                .attr('opacity', 0.82);
        })
        .on('mouseout', function (this: SVGGElement) {
            renderDetails(null);
            d3.select(this).select('.randoms-wins-bar').transition()
                .duration(70)
                .attr('opacity', 1);
        });

    // Transparent full-row hit area so the whole row is hoverable, not just the
    // (possibly tiny) colored bar on tail ships.
    nodes.append('rect')
        .attr('x', 0)
        .attr('y', (datum: RandomsChartRow) => y(datum.rowKey) ?? 0)
        .attr('width', width)
        .attr('height', foregroundBarHeight)
        .attr('fill', 'transparent');

    nodes.append('rect')
        .attr('x', 0)
        .attr('y', (datum: RandomsChartRow) => (y(datum.rowKey) ?? 0) + backgroundBarOffset)
        .attr('width', barWidth)
        .attr('height', backgroundBarHeight)
        .attr('rx', 3)
        .attr('fill', colors.barBg);

    nodes.append('rect')
        .classed('randoms-wins-bar', true)
        .attr('x', 0)
        .attr('y', (datum: RandomsChartRow) => (y(datum.rowKey) ?? 0) + foregroundBarOffset)
        .attr('width', (datum: RandomsChartRow) => barWidth(datum) * datum.win_ratio)
        .attr('height', foregroundBarHeight)
        .attr('rx', 3)
        .style('stroke', colors.axisLine)
        .style('stroke-width', 0.5)
        .attr('fill', (datum: RandomsChartRow) => selectRandomsColorByWr(datum.win_ratio, theme));

    // WR% label right edge per row, recorded so the delta pill can sit just to
    // its right (rather than over the loss region).
    const wrLabelEndByRow = new Map<string, number>();
    const wrLabels = nodes.append('text')
        .classed('randoms-wr-label', true)
        .attr('x', (datum: RandomsChartRow) => {
            const labelX = barWidth(datum) + 6;
            return labelX > width - 4 ? width - 4 : labelX;
        })
        .attr('y', (datum: RandomsChartRow) => (y(datum.rowKey) ?? 0) + foregroundBarOffset + (foregroundBarHeight / 2) + 3)
        .style('font-size', axisFontSize)
        .style('fill', colors.labelMuted)
        .attr('text-anchor', (datum: RandomsChartRow) => (barWidth(datum) + 6 > width - 4 ? 'end' : 'start'))
        .text((datum: RandomsChartRow) => `${(datum.win_ratio * 100).toFixed(1)}%`);

    wrLabels.each(function (this: SVGTextElement, datum: RandomsChartRow) {
        const bbox = this.getBBox();
        wrLabelEndByRow.set(datum.rowKey, bbox.x + bbox.width);
    });

    // Win-rate delta pill (task 4): for each ship played in the trailing 30-day
    // window with a computable delta, a small backgrounded badge sits just to
    // the right of the ship's overall WR% label. Non-interactive
    // (pointer-events:none) so the row hover beneath still fires. Ships not
    // played in the window — or played but with no computable delta — get none.
    const deltaRows = rows.filter((row) => {
        const stat = windowMap.get(row.ship_name);
        return stat != null && stat.deltaWinRate != null;
    });

    const deltaGroups = svg.selectAll('.randoms-delta')
        .data(deltaRows)
        .enter()
        .append('g')
        .classed('randoms-delta', true)
        .style('pointer-events', 'none');

    const deltaGap = 6;
    deltaGroups.each(function (this: SVGGElement, datum: RandomsChartRow) {
        const group = d3.select(this);
        const stat = windowMap.get(datum.ship_name);
        const delta = stat?.deltaWinRate ?? 0;
        const battles = stat?.battles ?? 0;
        // "+<games> <±delta>%": the window games-played count in a neutral tone,
        // the WR delta kept green/red. A delta that rounds to zero shows a
        // high-contrast "--%" (labelStrong: white in dark, near-black in light)
        // instead of a muted "0.0%" that washed out gray-on-gray on the pill.
        const isZeroDelta = Math.abs(delta) < 0.05;
        const gamesLabel = `+${battles}`;
        const deltaLabel = isZeroDelta ? '--%' : `${delta > 0 ? '+' : ''}${delta.toFixed(1)}%`;
        const baselineY = (y(datum.rowKey) ?? 0) + foregroundBarOffset + (foregroundBarHeight / 2) + 3;
        const deltaColor = isZeroDelta ? colors.labelStrong : delta > 0 ? colors.wrVeryGood : colors.wrBad;
        // Sit immediately right of this row's WR% label; fall back to the bar
        // end if (defensively) no WR label width was recorded.
        const leftX = (wrLabelEndByRow.get(datum.rowKey) ?? (barWidth(datum) + 6)) + deltaGap;

        const text = group.append('text')
            .attr('x', leftX)
            .attr('y', baselineY)
            .attr('text-anchor', 'start')
            .style('font-size', axisFontSize)
            .style('font-weight', '600');
        text.append('tspan')
            .style('fill', colors.labelMuted)
            .text(gamesLabel);
        text.append('tspan')
            .attr('dx', 4)
            .style('fill', deltaColor)
            .text(deltaLabel);

        const bbox = (text.node() as SVGTextElement).getBBox();
        const padX = 4;
        const padY = 1.5;

        group.insert('rect', 'text')
            .attr('x', bbox.x - padX)
            .attr('y', bbox.y - padY)
            .attr('width', bbox.width + padX * 2)
            .attr('height', bbox.height + padY * 2)
            .attr('rx', 3)
            .attr('fill', colors.surface)
            .attr('fill-opacity', 0.9)
            .style('stroke', colors.axisLine)
            .style('stroke-width', 0.5);
    });

    // Hovering a ship-name label on the left axis triggers the same readout (and
    // highlights its bar) — useful for tail ships whose bars are short.
    yAxis.selectAll('.tick text')
        .style('cursor', 'default')
        .on('mouseover', function (this: SVGTextElement, _event: MouseEvent, value: unknown) {
            const datum = rowByKey.get(String(value));
            if (!datum) return;
            renderDetails(datum);
            nodes.filter((row: RandomsChartRow) => row.rowKey === datum.rowKey)
                .select('.randoms-wins-bar')
                .transition().duration(70).attr('opacity', 0.82);
        })
        .on('mouseout', function (this: SVGTextElement, _event: MouseEvent, value: unknown) {
            renderDetails(null);
            nodes.filter((row: RandomsChartRow) => row.rowKey === String(value))
                .select('.randoms-wins-bar')
                .transition().duration(70).attr('opacity', 1);
        });
};

const RANDOMS_STALE_THRESHOLD_MS = 24 * 60 * 60 * 1000;
const RANDOMS_REHYDRATE_DELAY_MS = 6_000;
const RANDOMS_REHYDRATE_MAX_ATTEMPTS = 4;

const isRandomsTimestampStale = (timestamp: string | null): boolean => {
    if (!timestamp) {
        return true;
    }

    const updatedAt = new Date(timestamp).getTime();
    if (Number.isNaN(updatedAt)) {
        return true;
    }

    return Date.now() - updatedAt > RANDOMS_STALE_THRESHOLD_MS;
};

// Last successfully-applied randoms payload per player+realm, kept at module
// scope so switching tabs (which UNMOUNTS this component) and returning paints
// the prior result instantly instead of flashing empty. The fetch below uses
// ttlMs:0 so it never re-serves a STALE client-cached payload on remount — it
// always re-reads the freshest stored data from the server (cache-first, fast)
// and the rehydrate ladder still picks up a pending Celery refresh. This map is
// purely for instant paint; the network read is the source of truth.
const lastRandomsByKey = new Map<string, { rows: RandomsRow[]; updatedAt: string | null }>();

/**
 * Collapse a ship-class string to a vocabulary-independent key, so the
 * tier/type payload's "Aircraft Carrier" and this payload's "AirCarrier"
 * compare equal. The abbreviation map already carries both spellings; the
 * stripped-string fallback covers any class neither payload has named yet.
 */
const shipTypeKey = (shipType: string): string => (
    SHIP_TYPE_ABBREV[shipType] ?? shipType.replace(/[^a-z]/gi, '').toLowerCase()
);

/**
 * Resolve a drill-down request against the ships this captain actually has.
 *
 * Pure so the two things most likely to break can be tested directly: the
 * cross-payload class vocabulary, and the tier handling (an explicit tier is
 * kept only if some ship has it; an empty tier list means "all tiers of this
 * class", which must override the tab's default tier-5 floor).
 *
 * Returns nulls for anything that should be left alone.
 */
export const resolveRandomsFilterRequest = (
    rows: RandomsRow[],
    request: { shipTypes: string[]; tiers: number[] },
): { types: string[] | null; tiers: number[] | null } => {
    if (rows.length === 0) {
        return { types: null, tiers: null };
    }

    const wanted = new Set(request.shipTypes.map(shipTypeKey));
    const matchedTypes = Array.from(new Set(rows.map((row) => row.ship_type)))
        .filter((shipType) => wanted.has(shipTypeKey(shipType)));

    const allTiers = Array.from(new Set(rows.map((row) => row.ship_tier))).sort((a, b) => b - a);
    let tiers: number[] | null;
    if (request.tiers.length === 0) {
        // Whole-class drill-down: every tier, so the tab's default tier-5 floor
        // does not silently hide the low-tier half of that class.
        tiers = allTiers;
    } else {
        const present = new Set(allTiers);
        const matched = request.tiers.filter((tier) => present.has(tier));
        tiers = matched.length > 0 ? matched : null;
    }

    return {
        types: matchedTypes.length > 0 ? matchedTypes : null,
        tiers,
    };
};

const deriveRandomsSelections = (rows: RandomsRow[]): { types: string[]; tiers: number[] } => {
    const types = Array.from(new Set(rows.map((r) => r.ship_type)));
    const tiers = Array.from(new Set(rows.map((r) => r.ship_tier)))
        .filter((tier) => tier >= 5)
        .sort((a, b) => b - a);
    return { types, tiers };
};

const RandomsSVG: React.FC<RandomsSVGProps> = ({
    playerId,
    playerName,
    isLoading = false,
    theme = 'light',
    filterRequest = null,
    compact = false,
    windowName = DEFAULT_BATTLE_HISTORY_WINDOW,
    refreshNonce = 0,
}) => {
    const { realm } = useRealm();
    const requestSignal = usePlayerRequestSignal();
    // Seed from the module-scope cache so a tab-switch return repaints the prior
    // (already-fresh) result instantly instead of flashing a loader or showing
    // the stale-then-corrected ladder again.
    const seeded = lastRandomsByKey.get(`${playerId}:${realm}`) ?? null;
    const seededSelections = seeded ? deriveRandomsSelections(seeded.rows) : null;
    const [allShips, setAllShips] = useState<RandomsRow[]>(() => seeded?.rows ?? []);
    const [selectedTypes, setSelectedTypes] = useState<string[]>(() => seededSelections?.types ?? []);
    const [selectedTiers, setSelectedTiers] = useState<number[]>(() => seededSelections?.tiers ?? []);
    const [isChartLoading, setIsChartLoading] = useState(() => seeded === null);
    const [randomsUpdatedAt, setRandomsUpdatedAt] = useState<string | null>(() => seeded?.updatedAt ?? null);
    const [hoveredShip, setHoveredShip] = useState<RandomsRow | null>(null);
    // Minimum lifetime-battles cutoff. Always starts at 0 (no filtering).
    const [minBattles, setMinBattles] = useState<number>(0);
    // Minimum win-rate cutoff (whole %). Always starts at 0 (no filtering) and
    // resets to 0 on player/realm change; scaled 0..the player's best ship WR.
    const [minWR, setMinWR] = useState<number>(0);
    const [activityMode, setActivityMode] = useState<ActivityMode>('all');
    const [windowStats, setWindowStats] = useState<RandomsWindowMap>(() => new Map());
    // Whether the played-in-window join has SETTLED for the current window.
    // Only the compact variant reads it, and it must: that variant's ship set
    // IS the join, so drawing before it lands would paint every lifetime ship
    // and then cull — the opposite of what the chart claims to show.
    const [windowLoaded, setWindowLoaded] = useState(false);
    const containerRef = useRef<HTMLDivElement>(null);

    // Fetch ALL ships, then re-fetch if stale until the backend delivers fresh data.
    useEffect(() => {
        let cancelled = false;
        let rehydrateTimeout: ReturnType<typeof setTimeout> | null = null;
        // If we seeded from the last result, keep showing it during the
        // background re-read instead of flipping back to the loader.
        const hasSeed = lastRandomsByKey.has(`${playerId}:${realm}`);

        const applyResult = (data: unknown, updatedAt: string | null) => {
            const result = normalizeRandomsRows(data)
                .filter((row) => row.ship_type && row.ship_type.toLowerCase() !== 'unknown')
                .filter((row) => row.pvp_battles > 0);
            setAllShips(result);
            setRandomsUpdatedAt(updatedAt);

            const { types, tiers } = deriveRandomsSelections(result);
            // A drill-down that still owns the selection wins over the defaults.
            // Every fetch resolve reseeds the pills (ttlMs is 0, so this runs on
            // every mount), which would otherwise wipe a filter the user just
            // arrived with: on a return visit the module-cache seed makes
            // `allShips` non-empty at mount, so the drill-down applies FIRST and
            // this reseed would land on top of it.
            const owning = activeFilterRef.current;
            const resolved = owning ? resolveRandomsFilterRequest(result, owning) : null;
            setSelectedTypes(resolved?.types ?? types);
            setSelectedTiers(resolved?.tiers ?? tiers);

            // Persist for instant repaint on the next mount (tab-switch return).
            lastRandomsByKey.set(`${playerId}:${realm}`, { rows: result, updatedAt });
        };

        const fetchRandoms = async (attempt: number) => {
            if (attempt === 0 && !hasSeed) {
                setIsChartLoading(true);
            }

            try {
                const { data, headers } = await fetchSharedJson<unknown>(withRealm(`/api/fetch/randoms_data/${playerId}/?all=true`, realm), {
                    label: `Randoms data ${playerId}`,
                    responseHeaders: ['X-Randoms-Updated-At'],
                    // ttlMs:0 — no settled client cache. A stale payload cached on
                    // first view must never be re-served on a tab-switch remount;
                    // we always re-read the freshest stored data from the (fast,
                    // cache-first) server. Instant paint is handled by the module
                    // cache seed above; in-flight dedup still prevents dup fetches.
                    ttlMs: 0,
                    signal: requestSignal,
                    cacheKey: `randoms:${playerId}:${attempt}`,
                });

                if (cancelled) {
                    return;
                }

                const updatedAt = headers['X-Randoms-Updated-At'] ?? null;
                applyResult(data, updatedAt);

                // If still stale and we haven't exhausted retries, schedule a re-fetch
                // so we pick up the Celery-refreshed data without a page reload.
                if (isRandomsTimestampStale(updatedAt) && attempt < RANDOMS_REHYDRATE_MAX_ATTEMPTS) {
                    rehydrateTimeout = setTimeout(() => {
                        if (!cancelled) {
                            void fetchRandoms(attempt + 1);
                        }
                    }, RANDOMS_REHYDRATE_DELAY_MS * degradationMonitor.getPollIntervalMultiplier());
                }
            } catch (error) {
                // Benign cancellation (nav / realm switch) — not an error.
                if (isAbortError(error)) {
                    return;
                }
                if (!cancelled) {
                    console.error('Error fetching data:', error);
                }
            } finally {
                if (!cancelled && attempt === 0) {
                    setIsChartLoading(false);
                }
            }
        };

        void fetchRandoms(0);

        return () => {
            cancelled = true;
            if (rehydrateTimeout) {
                clearTimeout(rehydrateTimeout);
            }
        };
    }, [playerId, realm, requestSignal]);

    // Join the random-battle window (delta_win_rate + played-in-window) by ship
    // name. `windowName` is the battle-history window it reads over: the Ships
    // tab leaves it at the card default (30d), the Activity tab hands down its
    // live pill. Both the url and the cacheKey carry that window plus the host
    // card's refreshNonce, so each variant dedupes onto the request its own
    // host card is already making rather than opening a second one.
    //
    // For the full variant these are pure enrichment: if the fetch is slow or
    // fails, the delta pills and the Window Only filter simply stay inactive.
    // For the compact variant the join IS the data — hence `windowLoaded`,
    // which gates that variant's draw.
    useEffect(() => {
        if (!playerName) return;
        let cancelled = false;
        setWindowLoaded(false);

        void (async () => {
            try {
                const { data } = await fetchSharedJson<BattleHistoryPayload>(
                    battleHistoryFetchUrl(playerName, realm, windowName),
                    {
                        label: `Randoms window ${playerName}`,
                        ttlMs: BATTLE_HISTORY_FETCH_TTL_MS,
                        cacheKey: battleHistoryCacheKey(
                            playerName, realm, windowName, 'random', 0, refreshNonce,
                        ),
                        responseHeaders: ['X-Ranked-Observation-Pending', 'X-Ship-Pop-Pending'],
                        signal: requestSignal,
                    },
                );

                if (cancelled) return;

                const map: RandomsWindowMap = new Map();
                for (const ship of data?.by_ship ?? []) {
                    if (!ship.ship_name) continue;
                    const battles = ship.battles ?? 0;
                    const wins = ship.wins ?? 0;
                    map.set(ship.ship_name, {
                        deltaWinRate: ship.delta_win_rate ?? null,
                        battles,
                        wins,
                        // The payload carries losses directly; the subtraction
                        // is only a floor for an older backend that does not.
                        losses: ship.losses ?? Math.max(battles - wins, 0),
                    });
                }
                setWindowStats(map);
                setWindowLoaded(true);
            } catch (error) {
                // An abort is a navigation/realm switch, not an answer: leave
                // the gate closed so the compact chart shows its loader rather
                // than flashing "no ships" on the way out.
                if (isAbortError(error) || cancelled) return;
                // Enrichment-only for the full variant; the compact variant
                // shows its empty state rather than falling back to lifetime.
                setWindowStats(new Map());
                setWindowLoaded(true);
            }
        })();

        return () => {
            cancelled = true;
        };
    }, [playerName, realm, windowName, refreshNonce, requestSignal]);

    // Largest per-ship lifetime battle count — the min-battles slider's ceiling.
    const maxBattles = useMemo(
        () => allShips.reduce((max, row) => Math.max(max, row.pvp_battles), 0),
        [allShips],
    );

    // Highest per-ship win rate (whole %) — the Min WR slider's ceiling.
    const maxWR = useMemo(
        () => Math.round(allShips.reduce((max, row) => Math.max(max, row.win_ratio), 0) * 100),
        [allShips],
    );

    // Both cutoffs always reset to 0 on player/realm change (never carry over).
    useEffect(() => {
        setMinBattles(0);
        setMinWR(0);
    }, [playerId, realm]);

    // Keep the cutoff within reach: if the player's grindiest ship has fewer
    // battles than the current cutoff, the chart would empty out silently. Pull
    // the cutoff down to the ceiling so at least the top ship stays visible.
    useEffect(() => {
        if (maxBattles > 0 && minBattles > maxBattles) {
            setMinBattles(maxBattles);
        }
    }, [maxBattles, minBattles]);

    // With no ships played in the trailing window, Window Only would empty the
    // chart, so lock the Activity filter to All: Window Only is disabled in the
    // UI and the mode falls back to 'all' regardless of any stored value.
    const hasWindowActivity = windowStats.size > 0;
    const effectiveActivityMode: ActivityMode = hasWindowActivity ? activityMode : 'all';

    // Filter and sort every matching ship; the chart container scrolls to fit.
    //
    // The compact variant takes a separate branch rather than a locked mode:
    // its ship set is exactly the window join, with NO pill or slider
    // predicate applied. In particular the tier-5 floor in
    // deriveRandomsSelections must not reach it — a T4 ship played this window
    // belongs on that chart, and with no pills there would be nothing to
    // reveal or undo the floor with.
    const chartData = useMemo(() => {
        const base = compact
            ? allShips.filter((row) => windowStats.has(row.ship_name))
            : allShips.filter((row) => (
                selectedTypes.includes(row.ship_type)
                && selectedTiers.includes(row.ship_tier)
                && row.pvp_battles >= minBattles
                && row.win_ratio * 100 >= minWR
                // Window Only hides ships not played in the trailing window;
                // All keeps every matching ship.
                && (effectiveActivityMode !== 'window' || windowStats.has(row.ship_name))
            ));
        return base.slice().sort((a, b) => b.pvp_battles - a.pvp_battles);
    }, [compact, allShips, selectedTypes, selectedTiers, minBattles, minWR, effectiveActivityMode, windowStats]);

    // Draw chart when data (or the window join) changes. The compact variant
    // holds until the join has settled: its rows ARE the join, so drawing
    // early would paint the full lifetime list and then cull it.
    useEffect(() => {
        if (!containerRef.current) return;
        if (compact && !windowLoaded) return;
        d3.select(containerRef.current).selectAll("*").remove();
        setHoveredShip(null);
        if (chartData.length > 0) {
            drawBattlePlotDesign1(containerRef.current, chartData, theme, windowStats, setHoveredShip);
        }
    }, [compact, windowLoaded, chartData, theme, windowStats]);

    // Apply a drill-down request from the Profile tab's tier figure. Waits for
    // `allShips`, because a request can arrive before the payload lands (the
    // click switches tabs and mounts this component in the same beat), and
    // fires once per nonce so a second click re-applies.
    const appliedFilterNonce = useRef<number | null>(null);
    // The drill-down that currently owns the pills, or null once the user has
    // taken them over by hand. Read by applyResult so a refetch re-asserts the
    // drill-down instead of resetting to defaults.
    const activeFilterRef = useRef<RandomsFilterRequest | null>(null);
    useEffect(() => {
        if (!filterRequest || allShips.length === 0) {
            return;
        }
        if (appliedFilterNonce.current === filterRequest.nonce) {
            return;
        }
        appliedFilterNonce.current = filterRequest.nonce;
        activeFilterRef.current = filterRequest;

        const { types, tiers } = resolveRandomsFilterRequest(allShips, filterRequest);
        if (types) {
            setSelectedTypes(types);
        }
        if (tiers) {
            setSelectedTiers(tiers);
        }
    }, [filterRequest, allShips]);

    const availableTypes = Array.from(new Set(allShips.map((row) => row.ship_type)));
    // Tier pills stay floored at 5, so the tab opens exactly as it always has
    // and low-tier ships do not clutter the default view. The one exception is
    // a tier currently selected: a drill-down from the Profile tab's tier
    // figure can land on tier 2, and that tier needs a visible, un-pressable
    // pill so the filter it applied can be seen and undone rather than acting
    // invisibly.
    const availableTiers = Array.from(new Set([
        ...allShips.map((row) => row.ship_tier).filter((tier) => tier >= 5),
        ...selectedTiers,
    ])).sort((a, b) => b - a);

    const areAllSelected = <T extends string | number>(selected: T[], available: T[]) => (
        available.length > 0
        && selected.length === available.length
        && available.every((value) => selected.includes(value))
    );

    const toggleSelection = <T extends string | number>(current: T[], value: T, available: T[]) => {
        const allSelected = areAllSelected(current, available);
        if (allSelected) {
            return [value];
        }

        if (current.includes(value)) {
            const next = current.filter((entry) => entry !== value);
            return next.length > 0 ? next : [...available];
        }

        const next = [...current, value];
        return areAllSelected(next, available) ? [...available] : next;
    };

    const allTypesSelected = areAllSelected(selectedTypes, availableTypes);
    const allTiersSelected = areAllSelected(selectedTiers, availableTiers);

    const toggleType = (shipType: string) => {
        // The user is steering the pills now; release the drill-down's claim
        // so a later refetch reseeds defaults rather than re-asserting it.
        activeFilterRef.current = null;
        trackEvent('randoms-filter', { realm, control: 'type', value: shipType });
        setSelectedTypes((current) => toggleSelection(current, shipType, availableTypes));
    };

    const toggleTier = (tier: number) => {
        // The user is steering the pills now; release the drill-down's claim
        // so a later refetch reseeds defaults rather than re-asserting it.
        activeFilterRef.current = null;
        trackEvent('randoms-filter', { realm, control: 'tier', value: tier });
        setSelectedTiers((current) => toggleSelection(current, tier, availableTiers));
    };

    const selectAllTypes = () => {
        // The user is steering the pills now; release the drill-down's claim
        // so a later refetch reseeds defaults rather than re-asserting it.
        activeFilterRef.current = null;
        trackEvent('randoms-filter', { realm, control: 'type', value: 'all' });
        setSelectedTypes([...availableTypes]);
    };

    const selectAllTiers = () => {
        // The user is steering the pills now; release the drill-down's claim
        // so a later refetch reseeds defaults rather than re-asserting it.
        activeFilterRef.current = null;
        trackEvent('randoms-filter', { realm, control: 'tier', value: 'all' });
        setSelectedTiers([...availableTiers]);
    };

    // One control that returns EVERY filter on this tab to the state the tab
    // opens in: all types, the default tier floor, both cutoffs at zero, and
    // the activity mode back to All. Clearing also drops a drill-down's claim,
    // so an off-floor tier it pinned (say T2) releases its pill instead of
    // surviving the reset.
    const clearFilters = () => {
        activeFilterRef.current = null;
        const { types, tiers } = deriveRandomsSelections(allShips);
        setSelectedTypes(types);
        setSelectedTiers(tiers);
        setMinWR(0);
        setMinBattles(0);
        setActivityMode('all');
        trackEvent('randoms-filter', { realm, control: 'clear' });
    };

    const getFreshnessStatus = (timestamp: string | null): 'fresh' | 'stale' | 'unknown' => {
        if (!timestamp) {
            return 'unknown';
        }

        const updatedAt = new Date(timestamp).getTime();
        if (Number.isNaN(updatedAt)) {
            return 'unknown';
        }

        const ageMs = Date.now() - updatedAt;
        return ageMs <= 24 * 60 * 60 * 1000 ? 'fresh' : 'stale';
    };

    const formatTimestamp = (timestamp: string | null): string => {
        if (!timestamp) {
            return 'unknown';
        }

        const parsed = new Date(timestamp);
        if (Number.isNaN(parsed.getTime())) {
            return 'unknown';
        }

        return parsed.toLocaleString();
    };

    const randomsFreshness = getFreshnessStatus(randomsUpdatedAt);
    // Window record for the hovered ship, compact variant only — the full
    // variant's hover line stays lifetime end to end, because its rows are not
    // window-scoped and a mixed line there would have nothing to anchor it.
    const compactRecord = compact && hoveredShip
        ? windowStats.get(hoveredShip.ship_name) ?? null
        : null;

    const shouldGrayOut = isLoading || isChartLoading || (compact && !windowLoaded);
    const shouldShowEmptyState = !shouldGrayOut && chartData.length === 0;
    const filterButtonClass = (selected: boolean) => selected
        ? 'border border-[var(--accent-mid)] bg-[var(--accent-faint)] px-2 py-1 text-xs font-medium text-[var(--accent-dark)]'
        : 'border border-[var(--border)] bg-[var(--bg-surface)] px-2 py-1 text-xs font-medium text-[var(--text-secondary)]';

    return (
        <div>
            {/* pt-2.5/pl-[15px] is the shared tab-top header spot across the
                player insight tabs. */}
            {!compact ? (
            <div className="mb-2 pt-2.5 pl-[15px] text-xs text-[var(--text-secondary)]">
                Randoms data last refreshed: {formatTimestamp(randomsUpdatedAt)}
                {' · '}
                <span className={randomsFreshness === 'fresh' ? 'text-green-700' : randomsFreshness === 'stale' ? 'text-red-700' : 'text-[var(--text-secondary)]'}>
                    {randomsFreshness === 'fresh' ? 'fresh' : randomsFreshness === 'stale' ? 'stale' : 'unknown'}
                </span>
            </div>
            ) : null}
            {/* pl mirrors the chart's margin.left below (60 compact / 85 +
                RANDOMS_CHART_SHIFT_RIGHT_PX desktop) so the filter rows start
                on the y-axis line. */}
            {!compact ? (
            <div className="mt-[40px] space-y-3 pl-[75px] text-sm sm:pl-[115px]">
                <div className="flex flex-wrap justify-start gap-1">
                    <button
                        key="all-types"
                        type="button"
                        aria-pressed={allTypesSelected}
                        className={filterButtonClass(allTypesSelected)}
                        onClick={selectAllTypes}
                    >
                        All
                    </button>
                    {availableTypes.map((shipType) => (
                        <button
                            key={shipType}
                            type="button"
                            aria-pressed={selectedTypes.includes(shipType)}
                            className={filterButtonClass(selectedTypes.includes(shipType))}
                            onClick={() => toggleType(shipType)}
                        >
                            {shipType}
                        </button>
                    ))}
                    {/* Resets every filter on the tab, not just this row — so it
                        sits apart from the type pills rather than reading as one
                        more type. */}
                    <button
                        key="clear-filters"
                        type="button"
                        className={`${CLEAR_FILTERS_BUTTON_CLASS} ml-3`}
                        onClick={clearFilters}
                    >
                        Clear
                    </button>
                </div>
                <div className="flex flex-wrap justify-start gap-1">
                    <button
                        key="all-tiers"
                        type="button"
                        aria-pressed={allTiersSelected}
                        className={filterButtonClass(allTiersSelected)}
                        onClick={selectAllTiers}
                    >
                        All
                    </button>
                    {availableTiers.map((tier) => (
                        <button
                            key={tier}
                            type="button"
                            aria-pressed={selectedTiers.includes(tier)}
                            className={filterButtonClass(selectedTiers.includes(tier))}
                            onClick={() => toggleTier(tier)}
                        >
                            T{tier}
                        </button>
                    ))}
                </div>
                <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
                    <div className="flex items-center gap-2">
                        <label htmlFor="randoms-min-wr" className="shrink-0 font-semibold text-[var(--text-primary)]">
                            Min WR
                        </label>
                        <input
                            id="randoms-min-wr"
                            type="range"
                            min={0}
                            max={Math.max(maxWR, 1)}
                            step={1}
                            value={Math.min(minWR, Math.max(maxWR, 1))}
                            onChange={(event) => setMinWR(Number(event.target.value))}
                            onMouseUp={() => trackEvent('randoms-filter', { realm, control: 'min_wr', value: minWR })}
                            onTouchEnd={() => trackEvent('randoms-filter', { realm, control: 'min_wr', value: minWR })}
                            className="bh-scope-slider w-32 max-w-full cursor-pointer"
                            aria-label="Minimum win rate to show a ship"
                        />
                        <span className="min-w-[3.5rem] tabular-nums text-xs text-[var(--text-secondary)]">
                            &ge; {minWR}%
                        </span>
                    </div>
                    <div className="flex items-center gap-2">
                        <label htmlFor="randoms-min-battles" className="shrink-0 font-semibold text-[var(--text-primary)]">
                            Min battles
                        </label>
                        <input
                            id="randoms-min-battles"
                            type="range"
                            min={0}
                            max={Math.max(maxBattles, 1)}
                            step={1}
                            value={Math.min(minBattles, Math.max(maxBattles, 1))}
                            onChange={(event) => setMinBattles(Number(event.target.value))}
                            onMouseUp={() => trackEvent('randoms-filter', { realm, control: 'min_battles', value: minBattles })}
                            onTouchEnd={() => trackEvent('randoms-filter', { realm, control: 'min_battles', value: minBattles })}
                            className="bh-scope-slider w-32 max-w-full cursor-pointer"
                            aria-label="Minimum lifetime random battles to show a ship"
                        />
                        <span className="min-w-[3.5rem] tabular-nums text-xs text-[var(--text-secondary)]">
                            &ge; {minBattles}
                        </span>
                    </div>
                </div>
                <div className="flex flex-wrap items-center gap-2" role="radiogroup" aria-label="Activity filter">
                    <div className="shrink-0 font-semibold text-[var(--text-primary)]">Activity</div>
                    <div className="flex flex-wrap items-center gap-1">
                        {ACTIVITY_MODE_OPTIONS.map((option) => {
                            // No window activity → lock to All: disable Window
                            // Only so the user can't switch to an empty view.
                            const locked = !hasWindowActivity && option.value !== 'all';
                            return (
                                <button
                                    key={option.value}
                                    type="button"
                                    role="radio"
                                    aria-checked={effectiveActivityMode === option.value}
                                    disabled={locked}
                                    title={locked ? 'No activity in the trailing window' : undefined}
                                    className={`${filterButtonClass(effectiveActivityMode === option.value)}${locked ? ' cursor-not-allowed opacity-40' : ''}`}
                                    onClick={() => {
                                        if (locked) return;
                                        setActivityMode(option.value);
                                        trackEvent('randoms-filter', { realm, control: 'activity_mode', value: option.value });
                                    }}
                                >
                                    {option.label}
                                </button>
                            );
                        })}
                    </div>
                </div>
            </div>
            ) : null}

            {shouldShowEmptyState ? (
                // Matches the hover-details line's slot (my / min-h / pl / text-sm)
                // so the message starts at the same x,y whether the chart is
                // populated or empty. pl mirrors the filter rows above so the
                // text left-aligns with them; my-3 keeps the gap to the filters
                // above equal to the gap to the chart below.
                <div className="my-3 flex min-h-[1.5rem] items-center pl-[75px] text-sm sm:pl-[115px]">
                    <span className="text-[var(--text-secondary)]">
                        {compact ? 'No ships played in this window.' : 'No ships match the selected filters.'}
                    </span>
                </div>
            ) : null}

            {!shouldShowEmptyState ? (
                <div className="my-3 flex min-h-[1.5rem] items-center pl-[75px] text-sm sm:pl-[115px]">
                    {hoveredShip ? (
                        <span>
                            <span className="font-bold text-[var(--accent-dark)]">{hoveredShip.ship_name}</span>
                            <span className="text-[var(--text-secondary)]">{'  •  '}</span>
                            <span className="text-[var(--text-secondary)]">T{hoveredShip.ship_tier} {hoveredShip.ship_type}</span>
                            <span className="text-[var(--text-secondary)]">{'  •  '}</span>
                            <span className="text-[var(--text-secondary)]">
                                {hoveredShip.pvp_battles.toLocaleString()} battles
                                {' • '}
                                {/* The one window-scoped figure on an otherwise
                                    lifetime line. The compact chart's whole
                                    subject is the window, so the captain's
                                    record THERE is the useful quote; a lifetime
                                    win total says nothing the lifetime battle
                                    count beside it and the row's own WR label
                                    have not already said. Letters at 0.75em and the trailing
                                    qualifier follow the strip crosshair's
                                    readout, so a W/L record reads the same in
                                    both places. Falls back to the lifetime
                                    total if the join somehow lacks this ship. */}
                                {compactRecord ? (
                                    <>
                                        {compactRecord.wins.toLocaleString()}<span className="text-[0.75em]">W</span>
                                        {' '}
                                        {compactRecord.losses.toLocaleString()}<span className="text-[0.75em]">L</span>
                                        <span className="text-[var(--text-muted)]"> this window</span>
                                    </>
                                ) : (
                                    <>{hoveredShip.wins.toLocaleString()} wins</>
                                )}
                            </span>
                            {/* The compact line stops at the window record. Its
                                bars already print the ship's win rate at the end
                                of every row, so repeating it here spent the
                                line's last slot restating what the reader is
                                looking at. The Ships tab keeps it: that chart is
                                filterable by win rate, so the hovered value is
                                the figure the reader is steering by. */}
                            {!compact ? (
                                <>
                                    <span className="text-[var(--text-secondary)]">{'  •  '}</span>
                                    <span className="font-semibold text-[var(--text-primary)]">{(hoveredShip.win_ratio * 100).toFixed(1)}% win rate</span>
                                </>
                            ) : null}
                        </span>
                    ) : null}
                </div>
            ) : null}

            <div className="relative">
                <div
                    className={shouldGrayOut ? 'pointer-events-none opacity-60 grayscale transition' : 'transition'}
                    aria-busy={shouldGrayOut}
                >
                    <div
                        ref={containerRef}
                        className="mx-auto overflow-y-auto overflow-x-hidden"
                        style={{ maxHeight: `${RANDOMS_CHART_MAX_VIEWPORT_PX}px`, width: 'calc(100% - 30px)' }}
                    ></div>
                </div>
                {shouldGrayOut ? (
                    <div className="absolute inset-0 flex items-center justify-center rounded bg-[var(--bg-page)]/65">
                        <span className="rounded border border-[var(--border)] bg-[var(--bg-surface)] px-2 py-1 text-xs font-medium text-[var(--text-secondary)]">
                            {compact ? 'Loading ships played in this window...' : 'Loading random battles...'}
                        </span>
                    </div>
                ) : null}
            </div>
        </div>
    );
};

export default RandomsSVG;
