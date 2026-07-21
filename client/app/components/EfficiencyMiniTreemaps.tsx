import React, { useEffect, useMemo, useRef, useState } from 'react';
import * as d3 from 'd3';
import { badgeClassColor, chartColors, shipTypeShortColor, type ChartTheme } from '../lib/chartTheme';
import type { EfficiencyBadgeDot } from './EfficiencyBadgeTable';

type FilterControl = 'tier' | 'type' | 'award';

// One tile of a mini-treemap, already aggregated by the parent: a bucket of
// badged ships (a tier, a class, or an award grade) sized by its count.
// filterValue is what a click sets the matching filter dropdown to.
interface TreemapDatum {
    key: string;
    label: string;
    count: number;
    color: string;
    filterValue: string;
}

const TREEMAP_HEIGHT = 128;

// Pick black/white tile text by PERCEIVED brightness (YIQ), not HSL lightness:
// HSL lightness badly underrates yellows/oranges (e.g. dark-mode amber #fbbf24),
// so the old `d3.hsl(color).l` test put white text on bright warm tiles where it
// was unreadable. YIQ weights green heavily, matching how bright a fill looks.
const readableTextColor = (hex: string): string => {
    let value = hex.trim().replace('#', '');
    if (value.length === 3) {
        value = value.split('').map((ch) => ch + ch).join('');
    }
    const r = parseInt(value.slice(0, 2), 16);
    const g = parseInt(value.slice(2, 4), 16);
    const b = parseInt(value.slice(4, 6), 16);
    if ([r, g, b].some((channel) => Number.isNaN(channel))) {
        return '#f5f5f5';
    }
    const yiq = (r * 299 + g * 587 + b * 114) / 1000;
    return yiq >= 128 ? '#1a1a1a' : '#f5f5f5';
};

interface EfficiencyMiniTreemapProps {
    title: string;
    ariaLabel: string;
    data: TreemapDatum[];
    control: FilterControl;
    // Current filter value for this control ('all' = nothing selected); the
    // matching tile is outlined. Clicking a tile toggles that filter.
    selectedValue: string;
    onSelect: (control: FilterControl, filterValue: string) => void;
}

