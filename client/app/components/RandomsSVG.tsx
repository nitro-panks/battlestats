import React, { useEffect, useState, useRef, useMemo } from 'react';
import * as d3 from 'd3';
import { fetchSharedJson } from '../lib/sharedJsonFetch';
import { chartColors, type ChartTheme } from '../lib/chartTheme';
import { useRealm } from '../context/RealmContext';
import { withRealm } from '../lib/realmParams';
import { trackEvent } from '../lib/umami';

interface RandomsSVGProps {
    playerId: number;
    isLoading?: boolean;
    theme?: ChartTheme;
}

interface RandomsRow {
    pvp_battles: number;
    ship_name: string;
    ship_chart_name: string;
    ship_type: string;
    ship_tier: number;
    win_ratio: number;
    wins: number;
}

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
// Matches the Activity-tab battle-history table cap (800px) so the Ships chart
// uses the same vertical room instead of being pinned to a shorter box.
const RANDOMS_CHART_MAX_VIEWPORT_PX = 800;
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
    const margin = compact
        ? { top: 28, right: 14, bottom: 48, left: 52 }
        : { top: 28, right: 96, bottom: 48, left: 68 + RANDOMS_CHART_SHIFT_RIGHT_PX };
    const axisFontSize = compact ? '9px' : '10px';
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

    nodes.append('text')
        .attr('x', (datum: RandomsChartRow) => {
            const labelX = barWidth(datum) + 6;
            return labelX > width - 4 ? width - 4 : labelX;
        })
        .attr('y', (datum: RandomsChartRow) => (y(datum.rowKey) ?? 0) + foregroundBarOffset + (foregroundBarHeight / 2) + 3)
        .style('font-size', axisFontSize)
        .style('fill', colors.labelMuted)
        .attr('text-anchor', (datum: RandomsChartRow) => (barWidth(datum) + 6 > width - 4 ? 'end' : 'start'))
        .text((datum: RandomsChartRow) => `${(datum.win_ratio * 100).toFixed(1)}%`);

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

const deriveRandomsSelections = (rows: RandomsRow[]): { types: string[]; tiers: number[] } => {
    const types = Array.from(new Set(rows.map((r) => r.ship_type)));
    const tiers = Array.from(new Set(rows.map((r) => r.ship_tier)))
        .filter((tier) => tier >= 5)
        .sort((a, b) => b - a);
    return { types, tiers };
};

