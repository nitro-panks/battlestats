import React, { useEffect, useRef } from 'react';
import * as d3 from 'd3';
import { PLAYER_ROUTE_PANEL_FETCH_TTL_MS } from '../lib/playerRouteFetch';
import { fetchSharedJson } from '../lib/sharedJsonFetch';
import { chartColors, type ChartTheme } from '../lib/chartTheme';
import { useRealm } from '../context/RealmContext';
import { withRealm } from '../lib/realmParams';
import type { TierTypePayload, TierTypePlayerCell, TierTypeTile, TierTypeTrendPoint } from './playerProfileChartData';

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

const selectColorByWR = (winRatio: number): string => {
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

const buildShipTypeOrderMap = (): Map<string, number> => {
    return new Map(SHIP_TYPE_ORDER.map((label, index) => [label, index]));
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

const normalizeShipTypes = (payload: TierTypePayload): string[] => {
    const orderMap = buildShipTypeOrderMap();
    const labels = new Set<string>();

    payload.tiles.forEach((row: TierTypeTile) => labels.add(row.ship_type));
    payload.trend.forEach((row: TierTypeTrendPoint) => labels.add(row.ship_type));
    payload.player_cells.forEach((row: TierTypePlayerCell) => labels.add(row.ship_type));

    return [...labels].sort((left, right) => {
        const leftOrder = orderMap.get(left) ?? Number.MAX_SAFE_INTEGER;
        const rightOrder = orderMap.get(right) ?? Number.MAX_SAFE_INTEGER;
        if (leftOrder !== rightOrder) {
            return leftOrder - rightOrder;
        }

        return left.localeCompare(right);
    });
};

const normalizeTiers = (payload: TierTypePayload): number[] => {
    const tiers = new Set<number>();

    payload.tiles.forEach((row: TierTypeTile) => tiers.add(row.ship_tier));
    payload.player_cells.forEach((row: TierTypePlayerCell) => tiers.add(row.ship_tier));

    return [...tiers].sort((left, right) => right - left);
};

const renderSummaryCard = (
    summaryGroup: SvgGroupSelection,
    tile: TierTypeTile,
    playerCell: TierTypePlayerCell | undefined,
    trendDelta: number | null,
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
            fill: playerCell ? selectColorByWR(playerCell.win_ratio) : colors.heatmapUnavailable,
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

    const shipTypes = normalizeShipTypes(payload);
    const tiers = normalizeTiers(payload);
    if (!shipTypes.length || !tiers.length) {
        drawMessage(containerElement, 'Unable to build tier and ship-type chart axes.', svgWidth, 112, colors);
        return;
    }

    const compact = svgWidth < 480;
    const margin = compact
        ? { top: 48, right: 6, bottom: 42, left: 28 }
        : { top: 62, right: 18, bottom: 42, left: 42 };
    const axisFontSize = compact ? '9px' : '10px';
    const width = svgWidth - margin.left - margin.right;
    const height = svgHeight - margin.top - margin.bottom;
    const container = d3.select(containerElement);
    container.selectAll('*').remove();

    const svgRoot = container.append('svg')
        .attr('width', svgWidth)
        .attr('height', svgHeight);

    const svg = svgRoot.append('g')
        .attr('transform', `translate(${margin.left}, ${margin.top})`);

    const x = d3.scaleBand()
        .domain(shipTypes)
        .range([0, width])
        .padding(0.12);

    const y = d3.scaleBand()
        .domain(tiers.map((value) => String(value)))
        .range([0, height])
        .padding(0.12);

    const minTier = Math.min(...tiers);
    const maxTier = Math.max(...tiers);
    const yCenterOffset = y.bandwidth() / 2;
    const yTrend = d3.scaleLinear()
        .domain([minTier, maxTier])
        .range([height - yCenterOffset, yCenterOffset]);

    const tileByKey = new Map(payload.tiles.map((row: TierTypeTile) => [`${row.ship_type}:${row.ship_tier}`, row]));
    const playerCellByKey = new Map(payload.player_cells.map((row: TierTypePlayerCell) => [`${row.ship_type}:${row.ship_tier}`, row]));
    const trendByType = new Map(payload.trend.map((row: TierTypeTrendPoint) => [row.ship_type, row]));
    const maxTileCount = d3.max(payload.tiles, (row: TierTypeTile) => row.count) || 1;
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
        .data(payload.tiles)
        .enter()
        .append('rect')
        .attr('x', (row: TierTypeTile) => x(row.ship_type) ?? 0)
        .attr('y', (row: TierTypeTile) => y(String(row.ship_tier)) ?? 0)
        .attr('width', x.bandwidth())
        .attr('height', y.bandwidth())
        .attr('rx', 4)
        .attr('fill', (row: TierTypeTile) => tileColor(row.count))
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
        .attr('x', width / 2)
        .attr('y', height + 34)
        .attr('text-anchor', 'middle')
        .style('fill', colors.labelMuted)
        .style('font-size', axisFontSize)
        .text(payload.x_label);

    const summaryGroup = svgRoot.append('g')
        .attr('transform', `translate(${margin.left + 6}, 28)`);

    const renderSummary = (tile: TierTypeTile) => {
        summaryGroup.selectAll('*').remove();

        const playerCell = playerCellByKey.get(`${tile.ship_type}:${tile.ship_tier}`);
        const trendPoint = trendByType.get(tile.ship_type);
        const trendDelta = trendPoint ? tile.ship_tier - trendPoint.avg_tier : null;

        renderSummaryCard(summaryGroup, tile, playerCell, trendDelta, colors, svgWidth);
    };

    const tileNodes = svg.selectAll('.tier-type-tile')
        .data(payload.tiles)
        .enter()
        .append('rect')
        .attr('class', 'tier-type-tile')
        .attr('x', (row: TierTypeTile) => x(row.ship_type) ?? 0)
        .attr('y', (row: TierTypeTile) => y(String(row.ship_tier)) ?? 0)
        .attr('width', x.bandwidth())
        .attr('height', y.bandwidth())
        .attr('rx', 4)
        .attr('fill', 'transparent')
        .attr('stroke', 'transparent')
        .on('mouseover', function (this: SVGRectElement, _event: MouseEvent, row: TierTypeTile) {
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

    const trendLine = d3.line()
        .x((row: unknown) => {
            const point = row as TierTypeTrendPoint;
            return (x(point.ship_type) ?? 0) + (x.bandwidth() / 2);
        })
        .y((row: unknown) => {
            const point = row as TierTypeTrendPoint;
            return yTrend(point.avg_tier);
        })
        .curve(d3.curveMonotoneX);

    svg.append('path')
        .datum(payload.trend)
        .attr('fill', 'none')
        .attr('stroke', colors.labelStrong)
        .attr('stroke-width', 1.6)
        .attr('d', trendLine);

    svg.selectAll('.trend-marker')
        .data(payload.trend)
        .enter()
        .append('circle')
        .attr('class', 'trend-marker')
        .attr('cx', (row: TierTypeTrendPoint) => (x(row.ship_type) ?? 0) + (x.bandwidth() / 2))
        .attr('cy', (row: TierTypeTrendPoint) => yTrend(row.avg_tier))
        .attr('r', 2.8)
        .attr('fill', colors.labelStrong);

    svg.selectAll('.player-cell')
        .data(payload.player_cells)
        .enter()
        .append('circle')
        .attr('class', 'player-cell')
        .attr('cx', (row: TierTypePlayerCell) => (x(row.ship_type) ?? 0) + (x.bandwidth() / 2))
        .attr('cy', (row: TierTypePlayerCell) => (y(String(row.ship_tier)) ?? 0) + (y.bandwidth() / 2))
        .attr('r', (row: TierTypePlayerCell) => Math.max(4, playerRadius(row.pvp_battles)))
        .attr('fill', (row: TierTypePlayerCell) => selectColorByWR(row.win_ratio))
        .attr('fill-opacity', 0.92)
        .attr('stroke', colors.barStroke)
        .attr('stroke-width', 1.6);

    const defaultTile = payload.player_cells.length
        ? tileByKey.get(`${payload.player_cells[0].ship_type}:${payload.player_cells[0].ship_tier}`)
        : payload.tiles[0];

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

        const resolveWidth = () => Math.min(svgWidth, Math.max(containerElement.clientWidth || svgWidth, 320));

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
