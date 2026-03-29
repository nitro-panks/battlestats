import React, { useEffect, useId, useRef } from 'react';
import * as d3 from 'd3';
import { PLAYER_ROUTE_PANEL_FETCH_TTL_MS } from '../lib/playerRouteFetch';
import { fetchSharedJson } from '../lib/sharedJsonFetch';
import { ChartTheme, chartColors } from '../lib/chartTheme';

type DistributionMetric = 'win_rate' | 'survival_rate' | 'battles_played';

interface PopulationDistributionSVGProps {
    primaryMetric: DistributionMetric;
    primaryValue: number | null;
    overlayMetric?: DistributionMetric;
    overlayValue?: number | null;
    svgWidth?: number;
    svgHeight?: number;
    theme?: ChartTheme;
}

interface DistributionBin {
    bin_min: number;
    bin_max: number;
    count: number;
}

interface DistributionPayload {
    metric: DistributionMetric;
    label: string;
    x_label: string;
    scale: 'linear' | 'log';
    value_format: 'percent' | 'integer';
    tracked_population: number;
    bins: DistributionBin[];
}

interface DistributionPoint {
    value: number;
    count: number;
}

type LinearScale = ReturnType<typeof d3.scaleLinear>;
type LogScale = ReturnType<typeof d3.scaleLog>;
type GroupSelection = ReturnType<typeof d3.select>;

const WOWS_WR_BREAKPOINTS = [45, 50, 52, 54, 56, 60, 65];

const selectColorByWR = (winRatio: number, theme: ChartTheme = 'light'): string => {
    const c = chartColors[theme];
    if (winRatio > 65) return c.wrElite;
    if (winRatio >= 60) return c.wrSuperUnicum;
    if (winRatio >= 56) return c.wrUnicum;
    if (winRatio >= 54) return c.wrVeryGood;
    if (winRatio >= 52) return c.wrGood;
    if (winRatio >= 50) return c.wrAboveAvg;
    if (winRatio >= 45) return c.wrAverage;
    return c.wrBad;
};

const metricLineColor = (metric: DistributionMetric, theme: ChartTheme = 'light'): string => {
    const c = chartColors[theme];
    if (metric === 'survival_rate') return c.metricSurvival;
    if (metric === 'battles_played') return c.metricBattles;
    return c.metricWR;
};

const metricValueColor = (metric: DistributionMetric, value: number, theme: ChartTheme = 'light'): string => {
    if (metric === 'win_rate') {
        return selectColorByWR(value, theme);
    }

    return metricLineColor(metric, theme);
};

const midpointForBin = (bin: DistributionBin, scale: DistributionPayload['scale']): number => {
    if (scale === 'log') {
        return Math.sqrt(bin.bin_min * bin.bin_max);
    }

    return (bin.bin_min + bin.bin_max) / 2;
};

const formatMetricValue = (payload: DistributionPayload, value: number): string => {
    if (payload.value_format === 'percent') {
        return `${value.toFixed(1)}%`;
    }

    return d3.format(',')(Math.round(value));
};

const formatAxisTick = (payload: DistributionPayload, value: number): string => {
    if (payload.value_format === 'percent') {
        return `${value}%`;
    }

    const compact = d3.format('~s')(value);
    return compact.replace('G', 'B');
};

const uniqueSortedEdges = (bins: DistributionBin[]): number[] => Array.from(
    new Set(bins.flatMap((bin) => [bin.bin_min, bin.bin_max])),
).sort((left, right) => left - right);

const domainTickValues = (
    payload: DistributionPayload,
    overlayPayload: DistributionPayload | null,
    domain: [number, number],
): number[] => uniqueSortedEdges([
    ...payload.bins,
    ...(overlayPayload && overlayPayload.scale === payload.scale ? overlayPayload.bins : []),
]).filter((value) => value >= domain[0] && value <= domain[1]);

