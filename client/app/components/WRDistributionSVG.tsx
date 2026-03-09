import React, { useEffect, useRef } from 'react';
import * as d3 from 'd3';

interface WRDistributionProps {
    playerWR: number;
    svgWidth?: number;
    svgHeight?: number;
}

interface WRBin {
    wr_min: number;
    wr_max: number;
    count: number;
}

const selectColorByWR = (winRatio: number): string => {
    if (winRatio > 65) return '#810c9e';
    if (winRatio >= 60) return '#D042F3';
    if (winRatio >= 56) return '#3182bd';
    if (winRatio >= 54) return '#74c476';
    if (winRatio >= 52) return '#a1d99b';
    if (winRatio >= 50) return '#fed976';
    if (winRatio >= 45) return '#fd8d3c';
    return '#a50f15';
};

interface WRPoint {
    wr: number;
    count: number;
}

const drawDistribution = (
    containerElement: HTMLDivElement,
    playerWR: number,
    svgWidth: number,
    svgHeight: number,
) => {
    const margin = { top: 24, right: 20, bottom: 36, left: 50 };
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

    fetch('http://localhost:8888/api/fetch/wr_distribution/')
        .then(response => {
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            return response.json();
        })
        .then((bins: WRBin[]) => {
            if (!Array.isArray(bins) || bins.length === 0) {
                svg.append('text')
                    .attr('x', 0).attr('y', 16)
                    .style('fill', '#6b7280').style('font-size', '12px')
                    .text('No distribution data available.');
                return;
            }

            const points = bins.map(b => ({
                wr: (b.wr_min + b.wr_max) / 2,
                count: b.count,
            }));

            const totalPlayers = d3.sum(points, (d: WRPoint) => d.count);

            const x = d3.scaleLinear()
                .domain([bins[0].wr_min, bins[bins.length - 1].wr_max])
                .range([0, width]);

            const yMax = d3.max(points, (d: WRPoint) => d.count) || 1;
            const y = d3.scaleLinear()
                .domain([0, yMax * 1.08])
                .range([height, 0]);

            // x-axis
            svg.append('g')
                .attr('transform', `translate(0, ${height})`)
                .style('color', '#6b7280')
                .call(d3.axisBottom(x).ticks(8).tickFormat((d: number) => `${d}%`).tickSizeOuter(0))
                .selectAll('text')
                .style('font-size', '10px');

            // y-axis
            svg.append('g')
                .style('color', '#6b7280')
                .call(d3.axisLeft(y).ticks(5).tickFormat((d: number) => d3.format(',')(d)).tickSizeOuter(0))
                .selectAll('text')
                .style('font-size', '10px');

            // x-axis label
            svg.append('text')
                .attr('x', width / 2)
                .attr('y', height + 32)
                .attr('text-anchor', 'middle')
                .style('fill', '#6b7280')
                .style('font-size', '10px')
                .text('Win Rate');

            // gradient fill
            const defs = svg.append('defs');
            const gradient = defs.append('linearGradient')
                .attr('id', 'wr-area-gradient')
                .attr('x1', '0%').attr('x2', '100%')
                .attr('y1', '0%').attr('y2', '0%');

            const wrStops = [35, 40, 45, 50, 52, 54, 56, 60, 65, 75];
            wrStops.forEach(wr => {
                gradient.append('stop')
                    .attr('offset', `${((wr - bins[0].wr_min) / (bins[bins.length - 1].wr_max - bins[0].wr_min)) * 100}%`)
                    .attr('stop-color', selectColorByWR(wr))
                    .attr('stop-opacity', 0.35);
            });

            // area
            const area = d3.area()
                .x((d: WRPoint) => x(d.wr))
                .y0(height)
                .y1((d: WRPoint) => y(d.count))
                .curve(d3.curveBasis);

            svg.append('path')
                .datum(points)
                .attr('fill', 'url(#wr-area-gradient)')
                .attr('d', area);

            // line
            const line = d3.line()
                .x((d: WRPoint) => x(d.wr))
                .y((d: WRPoint) => y(d.count))
                .curve(d3.curveBasis);

            svg.append('path')
                .datum(points)
                .attr('fill', 'none')
                .attr('stroke', '#4292c6')
                .attr('stroke-width', 2)
                .attr('d', line);

            // player marker
            const clampedWR = Math.max(bins[0].wr_min, Math.min(bins[bins.length - 1].wr_max, playerWR));
            const px = x(clampedWR);

            // interpolate player count at their WR for marker height
            const bisect = d3.bisector((d: WRPoint) => d.wr).left;
            const idx = bisect(points, clampedWR);
            let playerCount = 0;
            if (idx <= 0) {
                playerCount = points[0].count;
            } else if (idx >= points.length) {
                playerCount = points[points.length - 1].count;
            } else {
                const p0 = points[idx - 1];
                const p1 = points[idx];
                const t = (clampedWR - p0.wr) / (p1.wr - p0.wr);
                playerCount = p0.count + t * (p1.count - p0.count);
            }

            // vertical line
            svg.append('line')
                .attr('x1', px).attr('x2', px)
                .attr('y1', y(playerCount))
                .attr('y2', height)
                .attr('stroke', selectColorByWR(playerWR))
                .attr('stroke-width', 2)
                .attr('stroke-dasharray', '4,3');

            // dot on curve
            svg.append('circle')
                .attr('cx', px)
                .attr('cy', y(playerCount))
                .attr('r', 5)
                .attr('fill', selectColorByWR(playerWR))
                .attr('stroke', '#fff')
                .attr('stroke-width', 1.5);

            // percentile calculation
            const playersBelow = bins
                .filter(b => b.wr_max <= clampedWR)
                .reduce((acc, b) => acc + b.count, 0);
            const currentBin = bins.find(b => b.wr_min <= clampedWR && b.wr_max > clampedWR);
            let partialCount = 0;
            if (currentBin) {
                const frac = (clampedWR - currentBin.wr_min) / (currentBin.wr_max - currentBin.wr_min);
                partialCount = frac * currentBin.count;
            }
            const percentile = totalPlayers > 0
                ? Math.round(((playersBelow + partialCount) / totalPlayers) * 100)
                : 0;

            // label above marker
            const labelGroup = svg.append('g')
                .attr('transform', `translate(${px}, ${y(playerCount) - 12})`);

            const labelText = labelGroup.append('text')
                .attr('text-anchor', 'middle')
                .attr('dominant-baseline', 'auto');

            labelText.append('tspan')
                .style('font-size', '11px')
                .style('font-weight', '700')
                .style('fill', selectColorByWR(playerWR))
                .text(`${playerWR}%`);

            labelText.append('tspan')
                .attr('dx', 4)
                .style('font-size', '10px')
                .style('font-weight', '400')
                .style('fill', '#6b7280')
                .text(`Top ${Math.max(1, 100 - percentile)}%`);

            // background pill behind label
            const textNode = labelText.node();
            if (textNode) {
                const bbox = textNode.getBBox();
                labelGroup.insert('rect', 'text')
                    .attr('x', bbox.x - 6)
                    .attr('y', bbox.y - 2)
                    .attr('width', bbox.width + 12)
                    .attr('height', bbox.height + 4)
                    .attr('rx', 4)
                    .attr('fill', 'rgba(255, 255, 255, 0.9)');
            }
        })
        .catch(() => {
            svg.selectAll('*').remove();
            svg.append('text')
                .attr('x', 0).attr('y', 16)
                .style('fill', '#6b7280').style('font-size', '12px')
                .text('Unable to load distribution chart.');
        });
};

const WRDistributionSVG: React.FC<WRDistributionProps> = ({ playerWR, svgWidth = 600, svgHeight = 240 }) => {
    const containerRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (containerRef.current) {
            drawDistribution(containerRef.current, playerWR, svgWidth, svgHeight);
        }
    }, [playerWR, svgWidth, svgHeight]);

    return <div ref={containerRef}></div>;
};

export default WRDistributionSVG;
