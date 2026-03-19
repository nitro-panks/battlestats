import React, { useEffect, useRef } from 'react';
import * as d3 from 'd3';
import { PLAYER_ROUTE_FETCH_TTL_MS } from '../lib/playerRouteFetch';
import { fetchSharedJson } from '../lib/sharedJsonFetch';

interface RankedWRBattlesHeatmapSVGProps {
    playerId: number;
    isLoading?: boolean;
    svgWidth?: number;
    svgHeight?: number;
    onVisibilityChange?: (isVisible: boolean) => void;
}

interface CorrelationTile {
    x_min: number;
    x_max: number;
    y_min: number;
    y_max: number;
    count: number;
}

interface CorrelationTrendPoint {
    x: number;
    y: number;
    count: number;
}

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
    x_domain: {
        min: number;
        max: number;
        bin_width?: number | null;
    };
    y_domain: {
        min: number;
        max: number;
        bin_width?: number | null;
    };
    tiles: CorrelationTile[];
    trend: CorrelationTrendPoint[];
    player_point?: CorrelationPoint | null;
}

const selectColorByWR = (winRate: number): string => {
    if (winRate > 65) return '#810c9e';
    if (winRate >= 60) return '#D042F3';
    if (winRate >= 56) return '#3182bd';
    if (winRate >= 54) return '#74c476';
    if (winRate >= 52) return '#a1d99b';
    if (winRate >= 50) return '#fed976';
    if (winRate >= 45) return '#fd8d3c';
    return '#a50f15';
};

const drawMessage = (containerElement: HTMLDivElement, message: string, svgWidth: number, svgHeight: number) => {
    const container = d3.select(containerElement);
    container.selectAll('*').remove();

    const svg = container.append('svg')
        .attr('width', svgWidth)
        .attr('height', svgHeight);

    svg.append('text')
        .attr('x', 16)
        .attr('y', 24)
        .style('fill', '#64748b')
        .style('font-size', '12px')
        .text(message);
};

