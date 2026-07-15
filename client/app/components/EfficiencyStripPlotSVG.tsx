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

const DOT_RADIUS = 5;
const DOT_PITCH = 14;
const HIT_RADIUS = 9;
const CELL_PAD_X = 7;
const BAND_PAD_Y = 9;
const MIN_BAND_HEIGHT = 34;
const MARGIN = { top: 44, right: 12, bottom: 40, left: 46 };

interface PositionedDot {
    dot: EfficiencyBadgeDot;
    cx: number;
    cy: number;
}

const compareDots = (left: EfficiencyBadgeDot, right: EfficiencyBadgeDot): number => {
    if (left.badgeClass !== right.badgeClass) {
        return left.badgeClass - right.badgeClass;
    }
    return left.shipName.localeCompare(right.shipName);
};

const drawChart = (
    containerElement: HTMLDivElement,
    dots: EfficiencyBadgeDot[],
    svgWidth: number,
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
    const dotsPerRow = Math.max(1, Math.floor((colWidth - 2 * CELL_PAD_X) / DOT_PITCH));

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
    cellDots.forEach((cell) => cell.sort(compareDots));

    // Each type band is as tall as its densest cell needs; total height follows.
    const bandHeights = shipTypes.map((shipType) => {
        const maxCellCount = Math.max(
            1,
            ...tiers.map((tier: number) => cellDots.get(`${shipType}|${tier}`)?.length ?? 0),
        );
        const rowsNeeded = Math.ceil(maxCellCount / dotsPerRow);
        return Math.max(MIN_BAND_HEIGHT, rowsNeeded * DOT_PITCH + 2 * BAND_PAD_Y);
    });
    const bandTops = bandHeights.reduce<number[]>((tops, _height, index) => {
        tops.push(index === 0 ? 0 : tops[index - 1] + bandHeights[index - 1]);
        return tops;
    }, []);
    const plotHeight = bandHeights.reduce((sum, height) => sum + height, 0);
    const svgHeight = MARGIN.top + plotHeight + MARGIN.bottom;

    const svgRoot = container.append('svg')
        .attr('width', svgWidth)
        .attr('height', svgHeight);

    const svg = svgRoot.append('g')
        .attr('transform', `translate(${MARGIN.left}, ${MARGIN.top})`);

    // Recessive cell scaffolding: tier column separators + type band separators.
    for (let index = 1; index < tiers.length; index += 1) {
        svg.append('line')
            .attr('x1', index * colWidth)
            .attr('x2', index * colWidth)
            .attr('y1', 0)
            .attr('y2', plotHeight)
            .attr('stroke', colors.gridLine)
            .attr('stroke-width', 1);
    }
    for (let index = 1; index < shipTypes.length; index += 1) {
        svg.append('line')
            .attr('x1', 0)
            .attr('x2', plotWidth)
            .attr('y1', bandTops[index])
            .attr('y2', bandTops[index])
            .attr('stroke', colors.gridLine)
            .attr('stroke-width', 1);
    }
    svg.append('line')
        .attr('x1', 0)
        .attr('x2', plotWidth)
        .attr('y1', plotHeight)
        .attr('y2', plotHeight)
        .attr('stroke', colors.axisLine)
        .attr('stroke-width', 1);

    shipTypes.forEach((shipType, index) => {
        svg.append('text')
            .attr('x', -10)
            .attr('y', bandTops[index] + bandHeights[index] / 2)
            .attr('text-anchor', 'end')
            .attr('dominant-baseline', 'middle')
            .style('font-size', '10px')
            .style('font-weight', '500')
            .style('fill', colors.axisText)
            .text(shipType);
    });

    tiers.forEach((tier: number, index: number) => {
        svg.append('text')
            .attr('x', index * colWidth + colWidth / 2)
            .attr('y', plotHeight + 16)
            .attr('text-anchor', 'middle')
            .style('font-size', '10px')
            .style('font-weight', '500')
            .style('fill', colors.labelMuted)
            .text(romanTier(tier));
    });

    svg.append('text')
        .attr('x', plotWidth / 2)
        .attr('y', plotHeight + 32)
        .attr('text-anchor', 'middle')
        .style('font-size', '10px')
        .style('fill', colors.labelMuted)
        .text('Ship Tier');

    // Left-aligned packed rows per cell, vertically centered in the band.
    const positioned: PositionedDot[] = [];
    shipTypes.forEach((shipType, typeIndex) => {
        tiers.forEach((tier: number, tierIndex: number) => {
            const cell = cellDots.get(`${shipType}|${tier}`);
            if (!cell) {
                return;
            }

            const rowsUsed = Math.ceil(cell.length / dotsPerRow);
            const blockHeight = rowsUsed * DOT_PITCH;
            const startX = tierIndex * colWidth + CELL_PAD_X + DOT_PITCH / 2;
            const startY = bandTops[typeIndex] + (bandHeights[typeIndex] - blockHeight) / 2 + DOT_PITCH / 2;

            cell.forEach((dot, dotIndex) => {
                positioned.push({
                    dot,
                    cx: startX + (dotIndex % dotsPerRow) * DOT_PITCH,
                    cy: startY + Math.floor(dotIndex / dotsPerRow) * DOT_PITCH,
                });
            });
        });
    });

    const summaryGroup = svgRoot.append('g')
        .attr('transform', `translate(${MARGIN.left}, 16)`);

    const renderSummary = (dot: EfficiencyBadgeDot) => {
        summaryGroup.selectAll('*').remove();

        const line = summaryGroup.append('text')
            .attr('x', 0)
            .attr('y', 0)
            .attr('dominant-baseline', 'middle')
            .style('font-size', '12px');

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

    const dotNodes = svg.append('g')
        .selectAll('circle')
        .data(positioned)
        .enter()
        .append('circle')
        .attr('class', 'badge-dot')
        .attr('cx', (entry: PositionedDot) => entry.cx)
        .attr('cy', (entry: PositionedDot) => entry.cy)
        .attr('r', DOT_RADIUS)
        .attr('fill', (entry: PositionedDot) => badgeClassColor(colors, entry.dot.badgeClass))
        .attr('stroke', colors.barStroke)
        .attr('stroke-width', 1.5)
        .nodes();

    // Oversized invisible hit targets so hovering small dots is forgiving.
    svg.append('g')
        .selectAll('circle')
        .data(positioned)
        .enter()
        .append('circle')
        .attr('class', 'badge-dot-hit')
        .attr('cx', (entry: PositionedDot) => entry.cx)
        .attr('cy', (entry: PositionedDot) => entry.cy)
        .attr('r', HIT_RADIUS)
        .attr('fill', 'transparent')
        .style('cursor', 'default')
        .on('mouseover', function (_event: MouseEvent, entry: PositionedDot) {
            const index = positioned.indexOf(entry);
            if (index >= 0) {
                d3.select(dotNodes[index])
                    .attr('stroke', colors.labelStrong)
                    .attr('stroke-width', 2);
            }
            renderSummary(entry.dot);
        })
        .on('mouseout', function (_event: MouseEvent, entry: PositionedDot) {
            const index = positioned.indexOf(entry);
            if (index >= 0) {
                d3.select(dotNodes[index])
                    .attr('stroke', colors.barStroke)
                    .attr('stroke-width', 1.5);
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
            drawChart(containerElement, dots, resolveWidth(), colors);
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
    }, [dots, svgWidth, theme]);

    return <div ref={containerRef} className="w-full" />;
};

export default EfficiencyStripPlotSVG;
