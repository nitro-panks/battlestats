import React, { useEffect, useState, useRef, useMemo } from 'react';
import * as d3 from 'd3';
import { PLAYER_ROUTE_PANEL_FETCH_TTL_MS } from '../lib/playerRouteFetch';
import { fetchSharedJson } from '../lib/sharedJsonFetch';
import { chartColors, type ChartTheme } from '../lib/chartTheme';

interface RandomsSVGProps {
    playerId: number;
    isLoading?: boolean;
    design?: RandomsChartDesign;
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

type RandomsChartDesign = 'design1' | 'design2';

const TOP_N = 20;
const DEFAULT_RANDOMS_DESIGN: RandomsChartDesign = 'design1';
const WR_BREAKPOINTS = [45, 50, 52, 54, 56, 60, 65];
const RANDOMS_CHART_SHIFT_RIGHT_PX = 15;
const RANDOMS_CHART_RIGHT_EXTENSION_PX = 10;
const RANDOMS_BAR_HEIGHT_INCREASE_PX = 2;
const RANDOMS_CHART_HEIGHT_INCREASE_PX = 100;

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

const selectShipTypeColor = (shipType: string, theme: ChartTheme): string => {
    const colors = chartColors[theme];
    switch (shipType) {
        case 'Destroyer':
            return colors.shipDD;
        case 'Cruiser':
            return colors.shipCA;
        case 'Battleship':
            return colors.shipBB;
        case 'AirCarrier':
        case 'Carrier':
            return colors.shipCV;
        case 'Submarine':
            return colors.shipSS;
        default:
            return colors.shipDefault;
    }
};

const drawBattlePlotDesign1 = (containerElement: HTMLDivElement, data: RandomsRow[], theme: ChartTheme) => {
    const colors = chartColors[theme];
    type RandomsChartRow = RandomsRow & { rowKey: string };

    const rows: RandomsChartRow[] = data.map((datum, index) => ({ ...datum, rowKey: `row-${index}` }));
    const labelByRowKey = new Map(rows.map((row) => [row.rowKey, row.ship_chart_name]));
    const containerWidth = containerElement.clientWidth;
    const compact = containerWidth < 580;
    const totalSvgWidth = Math.max(containerWidth || 0, 280) + RANDOMS_CHART_RIGHT_EXTENSION_PX;
    const totalSvgHeight = 420 + RANDOMS_CHART_HEIGHT_INCREASE_PX;
    const margin = compact
        ? { top: 28, right: 14, bottom: 48, left: 52 }
        : { top: 28, right: 96, bottom: 48, left: 68 + RANDOMS_CHART_SHIFT_RIGHT_PX };
    const axisFontSize = compact ? '9px' : '10px';
    const width = totalSvgWidth - margin.left - margin.right;
    const height = totalSvgHeight - margin.top - margin.bottom;

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
    svg.append('g')
        .style('color', colors.labelMid)
        .call(d3.axisLeft(y).tickSize(0).tickPadding(compact ? 4 : 6).tickFormat((value: number) => {
            const label = labelByRowKey.get(String(value)) ?? '';
            return compact ? truncateLabel(label, 8) : label;
        }))
        .selectAll('text')
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

    const detailGroup = svgRoot.append('g').attr('transform', `translate(${margin.left + width - 6}, 16)`);

    const renderDetails = (datum: RandomsRow | null) => {
        detailGroup.selectAll('*').remove();
        if (!datum) {
            return;
        }

        const detailText = detailGroup.append('text')
            .attr('x', 0)
            .attr('y', 0)
            .attr('text-anchor', 'end')
            .attr('dominant-baseline', 'hanging');

        detailText.append('tspan')
            .style('font-size', '11px')
            .style('font-weight', '700')
            .style('fill', colors.accentLink)
            .text(datum.ship_name);

        detailText.append('tspan')
            .style('font-size', '10px')
            .style('font-weight', '400')
            .style('fill', colors.separator)
            .text('  •  ');

        detailText.append('tspan')
            .style('font-size', '10px')
            .style('font-weight', '400')
            .style('fill', colors.labelMid)
            .text(`T${datum.ship_tier} ${datum.ship_type}`);

        detailText.append('tspan')
            .style('font-size', '10px')
            .style('font-weight', '400')
            .style('fill', colors.separator)
            .text('  •  ');

        detailText.append('tspan')
            .style('font-size', '10px')
            .style('font-weight', '400')
            .style('fill', colors.labelMid)
            .text(`${datum.pvp_battles.toLocaleString()} battles • ${datum.wins.toLocaleString()} wins`);

        detailText.append('tspan')
            .style('font-size', '10px')
            .style('font-weight', '400')
            .style('fill', colors.separator)
            .text('  •  ');

        detailText.append('tspan')
            .style('font-size', '10px')
            .style('font-weight', '700')
            .style('fill', colors.labelMid)
            .text(`${(datum.win_ratio * 100).toFixed(1)}% win rate`);
    };

    const nodes = svg.selectAll('.randoms-row')
        .data(rows)
        .enter()
        .append('g')
        .classed('randoms-row', true);

    nodes.append('rect')
        .attr('x', 0)
        .attr('y', (datum: RandomsChartRow) => (y(datum.rowKey) ?? 0) + backgroundBarOffset)
        .attr('width', (datum: RandomsChartRow) => x(datum.pvp_battles))
        .attr('height', backgroundBarHeight)
        .attr('rx', 3)
        .attr('fill', colors.barBg);

    nodes.append('rect')
        .attr('x', 0)
        .attr('y', (datum: RandomsChartRow) => (y(datum.rowKey) ?? 0) + foregroundBarOffset)
        .attr('width', (datum: RandomsChartRow) => x(datum.wins))
        .attr('height', foregroundBarHeight)
        .attr('rx', 3)
        .style('stroke', colors.axisLine)
        .style('stroke-width', 0.5)
        .attr('fill', (datum: RandomsChartRow) => selectRandomsColorByWr(datum.win_ratio, theme))
        .on('mouseover', function (this: SVGRectElement, _event: MouseEvent, datum: RandomsChartRow) {
            renderDetails(datum);
            d3.select(this).transition()
                .duration(70)
                .attr('opacity', 0.82);
        })
        .on('mouseout', function (this: SVGRectElement) {
            d3.select(this).transition()
                .duration(70)
                .attr('opacity', 1);
        });

    nodes.append('text')
        .attr('x', (datum: RandomsChartRow) => {
            const labelX = x(datum.pvp_battles) + 6;
            return labelX > width - 4 ? width - 4 : labelX;
        })
        .attr('y', (datum: RandomsChartRow) => (y(datum.rowKey) ?? 0) + foregroundBarOffset + (foregroundBarHeight / 2) + 3)
        .style('font-size', axisFontSize)
        .style('fill', colors.labelMuted)
        .attr('text-anchor', (datum: RandomsChartRow) => (x(datum.pvp_battles) + 6 > width - 4 ? 'end' : 'start'))
        .text((datum: RandomsChartRow) => `${(datum.win_ratio * 100).toFixed(1)}%`);

    if (data[0]) {
        renderDetails(data[0]);
    }
};

const drawBattlePlotDesign2 = (containerElement: HTMLDivElement, data: RandomsRow[], theme: ChartTheme) => {
    const colors = chartColors[theme];
    const margin = { top: 30, right: 22, bottom: 36, left: 57 };
    const width = 620 - margin.left - margin.right;
    const height = (360 + RANDOMS_CHART_HEIGHT_INCREASE_PX) - margin.top - margin.bottom;

    const svg = d3.select(containerElement)
        .append('svg')
        .attr('width', width + margin.left + margin.right)
        .attr('height', height + margin.top + margin.bottom)
        .append('g')
        .attr('transform', `translate(${margin.left}, ${margin.top})`);

    const winRates = data.map((datum) => datum.win_ratio * 100);
    const battleCounts = data.map((datum) => datum.pvp_battles);
    const wins = data.map((datum) => datum.wins);
    const xMin = Math.max(35, Math.floor((d3.min(winRates) ?? 40) - 2));
    const xMax = Math.min(80, Math.ceil((d3.max(winRates) ?? 65) + 2));
    const yMax = Math.max(d3.max(battleCounts) ?? 20, 20);

    const x = d3.scaleLinear()
        .domain([xMin, xMax])
        .range([0, width]);

    const y = d3.scaleLog()
        .domain([1, yMax * 1.15])
        .range([height, 0]);

    const radius = d3.scaleSqrt()
        .domain([0, Math.max(d3.max(wins) ?? 1, 1)])
        .range([4, 17]);

    svg.append('g')
        .attr('class', 'randoms-x-grid')
        .attr('transform', `translate(0, ${height})`)
        .call(d3.axisBottom(x).ticks(8).tickSize(-height).tickFormat(() => ''));

    svg.select('.randoms-x-grid')?.select('.domain')?.remove();
    svg.selectAll('.randoms-x-grid line')
        .style('stroke', colors.gridLine)
        .style('stroke-width', 1);

    svg.append('g')
        .attr('class', 'randoms-y-grid')
        .call(d3.axisLeft(y).tickValues([1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000].filter((value) => value <= yMax * 1.15)).tickSize(-width).tickFormat(() => ''));

    svg.select('.randoms-y-grid')?.select('.domain')?.remove();
    svg.selectAll('.randoms-y-grid line')
        .style('stroke', colors.gridLine)
        .style('stroke-width', 1);

    WR_BREAKPOINTS
        .filter((breakpoint) => breakpoint >= xMin && breakpoint <= xMax)
        .forEach((breakpoint) => {
            svg.append('line')
                .attr('x1', x(breakpoint))
                .attr('x2', x(breakpoint))
                .attr('y1', 0)
                .attr('y2', height)
                .attr('stroke', selectRandomsColorByWr(breakpoint / 100, theme))
                .attr('stroke-width', 1)
                .attr('opacity', 0.18);
        });

    svg.append('g')
        .attr('transform', `translate(0, ${height})`)
        .style('color', colors.labelMuted)
        .call(d3.axisBottom(x).ticks(8).tickFormat((value: number) => `${value}%`).tickSizeOuter(0))
        .selectAll('text')
        .style('font-size', '10px');

    svg.append('g')
        .style('color', colors.labelMuted)
        .call(d3.axisLeft(y).tickValues([1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000].filter((value) => value <= yMax * 1.15)).tickFormat((value: number) => d3.format(',')(value)).tickSizeOuter(0))
        .selectAll('text')
        .style('font-size', '10px');

    svg.append('text')
        .attr('x', width / 2)
        .attr('y', height + 32)
        .attr('text-anchor', 'middle')
        .style('fill', colors.labelMuted)
        .style('font-size', '10px')
        .text('Ship win rate');

    svg.append('text')
        .attr('transform', 'rotate(-90)')
        .attr('x', -height / 2)
        .attr('y', -38)
        .attr('text-anchor', 'middle')
        .style('fill', colors.labelMuted)
        .style('font-size', '10px')
        .text('Random battles played');

    const summaryGroup = svg.append('g').attr('transform', `translate(${Math.max(0, width - 168)}, 0)`);
    summaryGroup.append('text')
        .attr('x', 0)
        .attr('y', 0)
        .style('font-size', '11px')
        .style('font-weight', '700')
        .style('fill', colors.labelMid)
        .text('Design 2');
    summaryGroup.append('text')
        .attr('x', 0)
        .attr('y', 14)
        .style('font-size', '10px')
        .style('fill', colors.labelMuted)
        .text('x = win rate');
    summaryGroup.append('text')
        .attr('x', 0)
        .attr('y', 28)
        .style('font-size', '10px')
        .style('fill', colors.labelMuted)
        .text('y = battle volume');
    summaryGroup.append('text')
        .attr('x', 0)
        .attr('y', 42)
        .style('font-size', '10px')
        .style('fill', colors.labelMuted)
        .text('area = wins');
    summaryGroup.append('text')
        .attr('x', 0)
        .attr('y', 56)
        .style('font-size', '10px')
        .style('fill', colors.labelMuted)
        .text('fill = ship class');

    const detailGroup = svg.append('g').attr('class', 'randoms-detail').attr('transform', 'translate(0, 0)');

    const renderDetails = (datum: RandomsRow | null) => {
        detailGroup.selectAll('*').remove();
        if (!datum) {
            return;
        }

        const lines = [
            datum.ship_name,
            `T${datum.ship_tier} ${datum.ship_type}`,
            `${datum.pvp_battles.toLocaleString()} battles`,
            `${datum.wins.toLocaleString()} wins`,
            `${(datum.win_ratio * 100).toFixed(1)}% win rate`,
        ];

        lines.forEach((line, index) => {
            detailGroup.append('text')
                .attr('x', 0)
                .attr('y', index * 14)
                .style('font-size', index === 0 ? '11px' : '10px')
                .style('font-weight', index === 0 ? '700' : '400')
                .style('fill', index === 0 ? colors.labelStrong : colors.labelMid)
                .text(line);
        });

        const textNode = detailGroup.node();
        if (textNode) {
            const bbox = textNode.getBBox();
            detailGroup.insert('rect', 'text')
                .attr('x', bbox.x - 8)
                .attr('y', bbox.y - 6)
                .attr('width', bbox.width + 16)
                .attr('height', bbox.height + 12)
                .attr('rx', 6)
                .attr('fill', theme === 'dark' ? `${colors.surface}ee` : `${colors.surface}f0`)
                .attr('stroke', colors.axisLine);
        }
    };

    const points = svg.append('g')
        .selectAll('circle')
        .data(data)
        .enter()
        .append('circle')
        .attr('cx', (datum: RandomsRow) => x(datum.win_ratio * 100))
        .attr('cy', (datum: RandomsRow) => y(Math.max(1, datum.pvp_battles)))
        .attr('r', (datum: RandomsRow) => radius(datum.wins))
        .attr('fill', (datum: RandomsRow) => selectShipTypeColor(datum.ship_type, theme))
        .attr('fill-opacity', 0.82)
        .attr('stroke', (datum: RandomsRow) => selectRandomsColorByWr(datum.win_ratio, theme))
        .attr('stroke-width', 1.5)
        .style('cursor', 'default')
        .on('mouseover', function (this: SVGCircleElement, _event: MouseEvent, datum: RandomsRow) {
            d3.select(this)
                .raise()
                .transition()
                .duration(80)
                .attr('stroke-width', 2.5)
                .attr('fill-opacity', 0.96);
            renderDetails(datum);
        })
        .on('mouseout', function (this: SVGCircleElement, _event: MouseEvent, datum: RandomsRow) {
            d3.select(this)
                .transition()
                .duration(80)
                .attr('stroke-width', 1.5)
                .attr('fill-opacity', 0.82);
            renderDetails(null);
        });

    const labelledShips = [...data]
        .sort((left, right) => right.pvp_battles - left.pvp_battles)
        .slice(0, Math.min(8, data.length));

    labelledShips.forEach((datum, index) => {
        const pointX = x(datum.win_ratio * 100);
        const pointY = y(Math.max(1, datum.pvp_battles));
        const dx = pointX > width * 0.7 ? -10 : 10;
        const dy = (index % 2 === 0 ? -12 : 14) + (index % 3 === 0 ? -4 : 0);
        const anchor = dx < 0 ? 'end' : 'start';

        svg.append('line')
            .attr('x1', pointX)
            .attr('x2', pointX + dx)
            .attr('y1', pointY)
            .attr('y2', pointY + dy)
            .attr('stroke', colors.separator)
            .attr('stroke-width', 1);

        svg.append('text')
            .attr('x', pointX + dx + (dx < 0 ? -2 : 2))
            .attr('y', pointY + dy)
            .attr('text-anchor', anchor)
            .attr('dominant-baseline', 'middle')
            .style('font-size', '10px')
            .style('font-weight', '500')
            .style('fill', colors.labelMid)
            .text(datum.ship_chart_name);
    });

    const legendData = Array.from(new Set(data.map((datum) => datum.ship_type)));
    const legend = svg.append('g').attr('transform', `translate(0, ${height + 8})`);
    legendData.forEach((shipType, index) => {
        const row = legend.append('g').attr('transform', `translate(${index * 94}, 0)`);
        row.append('circle')
            .attr('cx', 0)
            .attr('cy', 0)
            .attr('r', 4)
            .attr('fill', selectShipTypeColor(shipType, theme));
        row.append('text')
            .attr('x', 8)
            .attr('y', 3)
            .style('font-size', '10px')
            .style('fill', colors.labelMuted)
            .text(shipType);
    });

    if (data.length > 0) {
        renderDetails(data[0]);
        points.filter((_datum: RandomsRow, index: number) => index === 0)
            .attr('stroke-width', 2.5)
            .attr('fill-opacity', 0.96);
    }
};

const RandomsSVG: React.FC<RandomsSVGProps> = ({
    playerId,
    isLoading = false,
    design = DEFAULT_RANDOMS_DESIGN,
    theme = 'light',
}) => {
    const [allShips, setAllShips] = useState<RandomsRow[]>([]);
    const [selectedTypes, setSelectedTypes] = useState<string[]>([]);
    const [selectedTiers, setSelectedTiers] = useState<number[]>([]);
    const [isChartLoading, setIsChartLoading] = useState(true);
    const [randomsUpdatedAt, setRandomsUpdatedAt] = useState<string | null>(null);
    const containerRef = useRef<HTMLDivElement>(null);

    // Fetch ALL ships once
    useEffect(() => {
        const fetchData = async () => {
            setIsChartLoading(true);
            try {
                const { data, headers } = await fetchSharedJson<unknown>(`/api/fetch/randoms_data/${playerId}/?all=true`, {
                    label: `Randoms data ${playerId}`,
                    responseHeaders: ['X-Randoms-Updated-At'],
                    ttlMs: PLAYER_ROUTE_PANEL_FETCH_TTL_MS,
                });
                const result = normalizeRandomsRows(data);
                setAllShips(result);
                setRandomsUpdatedAt(headers['X-Randoms-Updated-At'] ?? null);

                const types = Array.from(new Set(result.map((r) => r.ship_type)));
                const tiers = Array.from(new Set(result.map((r) => r.ship_tier)))
                    .filter((tier) => tier >= 5)
                    .sort((a, b) => b - a);
                setSelectedTypes(types);
                setSelectedTiers(tiers);
            } catch (error) {
                console.error('Error fetching data:', error);
            } finally {
                setIsChartLoading(false);
            }
        };
        fetchData();
    }, [playerId]);

    // Filter, sort, and take top N
    const chartData = useMemo(() => {
        const filtered = allShips.filter(
            (row) => selectedTypes.includes(row.ship_type) && selectedTiers.includes(row.ship_tier)
        );
        return filtered
            .sort((a, b) => b.pvp_battles - a.pvp_battles)
            .slice(0, TOP_N);
    }, [allShips, selectedTypes, selectedTiers]);

    // Draw chart when data changes
    useEffect(() => {
        if (!containerRef.current) return;
        d3.select(containerRef.current).selectAll("*").remove();
        if (chartData.length > 0) {
            if (design === 'design1') {
                drawBattlePlotDesign1(containerRef.current, chartData, theme);
            } else {
                drawBattlePlotDesign2(containerRef.current, chartData, theme);
            }
        }
    }, [chartData, design, theme]);

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
        setSelectedTypes((current) => toggleSelection(current, shipType, availableTypes));
    };

    const toggleTier = (tier: number) => {
        setSelectedTiers((current) => toggleSelection(current, tier, availableTiers));
    };

    const selectAllTypes = () => {
        setSelectedTypes([...availableTypes]);
    };

    const selectAllTiers = () => {
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

            <div className="relative">
                <div
                    className={shouldGrayOut ? 'pointer-events-none opacity-60 grayscale transition' : 'transition'}
                    aria-busy={shouldGrayOut}
                >
                    <div ref={containerRef}></div>
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
