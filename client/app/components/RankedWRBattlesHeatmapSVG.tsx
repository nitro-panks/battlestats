import React, { useEffect, useRef } from 'react';
import wrColor from '../lib/wrColor';
import * as d3 from 'd3';
import { PLAYER_ROUTE_PANEL_FETCH_TTL_MS } from '../lib/playerRouteFetch';
import { fetchSharedJson } from '../lib/sharedJsonFetch';
import { chartColors, type ChartTheme } from '../lib/chartTheme';
import { useRealm } from '../context/RealmContext';
import { withRealm } from '../lib/realmParams';
import { getRankedHeatmapTileBounds, getRankedHeatmapTrendX, getRankedHeatmapXDomain, type RankedHeatmapPayloadShape, type RankedHeatmapTile, type RankedHeatmapTrendPoint } from './rankedHeatmapPayload';

interface RankedWRBattlesHeatmapSVGProps {
    playerId: number;
    isLoading?: boolean;
    svgWidth?: number;
    svgHeight?: number;
    onVisibilityChange?: (isVisible: boolean) => void;
    theme?: ChartTheme;
}

type CorrelationTile = RankedHeatmapTile;

type CorrelationTrendPoint = RankedHeatmapTrendPoint;

interface CorrelationPoint {
    x: number;
    y: number;
    label?: string;
}

interface RankedWRBattlesPayload {
    metric: 'ranked_wr_battles';
    label: string;
    x_label: string;
    y_label: string;
    tracked_population: number;
    correlation: number | null;
    x_scale: 'linear' | 'log';
    y_scale: 'linear' | 'log';
    x_ticks?: number[];
    x_edges: number[];
    y_domain: {
        min: number;
        max: number;
        bin_width?: number | null;
    };
    tiles: CorrelationTile[];
    trend: CorrelationTrendPoint[];
    player_point?: CorrelationPoint | null;
}


type Colors = typeof chartColors['light'];

const drawMessage = (containerElement: HTMLDivElement, message: string, svgWidth: number, svgHeight: number, colors: Colors) => {
    const container = d3.select(containerElement);
    container.selectAll('*').remove();

    const svg = container.append('svg')
        .attr('width', svgWidth)
        .attr('height', svgHeight);

    svg.append('text')
        .attr('x', 16)
        .attr('y', 24)
        .style('fill', colors.labelMuted)
        .style('font-size', '12px')
        .text(message);
};

