import React, { useEffect, useRef } from 'react';
import * as d3 from 'd3';
import { PLAYER_ROUTE_PANEL_FETCH_TTL_MS } from '../lib/playerRouteFetch';
import { fetchSharedJson } from '../lib/sharedJsonFetch';
import { getCorrelationTileBounds, getCorrelationTrendX } from './wrDistributionPayload';
import { chartColors, type ChartTheme } from '../lib/chartTheme';

type D3Selection = ReturnType<typeof d3.select>;

interface WRDistributionDesign2Props {
    playerWR: number;
    playerSurvivalRate?: number | null;
    svgWidth?: number;
    svgHeight?: number;
    theme?: ChartTheme;
}

interface CorrelationDomain {
    min: number;
    max: number;
    bin_width: number;
}

interface CorrelationTile {
    x_index: number;
    y_index: number;
    count: number;
}

interface CorrelationTrendPoint {
    x_index: number;
    y: number;
    count: number;
}

interface CorrelationPayload {
    metric: 'win_rate_survival';
    label: string;
    x_label: string;
    y_label: string;
    tracked_population: number;
    correlation: number | null;
    x_domain: CorrelationDomain;
    y_domain: CorrelationDomain;
    tiles: CorrelationTile[];
    trend: CorrelationTrendPoint[];
}

const selectColorByWR = (winRatio: number): string => {
    if (winRatio > 65) return '#810c9e';
    if (winRatio >= 60) return '#D042F3';
    if (winRatio >= 56) return '#3182bd';
    if (winRatio >= 54) return '#74c476';
    if (winRatio >= 52) return '#a1d99b';
    if (winRatio >= 50) return '#fed976';
    if (winRatio >= 45) return '#fd8d3c';
    return '#a50f15';
};

const formatPercent = (value: number): string => `${value.toFixed(1)}%`;

const clampToDomain = (value: number, domain: CorrelationDomain): number => {
    return Math.min(Math.max(value, domain.min), domain.max);
};

const interpolateTrendValue = (trend: CorrelationTrendPoint[], xDomain: CorrelationDomain, targetX: number): number | null => {
    if (!trend.length) {
        return null;
    }

    const firstX = getCorrelationTrendX(trend[0], xDomain);
    if (targetX <= firstX) {
        return trend[0].y;
    }

    const lastX = getCorrelationTrendX(trend[trend.length - 1], xDomain);
    if (targetX >= lastX) {
        return trend[trend.length - 1].y;
    }

    for (let index = 1; index < trend.length; index += 1) {
        const left = trend[index - 1];
        const right = trend[index];
        const leftX = getCorrelationTrendX(left, xDomain);
        const rightX = getCorrelationTrendX(right, xDomain);
        if (targetX > rightX) {
            continue;
        }

        const span = rightX - leftX;
        if (span === 0) {
            return right.y;
        }

        const t = (targetX - leftX) / span;
        return left.y + ((right.y - left.y) * t);
    }

    return null;
};

const formatDelta = (value: number | null): string => {
    if (value == null) {
        return 'Trend unavailable';
    }

    const sign = value >= 0 ? '+' : '';
    return `${sign}${value.toFixed(1)} pts vs trend`;
};

const drawErrorState = (containerElement: HTMLDivElement, message: string, colors: typeof chartColors['light']) => {
    const container = d3.select(containerElement);
    container.selectAll('*').remove();

    const svg = container
        .append('svg')
        .attr('width', 600)
        .attr('height', 120)
        .append('g')
        .attr('transform', 'translate(16, 24)');

    svg.append('text')
        .attr('x', 0)
        .attr('y', 16)
        .style('fill', colors.labelText)
        .style('font-size', '12px')
        .text(message);
};

const appendSummaryBlock = (
    svgRoot: D3Selection,
    marginLeft: number,
    width: number,
    payload: CorrelationPayload,
    expectedSurvival: number | null,
    survivalDelta: number | null,
    colors: typeof chartColors['light'],
) => {
    const header = svgRoot.append('g').attr('transform', `translate(${marginLeft + width - 6}, 10)`);
    const headerText = header.append('text')
        .attr('x', 0)
        .attr('y', 0)
        .attr('text-anchor', 'end')
        .attr('dominant-baseline', 'hanging');

    headerText.append('tspan')
        .style('font-size', '11px')
        .style('font-weight', '700')
        .style('fill', colors.axisText)
        .text(payload.correlation == null ? 'r unavailable' : `r = ${payload.correlation.toFixed(2)}`);

    headerText.append('tspan')
        .style('font-size', '10px')
        .style('font-weight', '400')
        .style('fill', colors.separator)
        .text('  •  ');

    headerText.append('tspan')
        .style('font-size', '10px')
        .style('font-weight', '400')
        .style('fill', colors.axisText)
        .text(expectedSurvival == null ? 'Expected survival unavailable' : `Expected survival ${formatPercent(expectedSurvival)}`);

    headerText.append('tspan')
        .style('font-size', '10px')
        .style('font-weight', '400')
        .style('fill', colors.separator)
        .text('  •  ');

    headerText.append('tspan')
        .style('font-size', '10px')
        .style('font-weight', '700')
        .style('fill', survivalDelta != null ? (survivalDelta >= 0 ? colors.heatmapAboveTrend : colors.heatmapBelowTrend) : colors.labelMuted)
        .text(formatDelta(survivalDelta));
};

