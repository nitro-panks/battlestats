'use client';

// Realm top-ships treemap (DEV PREVIEW).
//
// The 25 most-played ships on the active realm over the last 24 hours, as a
// treemap: each tile is one ship, sized by battles, COLORED BY SHIP TYPE and
// SHADED BY TIER (lighter = low tier, darker = high tier). Fed by
// `/api/realm/<realm>/top-ships`, which aggregates BattleEvent over the rolling
// window and joins Ship for type/tier. Dev-gated by the caller.

import React, { useEffect, useMemo, useRef, useState } from 'react';
import * as d3 from 'd3';
import { fetchSharedJson } from '../lib/sharedJsonFetch';
import { useTheme } from '../context/ThemeContext';
import { chartColors } from '../lib/chartTheme';
import { useRealm } from '../context/RealmContext';

interface TopShip {
    ship_id: number;
    ship_name: string;
    ship_type: string | null;
    tier: number | null;
    battles: number;
}

type ShipMode = 'random' | 'ranked';

interface RealmTopShips {
    realm: string;
    hours: number;
    mode?: ShipMode;
    ships: TopShip[];
}

const SHIP_MODES: ShipMode[] = ['random', 'ranked'];
const SHIP_MODE_LABEL: Record<ShipMode, string> = { random: 'Random', ranked: 'Ranked' };

const SHIP_LIMIT = 25;
const TOP_SHIPS_FETCH_TTL_MS = 3_600_000; // 1h — mirrors the backend's hourly Redis TTL

const TYPE_LABEL: Record<string, string> = {
    Destroyer: 'DD',
    Cruiser: 'CA',
    Battleship: 'BB',
    AirCarrier: 'CV',
    Submarine: 'SS',
};
const TYPE_ORDER = ['Destroyer', 'Cruiser', 'Battleship', 'AirCarrier', 'Submarine'];

const fetchRealmTopShips = (realm: string, hours: number, mode: ShipMode): Promise<RealmTopShips> =>
    fetchSharedJson<RealmTopShips>(
        `/api/realm/${encodeURIComponent(realm)}/top-ships?hours=${hours}&mode=${mode}&limit=${SHIP_LIMIT}`,
        {
            label: `RealmTopShips:${realm}:${hours}:${mode}`,
            ttlMs: TOP_SHIPS_FETCH_TTL_MS,
            cacheKey: `top-ships:${realm}:${hours}:${mode}:${SHIP_LIMIT}`,
        },
    ).then(({ data }) => data);

interface HoverState {
    ship: string;
    type: string | null;
    tier: number | null;
    battles: number;
    x: number;
    y: number;
}

