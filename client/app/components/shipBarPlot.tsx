import React, { useEffect, useRef } from 'react';
import * as d3 from 'd3';
import { PLAYER_ROUTE_PANEL_FETCH_TTL_MS } from '../lib/playerRouteFetch';
import { fetchSharedJson } from '../lib/sharedJsonFetch';
import { barChartLabelGutter, chartColors, wrColorByRatio, type ChartColors as Colors, type ChartTheme } from '../lib/chartTheme';
import { useRealm } from '../context/RealmContext';
import { withRealm } from '../lib/realmParams';

// Shared win-rate bar plot backing TierSVG and TypeSVG, which are otherwise
// byte-identical. A chart is defined entirely by its config: the row key +
// detail title accessors, the bar orientation, the compact-mode dimensions,
// the fetch endpoint/label, and the post-fetch sort. createShipBarChart()
// returns a thin React component; TierSVG/TypeSVG are just two configs.

// The minimum row shape every ship bar plot needs; concrete rows (TierRow,
// TypeRow) extend it with their own key field.
export interface ShipBarRow {
    pvp_battles: number;
    wins: number;
    win_ratio: number;
}


export interface ShipBarPlotConfig<Row extends ShipBarRow> {
    // Stable per-row key for the band scale (e.g. String(ship_tier), ship_type).
    rowKey: (row: Row) => string;
    // Title line of the hover detail (e.g. `Tier 10`, `Destroyer`).
    detailTitle: (row: Row) => string;
    // CSS class prefix for the grid/row groups ('tier' | 'type').
    cssPrefix: string;
    // Bar direction: 'horizontal' (default) puts the key on the y axis with
    // battle volume growing rightward; 'vertical' puts the key on the x axis
    // with battle volume growing upward (columns).
    orientation?: 'horizontal' | 'vertical';
    // Compact-mode (narrow container) overrides; non-compact dims are shared.
    compactHeightCap: number;
    compactLeftMargin: number;
    // Fetch wiring.
    endpoint: (playerId: number) => string;
    fetchLabel: (playerId: number) => string;
    fetchErrorMessage: string;
    unexpectedPayloadMessage: string;
    // Sort applied to a freshly-fetched payload before render.
    sortRows: (rows: Row[]) => Row[];
    // Optional y-axis tick formatter (e.g. abbreviate ship types to their
    // class tags); defaults to the raw row key.
    axisTickLabel?: (key: string) => string;
    // Default chart height when the caller does not override svgHeight.
    defaultSvgHeight: number;
}

interface ShipBarChartProps<Row extends ShipBarRow> {
    playerId: number;
    data?: Row[];
    svgHeight?: number;
    theme?: ChartTheme;
}

