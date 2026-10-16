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
    DIET_CLASS_MARGIN_GAP,
    DIET_COL_BAR_MAX,
    DIET_ROW_STEP,
    DIET_TYPE_LABEL_BAND,
    SHIP_TYPE_ABBREV,
    buildTierTypeDietModel,
    cellTooltipLines,
    confidenceFadedWrColor,
    dietFigureHeight,
    formatWinPercent,
    type DietMarginTotal,
    type DietTooltipLine,
} from './tierTypeDietModel';
import DietHoverTooltip from './DietHoverTooltip';
import type { TierTypePayload, TierTypePlayerCell } from './playerProfileChartData';

/**
 * The Profile tab's ship-diet figure: what this captain sails, and where they
 * are actually good at it.
 *
 * Replaces three charts that were all views of one contingency table — the
 * tier x type population heatmap plus the standalone "Performance by Ship
 * Type" and "Performance by Tier" bar charts, which were its column and row
 * margins replotted. The margins are attached here instead.
 *
 * Encoding:
 *  - one bar per played tier x type cell, anchored to its class's baseline,
 *    length on a single scale shared by every cell in the figure (length from
 *    a common baseline is read more accurately than area, and it is what the
 *    old centred pills were reaching for without a baseline to read against)
 *  - fill = win rate on the shared WoWS ramp, damped toward neutral as the
 *    sample thins, so a 2-battle 100% cell cannot pose as a finding
 *  - row / column margins carry the per-tier and per-class totals
 *
 * The population layer is gone on purpose. It was 54% of tiles collapsed into
 * one quantized class, identical for every visitor, drawn under a second
 * colour scale that fought it.
 */

// Vertical layout constants live in tierTypeDietModel (dietFigureHeight) so
// the Map view can match this figure's height exactly.
const ROW_STEP = DIET_ROW_STEP;
const TIER_LABEL_W = 26;
const TYPE_LABEL_BAND = DIET_TYPE_LABEL_BAND;
const COL_BAR_MAX = DIET_COL_BAR_MAX;
const CELL_BAR_H = 11;
const CLASS_MARGIN_GAP = DIET_CLASS_MARGIN_GAP;
// Keeps the widest bar off the figure's right edge now that the grid runs the
// full width.
const GRID_RIGHT_PAD = 10;
const COMPACT_BREAKPOINT = 480;

/** Bar with a 4px rounded data-end and a square baseline, per the mark spec. */
const barPath = (x: number, y: number, w: number, h: number): string => {
    const radius = Math.min(4, w, h / 2);
    if (w <= radius) {
        return `M${x},${y} h${w} v${h} h${-w} Z`;
    }
    return `M${x},${y} h${w - radius} a${radius},${radius} 0 0 1 ${radius},${radius} `
        + `v${h - radius * 2} a${radius},${radius} 0 0 1 ${-radius},${radius} h${-(w - radius)} Z`;
};

interface HoverState {
    lines: DietTooltipLine[];
    x: number;
    y: number;
}

interface DrawHandlers {
    onCellEnter: (state: HoverState) => void;
    onCellLeave: () => void;
    /** Drill down to the Ships tab filtered to one tier x class. */
    onCellSelect?: (shipType: string, shipTier: number) => void;
    /** Drill down to the Ships tab filtered to one class, all tiers. */
    onClassSelect?: (shipType: string) => void;
}