const drawChart = (containerElement: HTMLDivElement, payload: RankedWRBattlesPayload, svgWidth: number, svgHeight: number, colors: Colors, theme: ChartTheme) => {
    if (!payload.tiles.length) {
        drawMessage(containerElement, 'No ranked population data available.', svgWidth, 120, colors);
        return;
    }

    const compact = svgWidth < 480;
    const margin = compact
        ? { top: 38, right: 8, bottom: 36, left: 38 }
        : { top: 48, right: 18, bottom: 42, left: 52 };
    const axisFontSize = compact ? '9px' : '10px';
    const width = svgWidth - margin.left - margin.right;
    const height = svgHeight - margin.top - margin.bottom;
    const container = d3.select(containerElement);
    container.selectAll('*').remove();

    const svgRoot = container.append('svg')
        .attr('width', svgWidth)
        .attr('height', svgHeight);

    const svg = svgRoot.append('g')
        .attr('transform', `translate(${margin.left}, ${margin.top})`);

    const [xMin, xMax] = getRankedHeatmapXDomain(payload as RankedHeatmapPayloadShape);

    const x = (payload.x_scale === 'log'
        ? d3.scaleLog()
            .domain([xMin, xMax])
            .range([0, width])
        : d3.scaleLinear()
            .domain([xMin, xMax])
            .range([0, width]));

    const y = (payload.y_scale === 'log'
        ? d3.scaleLog()
            .domain([Math.max(1, payload.y_domain.min), Math.max(payload.y_domain.max, payload.y_domain.min + 1)])
            .range([height, 0])
        : d3.scaleLinear()
            .domain([payload.y_domain.min, payload.y_domain.max])
            .range([height, 0]));

    const maxTileCount = d3.max(payload.tiles, (row: CorrelationTile) => row.count) || 1;
    const tileColor = theme === 'dark'
        ? d3.scaleSequential(d3.interpolateRgb('#1c2d3f', '#79c0ff')).domain([0, maxTileCount])
        : d3.scaleSequential(d3.interpolateBlues).domain([0, maxTileCount]);

    svg.selectAll('.ranked-heat-tile')
        .data(payload.tiles)
        .enter()
        .append('rect')
        .attr('class', 'ranked-heat-tile')
        .attr('x', (row: CorrelationTile) => {
            const bounds = getRankedHeatmapTileBounds(payload as RankedHeatmapPayloadShape, row);
            return x(Math.max(bounds.xMin, xMin));
        })
        .attr('y', (row: CorrelationTile) => {
            const bounds = getRankedHeatmapTileBounds(payload as RankedHeatmapPayloadShape, row);
            return y(bounds.yMax);
        })
        .attr('width', (row: CorrelationTile) => {
            const bounds = getRankedHeatmapTileBounds(payload as RankedHeatmapPayloadShape, row);
            return Math.max(1, x(Math.max(bounds.xMax, bounds.xMin + 0.001)) - x(Math.max(bounds.xMin, xMin)));
        })
        .attr('height', (row: CorrelationTile) => {
            const bounds = getRankedHeatmapTileBounds(payload as RankedHeatmapPayloadShape, row);
            return Math.max(1, y(bounds.yMin) - y(bounds.yMax));
        })
        .attr('rx', 0)
        .attr('fill', (row: CorrelationTile) => tileColor(row.count))
        .attr('stroke', 'none');

    const trendLine = d3.line()
        .x((row: unknown) => x(Math.max(getRankedHeatmapTrendX(payload as RankedHeatmapPayloadShape, row as CorrelationTrendPoint), xMin)))
        .y((row: unknown) => y((row as CorrelationTrendPoint).y))
        .curve(d3.curveMonotoneX);

    svg.append('path')
        .datum(payload.trend)
        .attr('fill', 'none')
        .attr('stroke', '#da7658')
        .attr('stroke-width', 1.6)
        .attr('stroke-dasharray', '5,4')
        .attr('d', trendLine);

    if (payload.player_point) {
        const point = payload.player_point;
        const clampedX = Math.min(Math.max(point.x, xMin), xMax);
        const clampedY = Math.min(Math.max(point.y, payload.y_domain.min), payload.y_domain.max);

        svg.append('circle')
            .attr('cx', x(clampedX))
            .attr('cy', y(clampedY))
            .attr('r', 8)
            .attr('fill', wrColor(point.y))
            .attr('fill-opacity', 0.95)
            .attr('stroke', colors.barStroke)
            .attr('stroke-width', 2);

        svg.append('circle')
            .attr('cx', x(clampedX))
            .attr('cy', y(clampedY))
            .attr('r', 12)
            .attr('fill', 'none')
            .attr('stroke', '#da7658')
            .attr('stroke-width', 1.6)
            .attr('stroke-dasharray', '4,3');
    }

    const xAxis = d3.axisBottom(x)
        .tickValues(payload.x_ticks && payload.x_ticks.length
            ? (compact ? payload.x_ticks.filter((_: number, i: number) => i % 2 === 0) : payload.x_ticks)
            : undefined)
        .tickFormat((value: unknown) => d3.format('~s')(Number(value)).replace('G', 'B'));

    const yAxis = d3.axisLeft(y)
        .ticks(compact ? 4 : 6)
        .tickFormat((value: unknown) => `${value}%`);

    svg.append('g')
        .attr('transform', `translate(0, ${height})`)
        .style('color', colors.labelMuted)
        .call(xAxis)
        .selectAll('text')
        .style('font-size', axisFontSize);

    svg.append('g')
        .style('color', colors.labelMuted)
        .call(yAxis)
        .selectAll('text')
        .style('font-size', axisFontSize);

    svg.selectAll('.domain').style('stroke', colors.axisLine);
    svg.selectAll('.tick line').style('stroke', colors.gridLine);

    svg.append('text')
        .attr('x', width / 2)
        .attr('y', height + (compact ? 28 : 34))
        .attr('text-anchor', 'middle')
        .style('fill', colors.labelMuted)
        .style('font-size', axisFontSize)
        .text(payload.x_label);

    svg.append('text')
        .attr('transform', 'rotate(-90)')
        .attr('x', -height / 2)
        .attr('y', compact ? -26 : -38)
        .attr('text-anchor', 'middle')
        .style('fill', colors.labelMuted)
        .style('font-size', axisFontSize)
        .text(payload.y_label);

    const summaryX = Math.round(width * 0.4);
    const summary = svgRoot.append('g').attr('transform', `translate(${margin.left}, ${compact ? 14 : 18})`);
    summary.append('text')
        .style('font-size', axisFontSize)
        .style('font-weight', '600')
        .style('fill', colors.axisText);

    if (payload.player_point) {
        summary.append('text')
            .attr('x', summaryX)
            .style('font-size', axisFontSize)
            .style('font-weight', '600')
            .style('fill', wrColor(payload.player_point.y))
            .text(`${Math.round(payload.player_point.x).toLocaleString()} games @ ${payload.player_point.y.toFixed(1)}%`);
    } else {
        summary.append('text')
            .attr('x', summaryX)
            .style('font-size', axisFontSize)
            .style('fill', colors.labelMuted)
            .text('No ranked history for this player');
    }
};