const payloadDomain = (payload: DistributionPayload): [number, number] => {
    if (payload.scale === 'log' && payload.bins.length) {
        const firstPoint = midpointForBin(payload.bins[0], payload.scale);
        const lastPoint = midpointForBin(payload.bins[payload.bins.length - 1], payload.scale);
        return [firstPoint, lastPoint];
    }

    const minValue = payload.bins[0]?.bin_min ?? 0;
    const maxValue = payload.bins[payload.bins.length - 1]?.bin_max ?? 1;
    return [minValue, maxValue];
};

const combinedDomain = (
    primaryPayload: DistributionPayload,
    primaryValue: number,
    overlayPayload: DistributionPayload | null,
    overlayValue: number | null,
): [number, number] => {
    const domainValues = [...payloadDomain(primaryPayload), primaryValue];

    if (overlayPayload && overlayPayload.scale === primaryPayload.scale) {
        domainValues.push(...payloadDomain(overlayPayload));
    }

    if (overlayValue != null) {
        domainValues.push(overlayValue);
    }

    const minValue = d3.min(domainValues) ?? 0;
    const maxValue = d3.max(domainValues) ?? 1;

    if (primaryPayload.scale === 'log') {
        return [Math.max(1, minValue), Math.max(Math.max(1, minValue) + 1, maxValue)];
    }

    return [minValue, maxValue];
};

const buildXScale = (
    payload: DistributionPayload,
    domain: [number, number],
    width: number,
): LinearScale | LogScale => {
    const [minValue, maxValue] = domain;

    if (payload.scale === 'log') {
        return d3.scaleLog()
            .domain([Math.max(1, minValue), Math.max(minValue + 1, maxValue)])
            .range([0, width]);
    }

    return d3.scaleLinear()
        .domain([minValue, maxValue])
        .range([0, width]);
};

const interpolateCountAtValue = (
    points: DistributionPoint[],
    targetValue: number,
    scaleType: DistributionPayload['scale'],
): number => {
    const bisect = d3.bisector((point: DistributionPoint) => point.value).left;
    const index = bisect(points, targetValue);

    if (index <= 0) {
        return points[0]?.count ?? 0;
    }

    if (index >= points.length) {
        return points[points.length - 1]?.count ?? 0;
    }

    const left = points[index - 1];
    const right = points[index];
    const leftValue = scaleType === 'log' ? Math.log(left.value) : left.value;
    const rightValue = scaleType === 'log' ? Math.log(right.value) : right.value;
    const target = scaleType === 'log' ? Math.log(targetValue) : targetValue;
    const span = rightValue - leftValue;

    if (span === 0) {
        return right.count;
    }

    const t = (target - leftValue) / span;
    return left.count + t * (right.count - left.count);
};

const fractionWithinBin = (
    bin: DistributionBin,
    targetValue: number,
    scaleType: DistributionPayload['scale'],
): number => {
    if (targetValue <= bin.bin_min) {
        return 0;
    }

    if (targetValue >= bin.bin_max) {
        return 1;
    }

    if (scaleType === 'log') {
        const lower = Math.log(bin.bin_min);
        const upper = Math.log(bin.bin_max);
        const target = Math.log(targetValue);
        return (target - lower) / (upper - lower);
    }

    return (targetValue - bin.bin_min) / (bin.bin_max - bin.bin_min);
};

const percentileLabelForValue = (
    bins: DistributionBin[],
    targetValue: number,
    scaleType: DistributionPayload['scale'],
    totalPopulation: number,
): string => {
    if (totalPopulation <= 0) {
        return 'Population unavailable';
    }

    const playersBelow = bins
        .filter((bin) => bin.bin_max <= targetValue)
        .reduce((sum, bin) => sum + bin.count, 0);

    const currentBin = bins.find((bin) => bin.bin_min <= targetValue && bin.bin_max > targetValue)
        ?? bins[bins.length - 1];
    const partialCount = currentBin
        ? fractionWithinBin(currentBin, targetValue, scaleType) * currentBin.count
        : 0;
    const percentile = Math.round(((playersBelow + partialCount) / totalPopulation) * 100);

    if (percentile <= 50) {
        return `Bottom ${Math.max(1, percentile)}%`;
    }

    return `Top ${Math.max(1, 100 - percentile)}%`;
};