const drawChart = (containerElement: HTMLDivElement, payload: RankedWRBattlesPayload, svgWidth: number, svgHeight: number) => {
    if (!payload.tiles.length) {
        drawMessage(containerElement, 'No ranked population data available.', svgWidth, 120);
        return;
    }

    const margin = { top: 48, right: 18, bottom: 42, left: 52 };
    const width = svgWidth - margin.left - margin.right;
    const height = svgHeight - margin.top - margin.bottom;
    const container = d3.select(containerElement);
    container.selectAll('*').remove();

    const svgRoot = container.append('svg')
        .attr('width', svgWidth)
        .attr('height', svgHeight);

    const svg = svgRoot.append('g')
        .attr('transform', `translate(${margin.left}, ${margin.top})`);

    const x = (payload.x_scale === 'log'
        ? d3.scaleLog()
            .domain([Math.max(1, payload.x_domain.min), Math.max(payload.x_domain.max, payload.x_domain.min + 1)])
            .range([0, width])
        : d3.scaleLinear()
            .domain([payload.x_domain.min, payload.x_domain.max])
            .range([0, width]));

    const y = (payload.y_scale === 'log'
        ? d3.scaleLog()
            .domain([Math.max(1, payload.y_domain.min), Math.max(payload.y_domain.max, payload.y_domain.min + 1)])
            .range([height, 0])
        : d3.scaleLinear()
            .domain([payload.y_domain.min, payload.y_domain.max])
            .range([height, 0]));

    const maxTileCount = d3.max(payload.tiles, (row: CorrelationTile) => row.count) || 1;
    const tileColor = d3.scaleSequential(d3.interpolateBlues).domain([0, maxTileCount]);

    svg.selectAll('.ranked-heat-tile')
        .data(payload.tiles)
        .enter()
        .append('rect')
        .attr('class', 'ranked-heat-tile')
        .attr('x', (row: CorrelationTile) => x(Math.max(row.x_min, payload.x_domain.min)))
        .attr('y', (row: CorrelationTile) => y(row.y_max))
        .attr('width', (row: CorrelationTile) => Math.max(1, x(Math.max(row.x_max, row.x_min + 0.001)) - x(Math.max(row.x_min, payload.x_domain.min))))
        .attr('height', (row: CorrelationTile) => Math.max(1, y(row.y_min) - y(row.y_max)))
        .attr('rx', 0)
        .attr('fill', (row: CorrelationTile) => tileColor(row.count))
        .attr('stroke', 'none');

    const trendLine = d3.line()
        .x((row: unknown) => x(Math.max((row as CorrelationTrendPoint).x, payload.x_domain.min)))
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
        const clampedX = Math.min(Math.max(point.x, payload.x_domain.min), payload.x_domain.max);
        const clampedY = Math.min(Math.max(point.y, payload.y_domain.min), payload.y_domain.max);

        svg.append('circle')
            .attr('cx', x(clampedX))
            .attr('cy', y(clampedY))
            .attr('r', 8)
            .attr('fill', selectColorByWR(point.y))
            .attr('fill-opacity', 0.95)
            .attr('stroke', '#ffffff')
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
        .tickValues(payload.x_ticks && payload.x_ticks.length ? payload.x_ticks : undefined)
        .tickFormat((value: unknown) => d3.format('~s')(Number(value)).replace('G', 'B'));

    const yAxis = d3.axisLeft(y)
        .ticks(6)
        .tickFormat((value: unknown) => `${value}%`);

    svg.append('g')
        .attr('transform', `translate(0, ${height})`)
        .style('color', '#64748b')
        .call(xAxis)
        .selectAll('text')
        .style('font-size', '10px');

    svg.append('g')
        .style('color', '#64748b')
        .call(yAxis)
        .selectAll('text')
        .style('font-size', '10px');

    svg.selectAll('.domain').style('stroke', '#cbd5e1');
    svg.selectAll('.tick line').style('stroke', '#e2e8f0');

    svg.append('text')
        .attr('x', width / 2)
        .attr('y', height + 34)
        .attr('text-anchor', 'middle')
        .style('fill', '#64748b')
        .style('font-size', '10px')
        .text(payload.x_label);

    svg.append('text')
        .attr('transform', 'rotate(-90)')
        .attr('x', -height / 2)
        .attr('y', -38)
        .attr('text-anchor', 'middle')
        .style('fill', '#64748b')
        .style('font-size', '10px')
        .text(payload.y_label);

    const summary = svgRoot.append('g').attr('transform', `translate(${margin.left}, 18)`);
    summary.append('text')
        .style('font-size', '10px')
        .style('font-weight', '600')
        .style('fill', '#475569');

    if (payload.player_point) {
        summary.append('text')
            .attr('x', 210)
            .style('font-size', '10px')
            .style('font-weight', '600')
            .style('fill', selectColorByWR(payload.player_point.y))
            .text(`${Math.round(payload.player_point.x).toLocaleString()} games @ ${payload.player_point.y.toFixed(1)}%`);
    } else {
        summary.append('text')
            .attr('x', 210)
            .style('font-size', '10px')
            .style('fill', '#64748b')
            .text('No ranked history for this player');
    }
};

const RankedWRBattlesHeatmapSVG: React.FC<RankedWRBattlesHeatmapSVGProps> = ({
    playerId,
    isLoading = false,
    svgWidth = 600,
    svgHeight = 276,
    onVisibilityChange,
}) => {
    const containerRef = useRef<HTMLDivElement | null>(null);

    useEffect(() => {
        if (!containerRef.current || isLoading) {
            return;
        }

        let isMounted = true;

        const fetchAndDraw = async () => {
            try {
                const { data: payload } = await fetchSharedJson<RankedWRBattlesPayload>(`http://localhost:8888/api/fetch/player_correlation/ranked_wr_battles/${playerId}/`, {
                    label: `Ranked correlation ${playerId}`,
                    ttlMs: PLAYER_ROUTE_FETCH_TTL_MS,
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

                drawChart(containerRef.current, payload, svgWidth, svgHeight);
            } catch {
                if (!isMounted || !containerRef.current) {
                    return;
                }

                onVisibilityChange?.(true);

                drawMessage(containerRef.current, 'Unable to load ranked heatmap.', svgWidth, 120);
            }
        };

        fetchAndDraw();

        return () => {
            isMounted = false;
        };
    }, [isLoading, onVisibilityChange, playerId, svgHeight, svgWidth]);

    return <div ref={containerRef} className="w-full overflow-hidden rounded-md border border-[#dbe9f6] bg-white" />;
};

export default RankedWRBattlesHeatmapSVG;