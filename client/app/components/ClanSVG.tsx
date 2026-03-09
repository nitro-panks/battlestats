import React, { useEffect, useRef } from 'react';
import * as d3 from 'd3';

interface ClanProps {
    clanId: number;
    onSelectMember?: (memberName: string) => void;
    svgWidth?: number;
    svgHeight?: number;
}

interface ClanData {
    player_name: string;
    pvp_battles: number;
    pvp_ratio: number;
}

const selectClanColorByWR = (winRatio: number) => {
    if (winRatio > 65) {
        return '#810c9e';
    }
    if (winRatio >= 60) {
        return '#D042F3';
    }
    if (winRatio >= 56) {
        return '#3182bd';
    }
    if (winRatio >= 54) {
        return '#74c476';
    }
    if (winRatio >= 52) {
        return '#a1d99b';
    }
    if (winRatio >= 50) {
        return '#fed976';
    }
    if (winRatio >= 45) {
        return '#fd8d3c';
    }
    if (winRatio >= 40) {
        return '#e6550d';
    }
    return '#a50f15';
};

const drawClanPlot = (
    containerElement: HTMLDivElement,
    clanId: number,
    onSelectMember: ClanProps['onSelectMember'],
    svgWidth: number,
    svgHeight: number,
) => {
    const margin = { top: 44, right: 16, bottom: 32, left: 38 };
    const width = svgWidth - margin.left - margin.right;
    const height = svgHeight - margin.top - margin.bottom;

    const container = d3.select(containerElement);
    container.selectAll('*').remove();

    const svg = container
        .append('svg')
        .attr('width', width + margin.left + margin.right)
        .attr('height', height + margin.top + margin.bottom)
        .append('g')
        .attr('transform', `translate(${margin.left}, ${margin.top})`);

    const filterType = 'active';
    const path = `http://localhost:8888/api/fetch/clan_data/${clanId}:${filterType}`;

    const showDetails = (datum: ClanData) => {
        const detailGroup = svg.append('g')
            .classed('details', true)
            .style('pointer-events', 'none');

        const detailText = detailGroup.append('text')
            .attr('x', 0)
            .attr('y', -16)
            .attr('dominant-baseline', 'middle');

        detailText.append('tspan')
            .style('font-size', '11px')
            .attr('font-weight', '700')
            .style('fill', '#084594')
            .text(datum.player_name);

        detailText.append('tspan')
            .attr('dx', 10)
            .style('font-size', '10px')
            .attr('font-weight', '400')
            .style('fill', '#6b7280')
            .text(`${datum.pvp_battles} Battles`);

        detailText.append('tspan')
            .attr('dx', 10)
            .style('font-size', '10px')
            .attr('font-weight', '400')
            .style('fill', '#6b7280')
            .text(`${datum.pvp_ratio}% WR`);

        const textNode = detailText.node();
        if (!textNode) {
            return;
        }

        const bbox = textNode.getBBox();
        detailGroup.insert('rect', 'text')
            .attr('x', bbox.x - 8)
            .attr('y', bbox.y - 4)
            .attr('width', bbox.width + 16)
            .attr('height', bbox.height + 8)
            .attr('rx', 6)
            .attr('fill', 'rgba(255, 255, 255, 0.92)');
    };

    const hideDetails = () => {
        svg.select('.details').remove();
    };

    fetch(path)
        .then(response => {
            if (!response.ok) {
                throw new Error(`Failed to fetch clan data: ${response.status}`);
            }
            return response.json();
        })
        .then((data: ClanData[]) => {
            if (!Array.isArray(data) || data.length === 0) {
                svg.append('text')
                    .attr('x', 0)
                    .attr('y', 16)
                    .attr('class', 'text-sm')
                    .style('fill', '#6b7280')
                    .text('No clan chart data available.');
                return;
            }

            const max = (d3.max(data, (datum: ClanData) => datum.pvp_battles) || 0) + 50;
            const ymax = (d3.max(data, (datum: ClanData) => datum.pvp_ratio) || 0) + 2;
            const ymin = (d3.min(data, (datum: ClanData) => datum.pvp_ratio) || 0) - 2;

            svg.selectAll('*').remove();

            const x = d3.scaleLinear()
                .domain([0, max])
                .range([0, width]);
            svg.append('g')
                .style('color', '#6b7280')
                .attr('transform', `translate(0, ${height})`)
                .call(d3.axisBottom(x).ticks(5).tickSizeOuter(0))
                .selectAll('text')
                .attr('transform', 'translate(-10,0)rotate(-45)')
                .style('text-anchor', 'end');

            const y = d3.scaleLinear()
                .domain([ymin, ymax])
                .range([height, 0]);
            svg.append('g')
                .style('color', '#6b7280')
                .call(d3.axisLeft(y).ticks(5).tickSizeOuter(0));

            svg.append('g')
                .selectAll('dot')
                .data(data)
                .enter()
                .append('circle')
                .attr('cx', (datum: ClanData) => x(datum.pvp_battles))
                .attr('cy', (datum: ClanData) => y(datum.pvp_ratio))
                .attr('r', 4)
                .style('stroke', '#444')
                .style('stroke-width', 1.25)
                .style('cursor', onSelectMember ? 'pointer' : 'default')
                .attr('fill', (datum: ClanData) => selectClanColorByWR(datum.pvp_ratio))
                .on('click', function (_event: MouseEvent, datum: ClanData) {
                    if (onSelectMember) {
                        onSelectMember(datum.player_name);
                    }
                })
                .on('mouseover', function (this: SVGCircleElement, _event: MouseEvent, datum: ClanData) {
                    showDetails(datum);
                    d3.select(this).transition()
                        .duration(50)
                        .attr('fill', '#bcbddc');
                })
                .on('mouseout', function (this: SVGCircleElement, _event: MouseEvent, datum: ClanData) {
                    hideDetails();
                    d3.select(this).transition()
                        .duration(50)
                        .attr('fill', selectClanColorByWR(datum.pvp_ratio));
                });
        })
        .catch(() => {
            svg.selectAll('*').remove();
            svg.append('text')
                .attr('x', 0)
                .attr('y', 16)
                .attr('class', 'text-sm')
                .style('fill', '#6b7280')
                .text('Unable to load clan chart.');
        });
};

const ClanSVG: React.FC<ClanProps> = ({ clanId, onSelectMember, svgWidth = 320, svgHeight = 280 }) => {
    const containerRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (containerRef.current) {
            drawClanPlot(containerRef.current, clanId, onSelectMember, svgWidth, svgHeight);
        }
    }, [clanId, onSelectMember, svgWidth, svgHeight]);

    return <div ref={containerRef}></div>;
};

export default ClanSVG;