export function createShipBarChart<Row extends ShipBarRow>(config: ShipBarPlotConfig<Row>): React.FC<ShipBarChartProps<Row>> {
    const normalizeRows = (data: unknown): Row[] => {
        if (Array.isArray(data)) {
            return data as Row[];
        }

        console.warn(config.unexpectedPayloadMessage, data);
        return [];
    };

    const drawPlot = (container: HTMLDivElement, playerId: number, svgHeight: number, colors: Colors, data?: Row[], realm?: string) => {
        const containerWidth = Math.max(container.clientWidth || 0, 280);
        const compact = containerWidth < 420;

        d3.select(container).selectAll('*').remove();

        const vertical = config.orientation === 'vertical';
        const totalSvgWidth = containerWidth;
        const totalSvgHeight = compact ? Math.min(svgHeight, config.compactHeightCap) : svgHeight;
        // Vertical charts trade the wide key-label left margin for a slim
        // battle-count axis, and reserve top headroom for the WR% column labels.
        const margin = vertical
            ? (compact
                ? { top: 16, right: 8, bottom: 30, left: config.compactLeftMargin }
                : { top: 18, right: 10, bottom: 34, left: 56 })
            : (compact
                ? { top: 8, right: 14, bottom: 42, left: config.compactLeftMargin }
                : { top: 8, right: 46, bottom: 48, left: 100 });
        const width = totalSvgWidth - margin.left - margin.right;
        const height = totalSvgHeight - margin.top - margin.bottom;
        const axisFontSize = compact ? '9px' : '12px';

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

        // Column variant: same visual grammar as the horizontal rows (thin
        // battle-volume backdrop, full-thickness wins bar tinted by win rate)
        // rotated 90° — key on the x band, battles on the y linear scale. The
        // end-of-bar "battles · WR%" label has no room over a column, so each
        // column carries a rounded WR% above its backdrop instead; battle
        // volume reads off the y axis.
        const renderColumns = (rows: Row[]) => {
                if (rows.length === 0) {
                    return;
                }

                const maxBattles = Math.max(d3.max(rows, (datum: Row) => datum.pvp_battles) || 0, 10);
                const x = d3.scaleBand()
                    .range([0, width])
                    .domain(rows.map((datum: Row) => config.rowKey(datum)))
                    .padding(0.18);

                const y = d3.scaleLinear()
                    .domain([0, maxBattles])
                    .range([height, 0]);

                svg.append('g')
                    .attr('class', `${config.cssPrefix}-grid`)
                    .call(d3.axisLeft(y).ticks(5).tickSize(-width).tickFormat(() => ''));

                svg.select(`.${config.cssPrefix}-grid`)?.select('.domain')?.remove();
                svg.selectAll(`.${config.cssPrefix}-grid line`)
                    .style('stroke', colors.gridLine)
                    .style('stroke-width', 1);

                svg.append('g')
                    .style('color', colors.labelMuted)
                    .call(d3.axisLeft(y).ticks(5).tickFormat((value: number) => d3.format(',')(Number(value))).tickSizeOuter(0))
                    .selectAll('text')
                    .style('font-size', axisFontSize);

                svg.append('g')
                    .attr('transform', `translate(0, ${height})`)
                    .style('color', colors.axisText)
                    .call(d3.axisBottom(x).tickSize(0).tickPadding(6).tickFormat((key: string) => (config.axisTickLabel ? config.axisTickLabel(key) : key)))
                    .selectAll('text')
                    .style('font-size', axisFontSize)
                    .style('font-weight', '500');

                svg.selectAll('.domain').style('stroke', colors.axisLine);

                const rowNodes = svg.selectAll(`.${config.cssPrefix}-row`)
                    .data(rows)
                    .enter()
                    .append('g')
                    .classed(`${config.cssPrefix}-row`, true);

                const fgBarWidth = x.bandwidth();
                const bgBarWidth = Math.max(3, Math.round(fgBarWidth * 0.5));
                const bgBarOffset = (fgBarWidth - bgBarWidth) / 2;

                rowNodes.append('rect')
                    .attr('x', (datum: Row) => (x(config.rowKey(datum)) ?? 0) + bgBarOffset)
                    .attr('y', (datum: Row) => y(datum.pvp_battles))
                    .attr('width', bgBarWidth)
                    .attr('height', (datum: Row) => height - y(datum.pvp_battles))
                    .attr('rx', 3)
                    .attr('fill', colors.barBg);

                rowNodes.append('rect')
                    .attr('x', (datum: Row) => x(config.rowKey(datum)) ?? 0)
                    .attr('y', (datum: Row) => y(datum.wins))
                    .attr('width', fgBarWidth)
                    .attr('height', (datum: Row) => height - y(datum.wins))
                    .attr('rx', 3)
                    .style('stroke', colors.axisLine)
                    .style('stroke-width', 0.5)
                    .attr('fill', (datum: Row) => wrColorByRatio(datum.win_ratio))
                    .on('mouseover', function (this: SVGRectElement) {
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

                rowNodes.filter((datum: Row) => datum.pvp_battles > 0)
                    .append('text')
                    .attr('x', (datum: Row) => (x(config.rowKey(datum)) ?? 0) + fgBarWidth / 2)
                    .attr('y', (datum: Row) => y(datum.pvp_battles) - 5)
                    .attr('text-anchor', 'middle')
                    .style('font-size', axisFontSize)
                    .style('fill', colors.labelMuted)
                    .text((datum: Row) => `${Math.round(datum.win_ratio * 100)}%`);
            };

        const renderRows = (rows: Row[]) => {
                if (rows.length === 0) {
                    return;
                }

                const maxBattles = Math.max(d3.max(rows, (datum: Row) => datum.pvp_battles) || 0, 10);
                // Scale the bars to end short of the plot's right edge by a fixed
                // label gutter, so the end-of-bar "battles · WR%" labels sit
                // beside the bars instead of over them. The gutter matches
                // barChartDataRightX so the heatmap/population charts stay aligned.
                const barAreaWidth = Math.max(width - barChartLabelGutter(totalSvgWidth), width * 0.35);
                const x = d3.scaleLinear()
                    .domain([0, maxBattles])
                    .range([0, barAreaWidth]);

                const y = d3.scaleBand()
                    .range([0, height])
                    .domain(rows.map((datum: Row) => config.rowKey(datum)))
                    .padding(0.18);

                svg.append('g')
                    .attr('class', `${config.cssPrefix}-grid`)
                    .attr('transform', `translate(0, ${height})`)
                    .call(d3.axisBottom(x).ticks(6).tickSize(-height).tickFormat(() => ''));

                svg.select(`.${config.cssPrefix}-grid`)?.select('.domain')?.remove();
                svg.selectAll(`.${config.cssPrefix}-grid line`)
                    .style('stroke', colors.gridLine)
                    .style('stroke-width', 1);

                svg.append('g')
                    .attr('transform', `translate(0, ${height})`)
                    .style('color', colors.labelMuted)
                    .call(d3.axisBottom(x).ticks(6).tickFormat((value: number) => d3.format(',')(Number(value))).tickSizeOuter(0))
                    .selectAll('text')
                    .style('font-size', axisFontSize);

                svg.append('g')
                    .style('color', colors.axisText)
                    .call(d3.axisLeft(y).tickSize(0).tickPadding(6).tickFormat((key: string) => (config.axisTickLabel ? config.axisTickLabel(key) : key)))
                    .selectAll('text')
                    .style('font-size', axisFontSize)
                    .style('font-weight', '500');

                svg.selectAll('.domain').style('stroke', colors.axisLine);

                const rowNodes = svg.selectAll(`.${config.cssPrefix}-row`)
                    .data(rows)
                    .enter()
                    .append('g')
                    .classed(`${config.cssPrefix}-row`, true);

                const fgBarHeight = y.bandwidth();
                const bgBarHeight = Math.max(3, Math.round(fgBarHeight * 0.5));
                const bgBarOffset = (fgBarHeight - bgBarHeight) / 2;

                rowNodes.append('rect')
                    .attr('x', 0)
                    .attr('y', (datum: Row) => (y(config.rowKey(datum)) ?? 0) + bgBarOffset)
                    .attr('width', (datum: Row) => x(datum.pvp_battles))
                    .attr('height', bgBarHeight)
                    .attr('rx', 3)
                    .attr('fill', colors.barBg);

                rowNodes.append('rect')
                    .attr('x', 0)
                    .attr('y', (datum: Row) => y(config.rowKey(datum)) ?? 0)
                    .attr('width', (datum: Row) => x(datum.wins))
                    .attr('height', fgBarHeight)
                    .attr('rx', 3)
                    .style('stroke', colors.axisLine)
                    .style('stroke-width', 0.5)
                    .attr('fill', (datum: Row) => wrColorByRatio(datum.win_ratio))
                    .on('mouseover', function (this: SVGRectElement) {
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
                    .attr('y', (datum: Row) => (y(config.rowKey(datum)) ?? 0) + (y.bandwidth() / 2) + 3)
                    .style('font-size', axisFontSize)
                    .style('fill', colors.labelMuted)
                    .text((datum: Row) => `${datum.pvp_battles.toLocaleString()} · ${(datum.win_ratio * 100).toFixed(1)}%`)
                    .each(function (this: SVGTextElement, datum: Row) {
                        const startX = x(datum.pvp_battles) + 6;
                        const textLength = this.getComputedTextLength();
                        if (startX + textLength <= width) {
                            d3.select(this).attr('text-anchor', 'start').attr('x', startX);
                        } else {
                            d3.select(this).attr('text-anchor', 'end').attr('x', width);
                        }
                    });
            };

        const render = vertical ? renderColumns : renderRows;

        if (data) {
            render(data);
            return;
        }

        fetchSharedJson<unknown>(withRealm(config.endpoint(playerId), realm || 'na'), {
            label: config.fetchLabel(playerId),
            ttlMs: PLAYER_ROUTE_PANEL_FETCH_TTL_MS,
        })
            .then(({ data: payload }) => {
                render(config.sortRows(normalizeRows(payload)));
            })
            .catch((error) => {
                console.error(config.fetchErrorMessage, error);
            });
    };

    const ShipBarChart: React.FC<ShipBarChartProps<Row>> = ({ playerId, data, svgHeight = config.defaultSvgHeight, theme = 'light' }) => {
        const containerRef = useRef<HTMLDivElement | null>(null);
        const { realm } = useRealm();

        useEffect(() => {
            const container = containerRef.current;
            if (!container) {
                return;
            }

            const colors = chartColors[theme];
            const render = () => {
                drawPlot(container, playerId, svgHeight, colors, data, realm);
            };

            render();
            window.addEventListener('resize', render);

            return () => {
                window.removeEventListener('resize', render);
            };
        }, [data, playerId, realm, svgHeight, theme]);

        return <div ref={containerRef} className="w-full"></div>;
    };

    return ShipBarChart;
}
