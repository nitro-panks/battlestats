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

// A tooltip line: plain muted text (spans the full row), or a value/label data
// pair laid out on a shared two-column grid so the values align vertically.
// `color` tints the value (the damage-delta row, same diverging scale as the
// tile).
type TooltipLine = string | { value: string; label: string; color?: string };

// One tile of a mini-treemap, already aggregated by the parent.
interface TreemapDatum {
    key: string;            // stable identity + default label
    label: string;          // text drawn on the tile
    sub?: string | null;    // second label line (avg dmg on the ships map, WR% on type/tier)
    size: number;           // area (battles)
    color: string;          // tile fill, computed by the parent per map
    tooltip: TooltipLine[]; // lines for the hover overlay; first is always the title string
    shipRow?: BattleHistoryByShip; // present only on the ships map (click target)
}

interface HoverState {
    lines: TooltipLine[];
    x: number;
    y: number;
}

interface MiniTreemapProps {
    title: React.ReactNode;
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
                        <div className="pb-[3px] font-semibold text-[var(--text-strong)]">
                            {typeof hover.lines[0] === 'string' ? hover.lines[0] : hover.lines[0].value}
                        </div>
                        <div className="grid grid-cols-[auto_1fr] gap-x-2">
                            {hover.lines.slice(1).map((line, i) => (
                                typeof line === 'string' ? (
                                    <div key={i} className="col-span-2 text-[var(--text-muted)]">{line}</div>
                                ) : (
                                    <React.Fragment key={i}>
                                        <span
                                            className="text-right font-semibold tabular-nums text-[var(--text-strong)]"
                                            style={line.color ? { color: line.color } : undefined}
                                        >
                                            {line.value}
                                        </span>
                                        <span className="text-[var(--text-muted)]">{line.label}</span>
                                    </React.Fragment>
                                )
                            ))}
                        </div>
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
// over the whole group, not an average of per-ship rates. The color (and the
// tooltip's colored row) follows the shared color metric:
//   dmg   — group actual ÷ expected damage, where expected = Σ(battles ×
//           ship_pop_avg_damage) over the ships that HAVE a baseline (actual
//           is summed over the same subset so the ratio is honest); no
//           baselined ship in the group → neutral.
//   kills — group frags ÷ battles on the fixed killsColor bands.
//   wr    — group WR on the shared wrColor bands.
const aggregateTiles = (
    rows: BattleHistoryByShip[],
    groupKey: (r: BattleHistoryByShip) => string | null,
    labelFor: (key: string) => string,
    colorMetric: ShipsColorMetric,
): TreemapDatum[] => {
    const groups = new Map<string, {
        battles: number; wins: number; ships: number; damage: number; frags: number;
        expectedDamage: number; baselinedDamage: number;
    }>();
    rows.forEach((r) => {
        const key = groupKey(r);
        if (key == null) return;
        const cur = groups.get(key) ?? {
            battles: 0, wins: 0, ships: 0, damage: 0, frags: 0,
            expectedDamage: 0, baselinedDamage: 0,
        };
        cur.battles += r.battles;
        cur.wins += r.wins;
        cur.ships += 1;
        cur.damage += r.damage;
        cur.frags += r.frags;
        const popAvg = r.ship_pop_avg_damage ?? null;
        if (popAvg != null && popAvg > 0 && r.battles > 0) {
            cur.expectedDamage += popAvg * r.battles;
            cur.baselinedDamage += r.damage;
        }
        groups.set(key, cur);
    });
    return Array.from(groups.entries())
        .filter(([, v]) => v.battles > 0)
        .map(([key, v]) => {
            const wr = (v.wins / v.battles) * 100;
            const avgDmg = v.damage / v.battles;
            const kpb = v.frags / v.battles;
            const ratio = v.expectedDamage > 0 ? v.baselinedDamage / v.expectedDamage : null;
            const color = colorMetric === 'wr'
                ? wrColor(wr)
                : colorMetric === 'kills'
                    ? killsColor(kpb)
                    : ratio != null ? damageRatioColor(ratio) : NEUTRAL_TILE;
            const sub = colorMetric === 'wr'
                ? `${wr.toFixed(1)}%`
                : colorMetric === 'kills'
                    ? kpb.toFixed(2)
                    : fmtDamage(avgDmg);
            return {
                key,
                label: labelFor(key),
                sub,
                size: v.battles,
                color,
                tooltip: [
                    labelFor(key),
                    ...(colorMetric === 'dmg' ? [
                        { value: fmtDamage(avgDmg), label: 'avg dmg' },
                        ratio != null
                            ? {
                                value: `${ratio >= 1 ? '+' : ''}${((ratio - 1) * 100).toFixed(0)}%`,
                                label: 'vs avg',
                                color: damageRatioColor(ratio),
                            }
                            : 'no ship-average baseline',
                    ] : []),
                    ...(colorMetric === 'kills'
                        ? [{ value: kpb.toFixed(2), label: 'kills / battle', color: killsColor(kpb) }]
                        : []),
                    { value: v.battles.toLocaleString(), label: v.battles === 1 ? 'battle' : 'battles' },
                    {
                        value: `${wr.toFixed(1)}%`,
                        label: 'WR',
                        ...(colorMetric === 'wr' ? { color: wrColor(wr) } : {}),
                    },
                    { value: String(v.ships), label: v.ships === 1 ? 'ship' : 'ships' },
                ],
            };
        });
};

// Color metric shared by all three maps. Size is always battles; this picks
// what the tile fill (and the tooltip's colored row) encodes. Not persisted —
// every load starts on 'wr', matching the slider's reset-to-default behavior.
type ShipsColorMetric = 'dmg' | 'kills' | 'wr';

// Pill order follows key order: WR% first (the default), then dmg, then Kills.
const COLOR_METRIC_LABEL: Record<ShipsColorMetric, string> = {
    wr: 'WR%',
    dmg: 'dmg',
    kills: 'Kills',
};

// Metric description used in the panels' aria-labels.
const METRIC_ARIA: Record<ShipsColorMetric, string> = {
    dmg: "average damage versus each ship's realm average",
    kills: 'kills per battle',
    wr: 'win rate',
};

// Fixed kills-per-battle bands using the wrColor hex ladder, so the ramp reads
// the same everywhere (red = poor → green = solid → blue/purple = elite). The
// anchors are absolute, not relative to the player: ~0.7 KPB is a typical
// Randoms average, 1.0+ is strong, 1.5+ elite.
export const killsColor = (kpb: number): string => {
    if (kpb > 2.0) return '#810c9e';
    if (kpb >= 1.5) return '#D042F3';
    if (kpb >= 1.2) return '#3182bd';
    if (kpb >= 1.0) return '#74c476';
    if (kpb >= 0.8) return '#a1d99b';
    if (kpb >= 0.6) return '#fed976';
    if (kpb >= 0.4) return '#fd8d3c';
    return '#a50f15';
};

const BattleHistoryTreemaps: React.FC<BattleHistoryTreemapsProps> = ({
    byShip,
    selectedShipId = null,
    onShipClick,
}) => {
    // Shared color metric (wr | dmg | kills) for ALL THREE maps — one pill row
    // in the ships-panel header drives the ships, type, and tier fills alike.
    // Not persisted; every load starts on 'wr'. One analytics event per switch.
    const [colorMetric, setColorMetric] = useState<ShipsColorMetric>('wr');
    const chooseColorMetric = (m: ShipsColorMetric) => {
        setColorMetric(m);
        trackEvent('battle-history-ships-color', { metric: m });
    };
    const typeTiles = useMemo(
        () => aggregateTiles(byShip, (r) => r.ship_type, shipTypeShort, colorMetric),
        [byShip, colorMetric],
    );
    const tierTiles = useMemo(
        () => aggregateTiles(
            byShip,
            (r) => (r.ship_tier != null ? String(r.ship_tier) : null),
            (key) => `T${key}`,
            colorMetric,
        ),
        [byShip, colorMetric],
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
                const kpb = r.battles > 0 ? r.frags / r.battles : 0;
                const kpbColor = killsColor(kpb);
                const color = colorMetric === 'wr'
                    ? wrColor(r.win_rate)
                    : colorMetric === 'kills'
                        ? kpbColor
                        : ratio != null ? damageRatioColor(ratio) : NEUTRAL_TILE;
                const sub = colorMetric === 'wr'
                    ? `${r.win_rate.toFixed(1)}%`
                    : colorMetric === 'kills'
                        ? kpb.toFixed(2)
                        : fmtDamage(r.avg_damage);
                return {
                    key: String(r.ship_id),
                    label: r.ship_name || `Ship ${r.ship_id}`,
                    sub,
                    size: r.battles,
                    color,
                    tooltip: [
                        r.ship_name || `Ship ${r.ship_id}`,
                        { value: fmtDamage(r.avg_damage), label: 'avg dmg' },
                        ratio != null
                            ? {
                                value: `${ratio >= 1 ? '+' : ''}${((ratio - 1) * 100).toFixed(0)}%`,
                                label: 'vs avg',
                                color: damageRatioColor(ratio),
                            }
                            : 'no ship-average baseline',
                        ...(colorMetric === 'kills'
                            ? [{ value: kpb.toFixed(2), label: 'kills / battle', color: kpbColor }]
                            : []),
                        { value: r.battles.toLocaleString(), label: r.battles === 1 ? 'battle' : 'battles' },
                        {
                            value: `${r.win_rate.toFixed(1)}%`,
                            label: 'WR',
                            ...(colorMetric === 'wr' ? { color: wrColor(r.win_rate) } : {}),
                        },
                    ],
                    shipRow: r,
                };
            }),
        [byShip, effectiveN, colorMetric],
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
                title={(
                    <span className="flex items-center gap-1">
                        <span>battles ×</span>
                        {(Object.keys(COLOR_METRIC_LABEL) as ShipsColorMetric[]).map((m) => (
                            <button
                                key={m}
                                type="button"
                                aria-pressed={colorMetric === m}
                                onClick={() => chooseColorMetric(m)}
                                className={`rounded px-1 py-0.5 uppercase tracking-wide transition-colors ${
                                    colorMetric === m
                                        ? 'bg-[var(--accent-faint)] text-[var(--text-strong)]'
                                        : 'text-[var(--text-muted)] hover:text-[var(--text-primary)]'
                                }`}
                            >
                                {COLOR_METRIC_LABEL[m]}
                            </button>
                        ))}
                    </span>
                )}
                ariaLabel={`Ships sized by battles played, colored by ${METRIC_ARIA[colorMetric]}`}
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
                    title={`Type × ${COLOR_METRIC_LABEL[colorMetric]}`}
                    ariaLabel={`Battles by ship type, colored by ${METRIC_ARIA[colorMetric]}`}
                    data={typeTiles}
                    height={PANEL_HEIGHT / 2}
                />
                <MiniTreemap
                    title={`Tier × ${COLOR_METRIC_LABEL[colorMetric]}`}
                    ariaLabel={`Battles by ship tier, colored by ${METRIC_ARIA[colorMetric]}`}
                    data={tierTiles}
                    height={PANEL_HEIGHT / 2}
                />
            </div>
        </div>
    );
};

export default BattleHistoryTreemaps;
