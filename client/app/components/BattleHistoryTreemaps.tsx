'use client';

// Three mini-treemaps summarizing the battle-history window shown in the
// per-ship table below them. AREA = volume everywhere; color differs by map:
//   • Type — DD/CA/BB/CV/SS, sized by battles, colored by the type's
//     aggregate WR (wins ÷ battles across its ships, `wrColor` scale).
//   • Ships — one tile per ship, sized by TOTAL damage in the window
//     (additive, so areas sum honestly), colored on a DIVERGING scale by the
//     player's avg damage vs the ship's realm-wide 30d average
//     (`ship_pop_avg_damage`, the ShipStats baseline) — red below expectation,
//     neutral at it, green above. Ships with no usable population baseline
//     render neutral gray. Clicking a ship tile toggles the same ShipStats
//     combat panel a table-row click does.
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

// The ships-by-damage map defaults to the top N ships by total window damage;
// beyond that the tiles shred into unreadable slivers. The Top 10 | All filter
// lets the user opt into the full list, persisted per-browser.
const SHIP_TILE_CAP = 10;

type ShipScope = 'top10' | 'all';
const SHIP_SCOPE_KEY = 'bs-bh-ships-scope';

function readStoredShipScope(): ShipScope | null {
    if (typeof window === 'undefined') return null;
    try {
        const raw = window.localStorage.getItem(SHIP_SCOPE_KEY);
        return raw === 'top10' || raw === 'all' ? raw : null;
    } catch {
        return null;
    }
}

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
    sub?: string | null;    // second label line (WR% or avg dmg)
    size: number;           // area (battles or total damage)
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
}

const MiniTreemap: React.FC<MiniTreemapProps> = ({
    title, ariaLabel, data, selectedKey = null, onTileClick, headerRight,
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
        svg.attr('viewBox', `0 0 ${width} ${PANEL_HEIGHT}`)
            .attr('width', '100%')
            .attr('height', PANEL_HEIGHT);

        const root = d3.hierarchy({ children: data } as { children: TreemapDatum[] })
            .sum((d: TreemapDatum) => Math.max(0, d.size || 0))
            .sort((a: { value?: number }, b: { value?: number }) => (b.value ?? 0) - (a.value ?? 0));
        d3.treemap().size([width, PANEL_HEIGHT]).paddingInner(2).round(true)(root);

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
            if (w < 34 || h < 18) return;
            const textColor = d3.hsl(d.data.color).l > 0.62 ? '#1a1a1a' : '#f5f5f5';
            const maxChars = Math.max(2, Math.floor((w - 6) / 6.0));
            const label = d.data.label.length > maxChars
                ? `${d.data.label.slice(0, maxChars - 1)}…`
                : d.data.label;
            const node = d3.select(this);
            node.append('text')
                .attr('x', 4).attr('y', 13)
                .attr('font-size', 10).attr('font-weight', 600).attr('fill', textColor)
                .style('pointer-events', 'none')
                .text(label);
            if (h >= 32 && d.data.sub) {
                node.append('text')
                    .attr('x', 4).attr('y', 25)
                    .attr('font-size', 9).attr('fill', textColor).attr('opacity', 0.85)
                    .style('pointer-events', 'none')
                    .text(d.data.sub);
            }
        });
    }, [data, width, selectedKey]);

    return (
        <div>
            <div className="mb-1 flex items-baseline justify-between text-[10px] font-semibold uppercase tracking-wide text-[var(--text-muted)]">
                <span>{title}</span>
                {headerRight}
            </div>
            <div ref={containerRef} className="relative w-full" style={{ height: PANEL_HEIGHT }}>
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
    // SSR-safe persisted scope: render the default first, then adopt the
    // stored choice post-hydration (same pattern as the landing Map/Plot
    // toggle's bs-landing-ship-view).
    const [shipScope, setShipScopeState] = useState<ShipScope>('top10');
    useEffect(() => {
        const stored = readStoredShipScope();
        if (stored) setShipScopeState(stored);
    }, []);
    const setShipScope = (next: ShipScope) => {
        setShipScopeState(next);
        trackEvent('battle-history-ships-scope', { scope: next });
        try {
            window.localStorage.setItem(SHIP_SCOPE_KEY, next);
        } catch {
            // Ignore storage failures (private mode / quota) — the scope still switches.
        }
    };

    const shipTiles = useMemo(
        () => byShip
            .filter((r) => r.damage > 0)
            .sort((a, b) => b.damage - a.damage)
            .slice(0, shipScope === 'top10' ? SHIP_TILE_CAP : byShip.length)
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
                    size: r.damage,
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
        [byShip, shipScope],
    );

    if (typeTiles.length === 0 && shipTiles.length === 0 && tierTiles.length === 0) {
        return null;
    }

    return (
        <div className="mb-5 grid grid-cols-1 gap-4 sm:grid-cols-3">
            <MiniTreemap
                title="By type"
                ariaLabel="Battles by ship type, colored by win rate"
                data={typeTiles}
            />
            <MiniTreemap
                title="Ships by damage"
                ariaLabel="Ships sized by total damage, colored by the player's average damage versus the ship's realm average"
                headerRight={(
                    <span className="flex items-center gap-1 normal-case tracking-normal">
                        <button
                            type="button"
                            onClick={() => setShipScope('top10')}
                            aria-pressed={shipScope === 'top10'}
                            className={shipScope === 'top10'
                                ? 'font-semibold text-[var(--text-primary)]'
                                : 'text-[var(--text-muted)] hover:text-[var(--text-primary)]'}
                        >
                            Top 10
                        </button>
                        <span aria-hidden className="text-[var(--border)]">|</span>
                        <button
                            type="button"
                            onClick={() => setShipScope('all')}
                            aria-pressed={shipScope === 'all'}
                            className={shipScope === 'all'
                                ? 'font-semibold text-[var(--text-primary)]'
                                : 'text-[var(--text-muted)] hover:text-[var(--text-primary)]'}
                        >
                            All
                        </button>
                    </span>
                )}
                data={shipTiles}
                selectedKey={selectedShipId != null ? String(selectedShipId) : null}
                onTileClick={onShipClick
                    ? (d) => { if (d.shipRow) onShipClick(d.shipRow); }
                    : undefined}
            />
            <MiniTreemap
                title="By tier"
                ariaLabel="Battles by ship tier, colored by win rate"
                data={tierTiles}
            />
        </div>
    );
};

export default BattleHistoryTreemaps;
