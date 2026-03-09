import React, { useEffect } from 'react';
import * as d3 from 'd3';

interface TypeSVGProps {
    playerId: number;
}

const selectTypeColorByWr = (winRatio: number): string => {
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

const drawTypePlot = (playerId: number) => {
    const container = document.getElementById('type_svg_container');
    if (!container) {
        return;
    }

    while (container.firstChild) {
        container.removeChild(container.firstChild);
    }

    const totalTypeSvgWidth = 500;
    const totalTypeSvgHeight = 210;
    const typeSvgMargin = { top: 44, right: 20, bottom: 50, left: 96 };
    const typeSvgWidth = totalTypeSvgWidth - typeSvgMargin.left - typeSvgMargin.right;
    const typeSvgHeight = totalTypeSvgHeight - typeSvgMargin.top - typeSvgMargin.bottom;

    const typeSvg = d3.select('#type_svg_container')
        .append('svg')
        .attr('width', totalTypeSvgWidth)
        .attr('height', totalTypeSvgHeight)
        .append('g')
        .attr('transform', `translate(${typeSvgMargin.left}, ${typeSvgMargin.top})`);

    const path = `http://localhost:8888/api/fetch/type_data/${playerId}`;

    const showTypeDetails = (datum: { pvp_battles: number; wins: number }) => {
        const detailX = 480;
        const detailY = 26;
        const winPercentage = ((datum.wins / datum.pvp_battles) * 100).toFixed(2);

        const detailGroup = d3.select('#type_svg_container').select('svg').append('g')
            .classed('details', true);

        detailGroup.append('text')
            .attr('x', detailX)
            .attr('y', detailY)
            .style('font-size', '12px')
            .attr('text-anchor', 'end')
            .attr('font-weight', '700')
            .text(`${datum.pvp_battles} Battles • ${winPercentage}% Win Rate`);
    };

    const hideTypeDetails = () => {
        d3.select('#type_svg_container').select('.details').remove();
    };

    fetch(path)
        .then(response => response.json())
        .then(data => {
            const max = d3.max(data, (datum: any) => +datum.pvp_battles);

            typeSvg.selectAll('*').remove();

            const x = d3.scaleLinear()
                .domain([0, max])
                .range([1, typeSvgWidth]);
            typeSvg.append('g')
                .attr('transform', `translate(0, ${typeSvgHeight})`)
                .call(d3.axisBottom(x))
                .selectAll('text')
                .attr('transform', 'translate(-10,0)rotate(-45)')
                .style('text-anchor', 'end');

            const y = d3.scaleBand()
                .range([0, typeSvgHeight])
                .domain(data.map((datum: any) => datum.ship_type))
                .padding(.1);
            typeSvg.append('g')
                .call(d3.axisLeft(y));

            typeSvg.append('text')
                .attr('x', typeSvgWidth)
                .attr('y', typeSvgHeight + 44)
                .attr('text-anchor', 'end')
                .style('font-size', '10px')
                .style('fill', '#6b7280')
                .text('Random Battles');

            typeSvg.append('text')
                .attr('transform', 'rotate(-90)')
                .attr('x', -2)
                .attr('y', -76)
                .attr('text-anchor', 'end')
                .style('font-size', '10px')
                .style('fill', '#6b7280')
                .text('Ship Type');

            const rectNodes = typeSvg.selectAll('.rect')
                .data(data)
                .enter()
                .append('g')
                .classed('rect', true);

            rectNodes.append('rect')
                .attr('x', x(0))
                .attr('y', (datum: any) => (y(datum.ship_type) ?? 0) + 3)
                .attr('width', (datum: any) => x(datum.pvp_battles))
                .attr('height', y.bandwidth() * .7)
                .attr('fill', '#d9d9d9');

            rectNodes.append('rect')
                .attr('x', x(0))
                .attr('y', (datum: any) => y(datum.ship_type) ?? 0)
                .attr('width', (datum: any) => x(datum.wins))
                .attr('height', y.bandwidth())
                .style('stroke', '#444')
                .style('stroke-width', 0.75)
                .attr('fill', (datum: any) => selectTypeColorByWr(datum.win_ratio))
                .on('mouseover', function (this: SVGRectElement, _event: MouseEvent, datum: any) {
                    showTypeDetails(datum);
                    d3.select(this).transition()
                        .duration(50)
                        .attr('fill', '#bcbddc');
                })
                .on('mouseout', function (this: SVGRectElement, _event: MouseEvent, datum: any) {
                    hideTypeDetails();
                    d3.select(this).transition()
                        .duration(50)
                        .attr('fill', selectTypeColorByWr(datum.win_ratio));
                });
        });
};

const TypeSVG: React.FC<TypeSVGProps> = ({ playerId }) => {
    useEffect(() => {
        drawTypePlot(playerId);
    }, [playerId]);

    return <div id="type_svg_container"></div>;
};

export default TypeSVG;