const RandomsSVG: React.FC<RandomsSVGProps> = ({
    playerId,
    isLoading = false,
    theme = 'light',
}) => {
    const { realm } = useRealm();
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
            setSelectedTypes(types);
            setSelectedTiers(tiers);

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
                    }, RANDOMS_REHYDRATE_DELAY_MS);
                }
            } catch (error) {
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
    }, [playerId, realm]);

    // Filter and sort every matching ship; the chart container scrolls to fit.
    const chartData = useMemo(() => {
        const filtered = allShips.filter(
            (row) => selectedTypes.includes(row.ship_type) && selectedTiers.includes(row.ship_tier)
        );
        return filtered
            .sort((a, b) => b.pvp_battles - a.pvp_battles);
    }, [allShips, selectedTypes, selectedTiers]);

    // Draw chart when data changes
    useEffect(() => {
        if (!containerRef.current) return;
        d3.select(containerRef.current).selectAll("*").remove();
        setHoveredShip(null);
        if (chartData.length > 0) {
            drawBattlePlotDesign1(containerRef.current, chartData, theme, setHoveredShip);
        }
    }, [chartData, theme]);

    const availableTypes = Array.from(new Set(allShips.map((row) => row.ship_type)));
    const availableTiers = Array.from(new Set(allShips.map((row) => row.ship_tier)))
        .filter((tier) => tier >= 5)
        .sort((a, b) => b - a);

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
        trackEvent('randoms-filter', { realm, control: 'type', value: shipType });
        setSelectedTypes((current) => toggleSelection(current, shipType, availableTypes));
    };

    const toggleTier = (tier: number) => {
        trackEvent('randoms-filter', { realm, control: 'tier', value: tier });
        setSelectedTiers((current) => toggleSelection(current, tier, availableTiers));
    };

    const selectAllTypes = () => {
        trackEvent('randoms-filter', { realm, control: 'type', value: 'all' });
        setSelectedTypes([...availableTypes]);
    };

    const selectAllTiers = () => {
        trackEvent('randoms-filter', { realm, control: 'tier', value: 'all' });
        setSelectedTiers([...availableTiers]);
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

    const shouldGrayOut = isLoading || isChartLoading;
    const shouldShowEmptyState = !shouldGrayOut && chartData.length === 0;
    const filterButtonClass = (selected: boolean) => selected
        ? 'border border-[var(--accent-mid)] bg-[var(--accent-faint)] px-2 py-1 text-xs font-medium text-[var(--accent-dark)]'
        : 'border border-[var(--border)] bg-[var(--bg-surface)] px-2 py-1 text-xs font-medium text-[var(--text-secondary)]';

    return (
        <div>
            <div className="mb-2 text-xs text-[var(--text-secondary)]">
                Randoms data last refreshed: {formatTimestamp(randomsUpdatedAt)}
                {' · '}
                <span className={randomsFreshness === 'fresh' ? 'text-green-700' : randomsFreshness === 'stale' ? 'text-red-700' : 'text-[var(--text-secondary)]'}>
                    {randomsFreshness === 'fresh' ? 'fresh' : randomsFreshness === 'stale' ? 'stale' : 'unknown'}
                </span>
            </div>
            <div className="mb-3 text-sm">
                <div className="mb-3 flex flex-wrap items-start gap-3">
                    <div className="w-20 shrink-0 font-semibold text-[var(--text-primary)]">Ship Type</div>
                    <div className="flex flex-1 flex-wrap justify-start gap-1">
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
                    </div>
                </div>
                <div className="mb-1 flex flex-wrap items-start gap-3">
                    <div className="w-20 shrink-0 font-semibold text-[var(--text-primary)]">Tier</div>
                    <div className="flex flex-1 flex-wrap justify-start gap-1">
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
                </div>
            </div>

            {shouldShowEmptyState ? (
                <p className="text-sm text-[var(--text-secondary)]">No ships match the selected filters.</p>
            ) : null}

            {!shouldShowEmptyState ? (
                <div className="mb-1 min-h-[1.25rem] text-xs">
                    {hoveredShip ? (
                        <span>
                            <span className="font-bold text-[var(--accent-dark)]">{hoveredShip.ship_name}</span>
                            <span className="text-[var(--text-secondary)]">{'  •  '}</span>
                            <span className="text-[var(--text-secondary)]">T{hoveredShip.ship_tier} {hoveredShip.ship_type}</span>
                            <span className="text-[var(--text-secondary)]">{'  •  '}</span>
                            <span className="text-[var(--text-secondary)]">{hoveredShip.pvp_battles.toLocaleString()} battles • {hoveredShip.wins.toLocaleString()} wins</span>
                            <span className="text-[var(--text-secondary)]">{'  •  '}</span>
                            <span className="font-semibold text-[var(--text-primary)]">{(hoveredShip.win_ratio * 100).toFixed(1)}% win rate</span>
                        </span>
                    ) : (
                        <span className="text-[var(--text-secondary)]">Hover a bar or ship name for ship details.</span>
                    )}
                </div>
            ) : null}

            <div className="relative">
                <div
                    className={shouldGrayOut ? 'pointer-events-none opacity-60 grayscale transition' : 'transition'}
                    aria-busy={shouldGrayOut}
                >
                    <div
                        ref={containerRef}
                        className="overflow-y-auto overflow-x-hidden"
                        style={{ maxHeight: `${RANDOMS_CHART_MAX_VIEWPORT_PX}px` }}
                    ></div>
                </div>
                {shouldGrayOut ? (
                    <div className="absolute inset-0 flex items-center justify-center rounded bg-[var(--bg-page)]/65">
                        <span className="rounded border border-[var(--border)] bg-[var(--bg-surface)] px-2 py-1 text-xs font-medium text-[var(--text-secondary)]">
                            Loading random battles...
                        </span>
                    </div>
                ) : null}
            </div>
        </div>
    );
};

export default RandomsSVG;
