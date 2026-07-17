import React, { useEffect, useRef } from 'react';
import * as d3 from 'd3';
import { PLAYER_ROUTE_PANEL_FETCH_TTL_MS } from '../lib/playerRouteFetch';
import { fetchSharedJson } from '../lib/sharedJsonFetch';
import { barChartDataRightX, chartColors, drawSvgMessage, resolveContainerChartWidth, wrColorByRatio, type ChartColors as Colors, type ChartTheme } from '../lib/chartTheme';
import { useRealm } from '../context/RealmContext';
import { withRealm } from '../lib/realmParams';
import type { TierTypePayload, TierTypePlayerCell } from './playerProfileChartData';
import { getTierTypeShipTypes, getTierTypeTiers, resolveTierTypeTiles, type ResolvedTierTypeTile } from './tierTypeHeatmapPayload';

interface TierTypeHeatmapSVGProps {
    playerId: number;
    data?: TierTypePayload;
    svgWidth?: number;
    svgHeight?: number;
    theme?: ChartTheme;
}

// Number of distinct population classes in the tile scale + legend.
const POP_LEGEND_CLASSES = 5;

// Approximate legend value: 3 significant figures with an SI suffix ("16M",
// "132K") — precise counts would just be noise at legend size.
const formatPopLegendValue = (value: number): string =>
    d3.format('.3~s')(value).replace('G', 'B').replace('k', 'K');

const SHIP_TYPE_ABBREV: Record<string, string> = {
    'Destroyer': 'DD',
    'Cruiser': 'CA',
    'Battleship': 'BB',
    'Aircraft Carrier': 'CV',
    'AirCarrier': 'CV',
    'Carrier': 'CV',
    'Submarine': 'SS',
};

