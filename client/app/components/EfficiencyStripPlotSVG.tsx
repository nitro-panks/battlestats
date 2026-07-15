import React, { useEffect, useRef } from 'react';
import * as d3 from 'd3';
import { chartColors, type ChartTheme } from '../lib/chartTheme';

export interface EfficiencyBadgeDot {
    shipId: number;
    shipName: string;
    shipType: string;
    shipTier: number;
    badgeClass: number;
    badgeLabel: string;
}

interface EfficiencyStripPlotSVGProps {
    dots: EfficiencyBadgeDot[];
    theme?: ChartTheme;
    svgWidth?: number;
    // Grow the plot to fill at least this SVG height (bands stretch, dots scale
    // up capped). 0 = intrinsic height only.
    minSvgHeight?: number;
}

type Colors = typeof chartColors['light'];

const SHIP_TYPE_ORDER = ['DD', 'CA', 'BB', 'CV', 'Sub'];

const ROMAN_TIERS = ['I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X', 'XI'];

const romanTier = (tier: number): string => ROMAN_TIERS[tier - 1] ?? String(tier);

export const badgeClassColor = (colors: Colors, badgeClass: number): string => {
    if (badgeClass === 1) return colors.badgeE;
    if (badgeClass === 2) return colors.badgeI;
    if (badgeClass === 3) return colors.badgeII;
    return colors.badgeIII;
};

const MIN_DOT_RADIUS = 5;
const MAX_DOT_RADIUS = 14;
const HIT_PAD = 4;
const CELL_PAD_X = 10;
const BAND_PAD_Y = 10;
const MIN_BAND_HEIGHT = 44;
const MARGIN = { top: 50, right: 12, bottom: 46, left: 56 };
const AXIS_FONT_SIZE = '13px';
const CAPTION_FONT_SIZE = '12px';
const SUMMARY_FONT_SIZE = '14px';
// Static force layout: anchor forces pull each dot to its (tier, type) cell
// center, collision packs neighbours into an organic blob. The x-anchor is
// stronger than the y-anchor so crowded cells elongate vertically into their
// band instead of bleeding into the next tier column.
const FORCE_X_STRENGTH = 0.4;
const FORCE_Y_STRENGTH = 0.12;
const COLLIDE_PAD = 2;
const SIMULATION_TICKS = 300;

interface SimNode {
    dot: EfficiencyBadgeDot;
    targetX: number;
    targetY: number;
    x: number;
    y: number;
    vx?: number;
    vy?: number;
    index?: number;
}

const compareDots = (left: EfficiencyBadgeDot, right: EfficiencyBadgeDot): number => {
    if (left.badgeClass !== right.badgeClass) {
        return left.badgeClass - right.badgeClass;
    }
    return left.shipName.localeCompare(right.shipName);
};

// Approximate diameter of n unit-radius circles packed into a round blob.
const blobDiameter = (count: number, radius: number): number =>
    2 * radius * (1 + Math.sqrt(count));