const RankedWRBattlesHeatmapSVG: React.FC<RankedWRBattlesHeatmapSVGProps> = ({
    playerId,
    isLoading = false,
    svgWidth = 600,
    svgHeight = 276,
    onVisibilityChange,
    theme = 'light',
}) => {
    const containerRef = useRef<HTMLDivElement | null>(null);
    const { realm } = useRealm();

    useEffect(() => {
        if (!containerRef.current || isLoading) {
            return;
        }

        const colors = chartColors[theme];
        let isMounted = true;
        let cachedPayload: RankedWRBattlesPayload | null = null;
        let resizeFrame: number | null = null;

        const resolveWidth = () => Math.min(svgWidth, Math.max(containerRef.current?.clientWidth || svgWidth, 280));

        const redraw = () => {
            if (cachedPayload && isMounted && containerRef.current) {
                drawChart(containerRef.current, cachedPayload, resolveWidth(), svgHeight, colors, theme);
            }
        };

        const onResize = () => {
            if (resizeFrame != null) cancelAnimationFrame(resizeFrame);
            resizeFrame = requestAnimationFrame(redraw);
        };

        const fetchAndDraw = async () => {
            try {
                const { data: payload } = await fetchSharedJson<RankedWRBattlesPayload>(withRealm(`/api/fetch/player_correlation/ranked_wr_battles/${playerId}/`, realm), {
                    label: `Ranked correlation ${playerId}`,
                    ttlMs: PLAYER_ROUTE_PANEL_FETCH_TTL_MS,
                });
                if (!isMounted || !containerRef.current) {
                    return;
                }

                const hasRankedHistory = Boolean(payload.player_point);
                onVisibilityChange?.(hasRankedHistory);
                if (!hasRankedHistory) {
                    containerRef.current.innerHTML = '';
                    return;
                }

                cachedPayload = payload;
                drawChart(containerRef.current, payload, resolveWidth(), svgHeight, colors, theme);
            } catch {
                if (!isMounted || !containerRef.current) {
                    return;
                }

                onVisibilityChange?.(true);

                drawMessage(containerRef.current, 'Unable to load ranked heatmap.', resolveWidth(), 120, colors);
            }
        };

        fetchAndDraw();
        window.addEventListener('resize', onResize);

        return () => {
            isMounted = false;
            window.removeEventListener('resize', onResize);
            if (resizeFrame != null) cancelAnimationFrame(resizeFrame);
        };
    }, [isLoading, onVisibilityChange, playerId, realm, svgHeight, svgWidth, theme]);

    return <div ref={containerRef} className="w-full overflow-hidden rounded-md border border-[var(--border)] bg-[var(--bg-surface)]" />;
};

export default RankedWRBattlesHeatmapSVG;
