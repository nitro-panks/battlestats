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
        ? { top: 16, right: 40, bottom: 46, left: 42 }
        : { top: 20, right: 52, bottom: 52, left: 52 };

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

    // --- Scales ---
    const xScale = d3.scaleLinear()
        .domain([0, sorted.length - 1])
        .range([0, width]);
    const xPos = (d: ClanBattleSeasonPoint) => xScale(sorted.indexOf(d));

    // Left Y: percentage (WR and activity)
    const maxPct = Math.max(
        d3.max(sorted, (d: ClanBattleSeasonPoint) => d.roster_win_rate) || 60,
        d3.max(sorted, (d: ClanBattleSeasonPoint) => memberCount > 0 ? (d.participants / memberCount) * 100 : 0) || 60,
    );
    const yPct = d3.scaleLinear()
        .domain([0, Math.min(Math.ceil(maxPct / 10) * 10 + 10, 100)])
        .range([height, 0]);

    // Right Y: participant count
    const maxParticipants = d3.max(sorted, (d: ClanBattleSeasonPoint) => d.participants) || 10;
    const yCount = d3.scaleLinear()
        .domain([0, Math.ceil(maxParticipants * 1.15)])
        .range([height, 0]);

    // --- Grid lines ---
    svg.append('g')
        .attr('class', 'cb-grid')
        .call(d3.axisLeft(yPct).ticks(5).tickSize(-width).tickFormat(() => ''))
        .select('.domain').remove();
    svg.selectAll('.cb-grid line')
        .style('stroke', colors.gridLine)
        .style('stroke-width', 1)
        .attr('stroke-dasharray', '2,2');

    // --- Axes ---
    // X axis — manual tick marks at each season position
    const xAxisG = svg.append('g')
        .attr('transform', `translate(0, ${height})`);

    sorted.forEach((d, i) => {
        const tx = xScale(i);
        const label = d.season_label;
        // Skip labels on compact if too many seasons
        const skip = compact && sorted.length > 16 && i % 2 !== 0;
        if (!skip) {
            xAxisG.append('text')
                .attr('x', tx)
                .attr('y', compact && sorted.length > 12 ? 12 : 14)
                .attr('text-anchor', compact && sorted.length > 12 ? 'end' : 'middle')
                .attr('transform', compact && sorted.length > 12 ? `rotate(-45, ${tx}, 12)` : '')
                .style('font-size', axisFontSize)
                .style('fill', colors.axisText)
                .text(label);
        }
    });

    // Left Y axis (percentage)
    const leftAxis = svg.append('g')
        .call(d3.axisLeft(yPct).ticks(5).tickFormat((value: number) => `${value}%`).tickSize(0).tickPadding(6));
    leftAxis.selectAll('text')
        .style('font-size', axisFontSize)
        .style('fill', colors.axisText);
    leftAxis.select('.domain').remove();

    // Right Y axis (participants)
    const rightAxis = svg.append('g')
        .attr('transform', `translate(${width}, 0)`)
        .call(d3.axisRight(yCount).ticks(5).tickFormat((value: number) => `${value}`).tickSize(0).tickPadding(6));
    rightAxis.selectAll('text')
        .style('font-size', axisFontSize)
        .style('fill', colors.axisText);
    rightAxis.select('.domain').remove();

    // --- Line data as coordinate arrays ---
    const wrPoints: [number, number][] = sorted.map(d => [xPos(d), yPct(d.roster_win_rate)]);
    const actPoints: [number, number][] = sorted.map(d => [xPos(d), yPct(memberCount > 0 ? (d.participants / memberCount) * 100 : 0)]);
    const partPoints: [number, number][] = sorted.map(d => [xPos(d), yCount(d.participants)]);

    const line = d3.line().curve(d3.curveMonotoneX);

    const wrColor = colors.metricWR;
    const activityColor = colors.activityActive;
    const participantColor = colors.accentMid;

    // --- Draw lines ---
    svg.append('path')
        .attr('fill', 'none')
        .attr('stroke', wrColor)
        .attr('stroke-width', 2)
        .attr('d', line(wrPoints));

    svg.append('path')
        .attr('fill', 'none')
        .attr('stroke', activityColor)
        .attr('stroke-width', 2)
        .attr('stroke-dasharray', '6,3')
        .attr('d', line(actPoints));

    svg.append('path')
        .attr('fill', 'none')
        .attr('stroke', participantColor)
        .attr('stroke-width', 2)
        .attr('stroke-dasharray', '2,2')
        .attr('d', line(partPoints));

    // --- Data points ---
    const dotRadius = compact ? 3 : 4;

    svg.selectAll('.dot-wr')
        .data(sorted)
        .enter()
        .append('circle')
        .attr('cx', (d: ClanBattleSeasonPoint) => xPos(d))
        .attr('cy', (d: ClanBattleSeasonPoint) => yPct(d.roster_win_rate))
        .attr('r', dotRadius)
        .attr('fill', wrColor)
        .attr('stroke', colors.chartBg)
        .attr('stroke-width', 1.5);

    svg.selectAll('.dot-act')
        .data(sorted)
        .enter()
        .append('circle')
        .attr('cx', (d: ClanBattleSeasonPoint) => xPos(d))
        .attr('cy', (d: ClanBattleSeasonPoint) => yPct(memberCount > 0 ? (d.participants / memberCount) * 100 : 0))
        .attr('r', dotRadius)
        .attr('fill', activityColor)
        .attr('stroke', colors.chartBg)
        .attr('stroke-width', 1.5);

    svg.selectAll('.dot-part')
        .data(sorted)
        .enter()
        .append('circle')
        .attr('cx', (d: ClanBattleSeasonPoint) => xPos(d))
        .attr('cy', (d: ClanBattleSeasonPoint) => yCount(d.participants))
        .attr('r', dotRadius)
        .attr('fill', participantColor)
        .attr('stroke', colors.chartBg)
        .attr('stroke-width', 1.5);

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

    // Invisible hit areas per season for hover
    const hitWidth = sorted.length > 1
        ? Math.abs(xScale(1) - xScale(0))
        : width;

    svg.selectAll('.hit-area')
        .data(sorted)
        .enter()
        .append('rect')
        .attr('x', (d: ClanBattleSeasonPoint) => xPos(d) - hitWidth / 2)
        .attr('y', 0)
        .attr('width', hitWidth)
        .attr('height', height)
        .attr('fill', 'transparent')
        .style('cursor', 'crosshair')
        .on('mousemove', function (_event: MouseEvent, d: ClanBattleSeasonPoint) {
            const actPct = memberCount > 0 ? ((d.participants / memberCount) * 100).toFixed(0) : '\u2014';
            tooltip
                .html(
                    `<strong>${d.season_name}</strong><br/>` +
                    `<span style="color:${wrColor}">WR: ${d.roster_win_rate.toFixed(1)}%</span><br/>` +
                    `<span style="color:${activityColor}">Activity: ${actPct}%</span><br/>` +
                    `<span style="color:${participantColor}">Players: ${d.participants}</span>`
                )
                .style('opacity', 1);

            const tooltipNode = tooltip.node() as HTMLDivElement;
            const tooltipWidth = tooltipNode.offsetWidth;
            const px = xPos(d) + margin.left;
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
        { label: 'Win Rate', color: wrColor, dash: '' },
        { label: 'Activity %', color: activityColor, dash: '6,3' },
        { label: 'Players', color: participantColor, dash: '2,2' },
    ];

    let legendX = 0;
    for (const item of legendItems) {
        legendG.append('line')
            .attr('x1', legendX)
            .attr('y1', 0)
            .attr('x2', legendX + 16)
            .attr('y2', 0)
            .attr('stroke', item.color)
            .attr('stroke-width', 2)
            .attr('stroke-dasharray', item.dash || 'none');

        legendG.append('text')
            .attr('x', legendX + 20)
            .attr('y', 0)
            .attr('dy', '0.35em')
            .style('font-size', legendFontSize)
            .style('fill', colors.labelText)
            .text(item.label);

        legendX += 20 + item.label.length * (compact ? 6 : 7) + 16;
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
