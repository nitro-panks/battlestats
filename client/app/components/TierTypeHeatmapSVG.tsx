import React, { useEffect, useRef } from 'react';
import * as d3 from 'd3';
import { PLAYER_ROUTE_PANEL_FETCH_TTL_MS } from '../lib/playerRouteFetch';
import { fetchSharedJson } from '../lib/sharedJsonFetch';
import { barChartDataRightX, chartColors, wrColorByRatio, type ChartTheme } from '../lib/chartTheme';
import { useRealm } from '../context/RealmContext';
import { withRealm } from '../lib/realmParams';
import type { TierTypePayload, TierTypePlayerCell } from './playerProfileChartData';
import { getTierTypeShipTypes, getTierTypeTiers, getTierTypeTileKey, resolveTierTypeTiles, type ResolvedTierTypeTile } from './tierTypeHeatmapPayload';

type SvgGroupSelection = ReturnType<typeof d3.select>;

interface TierTypeHeatmapSVGProps {
    playerId: number;
    data?: TierTypePayload;
    svgWidth?: number;
    svgHeight?: number;
    theme?: ChartTheme;
}

const SHIP_TYPE_ORDER = ['Destroyer', 'Cruiser', 'Battleship', 'Aircraft Carrier', 'Submarine'];

const SHIP_TYPE_ABBREV: Record<string, string> = {
    'Destroyer': 'DD',
    'Cruiser': 'CA',
    'Battleship': 'BB',
    'Aircraft Carrier': 'CV',
    'AirCarrier': 'CV',
    'Carrier': 'CV',
    'Submarine': 'Sub',
};

type Colors = typeof chartColors['light'];

const drawMessage = (containerElement: HTMLDivElement, message: string, svgWidth: number, svgHeight: number, colors: Colors) => {
    const container = d3.select(containerElement);
    container.selectAll('*').remove();

    const svg = container.append('svg')
        .attr('width', svgWidth)
        .attr('height', svgHeight);

    svg.append('text')
        .attr('x', 16)
        .attr('y', 24)
        .style('fill', colors.labelText)
        .style('font-size', '12px')
        .text(message);
};

