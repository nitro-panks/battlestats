import React, { useEffect, useMemo, useRef, useState } from 'react';
import * as d3 from 'd3';
import { chartColors, type ChartTheme } from '../lib/chartTheme';

interface LandingActivityAttritionMonth {
    month: string;
    total_players: number;
    active_players: number;
    cooling_players: number;
    dormant_players: number;
    active_share: number;
}

interface LandingActivityAttritionSummary {
    latest_month: string;
    population_signal: 'growing' | 'stable' | 'shrinking';
    signal_delta_pct: number | null;
    recent_active_avg: number;
    prior_active_avg: number;
    recent_new_avg: number;
    prior_new_avg: number;
    months_compared: number;
}

interface LandingActivityAttritionData {
    metric: 'landing_activity_attrition';
    label: string;
    x_label: string;
    y_label: string;
    tracked_population: number;
    months: LandingActivityAttritionMonth[];
    summary: LandingActivityAttritionSummary;
}

interface LandingActivityAttritionSVGProps {
    data: LandingActivityAttritionData;
    svgHeight?: number;
    theme?: ChartTheme;
}

interface ActiveTrendPoint {
    month: string;
    value: number;
}

type SvgRootSelection = ReturnType<typeof d3.select>;

const formatCompactCount = (value: number): string => d3.format('~s')(value).replace('G', 'B');

const formatMonthLabel = (value: string): string => d3.timeFormat('%b %y')(new Date(`${value}T00:00:00`));

const signalColor = (signal: LandingActivityAttritionSummary['population_signal'], colors: typeof chartColors['light']): string => {
    if (signal === 'growing') return colors.heatmapAboveTrend;
    if (signal === 'shrinking') return colors.heatmapBelowTrend;
    return colors.axisText;
};

const signalLabel = (summary: LandingActivityAttritionSummary): string => {
    if (summary.signal_delta_pct == null) {
        return `Observed field: ${summary.population_signal}`;
    }

    const signedDelta = summary.signal_delta_pct > 0 ? `+${summary.signal_delta_pct.toFixed(1)}%` : `${summary.signal_delta_pct.toFixed(1)}%`;
    return `Observed field: ${summary.population_signal} (${signedDelta})`;
};

const buildActiveTrend = (months: LandingActivityAttritionMonth[]): ActiveTrendPoint[] => months.map((row: LandingActivityAttritionMonth, index: number) => {
    const slice = months.slice(Math.max(0, index - 2), index + 1);
    const average = slice.reduce((sum: number, month: LandingActivityAttritionMonth) => sum + month.active_players, 0) / slice.length;
    return {
        month: row.month,
        value: average,
    };
});

const showDetails = (
    svgRoot: SvgRootSelection,
    row: LandingActivityAttritionMonth,
    trackedPopulation: number,
    colors: typeof chartColors['light'],
) => {
    svgRoot.select('.landing-activity-details').remove();

    const detailGroup = svgRoot
        .append('g')
        .attr('class', 'landing-activity-details')
        .attr('transform', 'translate(48, 14)');

    const title = detailGroup.append('text')
        .attr('x', 0)
        .attr('y', 0)
        .style('font-size', '11px')
        .style('font-weight', '700')
        .style('fill', colors.accentLink)
        .text(formatMonthLabel(row.month));

    const meta = detailGroup.append('text')
        .attr('x', 0)
        .attr('y', 18)
        .style('font-size', '10px')
        .style('fill', colors.axisText);

    meta.append('tspan').text(`${row.total_players.toLocaleString()} observed`);
    meta.append('tspan').attr('dx', 12).style('fill', colors.activityActive).text(`${row.active_players.toLocaleString()} active`);
    meta.append('tspan').attr('dx', 12).style('fill', colors.activityCooling).text(`${row.cooling_players.toLocaleString()} cooling`);
    meta.append('tspan').attr('dx', 12).style('fill', colors.labelMuted).text(`${row.dormant_players.toLocaleString()} dormant`);

    const sub = detailGroup.append('text')
        .attr('x', 0)
        .attr('y', 34)
        .style('font-size', '10px')
        .style('fill', colors.labelMuted)
        .text(`${row.active_share.toFixed(1)}% still active today • ${((row.total_players / Math.max(trackedPopulation, 1)) * 100).toFixed(1)}% of observed population`);

    const nodes = [title.node(), meta.node(), sub.node()].filter(Boolean) as SVGGraphicsElement[];
    const boxes = nodes.map((node: SVGGraphicsElement) => node.getBBox());
    const minX = Math.min(...boxes.map((box) => box.x));
    const minY = Math.min(...boxes.map((box) => box.y));
    const maxX = Math.max(...boxes.map((box) => box.x + box.width));
    const maxY = Math.max(...boxes.map((box) => box.y + box.height));

    detailGroup.insert('rect', 'text')
        .attr('x', minX - 10)
        .attr('y', minY - 6)
        .attr('width', maxX - minX + 20)
        .attr('height', maxY - minY + 12)
        .attr('rx', 6)
        .attr('fill', colors.surface)
        .attr('fill-opacity', 0.96);
};