// A flat count-sized treemap of one categorical breakdown. Mirrors the
// battle-history MiniTreemap pattern (ResizeObserver width → d3.treemap →
// direct labels + a hover tooltip) but trimmed to a single-level partition.
const EfficiencyMiniTreemap: React.FC<EfficiencyMiniTreemapProps> = ({ title, ariaLabel, data, control, selectedValue, onSelect }) => {
    const containerRef = useRef<HTMLDivElement | null>(null);
    const svgRef = useRef<SVGSVGElement | null>(null);
    const [width, setWidth] = useState(0);
    const [hover, setHover] = useState<{ text: string; x: number; y: number } | null>(null);
    // Read the latest onSelect from the D3 handler without re-running the
    // treemap effect when the parent re-creates the callback.
    const onSelectRef = useRef(onSelect);
    useEffect(() => { onSelectRef.current = onSelect; });

    useEffect(() => {
        if (!containerRef.current) return undefined;
        const ro = new ResizeObserver((entries) => {
            setWidth(Math.round(entries[0]?.contentRect.width ?? 0));
        });
        ro.observe(containerRef.current);
        return () => ro.disconnect();
    }, []);

    useEffect(() => {
        if (!svgRef.current) return;
        const svg = d3.select(svgRef.current);
        svg.selectAll('*').remove();
        if (width <= 0 || data.length === 0) {
            svg.attr('height', 0);
            return;
        }
        svg.attr('viewBox', `0 0 ${width} ${TREEMAP_HEIGHT}`)
            .attr('width', '100%')
            .attr('height', TREEMAP_HEIGHT);

        const root = d3.hierarchy({ children: data } as { children: TreemapDatum[] })
            .sum((d: TreemapDatum) => Math.max(0, d.count || 0))
            .sort((a: { value?: number }, b: { value?: number }) => (b.value ?? 0) - (a.value ?? 0));
        d3.treemap().size([width, TREEMAP_HEIGHT]).paddingInner(2).round(true)(root);

        const g = svg.selectAll('g').data(root.leaves()).join('g')
            .attr('transform', (d: { x0: number; y0: number }) => `translate(${d.x0},${d.y0})`);

        g.append('rect')
            .attr('width', (d: { x0: number; x1: number }) => Math.max(0, d.x1 - d.x0))
            .attr('height', (d: { y0: number; y1: number }) => Math.max(0, d.y1 - d.y0))
            .attr('rx', 2)
            .attr('fill', (d: { data: TreemapDatum }) => d.data.color)
            .attr('stroke', (d: { data: TreemapDatum }) => (
                d.data.filterValue === selectedValue ? 'var(--text-strong)' : 'var(--bg-card)'
            ))
            .attr('stroke-width', (d: { data: TreemapDatum }) => (
                d.data.filterValue === selectedValue ? 2 : 1
            ))
            .style('cursor', 'pointer')
            .on('click', function onClick(this: SVGRectElement, _event: MouseEvent, d: { data: TreemapDatum }) {
                onSelectRef.current(control, d.data.filterValue);
            })
            .on('mousemove', function onMove(this: SVGRectElement, event: MouseEvent, d: { data: TreemapDatum }) {
                const rect = containerRef.current?.getBoundingClientRect();
                setHover({
                    text: `${d.data.label}: ${d.data.count}`,
                    x: rect ? event.clientX - rect.left : 0,
                    y: rect ? event.clientY - rect.top : 0,
                });
                svg.selectAll('rect').attr('opacity', 0.55);
                d3.select(this).attr('opacity', 1);
            })
            .on('mouseleave', function onLeave(this: SVGRectElement) {
                setHover(null);
                svg.selectAll('rect').attr('opacity', 1);
            });

        // Label + count where they fit; text contrast is chosen off the tile's
        // own lightness so it reads on every hue.
        g.each(function labelTile(this: SVGGElement, d: { x0: number; x1: number; y0: number; y1: number; data: TreemapDatum }) {
            const w = d.x1 - d.x0;
            const h = d.y1 - d.y0;
            if (w < 30 || h < 18) return;
            const textColor = readableTextColor(d.data.color);
            const maxChars = Math.max(2, Math.floor((w - 6) / 7.2));
            const label = d.data.label.length > maxChars
                ? `${d.data.label.slice(0, maxChars - 1)}…`
                : d.data.label;
            const node = d3.select(this);
            node.append('text')
                .attr('x', 4).attr('y', 15)
                .attr('font-size', 12).attr('font-weight', 600).attr('fill', textColor)
                .style('pointer-events', 'none')
                .text(label);
            if (h >= 32) {
                node.append('text')
                    .attr('x', 4).attr('y', 29)
                    .attr('font-size', 11).attr('fill', textColor).attr('opacity', 0.85)
                    .style('pointer-events', 'none')
                    .text(String(d.data.count));
            }
        });
    }, [width, data, control, selectedValue]);

    return (
        <div>
            <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-[var(--text-secondary)]">{title}</div>
            <div ref={containerRef} className="relative">
                <svg ref={svgRef} role="img" aria-label={ariaLabel} />
                {hover ? (
                    <div
                        className="pointer-events-none absolute z-10 whitespace-nowrap rounded bg-[var(--bg-card)] px-2 py-1 text-xs text-[var(--text-primary)] shadow"
                        style={{ left: hover.x + 8, top: hover.y + 8, border: '1px solid var(--border)' }}
                    >
                        {hover.text}
                    </div>
                ) : null}
            </div>
        </div>
    );
};

interface EfficiencyMiniTreemapsProps {
    rows: EfficiencyBadgeDot[];
    theme: ChartTheme;
    // Current filter selections ('all' = none) so the active tile is outlined.
    selected: { tier: string; type: string; award: string };
    // Fired when a tile is clicked; the parent toggles the matching filter.
    onSelect: (control: FilterControl, filterValue: string) => void;
}