const drawChart = (
    containerElement: HTMLDivElement,
    payload: TierTypePayload,
    svgWidth: number,
    svgHeight: number,
    colors: Colors,
    theme: ChartTheme,
) => {
    if (!payload.tiles.length) {
        drawSvgMessage(containerElement, 'No tier and ship-type population data available.', { width: svgWidth, height: 112, color: colors.labelText });
        return;
    }

    if (payload.player_cells.length < 2) {
        drawSvgMessage(containerElement, 'This captain does not have enough tier and ship-type variety yet to draw a useful heatmap.', { width: svgWidth, height: 112, color: colors.labelText });
        return;
    }

    const shipTypes = getTierTypeShipTypes(payload);
    const tiers = getTierTypeTiers(payload);
    if (!shipTypes.length || !tiers.length) {
        drawSvgMessage(containerElement, 'Unable to build tier and ship-type chart axes.', { width: svgWidth, height: 112, color: colors.labelText });
        return;
    }

    const resolvedTiles = resolveTierTypeTiles(payload);

    const compact = svgWidth < 480;
    // left mirrors the Performance-by-Ship-Type chart's y-axis inset below it
    // (shipBarPlot compact left = 62 — the halved side-by-side column always
    // renders that chart compact) so the heatmap's y-axis lines up vertically
    // with it. top is a small pad now that the summary header above the grid
    // is gone.
    const margin = compact
        ? { top: 14, right: 6, bottom: 42, left: 28 }
        : { top: 16, right: 18, bottom: 42, left: 62 };
    const axisFontSize = compact ? '9px' : '12px';
    const width = svgWidth - margin.left - margin.right;
    const height = svgHeight - margin.top - margin.bottom;

    // Align the heatmap's rightmost tile with where the bar charts' *data* ends
    // (not the plot frame). Both SVGs share the same full container width, so
    // the shared helper reproduces that canvas x here.
    const barDataRight = barChartDataRightX(svgWidth);
    // Solve the band range whose last tile ends at barDataRight (in plot coords),
    // preserving the band's inner/outer padding: probe a unit-range band for the
    // fraction of the range at which the last tile ends, then scale up.
    const bandProbe = d3.scaleBand().domain(shipTypes).range([0, 1]).padding(0.12);
    const lastTileFrac = (bandProbe(shipTypes[shipTypes.length - 1]) ?? 0) + bandProbe.bandwidth();
    const bandRangeEnd = lastTileFrac > 0 ? (barDataRight - margin.left) / lastTileFrac : width;
    const container = d3.select(containerElement);
    container.selectAll('*').remove();

    const svgRoot = container.append('svg')
        .attr('width', svgWidth)
        .attr('height', svgHeight);

    const svg = svgRoot.append('g')
        .attr('transform', `translate(${margin.left}, ${margin.top})`);

    const x = d3.scaleBand()
        .domain(shipTypes)
        .range([0, bandRangeEnd])
        .padding(0.12);

    // Harmonize row height with the Performance-by-Tier bar chart: give each
    // heatmap tier row the same thickness (step) as a bar there. TierSVG renders
    // at svgHeight=280 with shipBarPlot's non-compact top=8/bottom=48 margins and
    // y padding 0.18 over 11 tier rows — a fixed, data-independent row height. We
    // reproduce that step here (d3 scaleBand: step = extent / (n + padding)), so
    // the tiles fill `yBandExtent` and sit just above the x-axis at `height`.
    const rowPadding = 0.18;
    const tierBarRowStep = (280 - 8 - 48) / (11 + rowPadding);
    const yBandExtent = Math.min(height, tierBarRowStep * (tiers.length + rowPadding));
    const y = d3.scaleBand()
        .domain(tiers.map((value) => String(value)))
        .range([0, yBandExtent])
        .padding(rowPadding);

    const maxTileCount = d3.max(resolvedTiles, (row: ResolvedTierTypeTile) => row.count) || 1;
    const maxPlayerBattles = d3.max(payload.player_cells, (row: TierTypePlayerCell) => row.pvp_battles) || 1;
    // Five distinct population classes (ColorBrewer Blues; the dark ramp is the
    // same five steps sampled from the theme's blue scale). Class breaks derive
    // from the realm payload's max tile count, so they recompute per realm and
    // whenever fresher data arrives.
    const popClassColors: string[] = theme === 'dark'
        ? d3.quantize(d3.interpolateRgb('#1c2d3f', '#79c0ff'), POP_LEGEND_CLASSES)
        : [...d3.schemeBlues[POP_LEGEND_CLASSES]];
    const tileColor = d3.scaleQuantize()
        .domain([0, maxTileCount])
        .range(popClassColors);

    svg.append('g')
        .attr('class', 'tier-type-grid')
        .selectAll('rect')
        .data(resolvedTiles)
        .enter()
        .append('rect')
        .attr('x', (row: ResolvedTierTypeTile) => x(row.ship_type) ?? 0)
        .attr('y', (row: ResolvedTierTypeTile) => y(String(row.ship_tier)) ?? 0)
        .attr('width', x.bandwidth())
        .attr('height', y.bandwidth())
        .attr('rx', 4)
        .attr('fill', (row: ResolvedTierTypeTile) => tileColor(row.count))
        .attr('stroke', colors.gridLineBlue)
        .attr('stroke-width', 0.8);

    const xAxisGroup = svg.append('g')
        .attr('transform', `translate(0, ${height})`)
        .style('color', colors.labelMuted)
        .call(d3.axisBottom(x).tickSize(0).tickFormat((d: string) => SHIP_TYPE_ABBREV[d] ?? d));
    // No axis line along the x axis — the tile grid frames itself.
    xAxisGroup.select('.domain').remove();
    xAxisGroup.selectAll('text')
        .style('font-size', axisFontSize)
        .style('font-weight', '700');

    svg.append('g')
        .style('color', colors.axisText)
        .call(d3.axisLeft(y).tickSize(0).tickPadding(compact ? 4 : 6))
        .selectAll('text')
        .style('font-size', axisFontSize)
        .style('font-weight', '500');

    svg.selectAll('.domain').style('stroke', colors.axisLine);

    // Population legend in the right-side whitespace (the bar-chart label
    // gutter): five discrete class swatches of the tile scale, darkest (most
    // battles) at the top, each labeled with the approximate upper bound of
    // its class so the background layer reads as "how populated is this
    // tier/type cell".
    if (!compact) {
        const legendX = bandRangeEnd + 24;
        const swatchSize = 18;
        const swatchGap = 2;
        const legendBarTop = 24;

        const legend = svg.append('g')
            .attr('transform', `translate(${legendX}, 4)`);

        legend.append('text')
            .attr('x', 0)
            .attr('y', 0)
            .attr('dominant-baseline', 'hanging')
            .style('fill', colors.labelMid)
            .style('font-size', '13px')
            .style('font-weight', '700')
            .text('Population');

        popClassColors.slice().reverse().forEach((classColor: string, index: number) => {
            const [, upperBound] = tileColor.invertExtent(classColor);
            const rowY = legendBarTop + index * (swatchSize + swatchGap);

            legend.append('rect')
                .attr('x', 0)
                .attr('y', rowY)
                .attr('width', swatchSize)
                .attr('height', swatchSize)
                .attr('rx', 3)
                .attr('fill', classColor)
                .attr('stroke', colors.gridLineBlue)
                .attr('stroke-width', 0.8);

            legend.append('text')
                .attr('x', swatchSize + 6)
                .attr('y', rowY + swatchSize / 2)
                .attr('dominant-baseline', 'central')
                .style('fill', colors.labelMuted)
                .style('font-size', '12px')
                .text(`≤ ${formatPopLegendValue(upperBound)}`);
        });
    }

    // Player markers are pills overlaid on (centered within) the population pills.
    // Width encodes how much this captain plays that tier/type: the most-played
    // cell fills 50% of the population pill's width, and the rest scale down
    // linearly by battle count (floored so a rarely-played cell stays visible).
    // Height is a fixed slice of the cell so they read as pills, not squares.
    const PLAYER_PILL_MAX_WIDTH_FRAC = 0.8;
    const playerPillHeight = Math.max(6, y.bandwidth() * 0.9);
    const playerPillWidth = (row: TierTypePlayerCell) => {
        const frac = maxPlayerBattles > 0 ? row.pvp_battles / maxPlayerBattles : 0;
        return Math.max(4, frac * PLAYER_PILL_MAX_WIDTH_FRAC * x.bandwidth());
    };

    svg.selectAll('.player-cell')
        .data(payload.player_cells)
        .enter()
        .append('rect')
        .attr('class', 'player-cell')
        .attr('width', (row: TierTypePlayerCell) => playerPillWidth(row))
        .attr('height', playerPillHeight)
        .attr('x', (row: TierTypePlayerCell) => (x(row.ship_type) ?? 0) + (x.bandwidth() - playerPillWidth(row)) / 2)
        .attr('y', (row: TierTypePlayerCell) => (y(String(row.ship_tier)) ?? 0) + (y.bandwidth() - playerPillHeight) / 2)
        .attr('rx', (row: TierTypePlayerCell) => Math.min(playerPillHeight, playerPillWidth(row)) / 2)
        .attr('fill', (row: TierTypePlayerCell) => wrColorByRatio(row.win_ratio))
        .attr('fill-opacity', 0.92)
        .attr('stroke', colors.barStroke)
        .attr('stroke-width', 1.6);
};

