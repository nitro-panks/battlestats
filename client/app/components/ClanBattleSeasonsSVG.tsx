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

const selectColorByWR = (wr: number): string => {
    if (wr > 65) return '#810c9e';
    if (wr >= 60) return '#D042F3';
    if (wr >= 56) return '#3182bd';
    if (wr >= 54) return '#74c476';
    if (wr >= 52) return '#a1d99b';
    if (wr >= 50) return '#fed976';
    if (wr >= 45) return '#fd8d3c';
    return '#a50f15';
};

interface SeasonRow {
    index: number;
    wr: number;
    activity: number;
    hasData: boolean;
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

    // --- Build complete season timeline with gaps filled ---
    const byId = new Map(sorted.map(s => [s.season_id, s]));
    const rangeOf = (id: number) => Math.floor(id / 100);

    const rangeGroups = new Map<number, number[]>();
    for (const s of sorted) {
        const r = rangeOf(s.season_id);
        if (!rangeGroups.has(r)) rangeGroups.set(r, []);
        rangeGroups.get(r)!.push(s.season_id);
    }

    const allIds: number[] = [];
    for (const [, ids] of [...rangeGroups.entries()].sort((a, b) => a[0] - b[0])) {
        const min = Math.min(...ids);
        const max = Math.max(...ids);
        for (let id = min; id <= max; id++) {
            allIds.push(id);
        }
    }

    const fullTimeline: ClanBattleSeasonPoint[] = allIds.map(id => {
        const existing = byId.get(id);
        if (existing) return existing;
        return {
            season_id: id,
            season_name: `Season ${id}`,
            season_label: `S${id}`,
            participants: 0,
            roster_win_rate: 0,
        };
    });

    const totalSvgWidth = containerWidth;
    const totalSvgHeight = compact ? Math.min(svgHeight, 260) : svgHeight;
    const margin = compact
        ? { top: 16, right: 14, bottom: 46, left: 42 }
        : { top: 20, right: 20, bottom: 52, left: 48 };

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

    // --- Per-season rows ---
    const rows: SeasonRow[] = fullTimeline.map((d, i) => ({
        index: i,
        wr: d.roster_win_rate,
        activity: memberCount > 0 ? (d.participants / memberCount) * 100 : 0,
        hasData: byId.has(d.season_id),
        season: d,
    }));

    // --- Scales ---
    // Band-like positioning via linear scale for even spacing
    const n = fullTimeline.length;
    const bandPadding = 0.2;
    const totalBandWidth = width / n;
    const barWidth = Math.max(1, totalBandWidth * (1 - bandPadding));
    const xScale = d3.scaleLinear()
        .domain([0, n - 1])
        .range([totalBandWidth / 2 - barWidth / 2, width - totalBandWidth / 2 - barWidth / 2]);
    const barCenter = (index: number) => xScale(index) + barWidth / 2;

    // Y scale — percentage 0-100
    const maxPct = Math.max(
        d3.max(rows, (d: SeasonRow) => d.wr) || 60,
        d3.max(rows, (d: SeasonRow) => d.activity) || 60,
    );
    const yDomainMax = Math.min(Math.ceil(maxPct / 10) * 10 + 10, 100);
    const yScale = d3.scaleLinear()
        .domain([0, yDomainMax])
        .range([height, 0]);

    // --- Grid lines ---
    svg.append('g')
        .attr('class', 'cb-grid')
        .call(d3.axisLeft(yScale).ticks(5).tickSize(-width).tickFormat(() => ''))
        .select('.domain').remove();
    svg.selectAll('.cb-grid line')
        .style('stroke', colors.gridLine)
        .style('stroke-width', 1)
        .attr('stroke-dasharray', '2,2');

    // --- Y axis ---
    const yAxis = svg.append('g')
        .call(d3.axisLeft(yScale).ticks(5).tickFormat((value: number) => `${value}%`).tickSize(0).tickPadding(6));
    yAxis.selectAll('text')
        .style('font-size', axisFontSize)
        .style('fill', colors.axisText);
    yAxis.select('.domain').remove();

    // --- Colors ---
    const activityColor = colors.activityActive;

    // --- Draw WR bars ---
    const cornerR = Math.min(3, barWidth / 2);

    // Rounded top-corner bar path
    const roundedTopBar = (bx: number, by: number, bw: number, bh: number, r: number): string => {
        if (bh <= 0) return '';
        const cr = Math.min(r, bh / 2, bw / 2);
        return `M${bx},${by + bh}V${by + cr}Q${bx},${by} ${bx + cr},${by}H${bx + bw - cr}Q${bx + bw},${by} ${bx + bw},${by + cr}V${by + bh}Z`;
    };

    const activeRows = rows.filter(d => d.hasData);

    // WR bars — colored by WR value
    svg.selectAll('.wr-bar')
        .data(activeRows)
        .enter()
        .append('path')
        .attr('class', 'wr-bar')
        .attr('d', (d: SeasonRow) => {
            const bx = xScale(d.index);
            const by = yScale(d.wr);
            return roundedTopBar(bx, by, barWidth, height - by, cornerR);
        })
        .attr('fill', (d: SeasonRow) => selectColorByWR(d.wr))
        .attr('fill-opacity', 0.75)
        .attr('stroke', (d: SeasonRow) => selectColorByWR(d.wr))
        .attr('stroke-width', 0.5)
        .attr('stroke-opacity', 0.9);

    // --- Activity line overlay ---
    const lineCoords: [number, number][] = activeRows.map(d => [barCenter(d.index), yScale(d.activity)]);
    const lineGen = d3.line().curve(d3.curveMonotoneX);