const drawChart = (
    containerElement: HTMLDivElement,
    payload: CorrelationPayload,
    playerWR: number,
    playerSurvivalRate: number,
    svgWidth: number,
    svgHeight: number,
    colors: typeof chartColors['light'],
) => {
    const compact = svgWidth < 480;
    const margin = compact
        ? { top: 38, right: 8, bottom: 28, left: 32 }
        : { top: 38, right: 18, bottom: 34, left: 44 };
    const axisFontSize = compact ? '9px' : '10px';
    const width = svgWidth - margin.left - margin.right;
    const height = svgHeight - margin.top - margin.bottom;

    const container = d3.select(containerElement);
    container.selectAll('*').remove();

    const svgRoot = container
        .append('svg')
        .attr('width', svgWidth)
        .attr('height', svgHeight);

    const svg = svgRoot
        .append('g')
        .attr('transform', `translate(${margin.left}, ${margin.top})`);

    if (!payload.tiles.length) {
        svg.append('text')
            .attr('x', 0)
            .attr('y', 16)
            .style('fill', colors.labelText)
            .style('font-size', '12px')
            .text('No correlation data available.');
        return;
    }

    const x = d3.scaleLinear()
        .domain([payload.x_domain.min, payload.x_domain.max])
        .range([0, width]);

    const y = d3.scaleLinear()
        .domain([payload.y_domain.min, payload.y_domain.max])
        .range([height, 0]);

    const maxTileCount = d3.max(payload.tiles, (tile: CorrelationTile) => tile.count) || 1;
    const tileOpacity = d3.scaleSqrt()
        .domain([0, maxTileCount])
        .range([0.08, 0.9]);

    svg.append('g')
        .attr('transform', `translate(0, ${height})`)
        .style('color', colors.labelMuted)
        .call(d3.axisBottom(x).ticks(compact ? 5 : 8).tickFormat((value: number) => `${value}%`).tickSizeOuter(0))
        .selectAll('text')
        .style('font-size', axisFontSize);

    svg.append('g')
        .style('color', colors.labelMuted)
        .call(d3.axisLeft(y).ticks(compact ? 5 : 7).tickFormat((value: number) => `${value}%`).tickSizeOuter(0))
        .selectAll('text')
        .style('font-size', axisFontSize);

    svg.append('g')
        .attr('class', 'grid-lines')
        .call(d3.axisLeft(y).ticks(compact ? 5 : 7).tickSize(-width).tickFormat(() => ''))
        .selectAll('line')
        .style('stroke', colors.gridLine)
        .style('stroke-width', 1);
    svg.select('.grid-lines')?.select('.domain')?.remove();

    svg.append('text')
        .attr('x', width / 2)
        .attr('y', height + (compact ? 24 : 32))
        .attr('text-anchor', 'middle')
        .style('fill', colors.labelMuted)
        .style('font-size', axisFontSize)
        .text(payload.x_label);

    svg.append('text')
        .attr('transform', 'rotate(-90)')
        .attr('x', -height / 2)
        .attr('y', compact ? -24 : -34)
        .attr('text-anchor', 'middle')
        .style('fill', colors.labelMuted)
        .style('font-size', axisFontSize)
        .text(payload.y_label);

    svg.append('g')
        .selectAll('rect')
        .data(payload.tiles)
        .enter()
        .append('rect')
        .attr('x', (tile: CorrelationTile) => x(getCorrelationTileBounds(payload, tile).xMin))
        .attr('y', (tile: CorrelationTile) => y(getCorrelationTileBounds(payload, tile).yMax))
        .attr('width', (tile: CorrelationTile) => {
            const bounds = getCorrelationTileBounds(payload, tile);
            return Math.max(1, x(bounds.xMax) - x(bounds.xMin));
        })
        .attr('height', (tile: CorrelationTile) => {
            const bounds = getCorrelationTileBounds(payload, tile);
            return Math.max(1, y(bounds.yMin) - y(bounds.yMax));
        })
        .attr('fill', colors.accentMid)
        .attr('opacity', (tile: CorrelationTile) => tileOpacity(tile.count));

    const trendLine = d3.line()
        .x((point: CorrelationTrendPoint) => x(getCorrelationTrendX(point, payload.x_domain)))
        .y((point: CorrelationTrendPoint) => y(point.y))
        .curve(d3.curveMonotoneX);

    svg.append('path')
        .datum(payload.trend)
        .attr('fill', 'none')
        .attr('stroke', colors.axisText)
        .attr('stroke-width', 1.75)
        .attr('d', trendLine);

    const expectedSurvival = interpolateTrendValue(payload.trend, payload.x_domain, playerWR);
    const survivalDelta = expectedSurvival == null ? null : playerSurvivalRate - expectedSurvival;
    const playerColor = selectColorByWR(playerWR);
    const plottedPlayerWR = clampToDomain(playerWR, payload.x_domain);
    const plottedPlayerSurvivalRate = clampToDomain(playerSurvivalRate, payload.y_domain);
    const playerX = x(plottedPlayerWR);
    const playerY = y(plottedPlayerSurvivalRate);
    const labelX = playerX > width * 0.7 ? playerX - 8 : playerX + 8;
    const labelAnchor = playerX > width * 0.7 ? 'end' : 'start';
    const labelY = playerY < height * 0.35 ? playerY + 28 : playerY - 18;

    svg.append('line')
        .attr('x1', playerX)
        .attr('x2', playerX)
        .attr('y1', height)
        .attr('y2', playerY)
        .attr('stroke', colors.separator)
        .attr('stroke-width', 1)
        .attr('stroke-dasharray', '3,3');

    svg.append('line')
        .attr('x1', 0)
        .attr('x2', playerX)
        .attr('y1', playerY)
        .attr('y2', playerY)
        .attr('stroke', colors.separator)
        .attr('stroke-width', 1)
        .attr('stroke-dasharray', '3,3');

    svg.append('circle')
        .attr('cx', playerX)
        .attr('cy', playerY)
        .attr('r', 5.5)
        .attr('fill', playerColor)
        .attr('stroke', colors.barStroke)
        .attr('stroke-width', 1.75);

    const label = svg.append('g').attr('transform', `translate(${labelX}, ${labelY})`);
    const labelText = label.append('text')
        .attr('text-anchor', labelAnchor)
        .attr('dominant-baseline', 'middle');

    labelText.append('tspan')
        .style('font-size', '11px')
        .style('font-weight', '700')
        .style('fill', playerColor)
        .text(`${formatPercent(playerWR)} / ${formatPercent(playerSurvivalRate)}`);

    labelText.append('tspan')
        .attr('x', 0)
        .attr('dy', 14)
        .style('font-size', '10px')
        .style('font-weight', '400')
        .style('fill', survivalDelta == null ? colors.labelMuted : (survivalDelta >= 0 ? colors.heatmapAboveTrend : colors.heatmapBelowTrend))
        .text(formatDelta(survivalDelta));

    const labelNode = labelText.node();
    if (labelNode) {
        const bbox = labelNode.getBBox();
        label.insert('rect', 'text')
            .attr('x', bbox.x - 6)
            .attr('y', bbox.y - 3)
            .attr('width', bbox.width + 12)
            .attr('height', bbox.height + 6)
            .attr('rx', 4)
            .attr('fill', colors.surface)
            .attr('fill-opacity', 0.96)
            .attr('stroke', colors.axisLine);
    }

    appendSummaryBlock(svgRoot, margin.left, width, payload, expectedSurvival, survivalDelta, colors);

    svg.append('text')
        .attr('x', width)
        .attr('y', height + 32)
        .attr('text-anchor', 'end')
        .style('fill', colors.separator)
        .style('font-size', '10px')
        .text(`tiles: ${payload.x_domain.bin_width.toFixed(1)} x ${payload.y_domain.bin_width.toFixed(1)} pts`);
};

