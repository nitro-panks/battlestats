import React, { useEffect, useRef } from 'react';
import wrColor from '../lib/wrColor';
import * as d3 from 'd3';
import { PLAYER_ROUTE_PANEL_FETCH_TTL_MS } from '../lib/playerRouteFetch';
import { fetchSharedJson } from '../lib/sharedJsonFetch';
import { getCorrelationTileBounds, getCorrelationTrendX } from './wrDistributionPayload';
import { chartColors, drawSvgMessage, resolveContainerChartWidth, type ChartColors as Colors, type ChartTheme } from '../lib/chartTheme';
import LoadingPanel from './LoadingPanel';
import { useRealm } from '../context/RealmContext';
import { withRealm } from '../lib/realmParams';

interface WRDistributionProps {
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

const drawChart = (
    containerElement: HTMLDivElement,
    payload: CorrelationPayload,
    playerWR: number,
    playerSurvivalRate: number,
    svgWidth: number,
    svgHeight: number,
    colors: Colors,
) => {
    const compact = svgWidth < 480;
    // Non-compact left pins to 30 — the compact left margin of the half-width
    // distribution histograms beside it in the Profile tab's population row — so
    // the two y axes share one vertical line; right trimmed to 28 (from the old
    // symmetric 44/44) to widen the plot by 30px total. Top is a slim 16 now that
    // the r/Expected-WR/trend summary header (which needed ~38) was removed.
    const margin = compact
        ? { top: 16, right: 32, bottom: 28, left: 32 }
        : { top: 16, right: 28, bottom: 34, left: 30 };
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

    // Axes flipped: x = survival rate, y = win rate
    const expectedWR = interpolateTrendValue(payload.trend, payload.x_domain, playerSurvivalRate);
    const wrDelta = expectedWR == null ? null : playerWR - expectedWR;
    const playerColor = wrColor(playerWR);
    const plottedPlayerSurvivalRate = clampToDomain(playerSurvivalRate, payload.x_domain);
    const plottedPlayerWR = clampToDomain(playerWR, payload.y_domain);
    const playerX = x(plottedPlayerSurvivalRate);
    const playerY = y(plottedPlayerWR);
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

    // The WR/survival + trend readout starts hidden and only appears while the
    // player dot is hovered (toggled by the transparent hit target appended after
    // the label below). pointer-events:none so the label itself never eats the
    // hover; opacity (not display) so getBBox below can still measure it.
    const label = svg.append('g')
        .attr('transform', `translate(${labelX}, ${labelY})`)
        .style('opacity', 0)
        .style('pointer-events', 'none')
        .style('transition', 'opacity 120ms ease-out');
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
        .style('fill', wrDelta == null ? colors.labelMuted : (wrDelta >= 0 ? colors.heatmapAboveTrend : colors.heatmapBelowTrend))
        .text(formatDelta(wrDelta));

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

    // Transparent, larger-than-the-dot hit target (appended last so it sits on
    // top of the guide lines) that reveals the readout on hover and hides it on
    // leave. Keyboard/touch users get it on focus/tap too.
    svg.append('circle')
        .attr('cx', playerX)
        .attr('cy', playerY)
        .attr('r', 12)
        .attr('fill', 'transparent')
        .attr('tabindex', 0)
        .style('cursor', 'pointer')
        .style('outline', 'none')
        .on('mouseenter focus', () => label.style('opacity', 1))
        .on('mouseleave blur', () => label.style('opacity', 0))
        .on('click', () => {
            const shown = label.style('opacity') === '1';
            label.style('opacity', shown ? 0 : 1);
        });

    svg.append('text')
        .attr('x', width)
        .attr('y', height + 32)
        .attr('text-anchor', 'end')
        .style('fill', colors.separator)
        .style('font-size', '10px')
        .text(`tiles: ${payload.x_domain.bin_width.toFixed(1)} x ${payload.y_domain.bin_width.toFixed(1)} pts`);
};

const WRDistributionSVG: React.FC<WRDistributionProps> = ({
    playerWR,
    playerSurvivalRate = null,
    svgWidth = 600,
    svgHeight = 248,
    theme = 'light',
}) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const { realm } = useRealm();

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
            const resolvedWidth = resolveContainerChartWidth(containerElement.clientWidth, svgWidth);
            drawChart(containerElement, cachedPayload, playerWR, playerSurvivalRate, resolvedWidth, svgHeight, colors);
        };

        const load = async () => {
            try {
                const { data: payload } = await fetchSharedJson<CorrelationPayload>(withRealm('/api/fetch/player_correlation/win_rate_survival/', realm), {
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
                    drawSvgMessage(containerElement, 'Unable to load win rate and survival chart.', { color: colors.labelText });
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
    }, [playerSurvivalRate, playerWR, realm, svgHeight, svgWidth, theme]);

    return (
        <div ref={containerRef}>
            <LoadingPanel label="Loading win rate distribution…" minHeight={svgHeight} />
        </div>
    );
};

export default WRDistributionSVG;
