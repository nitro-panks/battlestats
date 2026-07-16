'use client';

// Three mini-treemaps summarizing the battle-history window shown in the
// per-ship table below them. AREA = volume everywhere; color differs by map:
//   • Type — DD/CA/BB/CV/SS, sized by battles, colored by the type's
//     aggregate WR (wins ÷ battles across its ships, `wrColor` scale).
//   • Ships — one tile per ship, sized by BATTLES played (additive, so areas
//     sum honestly and match the other two panels' volume semantics), colored
//     on a DIVERGING scale by the player's avg damage vs the ship's
//     realm-wide 30d average (`ship_pop_avg_damage`, the ShipStats baseline)
//     — red below expectation, neutral at it, green above. Ships with no
//     usable population baseline render neutral gray. Clicking a ship tile
//     toggles the same ShipStats combat panel a table-row click does.
//   • Tier — tiles per tier, sized by battles, colored by tier aggregate WR.
//
// Purely presentational: BattleHistoryCard owns the fetch and passes the
// resolved `by_ship` rows, so the maps re-render on every Window/Mode pill
// change and always mirror the table.

import React, { useEffect, useMemo, useRef, useState } from 'react';
import * as d3 from 'd3';
import wrColor from '../lib/wrColor';
import { trackEvent } from '../lib/umami';
import type { BattleHistoryByShip } from './BattleHistoryCard';

// Damage figures on tiles/tooltips: 3 significant digits ("60.3k", "1.2M") —
// chartTheme's formatCompactCount ('~s') keeps full precision ("60.285k"),
// too noisy for a tile label.
const fmtDamage = (value: number): string => d3.format('.3~s')(value).replace('G', 'B');

const PANEL_HEIGHT = 150;

const SHIP_TYPE_LABEL: Record<string, string> = {
    Destroyer: 'DD',
    Cruiser: 'CA',
    Battleship: 'BB',
    AirCarrier: 'CV',
    Submarine: 'SS',
};

const shipTypeShort = (type: string | null | undefined): string => {
    if (!type) return '—';
    return SHIP_TYPE_LABEL[type] ?? type.slice(0, 2).toUpperCase();
};

// Fill for tiles with no color signal (e.g. a ship without a population
// damage baseline). A solid gray so the contrast-aware labels still work.
const NEUTRAL_TILE = '#6f7683';

// A range slider (1 → played-ship count) zooms the ships map into the top-N
// most-played ships live — the treemap re-lays-out on every tick. It resets to
// 25 (or the player's max when they played fewer) on every load; the choice is
// deliberately NOT persisted, so each visit starts from the same default view.
const DEFAULT_TOP_N = 25;

// Diverging fill for the damage map: the ratio of the player's avg damage to
// the ship's realm 30d average. 1.0 = at expectation (neutral gray); the ends
// clamp at 40% below / 50% above. Lab interpolation keeps the red→gray→green
// sweep from going muddy.
export const damageRatioColor: (ratio: number) => string = d3.scaleLinear()
    .domain([0.6, 1.0, 1.5])
    .range(['#a50f15', '#8b9099', '#2fa14b'] as unknown as number[])
    .interpolate(d3.interpolateLab as unknown as never)
    .clamp(true) as unknown as (ratio: number) => string;

// One tile of a mini-treemap, already aggregated by the parent.
interface TreemapDatum {
    key: string;            // stable identity + default label
    label: string;          // text drawn on the tile
    sub?: string | null;    // second label line (avg dmg on the ships map, WR% on type/tier)
    size: number;           // area (battles)
    color: string;          // tile fill, computed by the parent per map
    tooltip: string[];      // lines for the hover overlay
    shipRow?: BattleHistoryByShip; // present only on the ships map (click target)
}

interface HoverState {
    lines: string[];
    x: number;
    y: number;
}