const drawChart = (
    svgElement: SVGSVGElement,
    containerElement: HTMLDivElement,
    payload: TierTypePayload,
    svgWidth: number,
    colors: Colors,
    theme: ChartTheme,
    handlers: DrawHandlers,
) => {
    // The <svg> is owned by React and only its contents are redrawn, so the
    // hover overlay rendered beside it is never torn out from under React.
    const svgRoot = d3.select(svgElement);
    svgRoot.selectAll('*').remove();

    const model = buildTierTypeDietModel(payload);
    if (model.cells.length < 2) {
        svgRoot
            .attr('width', svgWidth)
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

    const compact = svgWidth < COMPACT_BREAKPOINT;

    // The grid runs the full width: the per-tier totals that used to occupy a
    // right-hand panel are gone, and the cells are what the figure is for.
    const gridW = Math.max(120, svgWidth - TIER_LABEL_W - GRID_RIGHT_PAD);
    const gridH = model.tiers.length * ROW_STEP;
    // Shared with the Map view, which fills exactly this height.
    const height = dietFigureHeight(model.tiers.length);

    svgRoot
        .attr('width', svgWidth)
        .attr('height', height)
        .attr('aria-label',
            `Random battles by tier: ${model.cells.length} tier and class combinations over `
            + `${model.totalBattles.toLocaleString()} random battles.`);

    const root = svgRoot.append('g').attr('transform', 'translate(0, 10)');
    const grid = root.append('g').attr('transform', `translate(${TIER_LABEL_W}, 0)`);

    const x = d3.scaleBand().domain(model.shipTypes).range([0, gridW]).padding(0.08);
    const y = d3.scaleBand().domain(model.tiers.map(String)).range([0, gridH]).padding(0);
    // One length scale for every cell in the figure, so a bar in the carrier
    // column is directly comparable to one in the battleship column.
    const cellLength = d3.scaleLinear().domain([0, model.maxCellBattles]).range([0, Math.max(8, x.bandwidth() - 6)]);

    // --- recessive frame ----------------------------------------------------
    grid.append('g').selectAll('line')
        .data(model.tiers)
        .enter()
        .append('line')
        .attr('x1', 0)
        .attr('x2', gridW)
        .attr('y1', (tier: number) => (y(String(tier)) ?? 0) + y.bandwidth())
        .attr('y2', (tier: number) => (y(String(tier)) ?? 0) + y.bandwidth())
        .attr('stroke', colors.gridLine)
        .attr('stroke-width', 1)
        .attr('stroke-opacity', 0.55);

    // Each class's own baseline: the thing the old centred pills lacked.
    grid.append('g').selectAll('line')
        .data(model.shipTypes)
        .enter()
        .append('line')
        .attr('x1', (shipType: string) => (x(shipType) ?? 0) + 1)
        .attr('x2', (shipType: string) => (x(shipType) ?? 0) + 1)
        .attr('y1', 0)
        .attr('y2', gridH)
        .attr('stroke', colors.axisLine)
        .attr('stroke-width', 1)
        .attr('stroke-opacity', 0.6);

    // --- tier labels --------------------------------------------------------
    root.append('g').selectAll('text')
        .data(model.tiers)
        .enter()
        .append('text')
        .attr('x', TIER_LABEL_W - 8)
        .attr('y', (tier: number) => (y(String(tier)) ?? 0) + y.bandwidth() / 2)
        .attr('text-anchor', 'end')
        .attr('dominant-baseline', 'central')
        .style('font-size', '11px')
        .style('font-variant-numeric', 'tabular-nums')
        .style('fill', colors.axisText)
        .text((tier: number) => tier);

    // --- the captain's cells ------------------------------------------------
    grid.append('g').selectAll('path')
        .data(model.cells)
        .enter()
        .append('path')
        .attr('d', (row: TierTypePlayerCell) => barPath(
            (x(row.ship_type) ?? 0) + 1,
            (y(String(row.ship_tier)) ?? 0) + (y.bandwidth() - CELL_BAR_H) / 2,
            Math.max(2, cellLength(row.pvp_battles)),
            CELL_BAR_H,
        ))
        .attr('fill', (row: TierTypePlayerCell) => confidenceFadedWrColor(row.win_ratio, row.pvp_battles, theme))
        // Native title so a cell's numbers are reachable without a pointer,
        // rather than gated behind the hover overlay.
        .append('title')
        .text((row: TierTypePlayerCell) => `Tier ${row.ship_tier} ${SHIP_TYPE_ABBREV[row.ship_type] ?? row.ship_type}: `
            + `${row.pvp_battles} battles, ${(row.win_ratio * 100).toFixed(1)}% win rate`);

    // --- class labels -------------------------------------------------------
    grid.append('g').selectAll('text')
        .data(model.shipTypes)
        .enter()
        .append('text')
        .attr('x', (shipType: string) => (x(shipType) ?? 0) + x.bandwidth() / 2)
        .attr('y', gridH + 15)
        .attr('text-anchor', 'middle')
        .style('font-size', '11px')
        .style('font-weight', '700')
        .style('fill', colors.axisText)
        .text((shipType: string) => SHIP_TYPE_ABBREV[shipType] ?? shipType);

    // --- column margin (was "Performance by Ship Type") ---------------------
    const colBase = gridH + TYPE_LABEL_BAND + CLASS_MARGIN_GAP;
    grid.append('line')
        .attr('x1', 0).attr('x2', gridW)
        .attr('y1', colBase).attr('y2', colBase)
        .attr('stroke', colors.axisLine)
        .attr('stroke-width', 1);

    const maxTypeBattles = d3.max(model.byType, (total: DietMarginTotal) => total.battles) || 1;
    const colBar = d3.scaleLinear().domain([0, maxTypeBattles]).range([0, COL_BAR_MAX]);
    const colBarW = Math.min(22, Math.max(8, x.bandwidth() - 10));

    model.byType.forEach((total) => {
        const bandCenter = (x(total.key) ?? 0) + x.bandwidth() / 2;
        if (total.battles > 0) {
            grid.append('rect')
                .attr('x', bandCenter - colBarW / 2)
                .attr('y', colBase)
                .attr('width', colBarW)
                .attr('height', colBar(total.battles))
                .attr('rx', 3)
                .attr('fill', confidenceFadedWrColor(total.winRatio, total.battles, theme));
        }
        grid.append('text')
            .attr('x', bandCenter)
            .attr('y', colBase + COL_BAR_MAX + 14)
            .attr('text-anchor', 'middle')
            .style('font-size', '11px')
            .style('font-variant-numeric', 'tabular-nums')
            .style('fill', colors.labelMid)
            .text(total.battles > 0 ? formatCompactCount(total.battles) : '—');
        if (total.battles > 0) {
            grid.append('text')
                .attr('x', bandCenter)
                .attr('y', colBase + COL_BAR_MAX + 27)
                .attr('text-anchor', 'middle')
                .style('font-size', '10px')
                .style('font-variant-numeric', 'tabular-nums')
                .style('fill', colors.labelMuted)
                .text(formatWinPercent(total.winRatio));
        }
    });

    if (!compact) {
        root.append('text')
            .attr('x', 0)
            .attr('y', colBase - 6)
            .style('font-size', '10px')
            .style('fill', colors.labelMuted)
            .text('Totals by class');
    }

    // No in-figure legend. The encoding (bar length = battles on one shared
    // scale, fill = win rate, fading toward grey as the sample thins) is
    // carried by the section heading's info tooltip instead, so the figure
    // ends at its own data. Per-cell numbers stay reachable through the hover
    // overlay and each bar's native <title>.

    // --- hover + drill-down layer -------------------------------------------
    // Hit target is the whole cell, so a 2px bar is still comfortably
    // reachable. Every plotted cell has a destination: the Ships tab reads the
    // same battles (`randoms_data?all=true` returns the identical battle total,
    // down to tier 1 and single-battle ships), so none of these are dead ends.
    const drillable = typeof handlers.onCellSelect === 'function';
    const hit = grid.append('g');
    model.cells.forEach((row) => {
        const cellX = x(row.ship_type) ?? 0;
        const cellY = y(String(row.ship_tier)) ?? 0;
        // Sits under the hit rect: a faint wash marking the row you are about
        // to drill into, so the click target is visible before you commit.
        const highlight = hit.append('rect')
            .attr('x', cellX)
            .attr('y', cellY + 1)
            .attr('width', x.bandwidth())
            .attr('height', y.bandwidth() - 2)
            .attr('rx', 3)
            .attr('fill', colors.gridLineBlue)
            .attr('fill-opacity', 0);

        hit.append('rect')
            .attr('x', cellX)
            .attr('y', cellY)
            .attr('width', x.bandwidth())
            .attr('height', y.bandwidth())
            .attr('fill', 'transparent')
            .style('cursor', drillable ? 'pointer' : 'crosshair')
            .on('mousemove', (event: MouseEvent) => {
                const bounds = containerElement.getBoundingClientRect();
                highlight.attr('fill-opacity', drillable ? 0.55 : 0);
                handlers.onCellEnter({
                    lines: cellTooltipLines(row, model, theme, drillable),
                    x: event.clientX - bounds.left,
                    y: event.clientY - bounds.top,
                });
            })
            .on('mouseleave', () => {
                highlight.attr('fill-opacity', 0);
                handlers.onCellLeave();
            })
            .on('click', () => {
                handlers.onCellSelect?.(row.ship_type, row.ship_tier);
            });
    });

    // Class-total column: same drill-down, tier left unfiltered.
    if (typeof handlers.onClassSelect === 'function') {
        model.byType.filter((total) => total.battles > 0).forEach((total) => {
            const bandX = x(total.key) ?? 0;
            const classHighlight = grid.append('rect')
                .attr('x', bandX)
                .attr('y', colBase + 1)
                .attr('width', x.bandwidth())
                .attr('height', COL_BAR_MAX + 29)
                .attr('rx', 3)
                .attr('fill', colors.gridLineBlue)
                .attr('fill-opacity', 0);

            grid.append('rect')
                .attr('x', bandX)
                .attr('y', colBase)
                .attr('width', x.bandwidth())
                .attr('height', COL_BAR_MAX + 30)
                .attr('fill', 'transparent')
                .style('cursor', 'pointer')
                .on('mousemove', (event: MouseEvent) => {
                    const bounds = containerElement.getBoundingClientRect();
                    classHighlight.attr('fill-opacity', 0.55);
                    handlers.onCellEnter({
                        lines: [
                            `All ${SHIP_TYPE_ABBREV[total.key] ?? total.key}`,
                            { value: formatCompactCount(total.battles), label: 'battles' },
                            {
                                value: formatWinPercent(total.winRatio),
                                label: 'win rate',
                                color: confidenceFadedWrColor(total.winRatio, total.battles, theme),
                            },
                            'Click to see these ships',
                        ],
                        x: event.clientX - bounds.left,
                        y: event.clientY - bounds.top,
                    });
                })
                .on('mouseleave', () => {
                    classHighlight.attr('fill-opacity', 0);
                    handlers.onCellLeave();
                })
                .on('click', () => {
                    handlers.onClassSelect?.(total.key);
                });
        });
    }
};

interface TierTypeDietSVGProps {
    /** Always supplied by the Profile panel behind a non-null guard. */
    data: TierTypePayload;
    svgWidth?: number;
    theme?: ChartTheme;
    /** Drill down to the Ships tab filtered to this tier x class. */
    onCellSelect?: (shipType: string, shipTier: number) => void;
    /** Drill down to the Ships tab filtered to this class, all tiers. */
    onClassSelect?: (shipType: string) => void;
}

const TierTypeDietSVG: React.FC<TierTypeDietSVGProps> = ({
    data,
    svgWidth = 570,
    theme = 'light',
    onCellSelect,
    onClassSelect,
}) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const svgRef = useRef<SVGSVGElement>(null);
    const [hover, setHover] = useState<HoverState | null>(null);
    const [measuredWidth, setMeasuredWidth] = useState(svgWidth);
    // Held in a ref so a parent re-render that hands down fresh callback
    // identities does not force a full D3 redraw of the figure. Synced in an
    // effect (not during render) and declared before the draw effect, so the
    // handlers are in place by the time the figure is first drawn.
    const selectHandlersRef = useRef({ onCellSelect, onClassSelect });
    useEffect(() => {
        selectHandlersRef.current = { onCellSelect, onClassSelect };
    }, [onCellSelect, onClassSelect]);

    useEffect(() => {
        const containerElement = containerRef.current;
        const svgElement = svgRef.current;
        if (!containerElement || !svgElement) {
            return;
        }

        const colors = chartColors[theme];
        let resizeFrame: number | null = null;

        const resolveWidth = () => resolveContainerChartWidth(containerElement.clientWidth, svgWidth);

        const redraw = () => {
            const width = resolveWidth();
            setMeasuredWidth(width);
            drawChart(svgElement, containerElement, data, width, colors, theme, {
                onCellEnter: setHover,
                onCellLeave: () => setHover(null),
                onCellSelect: selectHandlersRef.current.onCellSelect
                    ? (shipType, shipTier) => selectHandlersRef.current.onCellSelect?.(shipType, shipTier)
                    : undefined,
                onClassSelect: selectHandlersRef.current.onClassSelect
                    ? (shipType) => selectHandlersRef.current.onClassSelect?.(shipType)
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

export default TierTypeDietSVG;
