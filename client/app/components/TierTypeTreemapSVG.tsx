import React, { useEffect, useMemo, useRef, useState } from 'react';
import * as d3 from 'd3';
import {
    chartColors,
    formatCompactCount,
    resolveContainerChartWidth,
    type ChartColors as Colors,
    type ChartTheme,
} from '../lib/chartTheme';
import { useT } from '../context/LocaleContext';
import {
    SHIP_TYPE_ABBREV,
    buildTierTypeDietModel,
    confidenceFadedWrColor,
    marginTooltipLines,
    type DietMarginTotal,
    type TierTypeDietModel,
} from './tierTypeDietModel';
import DietHoverTooltip, { type DietHoverState } from './DietHoverTooltip';
import type { TierTypePayload } from './playerProfileChartData';

/**
 * The Profile tab's tier figure as a pair of treemaps: the "Map" half of the
 * Bars/Map toggle, mirroring the landing page's most-played-ships Map/Plot
 * switch and the Activity tab's type + tier treemap row.
 *
 * Two panels side by side, both aggregated from the same contingency table
 * TierTypeDietSVG draws cell by cell:
 *  - BY TIER  — one tile per tier the captain sails, AREA = battles at that
 *    tier across every class
 *  - BY CLASS — one tile per ship class, AREA = battles in that class across
 *    every tier (the bar view's bottom margin, as a map)
 * Fill in both = the group's win rate (wins ÷ battles over the whole group,
 * not an average of cell rates) on the shared WoWS ramp, damped toward neutral
 * as the sample thins, exactly as the bars are.
 *
 * Each panel is a part-to-whole reading of the diet along one axis; the bar
 * view keeps the per-cell cross of the two. Tiles drill into the Ships tab:
 * a tier tile filters to that tier across all classes, a class tile to that
 * class across all tiers.
 */

// Hit any tile, label only the ones with room. These gates match the landing
// treemap and the Activity treemaps, so tile typography is one system.
const LABEL_MIN_W = 40;
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
// leaf is described structurally: the rect d3.treemap wrote plus the group.
interface TreemapLeaf {
    x0: number;
    y0: number;
    x1: number;
    y1: number;
    data: DietMarginTotal;
}

type PanelAxis = 'tier' | 'class';

/** Panel height tracks its own width; two panels share a row on sm+. */
export const treemapPanelHeightForWidth = (width: number): number =>
    Math.max(200, Math.min(340, Math.round(width * 0.62)));

const tileLabel = (axis: PanelAxis, total: DietMarginTotal): string =>
    (axis === 'tier' ? `T${total.key}` : (SHIP_TYPE_ABBREV[total.key] ?? total.key));

interface DrawHandlers {
    onTileEnter: (state: DietHoverState) => void;
    onTileLeave: () => void;
    onTileSelect?: (key: string) => void;
}

