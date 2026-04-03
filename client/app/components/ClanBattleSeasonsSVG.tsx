import React, { useEffect, useRef } from 'react';
import * as d3 from 'd3';
import { chartColors, type ChartTheme } from '../lib/chartTheme';

export interface ClanBattleSeasonPoint {
    season_id: number;
    season_name: string;
    season_label: string;
    participants: number;
    roster_win_rate: number;
}

interface ClanBattleSeasonsSVGProps {
    seasons: ClanBattleSeasonPoint[];
    memberCount: number;
    svgHeight?: number;
    theme?: ChartTheme;
}

type Colors = typeof chartColors['light'];

interface SeasonRow {
    index: number;
    wr: number;
    activity: number;
    season: ClanBattleSeasonPoint;
}

const drawChart = (
    container: HTMLDivElement,
    seasons: ClanBattleSeasonPoint[],
    memberCount: number,
    svgHeight: number,
    colors: Colors,
) => {
    const containerWidth = Math.max(container.clientWidth || 0, 280);
    const compact = containerWidth < 480;

    d3.select(container).selectAll('*').remove();

    const sorted = [...seasons].sort((a, b) => a.season_id - b.season_id);
    if (sorted.length === 0) return;

    const totalSvgWidth = containerWidth;
    const totalSvgHeight = compact ? Math.min(svgHeight, 240) : svgHeight;
    const margin = compact
        ? { top: 16, right: 14, bottom: 46, left: 42 }
        : { top: 20, right: 20, bottom: 52, left: 52 };

    const width = totalSvgWidth - margin.left - margin.right;
    const height = totalSvgHeight - margin.top - margin.bottom;
    const axisFontSize = compact ? '9px' : '10px';
    const legendFontSize = compact ? '9px' : '11px';

    const svgRoot = d3.select(container)
        .append('svg')
        .attr('width', totalSvgWidth)
        .attr('height', totalSvgHeight)
        .attr('viewBox', `0 0 ${totalSvgWidth} ${totalSvgHeight}`)
        .style('display', 'block')
        .style('max-width', '100%');

    const svg = svgRoot
        .append('g')
        .attr('transform', `translate(${margin.left}, ${margin.top})`);

    // --- Build per-season rows ---
    const rows: SeasonRow[] = sorted.map((d, i) => ({
        index: i,
        wr: d.roster_win_rate,
        activity: memberCount > 0 ? (d.participants / memberCount) * 100 : 0,
        season: d,
    }));

    // --- Scales ---
    const xScale = d3.scaleLinear()
        .domain([0, sorted.length - 1])
        .range([0, width]);

    // Stack the two series so they flow together as a streamgraph.
    // d3.stack + stackOffsetWiggle centers the stream around the midline.
    const keys = ['wr', 'activity'];
    const stackData = rows.map(r => ({ index: r.index, wr: r.wr, activity: r.activity }));
    const stack = d3.stack()
        .keys(keys)
        .offset(d3.stackOffsetWiggle)
        .order(d3.stackOrderNone);
    const series = stack(stackData as Iterable<{ [key: string]: number }>);

    // Y domain from stacked extents
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const yMin = d3.min(series, (s: any) => d3.min(s, (d: any) => d[0] as number)) || 0;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const yMax = d3.max(series, (s: any) => d3.max(s, (d: any) => d[1] as number)) || 100;
    const yScale = d3.scaleLinear()
        .domain([yMin, yMax])
        .range([height, 0]);

    // --- Area generator ---
    const area = d3.area()
        .x((_d: [number, number], i: number) => xScale(i))
        .y0((d: [number, number]) => yScale(d[0]))
        .y1((d: [number, number]) => yScale(d[1]))
        .curve(d3.curveBasis);

    const wrColor = colors.metricWR;
    const activityColor = colors.activityActive;
    const seriesColors = [wrColor, activityColor];

    // --- Draw streams ---
    svg.selectAll('.stream')
        .data(series)
        .enter()
        .append('path')
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        .attr('d', (d: any) => area(d as [number, number][]))
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        .attr('fill', (_d: any, i: number) => seriesColors[i])
        .attr('fill-opacity', 0.55)
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        .attr('stroke', (_d: any, i: number) => seriesColors[i])
        .attr('stroke-width', 1.5)
        .attr('stroke-opacity', 0.8);

    // --- X axis ---
    const xAxisG = svg.append('g')
        .attr('transform', `translate(0, ${height})`);

    const maxTicks = 20;
    const tickStep = sorted.length > maxTicks ? Math.ceil(sorted.length / maxTicks) : 1;

    sorted.forEach((d, i) => {
        if (i % tickStep !== 0 && i !== sorted.length - 1) return;
        const tx = xScale(i);
        const rotate = compact && sorted.length > 12;
        xAxisG.append('text')
            .attr('x', tx)
            .attr('y', rotate ? 12 : 14)
            .attr('text-anchor', rotate ? 'end' : 'middle')
            .attr('transform', rotate ? `rotate(-45, ${tx}, 12)` : '')
            .style('font-size', axisFontSize)
            .style('fill', colors.axisText)
            .text(d.season_label);
    });

    // --- Tooltip overlay ---
    const tooltip = d3.select(container)
        .append('div')
        .style('position', 'absolute')
        .style('pointer-events', 'none')
        .style('background', colors.surface)
        .style('border', `1px solid ${colors.axisLine}`)
        .style('border-radius', '4px')
        .style('padding', '6px 10px')
        .style('font-size', '11px')
        .style('color', colors.labelStrong)
        .style('line-height', '1.4')
        .style('white-space', 'nowrap')
        .style('opacity', 0)
        .style('z-index', '10');

    const hitWidth = sorted.length > 1
        ? Math.abs(xScale(1) - xScale(0))
        : width;

    svg.selectAll('.hit-area')
        .data(rows)
        .enter()
        .append('rect')
        .attr('x', (d: SeasonRow) => xScale(d.index) - hitWidth / 2)
        .attr('y', 0)
        .attr('width', hitWidth)
        .attr('height', height)
        .attr('fill', 'transparent')
        .style('cursor', 'crosshair')
        .on('mousemove', function (_event: MouseEvent, d: SeasonRow) {
            tooltip
                .html(
                    `<strong>${d.season.season_name}</strong><br/>` +
                    `<span style="color:${wrColor}">WR: ${d.wr.toFixed(1)}%</span><br/>` +
                    `<span style="color:${activityColor}">Activity: ${d.activity.toFixed(0)}%</span>`
                )
                .style('opacity', 1);

            const tooltipNode = tooltip.node() as HTMLDivElement;
            const tooltipWidth = tooltipNode.offsetWidth;
            const px = xScale(d.index) + margin.left;
            const left = px + tooltipWidth + 12 > containerWidth
                ? px - tooltipWidth - 8
                : px + 12;
            tooltip
                .style('left', `${left}px`)
                .style('top', `${margin.top + 8}px`);
        })
        .on('mouseleave', () => {
            tooltip.style('opacity', 0);
        });

    // --- Legend ---
    const legendY = totalSvgHeight - (compact ? 10 : 14);
    const legendG = svgRoot.append('g')
        .attr('transform', `translate(${margin.left}, ${legendY})`);

    const legendItems = [
        { label: 'Win Rate %', color: wrColor },
        { label: 'Clan Activity %', color: activityColor },
    ];

    let legendX = 0;
    for (const item of legendItems) {
        legendG.append('rect')
            .attr('x', legendX)
            .attr('y', -5)
            .attr('width', 12)
            .attr('height', 10)
            .attr('rx', 2)
            .attr('fill', item.color)
            .attr('fill-opacity', 0.55)
            .attr('stroke', item.color)
            .attr('stroke-width', 1);

        legendG.append('text')
            .attr('x', legendX + 16)
            .attr('y', 0)
            .attr('dy', '0.35em')
            .style('font-size', legendFontSize)
            .style('fill', colors.labelText)
            .text(item.label);

        legendX += 16 + item.label.length * (compact ? 6 : 7) + 16;
    }
};

const ClanBattleSeasonsSVG: React.FC<ClanBattleSeasonsSVGProps> = ({
    seasons,
    memberCount,
    svgHeight = 280,
    theme = 'light',
}) => {
    const containerRef = useRef<HTMLDivElement | null>(null);

    useEffect(() => {
        const container = containerRef.current;
        if (!container || !seasons || seasons.length === 0) return;

        const colors = chartColors[theme];
        const render = () => drawChart(container, seasons, memberCount, svgHeight, colors);

        render();
        window.addEventListener('resize', render);
        return () => window.removeEventListener('resize', render);
    }, [seasons, memberCount, svgHeight, theme]);

    if (!seasons || seasons.length === 0) return null;

    return <div ref={containerRef} className="relative w-full" />;
};

export default React.memo(ClanBattleSeasonsSVG);