const TierTypeHeatmapSVG: React.FC<TierTypeHeatmapSVGProps> = ({
    playerId,
    data,
    svgWidth = 570,
    svgHeight = 286,
    theme = 'light',
}) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const { realm } = useRealm();

    useEffect(() => {
        const containerElement = containerRef.current;
        if (!containerElement) {
            return;
        }

        const colors = chartColors[theme];
        const abortController = new AbortController();

        let cachedPayload: TierTypePayload | null = null;
        let resizeFrame: number | null = null;

        // Flow to the full container width so the heatmap's right edge aligns
        // with the full-width bar charts below it, rather than capping at
        // svgWidth. Falls back to svgWidth pre-layout.
        const resolveWidth = () => resolveContainerChartWidth(containerElement.clientWidth, svgWidth);

        const redraw = () => {
            if (cachedPayload && containerElement) {
                drawChart(containerElement, cachedPayload, resolveWidth(), svgHeight, colors, theme);
            }
        };

        const onResize = () => {
            if (resizeFrame != null) cancelAnimationFrame(resizeFrame);
            resizeFrame = requestAnimationFrame(redraw);
        };

        const load = async () => {
            try {
                const payload = data ?? (await fetchSharedJson<TierTypePayload>(withRealm(`/api/fetch/player_correlation/tier_type/${playerId}/`, realm), {
                    label: `Tier type correlation ${playerId}`,
                    ttlMs: PLAYER_ROUTE_PANEL_FETCH_TTL_MS,
                })).data;
                if (abortController.signal.aborted) {
                    return;
                }

                cachedPayload = payload;
                drawChart(containerElement, payload, resolveWidth(), svgHeight, colors, theme);
            } catch {
                if (!abortController.signal.aborted) {
                    drawSvgMessage(containerElement, 'Unable to load tier and ship-type heatmap.', { width: resolveWidth(), height: 112, color: colors.labelText });
                }
            }
        };

        load();
        window.addEventListener('resize', onResize);
        return () => {
            abortController.abort();
            window.removeEventListener('resize', onResize);
            if (resizeFrame != null) cancelAnimationFrame(resizeFrame);
        };
    }, [data, playerId, realm, svgHeight, svgWidth, theme]);

    return <div ref={containerRef} className="w-full" />;
};

export default TierTypeHeatmapSVG;
