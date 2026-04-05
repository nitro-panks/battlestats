import React, { useEffect, useRef } from 'react';
import * as d3 from 'd3';
import { chartColors, type ChartTheme } from '../lib/chartTheme';

export interface ClanBattleSeasonPoint {
    season_id: number;
    season_name: string;
    season_label: string;
    start_date?: string | null;
    roster_battles?: number;
    roster_wins?: number;
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
    battles: number;
    wins: number;
    wr: number;
    hasData: boolean;
    season: ClanBattleSeasonPoint;
}

const drawChart = (
    container: HTMLDivElement,
    seasons: ClanBattleSeasonPoint[],
    _memberCount: number,
    svgHeight: number,
    colors: Colors,
) => {
    const containerWidth = Math.max(container.clientWidth || 0, 280);
    const compact = containerWidth < 480;

    d3.select(container).selectAll('*').remove();

    // Sort chronologically by start_date, falling back to season_id
    const sorted = [...seasons].sort((a, b) => {
        if (a.start_date && b.start_date) return a.start_date.localeCompare(b.start_date);
        if (a.start_date) return -1;
        if (b.start_date) return 1;
        return a.season_id - b.season_id;
    });
    if (sorted.length === 0) return;

    // --- Build complete season timeline with gaps filled ---
    const byId = new Map(sorted.map(s => [s.season_id, s]));

    const allIds: number[] = [];
    for (let i = 0; i < sorted.length; i++) {
        const curr = sorted[i];
        allIds.push(curr.season_id);
        if (i < sorted.length - 1) {
            const next = sorted[i + 1];
            const currRange = Math.floor(curr.season_id / 100);
            const nextRange = Math.floor(next.season_id / 100);
            if (currRange === nextRange) {
                for (let id = curr.season_id + 1; id < next.season_id; id++) {
                    allIds.push(id);
                }
            }
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
            roster_battles: 0,
            roster_wins: 0,
        };
    });

    const totalSvgWidth = containerWidth;
    const totalSvgHeight = compact ? Math.min(svgHeight, 260) : svgHeight;
    const margin = compact
        ? { top: 16, right: 14, bottom: 46, left: 48 }
        : { top: 20, right: 20, bottom: 52, left: 54 };

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
        battles: d.roster_battles || 0,
        wins: d.roster_wins || 0,
        wr: d.roster_win_rate,
        hasData: byId.has(d.season_id),
        season: d,
    }));

    // --- Scales ---
    const n = fullTimeline.length;
    const bandPadding = 0.2;
    const totalBandWidth = width / n;
    const barWidth = Math.max(1, totalBandWidth * (1 - bandPadding));
    const xScale = d3.scaleLinear()
        .domain([0, n - 1])
        .range([totalBandWidth / 2 - barWidth / 2, width - totalBandWidth / 2 - barWidth / 2]);
    const barCenter = (index: number) => xScale(index) + barWidth / 2;

    // Y scale — game count
    const maxBattles = d3.max(rows, (d: SeasonRow) => d.battles) || 100;
    const yDomainMax = Math.ceil(maxBattles * 1.1 / 10) * 10;
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
        .call(d3.axisLeft(yScale).ticks(5).tickSize(0).tickPadding(6));
    yAxis.selectAll('text')
        .style('font-size', axisFontSize)
        .style('fill', colors.axisText);
    yAxis.select('.domain').remove();

    // --- Draw layered bars ---
    const cornerR = Math.min(3, barWidth / 2);
    const winsBarWidth = barWidth * 0.65;
    const winsBarOffset = (barWidth - winsBarWidth) / 2;

    const roundedTopBar = (bx: number, by: number, bw: number, bh: number, r: number): string => {
        if (bh <= 0) return '';
        const cr = Math.min(r, bh / 2, bw / 2);
        return `M${bx},${by + bh}V${by + cr}Q${bx},${by} ${bx + cr},${by}H${bx + bw - cr}Q${bx + bw},${by} ${bx + bw},${by + cr}V${by + bh}Z`;
    };

    const activeRows = rows.filter(d => d.hasData);

    // Background grey bars — total battles
    svg.selectAll('.battles-bar')
        .data(activeRows)
        .enter()
        .append('path')
        .attr('class', 'battles-bar')
        .attr('d', (d: SeasonRow) => {
            const bx = xScale(d.index);
            const by = yScale(d.battles);
            return roundedTopBar(bx, by, barWidth, height - by, cornerR);
        })
        .attr('fill', colors.barBg);

    // Foreground colored bars — wins, colored by WR
    svg.selectAll('.wins-bar')
        .data(activeRows)
        .enter()
        .append('path')
        .attr('class', 'wins-bar')
        .attr('d', (d: SeasonRow) => {
            const bx = xScale(d.index) + winsBarOffset;
            const by = yScale(d.wins);
            return roundedTopBar(bx, by, winsBarWidth, height - by, cornerR);
        })
        .attr('fill', (d: SeasonRow) => selectColorByWR(d.wr))
        .attr('fill-opacity', 0.85)
        .attr('stroke', (d: SeasonRow) => selectColorByWR(d.wr))
        .attr('stroke-width', 0.5)
        .attr('stroke-opacity', 0.9);

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
                        `Battles: ${d.battles.toLocaleString()}<br/>` +
                        `<span style="color:${selectColorByWR(d.wr)}">Wins: ${d.wins.toLocaleString()} (${d.wr.toFixed(1)}%)</span>`
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

    // Games Played legend — grey swatch
    let legendX = 0;
    legendG.append('rect')
        .attr('x', legendX)
        .attr('y', -5)
        .attr('width', 12)
        .attr('height', 10)
        .attr('rx', 3)
        .attr('fill', colors.barBg);

    legendG.append('text')
        .attr('x', legendX + 16)
        .attr('y', 0)
        .attr('dy', '0.35em')
        .style('font-size', legendFontSize)
        .style('fill', colors.labelText)
        .text('Games Played');

    legendX += 16 + 'Games Played'.length * (compact ? 6 : 7) + 16;

    // Games Won legend — WR gradient swatch
    const gradId = 'cb-wr-grad';
    const defs = svgRoot.append('defs');
    const grad = defs.append('linearGradient').attr('id', gradId);
    grad.append('stop').attr('offset', '0%').attr('stop-color', '#fd8d3c');
    grad.append('stop').attr('offset', '33%').attr('stop-color', '#fed976');
    grad.append('stop').attr('offset', '66%').attr('stop-color', '#74c476');
    grad.append('stop').attr('offset', '100%').attr('stop-color', '#810c9e');

    legendG.append('rect')
        .attr('x', legendX)
        .attr('y', -5)
        .attr('width', 12)
        .attr('height', 10)
        .attr('rx', 3)
        .attr('fill', `url(#${gradId})`)
        .attr('fill-opacity', 0.85);

    legendG.append('text')
        .attr('x', legendX + 16)
        .attr('y', 0)
        .attr('dy', '0.35em')
        .style('font-size', legendFontSize)
        .style('fill', colors.labelText)
        .text('Games Won');
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