const WRDistributionDesign2SVG: React.FC<WRDistributionDesign2Props> = ({
    playerWR,
    playerSurvivalRate = null,
    svgWidth = 600,
    svgHeight = 248,
    theme = 'light',
}) => {
    const containerRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        const containerElement = containerRef.current;
        if (!containerElement || playerSurvivalRate == null) {
            return;
        }

        const colors = chartColors[theme];
        const abortController = new AbortController();
        let cachedPayload: CorrelationPayload | null = null;

        const draw = () => {
            if (!cachedPayload) return;
            const resolvedWidth = Math.min(svgWidth, Math.max(containerElement.clientWidth || svgWidth, 280));
            drawChart(containerElement, cachedPayload, playerWR, playerSurvivalRate, resolvedWidth, svgHeight, colors);
        };

        const load = async () => {
            try {
                const { data: payload } = await fetchSharedJson<CorrelationPayload>('/api/fetch/player_correlation/win_rate_survival/', {
                    label: 'Win rate survival correlation',
                    ttlMs: PLAYER_ROUTE_PANEL_FETCH_TTL_MS,
                });
                if (abortController.signal.aborted) {
                    return;
                }

                cachedPayload = payload;
                draw();
            } catch {
                if (!abortController.signal.aborted) {
                    drawErrorState(containerElement, 'Unable to load win rate and survival chart.', colors);
                }
            }
        };

        const onResize = () => draw();
        window.addEventListener('resize', onResize);

        load();
        return () => {
            abortController.abort();
            window.removeEventListener('resize', onResize);
        };
    }, [playerSurvivalRate, playerWR, svgHeight, svgWidth, theme]);

    return <div ref={containerRef}></div>;
};

export default WRDistributionDesign2SVG;
