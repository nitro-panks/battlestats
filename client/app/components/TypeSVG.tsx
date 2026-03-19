import React, { useEffect, useRef } from 'react';
import * as d3 from 'd3';
import { PLAYER_ROUTE_PANEL_FETCH_TTL_MS } from '../lib/playerRouteFetch';
import { fetchSharedJson } from '../lib/sharedJsonFetch';

interface TypeSVGProps {
    playerId: number;
    svgHeight?: number;
}

interface TypeRow {
    ship_type: string;
    pvp_battles: number;
    wins: number;
    win_ratio: number;
}

const normalizeTypeRows = (data: unknown): TypeRow[] => {
    if (Array.isArray(data)) {
        return data as TypeRow[];
    }

    console.warn('Unexpected type data payload:', data);
    return [];
};

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

const drawTypePlot = (container: HTMLDivElement, playerId: number, svgHeight: number) => {
    const containerWidth = Math.max(container.clientWidth || 0, 280);
    const compact = containerWidth < 420;

    d3.select(container).selectAll('*').remove();

    const totalSvgWidth = containerWidth;
    const totalSvgHeight = compact ? Math.min(svgHeight, 192) : svgHeight;
    const margin = compact
        ? { top: 28, right: 14, bottom: 42, left: 62 }
        : { top: 28, right: 96, bottom: 48, left: 68 };
    const width = totalSvgWidth - margin.left - margin.right;
    const height = totalSvgHeight - margin.top - margin.bottom;
    const detailFontSize = compact ? '9px' : '10px';
    const detailTitleSize = compact ? '10px' : '11px';
    const axisFontSize = compact ? '9px' : '10px';

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

    fetchSharedJson<unknown>(`http://localhost:8888/api/fetch/type_data/${playerId}/`, {
        label: `Type data ${playerId}`,
        ttlMs: PLAYER_ROUTE_PANEL_FETCH_TTL_MS,
    })
        .then(({ data }) => {
            const rows = normalizeTypeRows(data).sort((left, right) => right.pvp_battles - left.pvp_battles);
            if (rows.length === 0) {
                return;
            }

            const maxBattles = Math.max(d3.max(rows, (datum: TypeRow) => datum.pvp_battles) || 0, 10);
            const x = d3.scaleLinear()
                .domain([0, maxBattles * 1.08])
                .range([0, width]);

            const y = d3.scaleBand()
                .range([0, height])
                .domain(rows.map((datum: TypeRow) => datum.ship_type))
                .padding(0.08);

            svg.append('g')
                .attr('class', 'type-grid')
                .attr('transform', `translate(0, ${height})`)
                .call(d3.axisBottom(x).ticks(6).tickSize(-height).tickFormat(() => ''));

            svg.select('.type-grid')?.select('.domain')?.remove();
            svg.selectAll('.type-grid line')
                .style('stroke', '#e2e8f0')
                .style('stroke-width', 1);

            svg.append('g')
                .attr('transform', `translate(0, ${height})`)
                .style('color', '#64748b')
                .call(d3.axisBottom(x).ticks(6).tickFormat((value: number) => d3.format(',')(Number(value))).tickSizeOuter(0))
                .selectAll('text')
                .style('font-size', axisFontSize);

            svg.append('g')
                .style('color', '#475569')
                .call(d3.axisLeft(y).tickSize(0).tickPadding(6))
                .selectAll('text')
                .style('font-size', axisFontSize)
                .style('font-weight', '500');

            svg.selectAll('.domain').style('stroke', '#cbd5e1');

            svg.append('text')
                .attr('x', width)
                .attr('y', height + (compact ? 32 : 38))
                .attr('text-anchor', 'end')
                .style('font-size', axisFontSize)
                .style('fill', '#64748b')
                .text('Random battles');

            const detailGroup = svgRoot.append('g').attr('transform', `translate(${margin.left + width - 4}, 12)`);

            const renderDetails = (datum: TypeRow | null) => {
                detailGroup.selectAll('*').remove();
                if (!datum) {
                    return;
                }

                const detailText = detailGroup.append('text')
                    .attr('x', 0)
                    .attr('y', 0)
                    .attr('text-anchor', 'end')
                    .attr('dominant-baseline', 'hanging')
                    .style('display', compact ? 'none' : null);

                detailText.append('tspan')
                    .style('font-size', detailTitleSize)
                    .style('font-weight', '700')
                    .style('fill', '#084594')
                    .text(datum.ship_type);

                detailText.append('tspan')
                    .style('font-size', detailFontSize)
                    .style('font-weight', '400')
                    .style('fill', '#94a3b8')
                    .text('  •  ');

                detailText.append('tspan')
                    .style('font-size', detailFontSize)
                    .style('font-weight', '400')
                    .style('fill', '#475569')
                    .text(`${datum.pvp_battles.toLocaleString()} battles`);

                detailText.append('tspan')
                    .style('font-size', detailFontSize)
                    .style('font-weight', '400')
                    .style('fill', '#94a3b8')
                    .text('  •  ');

                detailText.append('tspan')
                    .style('font-size', detailFontSize)
                    .style('font-weight', '400')
                    .style('fill', '#475569')
                    .text(`${datum.wins.toLocaleString()} wins`);

                detailText.append('tspan')
                    .style('font-size', detailFontSize)
                    .style('font-weight', '400')
                    .style('fill', '#94a3b8')
                    .text('  •  ');

                detailText.append('tspan')
                    .style('font-size', detailFontSize)
                    .style('font-weight', '700')
                    .style('fill', '#475569')
                    .text(`${(datum.win_ratio * 100).toFixed(1)}% win rate`);
            };

            const rowNodes = svg.selectAll('.type-row')
                .data(rows)
                .enter()
                .append('g')
                .classed('type-row', true);

            rowNodes.append('rect')
                .attr('x', 0)
                .attr('y', (datum: TypeRow) => (y(datum.ship_type) ?? 0) + (y.bandwidth() * 0.1))
                .attr('width', (datum: TypeRow) => x(datum.pvp_battles))
                .attr('height', y.bandwidth() * 0.88)
                .attr('rx', 3)
                .attr('fill', '#dbe4f0');

            rowNodes.append('rect')
                .attr('x', 0)
                .attr('y', (datum: TypeRow) => y(datum.ship_type) ?? 0)
                .attr('width', (datum: TypeRow) => x(datum.wins))
                .attr('height', y.bandwidth())
                .attr('rx', 3)
                .style('stroke', '#334155')
                .style('stroke-width', 0.7)
                .attr('fill', (datum: TypeRow) => selectTypeColorByWr(datum.win_ratio))
                .on('mouseover', function (this: SVGRectElement, _event: MouseEvent, datum: TypeRow) {
                    renderDetails(datum);
                    d3.select(this)
                        .transition()
                        .duration(70)
                        .attr('opacity', 0.82);
                })
                .on('mouseout', function (this: SVGRectElement) {
                    d3.select(this)
                        .transition()
                        .duration(70)
                        .attr('opacity', 1);
                });

            rowNodes.append('text')
                .attr('x', (datum: TypeRow) => {
                    const labelX = x(datum.pvp_battles) + 6;
                    return labelX > width - 4 ? width - 4 : labelX;
                })
                .attr('y', (datum: TypeRow) => (y(datum.ship_type) ?? 0) + (y.bandwidth() / 2) + 3)
                .style('font-size', axisFontSize)
                .style('fill', '#64748b')
                .attr('text-anchor', (datum: TypeRow) => (x(datum.pvp_battles) + 6 > width - 4 ? 'end' : 'start'))
                .text((datum: TypeRow) => `${(datum.win_ratio * 100).toFixed(1)}%`);

            renderDetails(rows[0]);
        })
        .catch((error) => {
            console.error('Error fetching type data:', error);
        });
};

const TypeSVG: React.FC<TypeSVGProps> = ({ playerId, svgHeight = 210 }) => {
    const containerRef = useRef<HTMLDivElement | null>(null);

    useEffect(() => {
        const container = containerRef.current;
        if (!container) {
            return;
        }

        const render = () => {
            drawTypePlot(container, playerId, svgHeight);
        };

        render();
        window.addEventListener('resize', render);

        return () => {
            window.removeEventListener('resize', render);
        };
    }, [playerId, svgHeight]);

    return <div ref={containerRef} className="w-full"></div>;
};

export default TypeSVG;