const appendLabelPill = (
    svg: GroupSelection,
    x: number,
    y: number,
    segments: Array<{ text: string; fill: string; weight: string; fontSize: string }>,
    theme: ChartTheme = 'light',
) => {
    const labelGroup = svg.append('g').attr('transform', `translate(${x}, ${y})`);
    const labelText = labelGroup.append('text')
        .attr('text-anchor', 'middle')
        .attr('dominant-baseline', 'auto');

    segments.forEach((segment, index) => {
        labelText.append('tspan')
            .attr('dx', index === 0 ? 0 : 4)
            .style('font-size', segment.fontSize)
            .style('font-weight', segment.weight)
            .style('fill', segment.fill)
            .text(segment.text);
    });

    const textNode = labelText.node();
    if (!textNode) {
        return;
    }

    const bbox = textNode.getBBox();
    const c = chartColors[theme];
    labelGroup.insert('rect', 'text')
        .attr('x', bbox.x - 6)
        .attr('y', bbox.y - 2)
        .attr('width', bbox.width + 12)
        .attr('height', bbox.height + 4)
        .attr('rx', 4)
        .attr('fill', theme === 'dark' ? 'rgba(22, 27, 34, 0.92)' : 'rgba(255, 255, 255, 0.92)')
        .attr('stroke', c.separator)
        .attr('stroke-width', 1);
};

const appendLegend = (
    svg: GroupSelection,
    width: number,
    items: Array<{ label: string; color: string; dashed?: boolean }>,
    theme: ChartTheme = 'light',
) => {
    const legend = svg.append('g').attr('transform', `translate(${Math.max(0, width - 190)}, 6)`);

    items.forEach((item, index) => {
        const row = legend.append('g').attr('transform', `translate(0, ${index * 16})`);

        row.append('line')
            .attr('x1', 0)
            .attr('x2', 16)
            .attr('y1', 0)
            .attr('y2', 0)
            .attr('stroke', item.color)
            .attr('stroke-width', 2)
            .attr('stroke-dasharray', item.dashed ? '4,3' : null);

        row.append('text')
            .attr('x', 22)
            .attr('y', 3)
            .style('font-size', '10px')
            .style('fill', chartColors[theme].labelText)
            .text(item.label);
    });
};