const renderSummaryCard = (
    summaryGroup: SvgGroupSelection,
    tile: ResolvedTierTypeTile,
    playerCell: TierTypePlayerCell | undefined,
    colors: Colors,
    chartWidth?: number,
) => {
    const compact = chartWidth != null && chartWidth < 480;
    const availableWidth = compact ? Math.max(chartWidth - 40, 240) : 400;
    const columns = [0, availableWidth * 0.33, availableWidth * 0.66];
    const headers = ['Type', 'Population', 'Player'];
    const values = [
        {
            text: `${SHIP_TYPE_ABBREV[tile.ship_type] ?? tile.ship_type} T${tile.ship_tier}`,
            fill: colors.accentLink,
            weight: '700',
        },
        {
            text: tile.count.toLocaleString(),
            fill: colors.axisText,
            weight: '600',
        },
        {
            text: playerCell
                ? `${playerCell.pvp_battles.toLocaleString()} @ ${(playerCell.win_ratio * 100).toFixed(1)}%`
                : 'No battles in cell',
            fill: playerCell ? wrColorByRatio(playerCell.win_ratio) : colors.heatmapUnavailable,
            weight: playerCell ? '700' : '400',
        },
    ];

    headers.forEach((header, index) => {
        summaryGroup.append('text')
            .attr('x', columns[index])
            .attr('y', 0)
            .attr('text-anchor', 'start')
            .attr('dominant-baseline', 'hanging')
            .style('font-size', '9px')
            .style('font-weight', '600')
            .style('fill', colors.labelMuted)
            .text(header);
    });

    values.forEach((value, index) => {
        summaryGroup.append('text')
            .attr('x', columns[index])
            .attr('y', 14)
            .attr('text-anchor', 'start')
            .attr('dominant-baseline', 'hanging')
            .style('font-size', '10px')
            .style('font-weight', value.weight)
            .style('fill', value.fill)
            .text(value.text);
    });
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
        drawMessage(containerElement, 'No tier and ship-type population data available.', svgWidth, 112, colors);
        return;
    }

    if (payload.player_cells.length < 2) {
        drawMessage(containerElement, 'This captain does not have enough tier and ship-type variety yet to draw a useful heatmap.', svgWidth, 112, colors);
        return;
    }

    const shipTypes = getTierTypeShipTypes(payload);
    const tiers = getTierTypeTiers(payload);
    if (!shipTypes.length || !tiers.length) {
        drawMessage(containerElement, 'Unable to build tier and ship-type chart axes.', svgWidth, 112, colors);
        return;
    }

    const resolvedTiles = resolveTierTypeTiles(payload);

    const compact = svgWidth < 480;
    // left mirrors shipBarPlot's y-axis inset (68 non-compact) so the heatmap's
    // y-axis lines up vertically with the two bar charts below it.
    const margin = compact
        ? { top: 48, right: 6, bottom: 42, left: 28 }
        : { top: 62, right: 18, bottom: 42, left: 68 };
    const axisFontSize = compact ? '9px' : '10px';
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

    const tileByKey = new Map(resolvedTiles.map((row: ResolvedTierTypeTile) => [getTierTypeTileKey(row.ship_type, row.ship_tier), row]));
    const playerCellByKey = new Map(payload.player_cells.map((row: TierTypePlayerCell) => [getTierTypeTileKey(row.ship_type, row.ship_tier), row]));
    const maxTileCount = d3.max(resolvedTiles, (row: ResolvedTierTypeTile) => row.count) || 1;
    const maxPlayerBattles = d3.max(payload.player_cells, (row: TierTypePlayerCell) => row.pvp_battles) || 1;
    const tileColor = theme === 'dark'
        ? d3.scaleSequential(d3.interpolateRgb('#1c2d3f', '#79c0ff')).domain([0, maxTileCount])
        : d3.scaleSequential(d3.interpolateBlues).domain([0, maxTileCount]);
    const playerRadius = d3.scaleSqrt()
        .domain([0, maxPlayerBattles])
        .range([0, 14]);

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

    svg.append('g')
        .attr('transform', `translate(0, ${height})`)
        .style('color', colors.labelMuted)
        .call(d3.axisBottom(x).tickSize(0).tickFormat((d: string) => SHIP_TYPE_ABBREV[d] ?? d))
        .selectAll('text')
        .style('font-size', axisFontSize)
        .style('font-weight', '500');

    svg.append('g')
        .style('color', colors.axisText)
        .call(d3.axisLeft(y).tickSize(0).tickPadding(compact ? 4 : 6))
        .selectAll('text')
        .style('font-size', axisFontSize)
        .style('font-weight', '500');

    svg.selectAll('.domain').style('stroke', colors.axisLine);

    svg.append('text')
        .attr('x', bandRangeEnd / 2)
        .attr('y', height + 34)
        .attr('text-anchor', 'middle')
        .style('fill', colors.labelMuted)
        .style('font-size', axisFontSize)
        .text(payload.x_label);

    const summaryGroup = svgRoot.append('g')
        .attr('transform', `translate(${margin.left + 6}, 28)`);

    const renderSummary = (tile: ResolvedTierTypeTile) => {
        summaryGroup.selectAll('*').remove();

        const playerCell = playerCellByKey.get(getTierTypeTileKey(tile.ship_type, tile.ship_tier));

        renderSummaryCard(summaryGroup, tile, playerCell, colors, svgWidth);
    };

    const tileNodes = svg.selectAll('.tier-type-tile')
        .data(resolvedTiles)
        .enter()
        .append('rect')
        .attr('class', 'tier-type-tile')
        .attr('x', (row: ResolvedTierTypeTile) => x(row.ship_type) ?? 0)
        .attr('y', (row: ResolvedTierTypeTile) => y(String(row.ship_tier)) ?? 0)
        .attr('width', x.bandwidth())
        .attr('height', y.bandwidth())
        .attr('rx', 4)
        .attr('fill', 'transparent')
        .attr('stroke', 'transparent')
        .on('mouseover', function (this: SVGRectElement, _event: MouseEvent, row: ResolvedTierTypeTile) {
            renderSummary(row);
            d3.select(this)
                .attr('stroke', colors.labelStrong)
                .attr('stroke-width', 1.2);
        })
        .on('mouseout', function (this: SVGRectElement) {
            d3.select(this)
                .attr('stroke', 'transparent')
                .attr('stroke-width', 0);
        });

    svg.selectAll('.player-cell')
        .data(payload.player_cells)
        .enter()
        .append('circle')
        .attr('class', 'player-cell')
        .attr('cx', (row: TierTypePlayerCell) => (x(row.ship_type) ?? 0) + (x.bandwidth() / 2))
        .attr('cy', (row: TierTypePlayerCell) => (y(String(row.ship_tier)) ?? 0) + (y.bandwidth() / 2))
        .attr('r', (row: TierTypePlayerCell) => Math.max(4, playerRadius(row.pvp_battles)))
        .attr('fill', (row: TierTypePlayerCell) => wrColorByRatio(row.win_ratio))
        .attr('fill-opacity', 0.92)
        .attr('stroke', colors.barStroke)
        .attr('stroke-width', 1.6);

    const defaultTile = payload.player_cells.length
        ? tileByKey.get(getTierTypeTileKey(payload.player_cells[0].ship_type, payload.player_cells[0].ship_tier))
        : resolvedTiles[0];

    if (defaultTile) {
        renderSummary(defaultTile);
    }

    tileNodes.raise();
};

const TierTypeHeatmapSVG: React.FC<TierTypeHeatmapSVGProps> = ({
    playerId,
    data,
    svgWidth = 570,
    svgHeight = 332,
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

        // Flow to the full container width (floored for narrow screens) so the
        // heatmap's right edge aligns with the full-width bar charts below it,
        // rather than capping at svgWidth. Falls back to svgWidth pre-layout.
        const resolveWidth = () => Math.max(containerElement.clientWidth || svgWidth, 320);

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
                    drawMessage(containerElement, 'Unable to load tier and ship-type heatmap.', resolveWidth(), 112, colors);
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