const drawChart = (
    containerElement: HTMLDivElement,
    data: LandingActivityAttritionData,
    containerWidth: number,
    svgHeight: number,
    colors: typeof chartColors['light'],
) => {
    const ACTIVE_FILL = colors.activityActive;
    const COOLING_FILL = colors.activityCooling;
    const DORMANT_FILL = colors.activityDormant;
    const TREND_STROKE = colors.axisText;

    const margin = { top: 56, right: 18, bottom: 40, left: 50 };
    const width = containerWidth - margin.left - margin.right;
    const height = svgHeight - margin.top - margin.bottom;
    const container = d3.select(containerElement);
    container.selectAll('*').remove();

    const svgRoot = container
        .append('svg')
        .attr('width', width + margin.left + margin.right)
        .attr('height', svgHeight);

    const svg = svgRoot
        .append('g')
        .attr('transform', `translate(${margin.left}, ${margin.top})`);

    if (!data.months.length) {
        svg.append('text')
            .attr('x', 0)
            .attr('y', 16)
            .style('font-size', '12px')
            .style('fill', colors.labelText)
            .text('No activity and attrition data available.');
        return;
    }

    const x = d3.scaleBand()
        .domain(data.months.map((row: LandingActivityAttritionMonth) => row.month))
        .range([0, width])
        .paddingInner(0.22)
        .paddingOuter(0.08);

    const y = d3.scaleLinear()
        .domain([0, (d3.max(data.months, (row: LandingActivityAttritionMonth) => row.total_players) || 1) * 1.1])
        .range([height, 0]);

    svg.append('g')
        .attr('class', 'landing-activity-grid')
        .call(d3.axisLeft(y).ticks(5).tickSize(-width).tickFormat(() => ''));
    svg.select('.landing-activity-grid')?.select('.domain')?.remove();
    svg.selectAll('.landing-activity-grid line')
        .style('stroke', colors.gridLine)
        .style('stroke-width', 1);

    const tickStep = Math.max(1, Math.floor(data.months.length / 6));
    const tickValues = data.months
        .map((row: LandingActivityAttritionMonth) => row.month)
        .filter((month: string, index: number, values: string[]) => index % tickStep === 0 || index === values.length - 1);

    svg.append('g')
        .attr('transform', `translate(0, ${height})`)
        .style('color', colors.labelMuted)
        .call(d3.axisBottom(x).tickValues(tickValues).tickFormat((value: unknown) => formatMonthLabel(String(value))).tickSizeOuter(0))
        .selectAll('text')
        .style('font-size', '10px');

    svg.append('g')
        .style('color', colors.labelMuted)
        .call(d3.axisLeft(y).ticks(5).tickFormat((value: unknown) => formatCompactCount(Number(value))).tickSizeOuter(0))
        .selectAll('text')
        .style('font-size', '10px');

    svg.append('text')
        .attr('x', width / 2)
        .attr('y', height + 34)
        .attr('text-anchor', 'middle')
        .style('fill', colors.labelMuted)
        .style('font-size', '10px')
        .text(data.x_label);

    svg.append('text')
        .attr('transform', 'rotate(-90)')
        .attr('x', -height / 2)
        .attr('y', -36)
        .attr('text-anchor', 'middle')
        .style('fill', colors.labelMuted)
        .style('font-size', '10px')
        .text(data.y_label);

    const stackedData = d3.stack().keys(['active_players', 'cooling_players', 'dormant_players'])(data.months as any);
    const fillByKey: Record<string, string> = {
        active_players: ACTIVE_FILL,
        cooling_players: COOLING_FILL,
        dormant_players: DORMANT_FILL,
    };

    svg.append('g')
        .selectAll('g')
        .data(stackedData)
        .enter()
        .append('g')
        .attr('fill', (series: any) => fillByKey[series.key])
        .selectAll('rect')
        .data((series: any) => series.map((segment: any) => ({ ...segment, month: segment.data.month })))
        .enter()
        .append('rect')
        .attr('x', (segment: any) => x(segment.month) || 0)
        .attr('y', (segment: any) => y(segment[1]))
        .attr('width', x.bandwidth())
        .attr('height', (segment: any) => Math.max(0, y(segment[0]) - y(segment[1])))
        .attr('rx', 3);

    const activeTrend = buildActiveTrend(data.months);
    const activeLine = d3.line()
        .x((point: any) => (x(point.month) || 0) + (x.bandwidth() / 2))
        .y((point: any) => y(point.value))
        .curve(d3.curveMonotoneX);

    svg.append('path')
        .datum(activeTrend)
        .attr('fill', 'none')
        .attr('stroke', TREND_STROKE)
        .attr('stroke-width', 1.75)
        .attr('d', activeLine);

    svg.append('g')
        .selectAll('circle')
        .data(activeTrend)
        .enter()
        .append('circle')
        .attr('cx', (point: ActiveTrendPoint) => (x(point.month) || 0) + (x.bandwidth() / 2))
        .attr('cy', (point: ActiveTrendPoint) => y(point.value))
        .attr('r', 2.2)
        .attr('fill', TREND_STROKE);

    const latestTrendPoint = activeTrend[activeTrend.length - 1];
    if (latestTrendPoint) {
        svg.append('text')
            .attr('x', Math.min(width, (x(latestTrendPoint.month) || 0) + x.bandwidth() + 8))
            .attr('y', y(latestTrendPoint.value) - 6)
            .attr('text-anchor', 'end')
            .style('font-size', '10px')
            .style('font-weight', '700')
            .style('fill', TREND_STROKE)
            .text('3-mo active avg');
    }

    const summaryGroup = svgRoot
        .append('g')
        .attr('transform', `translate(${margin.left + width - 214}, 14)`);

    summaryGroup.append('text')
        .attr('x', 0)
        .attr('y', 0)
        .style('font-size', '10px')
        .style('font-weight', '700')
        .style('fill', signalColor(data.summary.population_signal, colors))
        .text(signalLabel(data.summary));

    summaryGroup.append('text')
        .attr('x', 0)
        .attr('y', 14)
        .style('font-size', '10px')
        .style('fill', colors.labelMuted)
        .text(`bars = cohort fate today • line = ${data.summary.months_compared}-month active pulse`);

    const legendItems = [
        { label: 'Active 30d', fill: ACTIVE_FILL },
        { label: 'Cooling 31-90d', fill: COOLING_FILL },
        { label: 'Dormant 90d+', fill: DORMANT_FILL },
    ];

    const legend = summaryGroup.append('g').attr('transform', 'translate(0, 28)');
    legendItems.forEach((item: { label: string; fill: string }, index: number) => {
        const row = legend.append('g').attr('transform', `translate(0, ${index * 13})`);
        row.append('rect')
            .attr('x', 0)
            .attr('y', -7)
            .attr('width', 8)
            .attr('height', 8)
            .attr('rx', 2)
            .attr('fill', item.fill);
        row.append('text')
            .attr('x', 13)
            .attr('y', 0)
            .style('font-size', '10px')
            .style('fill', colors.labelMuted)
            .text(item.label);
    });

    svg.append('g')
        .selectAll('rect')
        .data(data.months)
        .enter()
        .append('rect')
        .attr('x', (row: LandingActivityAttritionMonth) => x(row.month) || 0)
        .attr('y', 0)
        .attr('width', x.bandwidth())
        .attr('height', height)
        .attr('fill', 'transparent')
        .style('cursor', 'default')
        .on('mouseover', (_event: MouseEvent, row: LandingActivityAttritionMonth) => showDetails(svgRoot, row, data.tracked_population, colors))
        .on('mouseout', () => svgRoot.select('.landing-activity-details').remove());
};