const drawDistribution = (
    containerElement: HTMLDivElement,
    primaryPayload: DistributionPayload,
    primaryValue: number,
    overlayPayload: DistributionPayload | null,
    overlayValue: number | null,
    svgWidth: number,
    svgHeight: number,
    gradientId: string,
    theme: ChartTheme = 'light',
) => {
    const c = chartColors[theme];
    const compact = svgWidth < 480;
    const margin = compact
        ? { top: 22, right: 6, bottom: 28, left: 30 }
        : { top: 22, right: 14, bottom: 28, left: 42 };
    const axisFontSize = compact ? '9px' : '10px';
    const width = svgWidth - margin.left - margin.right;
    const height = svgHeight - margin.top - margin.bottom;

    const container = d3.select(containerElement);
    container.selectAll('*').remove();

    const svg = container
        .append('svg')
        .attr('width', svgWidth)
        .attr('height', svgHeight)
        .append('g')
        .attr('transform', `translate(${margin.left}, ${margin.top})`);

    if (!primaryPayload.bins.length) {
        svg.append('text')
            .attr('x', 0)
            .attr('y', 16)
            .style('fill', c.labelText)
            .style('font-size', '12px')
            .text('No distribution data available.');
        return;
    }

    const primaryPoints = primaryPayload.bins.map((bin) => ({
        value: midpointForBin(bin, primaryPayload.scale),
        count: bin.count,
    }));
    const overlayPoints = overlayPayload && overlayPayload.scale === primaryPayload.scale
        ? overlayPayload.bins.map((bin) => ({
            value: midpointForBin(bin, overlayPayload.scale),
            count: bin.count,
        }))
        : [];

    const xDomain = combinedDomain(primaryPayload, primaryValue, overlayPayload, overlayValue);
    const x = buildXScale(primaryPayload, xDomain, width);
    const yMax = d3.max([
        d3.max(primaryPoints, (point: DistributionPoint) => point.count) || 0,
        d3.max(overlayPoints, (point: DistributionPoint) => point.count) || 0,
    ]) || 1;
    const y = d3.scaleLinear()
        .domain([0, yMax * 1.08])
        .range([height, 0]);

    const xAxis = primaryPayload.scale === 'log'
        ? d3.axisBottom(x as LogScale)
            .tickValues(domainTickValues(primaryPayload, overlayPayload, xDomain)
                .filter((_, index, edges) => edges.length <= 7 || index % 2 === 0))
            .tickFormat((value: number) => formatAxisTick(primaryPayload, Number(value)))
            .tickSizeOuter(0)
        : d3.axisBottom(x as LinearScale)
            .ticks(8)
            .tickFormat((value: number) => formatAxisTick(primaryPayload, Number(value)))
            .tickSizeOuter(0);

    svg.append('g')
        .attr('transform', `translate(0, ${height})`)
        .style('color', c.axisText)
        .call(xAxis)
        .selectAll('text')
        .style('font-size', axisFontSize);

    svg.append('g')
        .style('color', c.axisText)
        .call(d3.axisLeft(y).ticks(3).tickFormat((value: number) => d3.format(',')(Number(value))).tickSizeOuter(0))
        .selectAll('text')
        .style('font-size', axisFontSize);

    svg.append('text')
        .attr('x', width / 2)
        .attr('y', height + (compact ? 24 : 32))
        .attr('text-anchor', 'middle')
        .style('fill', c.labelText)
        .style('font-size', axisFontSize)
        .text(primaryPayload.x_label);

    const defs = svg.append('defs');
    const gradient = defs.append('linearGradient')
        .attr('id', gradientId)
        .attr('x1', '0%')
        .attr('x2', '100%')
        .attr('y1', '0%')
        .attr('y2', '0%');

    if (primaryPayload.metric === 'win_rate') {
        const [domainMin, domainMax] = xDomain;
        const gradientStops = [domainMin, ...WOWS_WR_BREAKPOINTS, domainMax]
            .filter((value, index, values) => value >= domainMin && value <= domainMax && values.indexOf(value) === index)
            .sort((left, right) => left - right);

        gradientStops.forEach((stopValue) => {
            gradient.append('stop')
                .attr('offset', `${((stopValue - domainMin) / (domainMax - domainMin)) * 100}%`)
                .attr('stop-color', selectColorByWR(stopValue, theme))
                .attr('stop-opacity', theme === 'dark' ? 0.32 : 0.24);
        });
    } else {
        gradient.append('stop')
            .attr('offset', '0%')
            .attr('stop-color', metricLineColor(primaryPayload.metric, theme))
            .attr('stop-opacity', theme === 'dark' ? 0.34 : 0.26);

        gradient.append('stop')
            .attr('offset', '100%')
            .attr('stop-color', metricLineColor(primaryPayload.metric, theme))
            .attr('stop-opacity', theme === 'dark' ? 0.12 : 0.08);
    }

    const area = d3.area()
        .x((point: DistributionPoint) => x(point.value))
        .y0(height)
        .y1((point: DistributionPoint) => y(point.count))
        .curve(d3.curveBasis);

    svg.append('path')
        .datum(primaryPoints)
        .attr('fill', `url(#${gradientId})`)
        .attr('d', area);

    const line = d3.line()
        .x((point: DistributionPoint) => x(point.value))
        .y((point: DistributionPoint) => y(point.count))
        .curve(d3.curveBasis);

    svg.append('path')
        .datum(primaryPoints)
        .attr('fill', 'none')
        .attr('stroke', metricLineColor(primaryPayload.metric, theme))
        .attr('stroke-width', 2)
        .attr('d', line);

    if (primaryPayload.metric === 'win_rate') {
        WOWS_WR_BREAKPOINTS
            .filter((breakpoint) => breakpoint >= primaryPayload.bins[0].bin_min && breakpoint <= primaryPayload.bins[primaryPayload.bins.length - 1].bin_max)
            .forEach((breakpoint) => {
                const breakpointCount = interpolateCountAtValue(primaryPoints, breakpoint, primaryPayload.scale);
                svg.append('line')
                    .attr('x1', x(breakpoint))
                    .attr('x2', x(breakpoint))
                    .attr('y1', y(breakpointCount))
                    .attr('y2', height)
                    .attr('stroke', selectColorByWR(breakpoint, theme))
                    .attr('stroke-width', 1)
                    .attr('opacity', 0.9);
            });
    }

    const clampedPrimaryValue = Math.max(primaryPayload.bins[0].bin_min, Math.min(primaryPayload.bins[primaryPayload.bins.length - 1].bin_max, primaryValue));
    const primaryCount = interpolateCountAtValue(primaryPoints, clampedPrimaryValue, primaryPayload.scale);
    const primaryX = x(clampedPrimaryValue);
    const primaryColor = metricValueColor(primaryPayload.metric, primaryValue, theme);
    const primaryPercentile = percentileLabelForValue(
        primaryPayload.bins,
        clampedPrimaryValue,
        primaryPayload.scale,
        primaryPayload.tracked_population,
    );

    svg.append('line')
        .attr('x1', primaryX)
        .attr('x2', primaryX)
        .attr('y1', y(primaryCount))
        .attr('y2', height)
        .attr('stroke', primaryColor)
        .attr('stroke-width', 2)
        .attr('stroke-dasharray', '4,3');

    svg.append('circle')
        .attr('cx', primaryX)
        .attr('cy', y(primaryCount))
        .attr('r', 5)
        .attr('fill', primaryColor)
        .attr('stroke', c.chartBg)
        .attr('stroke-width', 1.5);

    appendLabelPill(svg, primaryX, y(primaryCount) - 12, [
        {
            text: formatMetricValue(primaryPayload, primaryValue),
            fill: primaryColor,
            weight: '700',
            fontSize: '11px',
        },
        {
            text: primaryPercentile,
            fill: c.labelText,
            weight: '400',
            fontSize: '10px',
        },
    ], theme);

    if (overlayPayload && overlayPayload.scale === primaryPayload.scale && overlayValue != null && overlayPayload.bins.length) {
        const overlayColor = metricValueColor(overlayPayload.metric, overlayValue, theme);
        const clampedOverlayValue = Math.max(overlayPayload.bins[0].bin_min, Math.min(overlayPayload.bins[overlayPayload.bins.length - 1].bin_max, overlayValue));
        const overlayCount = interpolateCountAtValue(overlayPoints, clampedOverlayValue, overlayPayload.scale);
        const overlayX = x(clampedOverlayValue);
        const overlayPercentile = percentileLabelForValue(
            overlayPayload.bins,
            clampedOverlayValue,
            overlayPayload.scale,
            overlayPayload.tracked_population,
        );

        svg.append('path')
            .datum(overlayPoints)
            .attr('fill', 'none')
            .attr('stroke', metricLineColor(overlayPayload.metric, theme))
            .attr('stroke-width', 2)
            .attr('stroke-dasharray', '5,4')
            .attr('opacity', 0.95)
            .attr('d', line);

        svg.append('line')
            .attr('x1', overlayX)
            .attr('x2', overlayX)
            .attr('y1', y(overlayCount))
            .attr('y2', height)
            .attr('stroke', overlayColor)
            .attr('stroke-width', 1.5)
            .attr('stroke-dasharray', '2,3');

        svg.append('circle')
            .attr('cx', overlayX)
            .attr('cy', y(overlayCount))
            .attr('r', 4.5)
            .attr('fill', c.chartBg)
            .attr('stroke', overlayColor)
            .attr('stroke-width', 2);

        appendLabelPill(svg, overlayX, Math.min(height - 4, y(overlayCount) + 24), [
            {
                text: `${formatMetricValue(overlayPayload, overlayValue)} survival`,
                fill: overlayColor,
                weight: '700',
                fontSize: '10px',
            },
            {
                text: overlayPercentile,
                fill: c.labelText,
                weight: '400',
                fontSize: '10px',
            },
        ], theme);

        appendLegend(svg, width, [
            { label: primaryPayload.label, color: metricLineColor(primaryPayload.metric, theme) },
            { label: overlayPayload.label, color: metricLineColor(overlayPayload.metric, theme), dashed: true },
        ], theme);
    }
};

