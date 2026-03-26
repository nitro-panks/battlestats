import React, { useEffect, useMemo, useRef, useState } from 'react';
import * as d3 from 'd3';

interface ClanDatum {
    clan_id: number;
    name: string;
    tag: string;
    members_count: number;
    clan_wr: number | null;
    total_battles: number | null;
}

interface LandingClanSVGProps {
    clans: ClanDatum[];
    heatmapClans?: ClanDatum[];
    onSelectClan?: (clan: ClanDatum) => void;
    svgHeight?: number;
}

interface PlotDatum {
    clan_id: number;
    name: string;
    tag: string;
    members_count: number;
    clan_wr: number;
    total_battles: number;
    total_wins: number;
}

const expandDomain = (minValue: number, maxValue: number, paddingRatio: number, floor?: number, ceiling?: number): [number, number] => {
    const span = Math.max(maxValue - minValue, 1);
    const padding = span * paddingRatio;
    const paddedMin = floor == null ? minValue - padding : Math.max(floor, minValue - padding);
    const paddedMax = ceiling == null ? maxValue + padding : Math.min(ceiling, maxValue + padding);

    if (paddedMin === paddedMax) {
        return [paddedMin - 1, paddedMax + 1];
    }

    return [paddedMin, paddedMax];
};

const selectLandingClanColorByWR = (winRatio: number): string => {
    if (winRatio > 65) return '#810c9e';
    if (winRatio >= 60) return '#D042F3';
    if (winRatio >= 56) return '#3182bd';
    if (winRatio >= 54) return '#74c476';
    if (winRatio >= 52) return '#a1d99b';
    if (winRatio >= 50) return '#fed976';
    if (winRatio >= 45) return '#fd8d3c';
    if (winRatio >= 40) return '#e6550d';
    return '#a50f15';
};

const formatCompactCount = (value: number): string => d3.format('~s')(value).replace('G', 'B');