interface MiniTreemapProps {
    title: string;
    ariaLabel: string;
    data: TreemapDatum[];
    selectedKey?: string | null;
    onTileClick?: (d: TreemapDatum) => void;
    // Optional control rendered flush right of the title (e.g. the ships
    // panel's Top 10 | All scope filter).
    headerRight?: React.ReactNode;
    // Panel height in px — the second-row type/tier maps run at half the
    // default so the full-width ships map stays the visual anchor.
    height?: number;
}

const MiniTreemap: React.FC<MiniTreemapProps> = ({
    title, ariaLabel, data, selectedKey = null, onTileClick, headerRight,
    height = PANEL_HEIGHT,
}) => {
    const containerRef = useRef<HTMLDivElement | null>(null);
    const svgRef = useRef<SVGSVGElement | null>(null);
    const [width, setWidth] = useState(0);
    const [hover, setHover] = useState<HoverState | null>(null);
    // Read the latest callback from the D3 handlers without re-rendering the
    // treemap when the parent re-creates it.
    const onTileClickRef = useRef(onTileClick);
    useEffect(() => { onTileClickRef.current = onTileClick; });

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
        svg.attr('viewBox', `0 0 ${width} ${height}`)
            .attr('width', '100%')
            .attr('height', height);

        const root = d3.hierarchy({ children: data } as { children: TreemapDatum[] })
            .sum((d: TreemapDatum) => Math.max(0, d.size || 0))
            .sort((a: { value?: number }, b: { value?: number }) => (b.value ?? 0) - (a.value ?? 0));
        d3.treemap().size([width, height]).paddingInner(2).round(true)(root);

        const clickable = !!onTileClickRef.current;
        const g = svg.selectAll('g').data(root.leaves()).join('g')
            .attr('transform', (d: { x0: number; y0: number }) => `translate(${d.x0},${d.y0})`);

        g.append('rect')
            .attr('width', (d: { x0: number; x1: number }) => Math.max(0, d.x1 - d.x0))
            .attr('height', (d: { y0: number; y1: number }) => Math.max(0, d.y1 - d.y0))
            .attr('rx', 2)
            .attr('fill', (d: { data: TreemapDatum }) => d.data.color)
            .attr('stroke', (d: { data: TreemapDatum }) => (
                selectedKey != null && d.data.key === selectedKey
                    ? 'var(--text-strong)'
                    : 'var(--bg-card)'
            ))
            .attr('stroke-width', (d: { data: TreemapDatum }) => (
                selectedKey != null && d.data.key === selectedKey ? 2 : 1
            ))
            .style('cursor', clickable ? 'pointer' : 'default')
            .on('click', function onClick(this: SVGRectElement, _event: MouseEvent, d: { data: TreemapDatum }) {
                onTileClickRef.current?.(d.data);
            })
            .on('mousemove', function onMove(this: SVGRectElement, event: MouseEvent, d: { data: TreemapDatum }) {
                const rect = containerRef.current?.getBoundingClientRect();
                setHover({
                    lines: d.data.tooltip,
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

        // Direct labels where they fit; contrast picked off the tile's own
        // lightness so text reads on every WR shade.
        g.each(function labelTile(this: SVGGElement, d: { x0: number; x1: number; y0: number; y1: number; data: TreemapDatum }) {
            const w = d.x1 - d.x0;
            const h = d.y1 - d.y0;
            if (w < 40 || h < 22) return;
            const textColor = d3.hsl(d.data.color).l > 0.62 ? '#1a1a1a' : '#f5f5f5';
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
            // The sub line (avg dmg / WR%) is drawn only when it fits whole —
            // a truncated number misleads; the tooltip always has the full data.
            if (h >= 38 && d.data.sub && d.data.sub.length <= Math.floor((w - 6) / 6.6)) {
                node.append('text')
                    .attr('x', 4).attr('y', 29)
                    .attr('font-size', 11).attr('fill', textColor).attr('opacity', 0.85)
                    .style('pointer-events', 'none')
                    .text(d.data.sub);
            }
        });
    }, [data, width, height, selectedKey]);

    return (
        <div>
            <div className="mb-1 flex items-baseline justify-between text-[10px] font-semibold uppercase tracking-wide text-[var(--text-muted)]">
                <span>{title}</span>
                {headerRight}
            </div>
            <div ref={containerRef} className="relative w-full" style={{ height }}>
                <svg ref={svgRef} role="img" aria-label={ariaLabel} />
                {hover && (
                    <div
                        className="pointer-events-none absolute z-10 rounded bg-[var(--bg-page)] px-2 py-1 text-xs shadow-md ring-1 ring-[var(--accent-faint)]"
                        style={{
                            left: Math.min(Math.max(hover.x + 10, 0), Math.max(width - 150, 0)),
                            top: Math.max(hover.y - 44, 0),
                        }}
                    >
                        <div className="font-semibold text-[var(--text-strong)]">{hover.lines[0]}</div>
                        {hover.lines.slice(1).map((line) => (
                            <div key={line} className="text-[var(--text-muted)]">{line}</div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
};

interface BattleHistoryTreemapsProps {
    byShip: BattleHistoryByShip[];
    selectedShipId?: number | null;
    onShipClick?: (row: BattleHistoryByShip) => void;
}

// Aggregate rows into one tile per group (type or tier); WR = wins ÷ battles
// over the whole group, not an average of per-ship rates.
const aggregateTiles = (
    rows: BattleHistoryByShip[],
    groupKey: (r: BattleHistoryByShip) => string | null,
    labelFor: (key: string) => string,
): TreemapDatum[] => {
    const groups = new Map<string, { battles: number; wins: number; damage: number; ships: number }>();
    rows.forEach((r) => {
        const key = groupKey(r);
        if (key == null) return;
        const cur = groups.get(key) ?? { battles: 0, wins: 0, damage: 0, ships: 0 };
        cur.battles += r.battles;
        cur.wins += r.wins;
        cur.damage += r.damage;
        cur.ships += 1;
        groups.set(key, cur);
    });
    return Array.from(groups.entries())
        .filter(([, v]) => v.battles > 0)
        .map(([key, v]) => {
            const wr = (v.wins / v.battles) * 100;
            return {
                key,
                label: labelFor(key),
                sub: `${wr.toFixed(1)}%`,
                size: v.battles,
                color: wrColor(wr),
                tooltip: [
                    labelFor(key),
                    `${v.battles.toLocaleString()} battles · ${wr.toFixed(1)}% WR`,
                    `${v.ships} ship${v.ships === 1 ? '' : 's'} · ${fmtDamage(v.damage)} dmg`,
                ],
            };
        });
};

const BattleHistoryTreemaps: React.FC<BattleHistoryTreemapsProps> = ({
    byShip,
    selectedShipId = null,
    onShipClick,
}) => {
    const typeTiles = useMemo(
        () => aggregateTiles(byShip, (r) => r.ship_type, shipTypeShort),
        [byShip],
    );
    const tierTiles = useMemo(
        () => aggregateTiles(
            byShip,
            (r) => (r.ship_tier != null ? String(r.ship_tier) : null),
            (key) => `T${key}`,
        ),
        [byShip],
    );
    // Slider zoom over the ships map: null = no explicit choice yet (use the
    // default min(25, roster)). Kept as the raw slider number, clamped against
    // the current window's ship count so a window/mode/player switch that
    // shrinks the list can't strand an oversized N. Not persisted — every load
    // starts from the default.
    const playedShipCount = useMemo(
        () => byShip.filter((r) => r.battles > 0).length,
        [byShip],
    );
    const [topN, setTopN] = useState<number | null>(null);
    const chooseTopN = (v: number) => setTopN(v);
    const effectiveN = Math.max(1, Math.min(topN ?? DEFAULT_TOP_N, playedShipCount));
    // One analytics event per RELEASE (not per tick — a drag emits dozens).
    const trackScopeRelease = () => trackEvent('battle-history-ships-scope', {
        scope: effectiveN >= playedShipCount ? 'all' : 'slider',
        count: effectiveN,
    });

    const shipTiles = useMemo(
        () => byShip
            .filter((r) => r.battles > 0)
            .sort((a, b) => b.battles - a.battles)
            .slice(0, effectiveN)
            .map((r): TreemapDatum => {
                const popAvg = r.ship_pop_avg_damage ?? null;
                const ratio = popAvg != null && popAvg > 0
                    ? r.avg_damage / popAvg
                    : null;
                const vsAvg = ratio != null
                    ? `${ratio >= 1 ? '+' : ''}${((ratio - 1) * 100).toFixed(0)}% vs ship avg`
                    : 'no ship-average baseline';
                return {
                    key: String(r.ship_id),
                    label: r.ship_name || `Ship ${r.ship_id}`,
                    sub: fmtDamage(r.avg_damage),
                    size: r.battles,
                    color: ratio != null ? damageRatioColor(ratio) : NEUTRAL_TILE,
                    tooltip: [
                        r.ship_name || `Ship ${r.ship_id}`,
                        `${fmtDamage(r.avg_damage)} avg dmg · ${vsAvg}`,
                        popAvg != null
                            ? `ship 30d avg ${fmtDamage(popAvg)} · ${fmtDamage(r.damage)} total`
                            : `${fmtDamage(r.damage)} total dmg`,
                        `${r.battles.toLocaleString()} battles · ${r.win_rate.toFixed(1)}% WR`,
                    ],
                    shipRow: r,
                };
            }),
        [byShip, effectiveN],
    );

    if (typeTiles.length === 0 && shipTiles.length === 0 && tierTiles.length === 0) {
        return null;
    }

    return (
        // Two rows: the ships map takes the full width on its own line (it has
        // the most tiles and earns the room); type + tier share the second
        // line at half width each.
        <div className="mb-5 space-y-4">
            <MiniTreemap
                title="battles × dmg"
                ariaLabel="Ships sized by battles played, colored by the player's average damage versus the ship's realm average"
                headerRight={playedShipCount > 1 ? (
                    <span className="flex items-center gap-1 normal-case tracking-normal">
                        <span className="tabular-nums text-[var(--text-muted)]">1</span>
                        <input
                            type="range"
                            min={1}
                            max={playedShipCount}
                            value={effectiveN}
                            onChange={(e) => chooseTopN(Number(e.target.value))}
                            // pointerup covers mouse, touch, AND pen in one
                            // handler; keyup covers keyboard-driven changes
                            // (arrows/Home/End), which fire no pointer events
                            // at all — without it those zooms went untracked.
                            onPointerUp={trackScopeRelease}
                            onKeyUp={(e) => {
                                if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End', 'PageUp', 'PageDown'].includes(e.key)) {
                                    trackScopeRelease();
                                }
                            }}
                            aria-label="Number of most-played ships shown"
                            className="bh-scope-slider w-56 cursor-pointer"
                        />
                        {/* Content-width so the whole 1↔slider↔N assembly sits
                            flush against the panel's right edge (a digit-count
                            change nudges the track a few px — acceptable). */}
                        <span className="tabular-nums text-[var(--text-muted)]">
                            {effectiveN}
                        </span>
                    </span>
                ) : undefined}
                data={shipTiles}
                selectedKey={selectedShipId != null ? String(selectedShipId) : null}
                onTileClick={onShipClick
                    ? (d) => { if (d.shipRow) onShipClick(d.shipRow); }
                    : undefined}
            />
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <MiniTreemap
                    title="Type × WR"
                    ariaLabel="Battles by ship type, colored by win rate"
                    data={typeTiles}
                    height={PANEL_HEIGHT / 2}
                />
                <MiniTreemap
                    title="Tier × WR"
                    ariaLabel="Battles by ship tier, colored by win rate"
                    data={tierTiles}
                    height={PANEL_HEIGHT / 2}
                />
            </div>
        </div>
    );
};

export default BattleHistoryTreemaps;