const AWARD_LABELS: Record<number, string> = { 1: 'Expert', 2: 'I', 3: 'II', 4: 'III' };

// ColorBrewer sequential "Blues" (9-class). The tier treemap shades tiles by
// tier value — higher tier = deeper blue — an ordinal encoding orthogonal to
// the count-driven tile size. We map into indices 2..8 (skipping the two palest
// steps, near-white, which wouldn't read on the card) so every tile is visible;
// tile-label contrast is still handled by readableTextColor.
const COLORBREWER_BLUES = [
    '#f7fbff', '#deebf7', '#c6dbef', '#9ecae1', '#6baed6',
    '#4292c6', '#2171b5', '#08519c', '#08306b',
];
const tierBlue = (tier: number, minTier: number, maxTier: number): string => {
    const norm = maxTier === minTier ? 0.6 : (tier - minTier) / (maxTier - minTier);
    const idx = Math.round(2 + norm * (COLORBREWER_BLUES.length - 1 - 2));
    return COLORBREWER_BLUES[idx];
};

// Three small-multiples treemaps — Tier, Type, Award — each partitioning the
// (filtered) badged ships by that dimension, sized by ship count. Type reuses
// the table's class palette; Award the quality colors; Tier a neutral fill.
const EfficiencyMiniTreemaps: React.FC<EfficiencyMiniTreemapsProps> = ({ rows, theme, selected, onSelect }) => {
    const colors = chartColors[theme];

    const { tierData, typeData, awardData } = useMemo(() => {
        const tierCounts = new Map<number, number>();
        const typeCounts = new Map<string, number>();
        const awardCounts = new Map<number, number>();
        for (const row of rows) {
            tierCounts.set(row.shipTier, (tierCounts.get(row.shipTier) ?? 0) + 1);
            typeCounts.set(row.shipType, (typeCounts.get(row.shipType) ?? 0) + 1);
            awardCounts.set(row.badgeClass, (awardCounts.get(row.badgeClass) ?? 0) + 1);
        }

        const tierValues = Array.from(tierCounts.keys());
        const minTier = tierValues.length ? Math.min(...tierValues) : 0;
        const maxTier = tierValues.length ? Math.max(...tierValues) : 0;
        const tier: TreemapDatum[] = Array.from(tierCounts.entries())
            .sort((a, b) => b[0] - a[0])
            .map(([tierValue, count]) => ({
                key: `tier-${tierValue}`,
                label: `T${tierValue}`,
                count,
                color: tierBlue(tierValue, minTier, maxTier),
                filterValue: String(tierValue),
            }));

        const type: TreemapDatum[] = Array.from(typeCounts.entries())
            .map(([shipType, count]) => ({
                key: `type-${shipType}`,
                label: shipType,
                count,
                color: shipTypeShortColor(colors, shipType),
                filterValue: shipType,
            }));

        const award: TreemapDatum[] = Array.from(awardCounts.entries())
            .sort((a, b) => a[0] - b[0])
            .map(([badgeClass, count]) => ({
                key: `award-${badgeClass}`,
                label: AWARD_LABELS[badgeClass] ?? `Class ${badgeClass}`,
                count,
                color: badgeClassColor(colors, badgeClass),
                filterValue: String(badgeClass),
            }));

        return { tierData: tier, typeData: type, awardData: award };
    }, [rows, colors]);

    return (
        <div className="grid grid-cols-3 gap-3">
            <EfficiencyMiniTreemap title="Tier" ariaLabel="Badged ships by tier" data={tierData} control="tier" selectedValue={selected.tier} onSelect={onSelect} />
            <EfficiencyMiniTreemap title="Type" ariaLabel="Badged ships by class" data={typeData} control="type" selectedValue={selected.type} onSelect={onSelect} />
            <EfficiencyMiniTreemap title="Award" ariaLabel="Badged ships by award grade" data={awardData} control="award" selectedValue={selected.award} onSelect={onSelect} />
        </div>
    );
};

export default EfficiencyMiniTreemaps;
