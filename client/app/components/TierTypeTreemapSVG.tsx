import React, { useEffect, useRef, useState } from 'react';
import * as d3 from 'd3';
import {
    chartColors,
    formatCompactCount,
    resolveContainerChartWidth,
    type ChartColors as Colors,
    type ChartTheme,
} from '../lib/chartTheme';
import {
    SHIP_TYPE_ABBREV,
    buildTierTypeDietModel,
    cellTooltipLines,
    confidenceFadedWrColor,
    type TierTypeDietModel,
} from './tierTypeDietModel';
import DietHoverTooltip, { type DietHoverState } from './DietHoverTooltip';
import type { TierTypePayload, TierTypePlayerCell } from './playerProfileChartData';

/**
 * The Profile tab's tier figure as a treemap: the "Map" half of the Bars/Map
 * toggle, mirroring the landing page's most-played-ships Map/Plot switch.
 *
 * Same contingency table as TierTypeDietSVG, same encoding of each cell:
 *  - one tile per played tier x class cell, AREA = battles (additive, so the
 *    whole figure is the captain's random-battle diet and each tile is its
 *    share of it; the bar view reads the same count as length instead)
 *  - fill = win rate on the shared WoWS ramp, damped toward neutral as the
 *    sample thins, exactly as the bars are
 *  - tiles with room carry a direct label: tier + class, battles, win rate
 *
 * The treemap gives up the bar view's per-class baselines and class-total
 * margin; in exchange the part-to-whole reading (how much of the diet is T10
 * BB?) is immediate. The two views share one model, one tooltip, and one
 * drill-down into the Ships tab.
 */

// Hit any tile, label only the ones with room. These gates match the landing
// treemap and the Activity treemaps, so tile typography is one system.
const LABEL_MIN_W = 46;
const LABEL_MIN_H = 24;
const SECOND_LINE_MIN_H = 38;
const THIRD_LINE_MIN_H = 52;
const LABEL_PAD_X = 5;

// A line is drawn only when it fits the tile's width, so a narrow tile never
// bleeds "194 bat" into its neighbour. Width is estimated from the font size
// (about 0.58em per glyph for these digit-heavy strings, tabular nums on).
const fitsWidth = (text: string, fontSize: number, tileWidth: number): boolean =>
    text.length * fontSize * 0.58 + LABEL_PAD_X * 2 <= tileWidth;

// d3 is untyped in this project (see RealmTopShipsTreemapSVG), so the laid-out
// leaf is described structurally: the rect d3.treemap wrote plus the cell.
interface TreemapLeaf {
    x0: number;
    y0: number;
    x1: number;
    y1: number;
    data: TierTypePlayerCell;
}

/** Treemap height tracks width but stays within the bar view's usual range. */
export const treemapHeightForWidth = (width: number): number =>
    Math.max(260, Math.min(420, Math.round(width * 0.55)));

const tileLabel = (row: TierTypePlayerCell): string =>
    `T${row.ship_tier} ${SHIP_TYPE_ABBREV[row.ship_type] ?? row.ship_type}`;

interface DrawHandlers {
    onCellEnter: (state: DietHoverState) => void;
    onCellLeave: () => void;
    /** Drill down to the Ships tab filtered to one tier x class. */
    onCellSelect?: (shipType: string, shipTier: number) => void;
}