const drawChart = (
    containerElement: HTMLDivElement,
    dots: EfficiencyBadgeDot[],
    svgWidth: number,
    minSvgHeight: number,
    colors: Colors,
) => {
    const container = d3.select(containerElement);
    container.selectAll('*').remove();

    if (!dots.length) {
        return;
    }

    // Tier columns always span at least V–X; extend to any outlier tier present
    // (superships at XI, legacy low-tier badges) so no dot falls off the plot.
    const dataMinTier = d3.min(dots, (dot: EfficiencyBadgeDot) => dot.shipTier) ?? 5;
    const dataMaxTier = d3.max(dots, (dot: EfficiencyBadgeDot) => dot.shipTier) ?? 10;
    const minTier = Math.min(5, dataMinTier);
    const maxTier = Math.max(10, dataMaxTier);
    const tiers = d3.range(minTier, maxTier + 1);

    const presentTypes = new Set(dots.map((dot) => dot.shipType));
    const shipTypes = [
        ...SHIP_TYPE_ORDER.filter((shipType) => presentTypes.has(shipType)),
        ...[...presentTypes].filter((shipType) => !SHIP_TYPE_ORDER.includes(shipType)).sort(),
    ];

    const plotWidth = svgWidth - MARGIN.left - MARGIN.right;
    const colWidth = plotWidth / tiers.length;

    const cellDots = new Map<string, EfficiencyBadgeDot[]>();
    for (const dot of dots) {
        const key = `${dot.shipType}|${dot.shipTier}`;
        const cell = cellDots.get(key);
        if (cell) {
            cell.push(dot);
        } else {
            cellDots.set(key, [dot]);
        }
    }

    // Dot radius: the densest cell's blob must fit its column; the panel-fill
    // stretch below reclaims any leftover vertical room as breathing space.
    const cellCounts = [...cellDots.values()].map((cell) => cell.length);
    let dotRadius = Math.min(
        MAX_DOT_RADIUS,
        ...cellCounts.map((count) => (colWidth - 2 * CELL_PAD_X) / (2 * (1 + Math.sqrt(count)))),
    );
    dotRadius = Math.max(MIN_DOT_RADIUS, dotRadius);

    // Each type band is as tall as its densest blob needs.
    const computeBandHeights = (radius: number): number[] => shipTypes.map((shipType) => {
        const maxCellCount = Math.max(
            1,
            ...tiers.map((tier: number) => cellDots.get(`${shipType}|${tier}`)?.length ?? 0),
        );
        return Math.max(MIN_BAND_HEIGHT, blobDiameter(maxCellCount, radius) + 2 * BAND_PAD_Y);
    });

    const availablePlot = Math.max(0, minSvgHeight - MARGIN.top - MARGIN.bottom);
    let bandBase = computeBandHeights(dotRadius);
    let bandTotal = bandBase.reduce((sum, height) => sum + height, 0);
    if (availablePlot > 0 && bandTotal > availablePlot) {
        dotRadius = Math.max(MIN_DOT_RADIUS, dotRadius * (availablePlot / bandTotal));
        bandBase = computeBandHeights(dotRadius);
        bandTotal = bandBase.reduce((sum, height) => sum + height, 0);
    }
    const bandStretch = availablePlot > bandTotal ? availablePlot / bandTotal : 1;
    const bandHeights = bandBase.map((height) => height * bandStretch);

    const bandTops = bandHeights.reduce<number[]>((tops, _height, index) => {
        tops.push(index === 0 ? 0 : tops[index - 1] + bandHeights[index - 1]);
        return tops;
    }, []);
    const plotHeight = bandHeights.reduce((sum, height) => sum + height, 0);
    const svgHeight = MARGIN.top + plotHeight + MARGIN.bottom;

    // Force-cluster the dots around their (tier, type) cell centers. Seeded
    // deterministic start positions near each anchor keep the static layout
    // stable across renders; the collision force packs each cell into a blob.
    const nodes: SimNode[] = [];
    shipTypes.forEach((shipType, typeIndex) => {
        tiers.forEach((tier: number, tierIndex: number) => {
            const cell = cellDots.get(`${shipType}|${tier}`);
            if (!cell) {
                return;
            }

            const targetX = tierIndex * colWidth + colWidth / 2;
            const targetY = bandTops[typeIndex] + bandHeights[typeIndex] / 2;
            [...cell].sort(compareDots).forEach((dot, dotIndex) => {
                nodes.push({
                    dot,
                    targetX,
                    targetY,
                    x: targetX + ((dotIndex * 37) % 17) - 8,
                    y: targetY + ((dotIndex * 23) % 13) - 6,
                });
            });
        });
    });

    d3.forceSimulation(nodes)
        .force('x', d3.forceX((node: SimNode) => node.targetX).strength(FORCE_X_STRENGTH))
        .force('y', d3.forceY((node: SimNode) => node.targetY).strength(FORCE_Y_STRENGTH))
        .force('collide', d3.forceCollide(dotRadius + COLLIDE_PAD).iterations(3))
        .stop()
        .tick(SIMULATION_TICKS);

    // Keep settled dots inside the plot frame and their own type band.
    nodes.forEach((node) => {
        const typeIndex = shipTypes.indexOf(node.dot.shipType);
        const bandTop = bandTops[typeIndex] ?? 0;
        const bandHeight = bandHeights[typeIndex] ?? plotHeight;
        node.x = Math.max(dotRadius + 1, Math.min(plotWidth - dotRadius - 1, node.x));
        node.y = Math.max(bandTop + dotRadius + 1, Math.min(bandTop + bandHeight - dotRadius - 1, node.y));
    });

    const svgRoot = container.append('svg')
        .attr('width', svgWidth)
        .attr('height', svgHeight);

    const svg = svgRoot.append('g')
        .attr('transform', `translate(${MARGIN.left}, ${MARGIN.top})`);

    // No grid — the clusters themselves carry the lattice; a single baseline
    // anchors the tier labels.
    svg.append('line')
        .attr('x1', 0)
        .attr('x2', plotWidth)
        .attr('y1', plotHeight)
        .attr('y2', plotHeight)
        .attr('stroke', colors.axisLine)
        .attr('stroke-width', 1);

    shipTypes.forEach((shipType, index) => {
        svg.append('text')
            .attr('x', -12)
            .attr('y', bandTops[index] + bandHeights[index] / 2)
            .attr('text-anchor', 'end')
            .attr('dominant-baseline', 'middle')
            .style('font-size', AXIS_FONT_SIZE)
            .style('font-weight', '500')
            .style('fill', colors.axisText)
            .text(shipType);
    });

    tiers.forEach((tier: number, index: number) => {
        svg.append('text')
            .attr('x', index * colWidth + colWidth / 2)
            .attr('y', plotHeight + 20)
            .attr('text-anchor', 'middle')
            .style('font-size', AXIS_FONT_SIZE)
            .style('font-weight', '500')
            .style('fill', colors.labelMuted)
            .text(romanTier(tier));
    });

    svg.append('text')
        .attr('x', plotWidth / 2)
        .attr('y', plotHeight + 40)
        .attr('text-anchor', 'middle')
        .style('font-size', CAPTION_FONT_SIZE)
        .style('fill', colors.labelMuted)
        .text('Ship Tier');

    const summaryGroup = svgRoot.append('g')
        .attr('transform', `translate(${MARGIN.left}, 18)`);

    const renderSummary = (dot: EfficiencyBadgeDot) => {
        summaryGroup.selectAll('*').remove();

        const line = summaryGroup.append('text')
            .attr('x', 0)
            .attr('y', 0)
            .attr('dominant-baseline', 'middle')
            .style('font-size', SUMMARY_FONT_SIZE);

        line.append('tspan')
            .style('font-weight', '700')
            .style('fill', colors.labelStrong)
            .text(dot.shipName);

        line.append('tspan')
            .style('fill', colors.labelMuted)
            .text('  ·  ');

        line.append('tspan')
            .style('font-weight', '700')
            .style('fill', badgeClassColor(colors, dot.badgeClass))
            .text(`Badge ${dot.badgeLabel}`);

        line.append('tspan')
            .style('fill', colors.labelMid)
            .text(`  ·  ${dot.shipType}  ·  Tier ${romanTier(dot.shipTier)}`);
    };

    const dotStrokeWidth = dotRadius > 8 ? 2 : 1.5;
    const dotNodes = svg.append('g')
        .selectAll('circle')
        .data(nodes)
        .enter()
        .append('circle')
        .attr('class', 'badge-dot')
        .attr('cx', (node: SimNode) => node.x)
        .attr('cy', (node: SimNode) => node.y)
        .attr('r', dotRadius)
        .attr('fill', (node: SimNode) => badgeClassColor(colors, node.dot.badgeClass))
        .attr('stroke', colors.barStroke)
        .attr('stroke-width', dotStrokeWidth)
        .nodes();

    // Oversized invisible hit targets so hovering small dots is forgiving.
    svg.append('g')
        .selectAll('circle')
        .data(nodes)
        .enter()
        .append('circle')
        .attr('class', 'badge-dot-hit')
        .attr('cx', (node: SimNode) => node.x)
        .attr('cy', (node: SimNode) => node.y)
        .attr('r', dotRadius + HIT_PAD)
        .attr('fill', 'transparent')
        .style('cursor', 'default')
        .on('mouseover', function (_event: MouseEvent, node: SimNode) {
            const index = nodes.indexOf(node);
            if (index >= 0) {
                d3.select(dotNodes[index])
                    .attr('stroke', colors.labelStrong)
                    .attr('stroke-width', dotStrokeWidth + 0.5);
            }
            renderSummary(node.dot);
        })
        .on('mouseout', function (_event: MouseEvent, node: SimNode) {
            const index = nodes.indexOf(node);
            if (index >= 0) {
                d3.select(dotNodes[index])
                    .attr('stroke', colors.barStroke)
                    .attr('stroke-width', dotStrokeWidth);
            }
        });

    // Default the summary to the player's best badge (class asc, then name) so
    // the strip never starts blank.
    const bestDot = [...dots].sort(compareDots)[0];
    renderSummary(bestDot);
};

const EfficiencyStripPlotSVG: React.FC<EfficiencyStripPlotSVGProps> = ({
    dots,
    theme = 'light',
    svgWidth = 570,
    minSvgHeight = 0,
}) => {
    const containerRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        const containerElement = containerRef.current;
        if (!containerElement) {
            return;
        }

        const colors = chartColors[theme];
        let resizeFrame: number | null = null;

        const resolveWidth = () => Math.max(containerElement.clientWidth || svgWidth, 320);

        const redraw = () => {
            drawChart(containerElement, dots, resolveWidth(), minSvgHeight, colors);
        };

        const onResize = () => {
            if (resizeFrame != null) cancelAnimationFrame(resizeFrame);
            resizeFrame = requestAnimationFrame(redraw);
        };

        redraw();
        window.addEventListener('resize', onResize);
        return () => {
            window.removeEventListener('resize', onResize);
            if (resizeFrame != null) cancelAnimationFrame(resizeFrame);
        };
    }, [dots, minSvgHeight, svgWidth, theme]);

    return <div ref={containerRef} className="w-full" />;
};

export default EfficiencyStripPlotSVG;