const drawPanel = (
    svgElement: SVGSVGElement,
    containerElement: HTMLDivElement,
    axis: PanelAxis,
    totals: DietMarginTotal[],
    model: TierTypeDietModel,
    width: number,
    theme: ChartTheme,
    handlers: DrawHandlers,
) => {
    const svgRoot = d3.select(svgElement);
    svgRoot.selectAll('*').remove();

    const height = treemapPanelHeightForWidth(width);
    const played = totals.filter((total) => total.battles > 0);
    svgRoot
        .attr('width', width)
        .attr('height', height)
        .attr('aria-label', axis === 'tier'
            ? `Random battles by tier treemap: ${played.length} tiers over `
              + `${model.totalBattles.toLocaleString()} random battles, each tile sized by battles.`
            : `Random battles by ship class treemap: ${played.length} classes over `
              + `${model.totalBattles.toLocaleString()} random battles, each tile sized by battles.`);

    const root = d3.hierarchy({ children: played } as { children: DietMarginTotal[] })
        .sum((node: Partial<DietMarginTotal>) => node.battles ?? 0)
        .sort((a: { value?: number }, b: { value?: number }) => (b.value ?? 0) - (a.value ?? 0));
    d3.treemap().size([width, height]).paddingInner(2).round(true)(root);
    const leaves = root.leaves() as TreemapLeaf[];

    const drillable = typeof handlers.onTileSelect === 'function';
    const tiles = svgRoot.selectAll('g.tile')
        .data(leaves)
        .join('g')
        .attr('class', 'tile')
        .attr('data-axis', axis)
        .attr('transform', (leaf: TreemapLeaf) => `translate(${leaf.x0},${leaf.y0})`);

    tiles.append('rect')
        .attr('width', (leaf: TreemapLeaf) => Math.max(0, leaf.x1 - leaf.x0))
        .attr('height', (leaf: TreemapLeaf) => Math.max(0, leaf.y1 - leaf.y0))
        .attr('rx', 2)
        .attr('fill', (leaf: TreemapLeaf) => confidenceFadedWrColor(leaf.data.winRatio, leaf.data.battles, theme))
        .attr('stroke', 'var(--bg-card)')
        .attr('stroke-width', 1)
        .style('cursor', drillable ? 'pointer' : 'crosshair')
        .on('mousemove', function onMove(this: SVGRectElement, event: MouseEvent, leaf: TreemapLeaf) {
            const bounds = containerElement.getBoundingClientRect();
            svgRoot.selectAll('g.tile rect').attr('opacity', 0.55);
            d3.select(this).attr('opacity', 1);
            handlers.onTileEnter({
                lines: marginTooltipLines(axis, leaf.data, model, theme, drillable),
                x: event.clientX - bounds.left,
                y: event.clientY - bounds.top,
            });
        })
        .on('mouseleave', () => {
            svgRoot.selectAll('g.tile rect').attr('opacity', 1);
            handlers.onTileLeave();
        })
        .on('click', (_event: MouseEvent, leaf: TreemapLeaf) => {
            handlers.onTileSelect?.(leaf.data.key);
        })
        // Native title so a tile's numbers are reachable without a pointer.
        .append('title')
        .text((leaf: TreemapLeaf) => `${tileLabel(axis, leaf.data)}: ${leaf.data.battles} battles, `
            + `${(leaf.data.winRatio * 100).toFixed(1)}% win rate`);

    // Direct labels where they fit; contrast picked off the tile's own
    // lightness so text reads on every WR shade and on the thin-sample neutral.
    tiles.each(function labelTile(this: SVGGElement, leaf: TreemapLeaf) {
        const w = leaf.x1 - leaf.x0;
        const h = leaf.y1 - leaf.y0;
        if (w < LABEL_MIN_W || h < LABEL_MIN_H) return;
        const fill = confidenceFadedWrColor(leaf.data.winRatio, leaf.data.battles, theme);
        const textColor = d3.hsl(fill).l > 0.62 ? '#1a1a1a' : '#f5f5f5';
        const node = d3.select(this);
        const title = tileLabel(axis, leaf.data);
        if (!fitsWidth(title, 12, w)) return;
        node.append('text')
            .attr('x', LABEL_PAD_X).attr('y', 15)
            .attr('font-size', 12).attr('font-weight', 600).attr('fill', textColor)
            .style('pointer-events', 'none')
            .text(title);
        const battlesLine = `${formatCompactCount(leaf.data.battles)} battles`;
        if (h >= SECOND_LINE_MIN_H && fitsWidth(battlesLine, 11, w)) {
            node.append('text')
                .attr('x', LABEL_PAD_X).attr('y', 29)
                .attr('font-size', 11).attr('fill', textColor).attr('opacity', 0.85)
                .style('font-variant-numeric', 'tabular-nums')
                .style('pointer-events', 'none')
                .text(battlesLine);
        }
        const wrLine = `${(leaf.data.winRatio * 100).toFixed(1)}% WR`;
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

interface TreemapPanelProps {
    axis: PanelAxis;
    caption: string;
    totals: DietMarginTotal[];
    model: TierTypeDietModel;
    svgWidth: number;
    theme: ChartTheme;
    onTileSelect?: (key: string) => void;
}

// One panel: a caption, its own measured width, its own hover overlay. Each
// panel measures itself because the pair share a row on sm+ and stack below.
const TreemapPanel: React.FC<TreemapPanelProps> = ({
    axis, caption, totals, model, svgWidth, theme, onTileSelect,
}) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const svgRef = useRef<SVGSVGElement>(null);
    const [hover, setHover] = useState<DietHoverState | null>(null);
    const [measuredWidth, setMeasuredWidth] = useState(svgWidth);
    // Held in a ref so a parent re-render handing down a fresh callback does
    // not force a full D3 redraw (same arrangement as TierTypeDietSVG).
    const onTileSelectRef = useRef(onTileSelect);
    useEffect(() => {
        onTileSelectRef.current = onTileSelect;
    }, [onTileSelect]);

    useEffect(() => {
        const containerElement = containerRef.current;
        const svgElement = svgRef.current;
        if (!containerElement || !svgElement) {
            return;
        }
        let resizeFrame: number | null = null;

        const redraw = () => {
            const width = resolveContainerChartWidth(containerElement.clientWidth, svgWidth);
            setMeasuredWidth(width);
            drawPanel(svgElement, containerElement, axis, totals, model, width, theme, {
                onTileEnter: setHover,
                onTileLeave: () => setHover(null),
                onTileSelect: onTileSelectRef.current
                    ? (key) => onTileSelectRef.current?.(key)
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
    }, [axis, totals, model, svgWidth, theme]);

    return (
        <div className="min-w-0">
            <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-[var(--text-muted)]">
                {caption}
            </div>
            <div ref={containerRef} className="relative w-full">
                <svg ref={svgRef} role="img" />
                {hover && <DietHoverTooltip hover={hover} containerWidth={measuredWidth} />}
            </div>
        </div>
    );
};

interface TierTypeTreemapSVGProps {
    /** Always supplied by the Profile panel behind a non-null guard. */
    data: TierTypePayload;
    /** Fallback width per panel when the container cannot be measured. */
    svgWidth?: number;
    theme?: ChartTheme;
    /** Drill down to the Ships tab filtered to this tier, all classes. */
    onTierSelect?: (shipTier: number) => void;
    /** Drill down to the Ships tab filtered to this class, all tiers. */
    onClassSelect?: (shipType: string) => void;
}

const TierTypeTreemapSVG: React.FC<TierTypeTreemapSVGProps> = ({
    data,
    svgWidth = 280,
    theme = 'light',
    onTierSelect,
    onClassSelect,
}) => {
    const t = useT();
    const model = useMemo(() => buildTierTypeDietModel(data), [data]);
    const colors: Colors = chartColors[theme];

    if (model.cells.length < 2) {
        return (
            <div className="relative w-full">
                <svg role="img" width="100%" height={112} aria-label="Not enough ship variety to chart">
                    <text x={16} y={24} style={{ fill: colors.labelText, fontSize: '12px' }}>
                        This captain does not have enough tier and ship-type variety yet to draw a useful chart.
                    </text>
                </svg>
            </div>
        );
    }

    return (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <TreemapPanel
                axis="tier"
                caption={t('common.tier')}
                totals={model.byTier}
                model={model}
                svgWidth={svgWidth}
                theme={theme}
                onTileSelect={onTierSelect ? (key) => onTierSelect(Number(key)) : undefined}
            />
            <TreemapPanel
                axis="class"
                caption={t('common.type')}
                totals={model.byType}
                model={model}
                svgWidth={svgWidth}
                theme={theme}
                onTileSelect={onClassSelect}
            />
        </div>
    );
};

export default TierTypeTreemapSVG;