const RealmTopShipsTreemapSVG: React.FC<{ hours?: number }> = ({ hours = 24 }) => {
    const { realm } = useRealm();
    const { theme } = useTheme();
    const palette = chartColors[theme];
    const containerRef = useRef<HTMLDivElement | null>(null);
    const svgRef = useRef<SVGSVGElement | null>(null);
    const [data, setData] = useState<RealmTopShips | null>(null);
    const [width, setWidth] = useState(0);
    const [hover, setHover] = useState<HoverState | null>(null);
    const [mode, setMode] = useState<ShipMode>('random');

    const typeColor = useMemo(() => (type: string | null): string => {
        switch (type) {
            case 'Destroyer': return palette.shipDD;
            case 'Cruiser': return palette.shipCA;
            case 'Battleship': return palette.shipBB;
            case 'AirCarrier': return palette.shipCV;
            case 'Submarine': return palette.shipSS;
            default: return palette.shipDefault;
        }
    }, [palette]);

    // Shade the type color by tier around its own lightness: low tier lighter,
    // high tier darker — keeps the type hue recognizable while encoding tier.
    const shadeByTier = (base: string, tier: number | null): string => {
        const c = d3.hsl(base);
        const t = (tier == null ? 6 : Math.max(1, Math.min(11, tier))) / 11;
        c.l = Math.max(0.16, Math.min(0.86, c.l * (1.28 - 0.56 * t)));
        return c.toString();
    };

    useEffect(() => {
        let cancelled = false;
        fetchRealmTopShips(realm, hours, mode)
            .then((d) => { if (!cancelled) setData(d); })
            .catch(() => { if (!cancelled) setData(null); });
        return () => { cancelled = true; };
    }, [realm, hours, mode]);

    useEffect(() => {
        if (!containerRef.current) return undefined;
        const ro = new ResizeObserver((entries) => {
            setWidth(Math.round(entries[0]?.contentRect.width ?? 0));
        });
        ro.observe(containerRef.current);
        return () => ro.disconnect();
    }, []);

    const height = useMemo(
        () => Math.max(280, Math.min(440, Math.round(width * 0.4))),
        [width],
    );

    useEffect(() => {
        if (!svgRef.current || !data || width <= 0 || data.ships.length === 0) return;

        const root = d3.hierarchy({ children: data.ships } as { children: TopShip[] })
            .sum((d: TopShip) => d.battles || 0)
            .sort((a: { value?: number }, b: { value?: number }) => (b.value ?? 0) - (a.value ?? 0));

        d3.treemap().size([width, height]).paddingInner(2).round(true)(root);
        const leaves = root.leaves();

        const svg = d3.select(svgRef.current);
        svg.selectAll('*').remove();
        svg.attr('viewBox', `0 0 ${width} ${height}`).attr('width', '100%').attr('height', height);

        const g = svg.selectAll('g').data(leaves).join('g')
            .attr('transform', (d: { x0: number; y0: number }) => `translate(${d.x0},${d.y0})`);

        g.append('rect')
            .attr('width', (d: { x0: number; x1: number }) => Math.max(0, d.x1 - d.x0))
            .attr('height', (d: { y0: number; y1: number }) => Math.max(0, d.y1 - d.y0))
            .attr('rx', 2)
            .attr('fill', (d: { data: TopShip }) => shadeByTier(typeColor(d.data.ship_type), d.data.tier))
            .attr('stroke', 'var(--bg-card)')
            .attr('stroke-width', 1)
            .style('cursor', 'pointer')
            .on('mousemove', function onMove(this: SVGRectElement, event: MouseEvent, d: { data: TopShip }) {
                const rect = containerRef.current?.getBoundingClientRect();
                setHover({
                    ship: d.data.ship_name,
                    type: d.data.ship_type,
                    tier: d.data.tier,
                    battles: d.data.battles,
                    x: rect ? event.clientX - rect.left : 0,
                    y: rect ? event.clientY - rect.top : 0,
                });
                svg.selectAll('rect').attr('opacity', 0.4);
                d3.select(this).attr('opacity', 1);
            })
            .on('mouseleave', function onLeave() {
                setHover(null);
                svg.selectAll('rect').attr('opacity', 1);
            });

        // Labels on tiles with enough room. Text color picks contrast off the
        // tile's own lightness so it reads on both light and dark shades.
        g.each(function labelTile(this: SVGGElement, d: { x0: number; x1: number; y0: number; y1: number; data: TopShip }) {
            const w = d.x1 - d.x0;
            const h = d.y1 - d.y0;
            if (w < 46 || h < 24) return;
            const fill = shadeByTier(typeColor(d.data.ship_type), d.data.tier);
            const textColor = d3.hsl(fill).l > 0.62 ? '#1a1a1a' : '#f5f5f5';
            const maxChars = Math.max(3, Math.floor((w - 8) / 6.2));
            const name = d.data.ship_name.length > maxChars
                ? `${d.data.ship_name.slice(0, maxChars - 1)}…`
                : d.data.ship_name;
            const node = d3.select(this);
            node.append('text')
                .attr('x', 5).attr('y', 15)
                .attr('font-size', 11).attr('font-weight', 600).attr('fill', textColor)
                .text(name);
            if (h >= 38) {
                node.append('text')
                    .attr('x', 5).attr('y', 29)
                    .attr('font-size', 10).attr('fill', textColor).attr('opacity', 0.85)
                    .text(`${d.data.battles.toLocaleString()} · T${d.data.tier ?? '?'}`);
            }
        });
    }, [data, width, height, palette, typeColor]);

    const presentTypes = useMemo(() => {
        if (!data) return [] as string[];
        const seen = new Set(data.ships.map((s) => s.ship_type ?? ''));
        return TYPE_ORDER.filter((t) => seen.has(t));
    }, [data]);

    return (
        <section
            className="w-full"
            aria-label="Realm top ships treemap"
        >
            <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
                <div className="flex items-center gap-3">
                    <h2 className="text-sm font-semibold uppercase tracking-wide text-[var(--text-muted)]">
                        {realm.toUpperCase()} most-played ships · last {hours}h
                    </h2>
                    <div className="flex items-center gap-1 text-xs" role="group" aria-label="Battle mode">
                        {SHIP_MODES.map((m) => (
                            <button
                                key={m}
                                type="button"
                                onClick={() => setMode(m)}
                                aria-pressed={mode === m}
                                className={`rounded px-2 py-0.5 transition-colors ${
                                    mode === m
                                        ? 'bg-[var(--accent-mid)] text-[var(--bg-card)] font-semibold'
                                        : 'text-[var(--text-muted)] hover:text-[var(--text-strong)]'
                                }`}
                            >
                                {SHIP_MODE_LABEL[m]}
                            </button>
                        ))}
                    </div>
                </div>
                <div className="flex items-center gap-2">
                    {presentTypes.map((t) => (
                        <span key={t} className="flex items-center gap-1 text-[10px] text-[var(--text-muted)]">
                            <span
                                className="inline-block h-2 w-2 rounded-sm"
                                style={{ backgroundColor: typeColor(t) }}
                            />
                            {TYPE_LABEL[t] ?? t}
                        </span>
                    ))}
                </div>
            </div>
            <div ref={containerRef} className="relative w-full">
                <svg ref={svgRef} role="img" aria-label={`${realm} top ${SHIP_LIMIT} most-played ships over ${hours} hours`} />
                {hover && (
                    <div
                        className="pointer-events-none absolute z-10 rounded bg-[var(--bg-page)] px-2 py-1 text-xs shadow-md ring-1 ring-[var(--accent-faint)]"
                        style={{
                            left: Math.min(Math.max(hover.x + 10, 0), Math.max(width - 150, 0)),
                            top: Math.max(hover.y - 40, 0),
                        }}
                    >
                        <div className="font-semibold text-[var(--text-strong)]">{hover.ship}</div>
                        <div className="text-[var(--text-muted)]">
                            {hover.battles.toLocaleString()} battles · {TYPE_LABEL[hover.type ?? ''] ?? hover.type ?? '—'} · T{hover.tier ?? '?'}
                        </div>
                    </div>
                )}
            </div>
        </section>
    );
};

export default RealmTopShipsTreemapSVG;