const drawTreemap = (
    svgElement: SVGSVGElement,
    containerElement: HTMLDivElement,
    model: TierTypeDietModel,
    width: number,
    colors: Colors,
    theme: ChartTheme,
    handlers: DrawHandlers,
) => {
    const svgRoot = d3.select(svgElement);
    svgRoot.selectAll('*').remove();

    if (model.cells.length < 2) {
        svgRoot
            .attr('width', width)
            .attr('height', 112)
            .attr('aria-label', 'Not enough ship variety to chart')
            .append('text')
            .attr('x', 16)
            .attr('y', 24)
            .style('fill', colors.labelText)
            .style('font-size', '12px')
            .text('This captain does not have enough tier and ship-type variety yet to draw a useful chart.');
        return;
    }

    const height = treemapHeightForWidth(width);
    svgRoot
        .attr('width', width)
        .attr('height', height)
        .attr('aria-label',
            `Random battles by tier treemap: ${model.cells.length} tier and class combinations over `
            + `${model.totalBattles.toLocaleString()} random battles, each tile sized by battles.`);

    const root = d3.hierarchy({ children: model.cells } as { children: TierTypePlayerCell[] })
        .sum((node: Partial<TierTypePlayerCell>) => node.pvp_battles ?? 0)
        .sort((a: { value?: number }, b: { value?: number }) => (b.value ?? 0) - (a.value ?? 0));
    d3.treemap().size([width, height]).paddingInner(2).round(true)(root);
    const leaves = root.leaves() as TreemapLeaf[];

    const drillable = typeof handlers.onCellSelect === 'function';
    const tiles = svgRoot.selectAll('g.tile')
        .data(leaves)
        .join('g')
        .attr('class', 'tile')
        .attr('transform', (leaf: TreemapLeaf) => `translate(${leaf.x0},${leaf.y0})`);

    tiles.append('rect')
        .attr('width', (leaf: TreemapLeaf) => Math.max(0, leaf.x1 - leaf.x0))
        .attr('height', (leaf: TreemapLeaf) => Math.max(0, leaf.y1 - leaf.y0))
        .attr('rx', 2)
        .attr('fill', (leaf: TreemapLeaf) => confidenceFadedWrColor(leaf.data.win_ratio, leaf.data.pvp_battles, theme))
        .attr('stroke', 'var(--bg-card)')
        .attr('stroke-width', 1)
        .style('cursor', drillable ? 'pointer' : 'crosshair')
        .on('mousemove', function onMove(this: SVGRectElement, event: MouseEvent, leaf: TreemapLeaf) {
            const bounds = containerElement.getBoundingClientRect();
            svgRoot.selectAll('g.tile rect').attr('opacity', 0.55);
            d3.select(this).attr('opacity', 1);
            handlers.onCellEnter({
                lines: cellTooltipLines(leaf.data, model, theme, drillable),
                x: event.clientX - bounds.left,
                y: event.clientY - bounds.top,
            });
        })
        .on('mouseleave', () => {
            svgRoot.selectAll('g.tile rect').attr('opacity', 1);
            handlers.onCellLeave();
        })
        .on('click', (_event: MouseEvent, leaf: TreemapLeaf) => {
            handlers.onCellSelect?.(leaf.data.ship_type, leaf.data.ship_tier);
        })
        // Native title so a tile's numbers are reachable without a pointer.
        .append('title')
        .text((leaf: TreemapLeaf) => `${tileLabel(leaf.data)}: ${leaf.data.pvp_battles} battles, `
            + `${(leaf.data.win_ratio * 100).toFixed(1)}% win rate`);

    // Direct labels where they fit; contrast picked off the tile's own
    // lightness so text reads on every WR shade and on the thin-sample neutral.
    tiles.each(function labelTile(this: SVGGElement, leaf: TreemapLeaf) {
        const w = leaf.x1 - leaf.x0;
        const h = leaf.y1 - leaf.y0;
        if (w < LABEL_MIN_W || h < LABEL_MIN_H) return;
        const fill = confidenceFadedWrColor(leaf.data.win_ratio, leaf.data.pvp_battles, theme);
        const textColor = d3.hsl(fill).l > 0.62 ? '#1a1a1a' : '#f5f5f5';
        const node = d3.select(this);
        const title = tileLabel(leaf.data);
        if (!fitsWidth(title, 12, w)) return;
        node.append('text')
            .attr('x', LABEL_PAD_X).attr('y', 15)
            .attr('font-size', 12).attr('font-weight', 600).attr('fill', textColor)
            .style('pointer-events', 'none')
            .text(title);
        const battlesLine = `${formatCompactCount(leaf.data.pvp_battles)} battles`;
        if (h >= SECOND_LINE_MIN_H && fitsWidth(battlesLine, 11, w)) {
            node.append('text')
                .attr('x', LABEL_PAD_X).attr('y', 29)
                .attr('font-size', 11).attr('fill', textColor).attr('opacity', 0.85)
                .style('font-variant-numeric', 'tabular-nums')
                .style('pointer-events', 'none')
                .text(battlesLine);
        }
        const wrLine = `${(leaf.data.win_ratio * 100).toFixed(1)}% WR`;
        if (h >= THIRD_LINE_MIN_H && fitsWidth(wrLine, 10, w)) {
            node.append('text')
                .attr('x', LABEL_PAD_X).attr('y', 42)
                .attr('font-size', 10).attr('fill', textColor).attr('opacity', 0.75)
                .style('font-variant-numeric', 'tabular-nums')
                .style('pointer-events', 'none')
                .text(wrLine);
        }
    });
};

interface TierTypeTreemapSVGProps {
    /** Always supplied by the Profile panel behind a non-null guard. */
    data: TierTypePayload;
    svgWidth?: number;
    theme?: ChartTheme;
    /** Drill down to the Ships tab filtered to this tier x class. */
    onCellSelect?: (shipType: string, shipTier: number) => void;
}

const TierTypeTreemapSVG: React.FC<TierTypeTreemapSVGProps> = ({
    data,
    svgWidth = 570,
    theme = 'light',
    onCellSelect,
}) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const svgRef = useRef<SVGSVGElement>(null);
    const [hover, setHover] = useState<DietHoverState | null>(null);
    const [measuredWidth, setMeasuredWidth] = useState(svgWidth);
    // Held in a ref so a parent re-render handing down a fresh callback does
    // not force a full D3 redraw (same arrangement as TierTypeDietSVG).
    const onCellSelectRef = useRef(onCellSelect);
    useEffect(() => {
        onCellSelectRef.current = onCellSelect;
    }, [onCellSelect]);

    useEffect(() => {
        const containerElement = containerRef.current;
        const svgElement = svgRef.current;
        if (!containerElement || !svgElement) {
            return;
        }

        const colors = chartColors[theme];
        const model = buildTierTypeDietModel(data);
        let resizeFrame: number | null = null;

        const redraw = () => {
            const width = resolveContainerChartWidth(containerElement.clientWidth, svgWidth);
            setMeasuredWidth(width);
            drawTreemap(svgElement, containerElement, model, width, colors, theme, {
                onCellEnter: setHover,
                onCellLeave: () => setHover(null),
                onCellSelect: onCellSelectRef.current
                    ? (shipType, shipTier) => onCellSelectRef.current?.(shipType, shipTier)
                    : undefined,
            });
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
            setHover(null);
        };
    }, [data, svgWidth, theme]);

    return (
        <div ref={containerRef} className="relative w-full">
            <svg ref={svgRef} role="img" />
            {hover && <DietHoverTooltip hover={hover} containerWidth={measuredWidth} />}
        </div>
    );
};

export default TierTypeTreemapSVG;