const LandingActivityAttritionSVG: React.FC<LandingActivityAttritionSVGProps> = ({
    data,
    svgHeight = 300,
    theme = 'light',
}) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const [containerWidth, setContainerWidth] = useState(320);
    const [isChartReady, setIsChartReady] = useState(false);
    const chartSignature = useMemo(() => JSON.stringify({
        months: data.months.map((row: LandingActivityAttritionMonth) => [row.month, row.total_players, row.active_players, row.cooling_players, row.dormant_players]),
        summary: data.summary,
        svgHeight,
    }), [data, svgHeight]);

    useEffect(() => {
        if (!containerRef.current) {
            return;
        }

        const observer = new ResizeObserver((entries: ResizeObserverEntry[]) => {
            for (const entry of entries) {
                setContainerWidth(entry.contentRect.width);
            }
        });

        observer.observe(containerRef.current);
        setContainerWidth(containerRef.current.clientWidth);
        return () => observer.disconnect();
    }, []);

    useEffect(() => {
        if (!containerRef.current || containerWidth < 100) {
            return;
        }

        const colors = chartColors[theme];
        setIsChartReady(false);
        drawChart(containerRef.current, data, containerWidth, svgHeight, colors);
        const frameId = window.requestAnimationFrame(() => setIsChartReady(true));
        return () => window.cancelAnimationFrame(frameId);
    }, [chartSignature, containerWidth, data, svgHeight, theme]);

    return <div ref={containerRef} className={`pr-8 transition-opacity duration-150 md:pr-16 ${isChartReady ? 'opacity-100' : 'opacity-0'}`}></div>;
};

export default LandingActivityAttritionSVG;