const drawErrorState = (containerElement: HTMLDivElement, message: string, theme: ChartTheme = 'light') => {
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
        .style('fill', chartColors[theme].labelText)
        .style('font-size', '12px')
        .text(message);
};

const fetchDistribution = async (metric: DistributionMetric, signal: AbortSignal): Promise<DistributionPayload> => {
    const { data } = await fetchSharedJson<DistributionPayload>(`/api/fetch/player_distribution/${metric}/`, {
        label: `Player distribution ${metric}`,
        ttlMs: PLAYER_ROUTE_PANEL_FETCH_TTL_MS,
    });

    if (signal.aborted) {
        throw new DOMException('Aborted', 'AbortError');
    }

    return data;
};

const PopulationDistributionSVG: React.FC<PopulationDistributionSVGProps> = ({
    primaryMetric,
    primaryValue,
    overlayMetric,
    overlayValue,
    svgWidth = 600,
    svgHeight = 184,
    theme = 'light',
}) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const chartId = useId().replace(/:/g, '-');

    useEffect(() => {
        const containerElement = containerRef.current;
        if (!containerElement || primaryValue == null) {
            return;
        }

        const abortController = new AbortController();
        let cachedPrimary: DistributionPayload | null = null;
        let cachedOverlay: DistributionPayload | null = null;

        const draw = () => {
            if (!cachedPrimary) return;
            const resolvedWidth = Math.min(svgWidth, Math.max(containerElement.clientWidth || svgWidth, 280));
            drawDistribution(
                containerElement,
                cachedPrimary,
                primaryValue,
                cachedOverlay,
                overlayValue ?? null,
                resolvedWidth,
                svgHeight,
                `${chartId}-${primaryMetric}-gradient`,
                theme,
            );
        };

        const onResize = () => draw();
        window.addEventListener('resize', onResize);

        const load = async () => {
            try {
                const [primaryPayload, overlayPayload] = await Promise.all([
                    fetchDistribution(primaryMetric, abortController.signal),
                    overlayMetric ? fetchDistribution(overlayMetric, abortController.signal) : Promise.resolve(null),
                ]);

                if (abortController.signal.aborted) {
                    return;
                }

                cachedPrimary = primaryPayload;
                cachedOverlay = overlayPayload;
                draw();
            } catch {
                if (!abortController.signal.aborted) {
                    drawErrorState(containerElement, 'Unable to load distribution chart.', theme);
                }
            }
        };

        load();
        return () => {
            abortController.abort();
            window.removeEventListener('resize', onResize);
        };
    }, [chartId, overlayMetric, overlayValue, primaryMetric, primaryValue, svgHeight, svgWidth, theme]);

    return (
        <div ref={containerRef}>
            <div
                className="flex animate-pulse items-center justify-center rounded-md border border-[var(--border)] bg-[var(--bg-surface)] text-sm text-[var(--accent-light)]"
                style={{ minHeight: svgHeight }}
            >
                Loading distribution…
            </div>
        </div>
    );
};

export default PopulationDistributionSVG;