    if (lineCoords.length > 1) {
        svg.append('path')
            .attr('d', lineGen(lineCoords))
            .attr('fill', 'none')
            .attr('stroke', activityColor)
            .attr('stroke-width', 2)
            .attr('stroke-opacity', 0.9);
    }

    // Activity dots
    svg.selectAll('.activity-dot')
        .data(activeRows)
        .enter()
        .append('circle')
        .attr('class', 'activity-dot')
        .attr('cx', (d: SeasonRow) => barCenter(d.index))
        .attr('cy', (d: SeasonRow) => yScale(d.activity))
        .attr('r', Math.min(4, barWidth / 3))
        .attr('fill', activityColor)
        .attr('stroke', colors.surface)
        .attr('stroke-width', 1.5);

    // --- X axis ---
    const xAxisG = svg.append('g')
        .attr('transform', `translate(0, ${height})`);

    const maxTicks = 20;
    const tickStep = fullTimeline.length > maxTicks ? Math.ceil(fullTimeline.length / maxTicks) : 1;

    fullTimeline.forEach((d, i) => {
        if (i % tickStep !== 0 && i !== fullTimeline.length - 1) return;
        const tx = barCenter(i);
        const rotate = compact && fullTimeline.length > 12;
        xAxisG.append('text')
            .attr('x', tx)
            .attr('y', rotate ? 12 : 14)
            .attr('text-anchor', rotate ? 'end' : 'middle')
            .attr('transform', rotate ? `rotate(-45, ${tx}, 12)` : '')
            .style('font-size', axisFontSize)
            .style('fill', byId.has(d.season_id) ? colors.axisText : colors.labelMuted)
            .text(d.season_label);
    });

    // --- Tooltip ---
    const tooltip = d3.select(container)
        .append('div')
        .style('position', 'absolute')
        .style('pointer-events', 'none')
        .style('background', colors.surface)
        .style('border', `1px solid ${colors.axisLine}`)
        .style('border-radius', '6px')
        .style('padding', '8px 12px')
        .style('font-size', '11px')
        .style('color', colors.labelStrong)
        .style('line-height', '1.5')
        .style('white-space', 'nowrap')
        .style('opacity', 0)
        .style('z-index', '10')
        .style('box-shadow', '0 2px 8px rgba(0,0,0,0.12)');

    const hitWidth = fullTimeline.length > 1
        ? Math.abs(width / fullTimeline.length)
        : width;

    svg.selectAll('.hit-area')
        .data(rows)
        .enter()
        .append('rect')
        .attr('x', (d: SeasonRow) => xScale(d.index) - hitWidth * 0.1)
        .attr('y', 0)
        .attr('width', hitWidth)
        .attr('height', height)
        .attr('fill', 'transparent')
        .style('cursor', 'crosshair')
        .on('mousemove', function (_event: MouseEvent, d: SeasonRow) {
            if (!d.hasData) {
                tooltip
                    .html(`<strong>${d.season.season_name}</strong><br/><span style="color:${colors.labelMuted}">Did not participate</span>`)
                    .style('opacity', 1);
            } else {
                tooltip
                    .html(
                        `<strong>${d.season.season_name}</strong><br/>` +
                        `<span style="color:${selectColorByWR(d.wr)}">WR: ${d.wr.toFixed(1)}%</span><br/>` +
                        `<span style="color:${activityColor}">Activity: ${d.activity.toFixed(0)}%</span>`
                    )
                    .style('opacity', 1);
            }

            const tooltipNode = tooltip.node() as HTMLDivElement;
            const tooltipWidth = tooltipNode.offsetWidth;
            const px = xScale(d.index) + margin.left + barWidth;
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

    // WR legend — gradient swatch showing WR color range
    const gradId = 'cb-wr-grad';
    const defs = svgRoot.append('defs');
    const grad = defs.append('linearGradient').attr('id', gradId);
    grad.append('stop').attr('offset', '0%').attr('stop-color', '#fd8d3c');
    grad.append('stop').attr('offset', '33%').attr('stop-color', '#fed976');
    grad.append('stop').attr('offset', '66%').attr('stop-color', '#74c476');
    grad.append('stop').attr('offset', '100%').attr('stop-color', '#810c9e');

    let legendX = 0;
    legendG.append('rect')
        .attr('x', legendX)
        .attr('y', -5)
        .attr('width', 12)
        .attr('height', 10)
        .attr('rx', 3)
        .attr('fill', `url(#${gradId})`)
        .attr('fill-opacity', 0.75);

    legendG.append('text')
        .attr('x', legendX + 16)
        .attr('y', 0)
        .attr('dy', '0.35em')
        .style('font-size', legendFontSize)
        .style('fill', colors.labelText)
        .text('Win Rate %');

    legendX += 16 + 'Win Rate %'.length * (compact ? 6 : 7) + 16;

    // Activity legend — line + dot swatch
    legendG.append('line')
        .attr('x1', legendX)
        .attr('y1', 0)
        .attr('x2', legendX + 12)
        .attr('y2', 0)
        .attr('stroke', activityColor)
        .attr('stroke-width', 2);
    legendG.append('circle')
        .attr('cx', legendX + 6)
        .attr('cy', 0)
        .attr('r', 3)
        .attr('fill', activityColor)
        .attr('stroke', colors.surface)
        .attr('stroke-width', 1);

    legendG.append('text')
        .attr('x', legendX + 16)
        .attr('y', 0)
        .attr('dy', '0.35em')
        .style('font-size', legendFontSize)
        .style('fill', colors.labelText)
        .text('Clan Activity %');
};

const ClanBattleSeasonsSVG: React.FC<ClanBattleSeasonsSVGProps> = ({
    seasons,
    memberCount,
    svgHeight = 300,
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
