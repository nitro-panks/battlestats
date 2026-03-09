import React, { useEffect } from 'react';
import * as d3 from 'd3';

interface TierSVGProps {
    playerId: number;
}

const selectTierColorByWr = (winRatio: number): string => {
    if (winRatio > 0.65) return '#810c9e';
    if (winRatio >= 0.60) return '#D042F3';
    if (winRatio >= 0.56) return '#3182bd';
    if (winRatio >= 0.54) return '#74c476';
    if (winRatio >= 0.52) return '#a1d99b';
    if (winRatio >= 0.50) return '#fed976';
    if (winRatio >= 0.45) return '#fd8d3c';
    if (winRatio >= 0.40) return '#e6550d';
    return '#a50f15';
};

const drawTierPlot = (playerId: number) => {
    const container = document.getElementById('tier_svg_container');
    if (!container) {
        return;
    }

    d3.select(container).selectAll('*').remove();

    const totalSvgWidth = 500;
    const totalSvgHeight = 334;
    const tierSvgMargin = { top: 44, right: 20, bottom: 50, left: 70 };
    const svgWidth = totalSvgWidth - tierSvgMargin.left - tierSvgMargin.right;
    const svgHeight = totalSvgHeight - tierSvgMargin.top - tierSvgMargin.bottom;

    const svg = d3.select('#tier_svg_container')
        .append('svg')
        .attr('width', totalSvgWidth)
        .attr('height', totalSvgHeight)
        .append('g')
        .attr('transform', `translate(${tierSvgMargin.left}, ${tierSvgMargin.top})`);

    const path = `http://localhost:8888/api/fetch/tier_data/${playerId}`;

    const showTierDetails = (datum: { pvp_battles: number; wins: number }) => {
        const detailX = 480;
        const detailY = 26;
        const winPercentage = ((datum.wins / datum.pvp_battles) * 100).toFixed(2);

        const detailGroup = d3.select('#tier_svg_container').select('svg').append('g')
            .classed('details', true);

        detailGroup.append('text')
            .attr('x', detailX)
            .attr('y', detailY)
            .style('font-size', '12px')
            .attr('text-anchor', 'end')
            .attr('font-weight', '700')
            .text(`${datum.pvp_battles} Battles • ${winPercentage}% Win Rate`);
    };

    const hideTierDetails = () => {
        d3.select('#tier_svg_container').select('.details').remove();
    };

    fetch(path)
        .then(response => response.json())
        .then(data => {
            const max = d3.max(data, (datum: any) => +datum.pvp_battles);

            svg.selectAll('*').remove();

            const x = d3.scaleLinear()
                .domain([0, max])
                .range([1, svgWidth]);
            svg.append('g')
                .attr('transform', `translate(0, ${svgHeight})`)
                .call(d3.axisBottom(x))
                .selectAll('text')
                .attr('transform', 'translate(-10,0)rotate(-45)')
                .style('text-anchor', 'end');

            const y = d3.scaleBand()
                .range([0, svgHeight])
                .domain(data.map((datum: any) => datum.ship_tier))
                .padding(.1);
            svg.append('g')
                .call(d3.axisLeft(y));

            svg.append('text')
                .attr('x', svgWidth)
                .attr('y', svgHeight + 44)
                .attr('text-anchor', 'end')
                .style('font-size', '10px')
                .style('fill', '#6b7280')
                .text('Random Battles');

            svg.append('text')
                .attr('transform', 'rotate(-90)')
                .attr('x', -2)
                .attr('y', -52)
                .attr('text-anchor', 'end')
                .style('font-size', '10px')
                .style('fill', '#6b7280')
                .text('Ship Tier');

            const rectNodes = svg.selectAll('.rect')
                .data(data)
                .enter()
                .append('g')
                .classed('rect', true);

            rectNodes.append('rect')
                .attr('x', x(0))
                .attr('y', (datum: any) => (y(datum.ship_tier) ?? 0) + 3)
                .attr('width', (datum: any) => x(datum.pvp_battles))
                .attr('height', y.bandwidth() * .7)
                .attr('fill', '#d9d9d9');

            rectNodes.append('rect')
                .attr('x', x(0))
                .attr('y', (datum: any) => y(datum.ship_tier) ?? 0)
                .attr('width', (datum: any) => x(datum.wins))
                .attr('height', y.bandwidth())
                .style('stroke', '#444')
                .style('stroke-width', 0.75)
                .attr('fill', (datum: any) => selectTierColorByWr(datum.win_ratio))
                .on('mouseover', function (this: SVGRectElement, _event: MouseEvent, datum: any) {
                    showTierDetails(datum);
                    d3.select(this).transition()
                        .duration(50)
                        .attr('fill', '#bcbddc');
                })
                .on('mouseout', function (this: SVGRectElement, _event: MouseEvent, datum: any) {
                    hideTierDetails();
                    d3.select(this).transition()
                        .duration(50)
                        .attr('fill', selectTierColorByWr(datum.win_ratio));
                });
        });
};

const TierSVG: React.FC<TierSVGProps> = ({ playerId }) => {
    useEffect(() => {
        drawTierPlot(playerId);
    }, [playerId]);

    return <div id="tier_svg_container"></div>;
};

export default TierSVG;