const drawLandingClanChart = (
    containerElement: HTMLDivElement,
    clans: ClanDatum[],
    onSelectClan: LandingClanSVGProps['onSelectClan'],
    containerWidth: number,
    svgHeight: number,
) => {
    const margin = { top: 56, right: 16, bottom: 32, left: 48 };
    const width = containerWidth - margin.left - margin.right;
    const height = svgHeight - margin.top - margin.bottom;

    const container = d3.select(containerElement);
    container.selectAll('*').remove();

    const plotData: PlotDatum[] = clans
        .filter((clan) => clan.clan_wr != null && clan.total_battles != null && clan.total_battles > 0)
        .map((clan) => ({
            ...clan,
            clan_wr: clan.clan_wr as number,
            total_battles: clan.total_battles as number,
            total_wins: (clan.total_battles as number) * ((clan.clan_wr as number) / 100),
        }));

    const svgRoot = container
        .append('svg')
        .attr('width', width + margin.left + margin.right)
        .attr('height', height + margin.top + margin.bottom);

    const svg = svgRoot
        .append('g')
        .attr('transform', `translate(${margin.left}, ${margin.top})`);

    if (plotData.length === 0) {
        svg.append('text')
            .attr('x', 0)
            .attr('y', 16)
            .style('font-size', '12px')
            .style('fill', '#6b7280')
            .text('No clan chart data available.');
        return;
    }

    const battlesExtent = d3.extent(plotData, (datum: PlotDatum) => datum.total_battles) as [number, number];
    const wrExtent = d3.extent(plotData, (datum: PlotDatum) => datum.clan_wr) as [number, number];
    const [xMin, xMax] = expandDomain(battlesExtent[0], battlesExtent[1], 0.1, 0);
    const [yMin, yMax] = expandDomain(wrExtent[0], wrExtent[1], 0.08, 0, 100);

    const x = d3.scaleLinear().domain([xMin, xMax]).range([0, width]);
    const y = d3.scaleLinear().domain([yMin, yMax]).range([height, 0]);

    svg.append('g')
        .attr('class', 'landing-clan-x-grid')
        .attr('transform', `translate(0, ${height})`)
        .call(d3.axisBottom(x).ticks(6).tickSize(-height).tickFormat(() => ''));
    svg.select('.landing-clan-x-grid')?.select('.domain')?.remove();
    svg.selectAll('.landing-clan-x-grid line')
        .style('stroke', '#dbeafe')
        .style('stroke-width', 1);

    svg.append('g')
        .attr('class', 'landing-clan-y-grid')
        .call(d3.axisLeft(y).ticks(5).tickSize(-width).tickFormat(() => ''));
    svg.select('.landing-clan-y-grid')?.select('.domain')?.remove();
    svg.selectAll('.landing-clan-y-grid line')
        .style('stroke', '#eff6ff')
        .style('stroke-width', 1);

    svg.append('g')
        .attr('transform', `translate(0, ${height})`)
        .style('color', '#64748b')
        .call(d3.axisBottom(x).ticks(6).tickFormat((value: number) => formatCompactCount(value)).tickSizeOuter(0))
        .selectAll('text')
        .style('font-size', '10px');

    svg.append('g')
        .style('color', '#64748b')
        .call(d3.axisLeft(y).ticks(5).tickFormat((value: number) => `${value.toFixed(0)}%`).tickSizeOuter(0))
        .selectAll('text')
        .style('font-size', '10px');

    svg.append('text')
        .attr('class', 'axisLabel')
        .style('font-size', '10px')
        .style('fill', '#6b7280')
        .attr('text-anchor', 'middle')
        .attr('x', width / 2)
        .attr('y', height + 30)
        .text('Total Battles Played');

    svg.append('text')
        .attr('class', 'axisLabel')
        .style('font-size', '10px')
        .style('fill', '#6b7280')
        .attr('text-anchor', 'middle')
        .attr('transform', 'rotate(-90)')
        .attr('x', -height / 2)
        .attr('y', -34)
        .text('Clan WR');

    const showDetails = (datum: PlotDatum) => {
        const detailGroup = svgRoot
            .append('g')
            .classed('details', true)
            .attr('transform', `translate(${margin.left}, 14)`);

        const titleText = detailGroup.append('text')
            .attr('x', 0)
            .attr('y', 0)
            .style('font-size', '11px')
            .attr('font-weight', '700')
            .style('fill', '#084594')
            .text(`[${datum.tag}] ${datum.name}`);

        const metaText = detailGroup.append('text')
            .attr('x', 0)
            .attr('y', 18)
            .style('font-size', '10px')
            .attr('font-weight', '400')
            .style('fill', '#6b7280');

        metaText.append('tspan')
            .text(`${Math.round(datum.total_wins).toLocaleString()} Wins`);

        metaText.append('tspan')
            .attr('dx', 12)
            .text(`${datum.total_battles.toLocaleString()} Battles`);

        metaText.append('tspan')
            .attr('dx', 12)
            .text(`${datum.clan_wr.toFixed(1)}% WR`);

        metaText.append('tspan')
            .attr('dx', 12)
            .text(`${datum.members_count} Members`);

        const titleNode = titleText.node();
        const metaNode = metaText.node();
        if (!titleNode || !metaNode) {
            return;
        }

        const titleBox = titleNode.getBBox();
        const metaBox = metaNode.getBBox();
        const minX = Math.min(titleBox.x, metaBox.x);
        const minY = Math.min(titleBox.y, metaBox.y);
        const maxX = Math.max(titleBox.x + titleBox.width, metaBox.x + metaBox.width);
        const maxY = Math.max(titleBox.y + titleBox.height, metaBox.y + metaBox.height);

        detailGroup.insert('rect', 'text')
            .attr('x', minX - 10)
            .attr('y', minY - 6)
            .attr('width', maxX - minX + 20)
            .attr('height', maxY - minY + 12)
            .attr('rx', 6)
            .attr('fill', 'rgba(255, 255, 255, 0.94)');
    };

    const hideDetails = () => {
        svgRoot.select('.details').remove();
    };

    svg.append('g')
        .selectAll('dot')
        .data(plotData)
        .enter()
        .append('circle')
        .attr('cx', (datum: PlotDatum) => x(datum.total_battles))
        .attr('cy', (datum: PlotDatum) => y(datum.clan_wr))
        .attr('r', 5)
        .style('cursor', onSelectClan ? 'pointer' : 'default')
        .attr('fill', (datum: PlotDatum) => selectLandingClanColorByWR(datum.clan_wr))
        .attr('stroke', '#444')
        .attr('stroke-width', 1.25);

    const circles = svg.selectAll('circle');

    circles
        .on('click', function (_event: MouseEvent, datum: PlotDatum) {
            if (onSelectClan) {
                onSelectClan(datum);
            }
        })
        .on('mouseover', function (this: SVGCircleElement, _event: MouseEvent, datum: PlotDatum) {
            showDetails(datum);
            d3.select(this).transition().duration(50).attr('fill', '#bcbddc');
        })
        .on('mouseout', function (this: SVGCircleElement, _event: MouseEvent, datum: PlotDatum) {
            hideDetails();
            d3.select(this).transition().duration(50).attr('fill', selectLandingClanColorByWR(datum.clan_wr));
        });
};

const LandingClanSVG: React.FC<LandingClanSVGProps> = ({
    clans,
    heatmapClans,
    onSelectClan,
    svgHeight = 300,
}) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const [containerWidth, setContainerWidth] = useState(320);
    const [isChartReady, setIsChartReady] = useState(false);
    const chartSignature = useMemo(() => JSON.stringify({
        clans: clans.map((clan) => [clan.clan_id, clan.clan_wr, clan.total_battles, clan.members_count]),
        svgHeight,
    }), [clans, svgHeight]);

    useEffect(() => {
        if (!containerRef.current) return;
        const observer = new ResizeObserver((entries) => {
            for (const entry of entries) {
                setContainerWidth(entry.contentRect.width);
            }
        });
        observer.observe(containerRef.current);
        setContainerWidth(containerRef.current.clientWidth);
        return () => observer.disconnect();
    }, []);

    useEffect(() => {
        if (!containerRef.current || containerWidth < 100) return;
        setIsChartReady(false);
        drawLandingClanChart(containerRef.current, clans, onSelectClan, containerWidth, svgHeight);
        const frameId = window.requestAnimationFrame(() => {
            setIsChartReady(true);
        });
        return () => window.cancelAnimationFrame(frameId);
    }, [chartSignature, clans, containerWidth, onSelectClan, svgHeight]);

    return <div ref={containerRef} className={`pr-8 transition-opacity duration-150 md:pr-16 ${isChartReady ? 'opacity-100' : 'opacity-0'}`}></div>;
};

export default LandingClanSVG;
